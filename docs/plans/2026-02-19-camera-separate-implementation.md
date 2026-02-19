# Camera-Separate Integration + Deployment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add CameraStreamer (background thread camera capture) to kachaka_core, update MCP Server and Skill docs, then deploy everything (git, MCP registration, Skill symlink, CLAUDE.md).

**Architecture:** New `kachaka_core/camera.py` module with `CameraStreamer` class based on the sync_camera_separate pattern proven optimal in connection-test Round 1. MCP Server gets 4 new streaming tools. Existing single-shot camera APIs unchanged.

**Tech Stack:** Python 3.10+, threading, kachaka-api (gRPC), FastMCP, pytest + unittest.mock

---

### Task 1: CameraStreamer — Tests

**Files:**
- Create: `tests/test_camera.py`

**Step 1: Write the failing tests**

```python
"""Tests for kachaka_core.camera — CameraStreamer background capture."""

from __future__ import annotations

import base64
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from kachaka_core.camera import CameraStreamer
from kachaka_core.connection import KachakaConnection


@pytest.fixture(autouse=True)
def _clean_pool():
    KachakaConnection.clear_pool()
    yield
    KachakaConnection.clear_pool()


def _make_conn(mock_client):
    with patch("kachaka_core.connection.KachakaApiClient", return_value=mock_client):
        return KachakaConnection.get("test-robot")


def _make_image(data: bytes = b"fake-jpeg"):
    img = MagicMock()
    img.data = data
    img.format = "jpeg"
    return img


class TestCameraStreamerInit:
    def test_defaults(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        streamer = CameraStreamer(conn)

        assert streamer.is_running is False
        assert streamer.latest_frame is None
        assert streamer.stats == {"total_frames": 0, "dropped": 0, "drop_rate_pct": 0.0}

    def test_invalid_camera_raises(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)

        with pytest.raises(ValueError, match="camera must be"):
            CameraStreamer(conn, camera="side")


class TestCameraStreamerLifecycle:
    def test_start_and_stop(self):
        mock_client = MagicMock()
        mock_client.get_front_camera_ros_compressed_image.return_value = _make_image()
        conn = _make_conn(mock_client)

        streamer = CameraStreamer(conn, interval=0.05)
        streamer.start()
        assert streamer.is_running is True

        time.sleep(0.15)
        streamer.stop()
        assert streamer.is_running is False

    def test_double_start_is_noop(self):
        mock_client = MagicMock()
        mock_client.get_front_camera_ros_compressed_image.return_value = _make_image()
        conn = _make_conn(mock_client)

        streamer = CameraStreamer(conn, interval=0.05)
        streamer.start()
        streamer.start()  # should not raise or start second thread
        streamer.stop()

    def test_stop_without_start_is_noop(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)

        streamer = CameraStreamer(conn)
        streamer.stop()  # should not raise


class TestCameraStreamerCapture:
    def test_captures_front_camera(self):
        mock_client = MagicMock()
        mock_client.get_front_camera_ros_compressed_image.return_value = _make_image(b"front-data")
        conn = _make_conn(mock_client)

        streamer = CameraStreamer(conn, interval=0.05, camera="front")
        streamer.start()
        time.sleep(0.15)
        streamer.stop()

        frame = streamer.latest_frame
        assert frame is not None
        assert frame["ok"] is True
        assert frame["format"] == "jpeg"
        assert frame["image_base64"] == base64.b64encode(b"front-data").decode()
        assert "timestamp" in frame

    def test_captures_back_camera(self):
        mock_client = MagicMock()
        mock_client.get_back_camera_ros_compressed_image.return_value = _make_image(b"back-data")
        conn = _make_conn(mock_client)

        streamer = CameraStreamer(conn, interval=0.05, camera="back")
        streamer.start()
        time.sleep(0.15)
        streamer.stop()

        frame = streamer.latest_frame
        assert frame is not None
        assert frame["image_base64"] == base64.b64encode(b"back-data").decode()

    def test_stats_count_frames(self):
        mock_client = MagicMock()
        mock_client.get_front_camera_ros_compressed_image.return_value = _make_image()
        conn = _make_conn(mock_client)

        streamer = CameraStreamer(conn, interval=0.05)
        streamer.start()
        time.sleep(0.2)
        streamer.stop()

        stats = streamer.stats
        assert stats["total_frames"] > 0
        assert stats["dropped"] == 0
        assert stats["drop_rate_pct"] == 0.0


class TestCameraStreamerErrors:
    def test_grpc_error_increments_dropped(self):
        mock_client = MagicMock()
        mock_client.get_front_camera_ros_compressed_image.side_effect = Exception("network down")
        conn = _make_conn(mock_client)

        streamer = CameraStreamer(conn, interval=0.05)
        streamer.start()
        time.sleep(0.15)
        streamer.stop()

        stats = streamer.stats
        assert stats["dropped"] > 0
        assert stats["total_frames"] == 0
        assert streamer.latest_frame is None  # no successful frame

    def test_recovers_after_error(self):
        mock_client = MagicMock()
        call_count = 0

        def side_effect():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("transient")
            return _make_image()

        mock_client.get_front_camera_ros_compressed_image.side_effect = side_effect
        conn = _make_conn(mock_client)

        streamer = CameraStreamer(conn, interval=0.05)
        streamer.start()
        time.sleep(0.3)
        streamer.stop()

        assert streamer.latest_frame is not None
        assert streamer.stats["dropped"] >= 2
        assert streamer.stats["total_frames"] > 0


class TestCameraStreamerCallback:
    def test_on_frame_called(self):
        mock_client = MagicMock()
        mock_client.get_front_camera_ros_compressed_image.return_value = _make_image()
        conn = _make_conn(mock_client)

        received = []
        streamer = CameraStreamer(conn, interval=0.05, on_frame=received.append)
        streamer.start()
        time.sleep(0.15)
        streamer.stop()

        assert len(received) > 0
        assert received[0]["ok"] is True
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/snaken/CodeBase/kachaka-sdk-toolkit && .venv/bin/python -m pytest tests/test_camera.py -v 2>&1 | head -30`
Expected: FAIL — `ModuleNotFoundError: No module named 'kachaka_core.camera'`

**Step 3: Commit test file**

```bash
cd /home/snaken/CodeBase/kachaka-sdk-toolkit
git add tests/test_camera.py
git commit -m "test: add CameraStreamer tests (red phase)"
```

---

### Task 2: CameraStreamer — Implementation

**Files:**
- Create: `kachaka_core/camera.py`
- Modify: `kachaka_core/__init__.py`

**Step 1: Write CameraStreamer implementation**

```python
"""Background-thread camera capture — does not block the main loop.

Based on the sync_camera_separate pattern proven optimal in
kachaka-connection-test Round 1 (30-40% lower RTT, lowest camera drop rates).

Usage::

    conn = KachakaConnection.get("192.168.1.100")
    streamer = CameraStreamer(conn, interval=1.0)
    streamer.start()
    frame = streamer.latest_frame  # non-blocking
    streamer.stop()
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from typing import Callable, Optional

from .connection import KachakaConnection

logger = logging.getLogger(__name__)

_VALID_CAMERAS = {"front", "back"}


class CameraStreamer:
    """Continuously captures camera frames in a daemon thread.

    Args:
        conn: A :class:`KachakaConnection` instance.
        interval: Seconds between capture attempts (default ``1.0``).
        camera: ``"front"`` or ``"back"`` (default ``"front"``).
        on_frame: Optional callback invoked with the frame dict after
            each successful capture.
    """

    def __init__(
        self,
        conn: KachakaConnection,
        interval: float = 1.0,
        camera: str = "front",
        on_frame: Optional[Callable[[dict], None]] = None,
    ):
        if camera not in _VALID_CAMERAS:
            raise ValueError(f"camera must be one of {_VALID_CAMERAS}, got {camera!r}")
        self._conn = conn
        self._interval = interval
        self._camera = camera
        self._on_frame = on_frame

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[dict] = None
        self._total_frames = 0
        self._dropped = 0

    # ── Public API ────────────────────────────────────────────────────

    def start(self) -> None:
        """Start capturing in a background daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("CameraStreamer started (%s, %.1fs interval)", self._camera, self._interval)

    def stop(self) -> None:
        """Signal the capture thread to stop and wait for it to finish."""
        if self._thread is None or not self._thread.is_alive():
            return
        self._stop_event.set()
        self._thread.join(timeout=5)
        logger.info("CameraStreamer stopped (%s)", self._camera)

    @property
    def latest_frame(self) -> Optional[dict]:
        """Most recent captured frame, or ``None`` if no frame yet."""
        with self._frame_lock:
            return self._latest_frame

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> dict:
        """Capture statistics: total successful frames, dropped, drop rate."""
        total_attempts = self._total_frames + self._dropped
        drop_rate = (self._dropped / total_attempts * 100) if total_attempts > 0 else 0.0
        return {
            "total_frames": self._total_frames,
            "dropped": self._dropped,
            "drop_rate_pct": round(drop_rate, 1),
        }

    # ── Internal ──────────────────────────────────────────────────────

    def _capture_loop(self) -> None:
        sdk = self._conn.client
        capture_fn = (
            sdk.get_front_camera_ros_compressed_image
            if self._camera == "front"
            else sdk.get_back_camera_ros_compressed_image
        )

        while not self._stop_event.is_set():
            try:
                img = capture_fn()
                b64 = base64.b64encode(img.data).decode()
                frame = {
                    "ok": True,
                    "image_base64": b64,
                    "format": getattr(img, "format", "jpeg") or "jpeg",
                    "timestamp": time.time(),
                }
                with self._frame_lock:
                    self._latest_frame = frame
                self._total_frames += 1

                if self._on_frame is not None:
                    try:
                        self._on_frame(frame)
                    except Exception:
                        logger.debug("on_frame callback error", exc_info=True)

            except Exception as exc:
                self._dropped += 1
                logger.debug("CameraStreamer capture error: %s", exc)

            self._stop_event.wait(self._interval)
```

**Step 2: Update `__init__.py` to export CameraStreamer**

Edit `kachaka_core/__init__.py` — add `CameraStreamer` to imports and `__all__`:

```python
"""kachaka_core — Kachaka Robot SDK unified wrapper layer.

Single source of truth shared by MCP Server and Skill.
All robot operations MUST go through this layer.
"""

from .camera import CameraStreamer
from .connection import KachakaConnection
from .commands import KachakaCommands
from .queries import KachakaQueries

__all__ = ["CameraStreamer", "KachakaConnection", "KachakaCommands", "KachakaQueries"]
```

**Step 3: Run tests to verify they pass**

Run: `cd /home/snaken/CodeBase/kachaka-sdk-toolkit && .venv/bin/python -m pytest tests/test_camera.py -v`
Expected: ALL PASS

**Step 4: Run full test suite to verify no regressions**

Run: `cd /home/snaken/CodeBase/kachaka-sdk-toolkit && .venv/bin/python -m pytest tests/ -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
cd /home/snaken/CodeBase/kachaka-sdk-toolkit
git add kachaka_core/camera.py kachaka_core/__init__.py
git commit -m "feat: add CameraStreamer for background camera capture

Based on sync_camera_separate pattern from connection-test Round 1.
Daemon thread captures frames without blocking main loop."
```

---

### Task 3: MCP Server — Streaming Tools

**Files:**
- Modify: `mcp_server/server.py`

**Step 1: Add streaming tools to server.py**

Append after the existing `# ── Camera` section (after `capture_back_camera` tool, before `# ── Map`):

```python
# ── Camera streaming ────────────────────────────────────────────────

from kachaka_core.camera import CameraStreamer

_streamers: dict[str, CameraStreamer] = {}


def _streamer_key(ip: str, camera: str) -> str:
    return f"{KachakaConnection._normalise_target(ip)}:{camera}"


@mcp.tool()
def start_camera_stream(ip: str, interval: float = 1.0, camera: str = "front") -> dict:
    """Start continuous camera capture in a background thread.

    Frames are captured every ``interval`` seconds without blocking other
    operations.  Use ``get_camera_frame`` to retrieve the latest image.
    """
    conn = KachakaConnection.get(ip)
    key = _streamer_key(ip, camera)
    existing = _streamers.get(key)
    if existing is not None and existing.is_running:
        return {"ok": True, "message": "already running", "stats": existing.stats}
    streamer = CameraStreamer(conn, interval=interval, camera=camera)
    streamer.start()
    _streamers[key] = streamer
    return {"ok": True, "message": f"{camera} camera stream started"}


@mcp.tool()
def get_camera_frame(ip: str, camera: str = "front") -> dict:
    """Get the latest frame from a running camera stream.

    Must call ``start_camera_stream`` first.
    """
    key = _streamer_key(ip, camera)
    streamer = _streamers.get(key)
    if streamer is None or not streamer.is_running:
        return {"ok": False, "error": "stream not started — call start_camera_stream first"}
    frame = streamer.latest_frame
    if frame is None:
        return {"ok": False, "error": "no frame captured yet — try again shortly"}
    return frame


@mcp.tool()
def stop_camera_stream(ip: str, camera: str = "front") -> dict:
    """Stop a running camera stream."""
    key = _streamer_key(ip, camera)
    streamer = _streamers.pop(key, None)
    if streamer is None:
        return {"ok": True, "message": "no stream to stop"}
    streamer.stop()
    return {"ok": True, "message": f"{camera} camera stream stopped", "stats": streamer.stats}


@mcp.tool()
def get_camera_stats(ip: str, camera: str = "front") -> dict:
    """Get capture statistics for a running camera stream."""
    key = _streamer_key(ip, camera)
    streamer = _streamers.get(key)
    if streamer is None:
        return {"ok": False, "error": "no stream active"}
    return {"ok": True, **streamer.stats, "is_running": streamer.is_running}
```

Also add the import at the top of the file (alongside existing imports):

```python
from kachaka_core.camera import CameraStreamer
```

**Step 2: Verify server.py has no syntax errors**

Run: `cd /home/snaken/CodeBase/kachaka-sdk-toolkit && .venv/bin/python -c "import mcp_server.server; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
cd /home/snaken/CodeBase/kachaka-sdk-toolkit
git add mcp_server/server.py
git commit -m "feat: add camera streaming MCP tools

start_camera_stream, get_camera_frame, stop_camera_stream, get_camera_stats"
```

---

### Task 4: Skill Documentation Update

**Files:**
- Modify: `skill/SKILL.md`

**Step 1: Add CameraStreamer section to SKILL.md**

Insert after the existing `### Decoding the image` section and before `## Map`:

```markdown
## Camera Streaming (Best Practice)

For continuous monitoring, use `CameraStreamer` instead of calling `get_front_camera_image()` in a loop. This pattern was proven optimal in connection-test Round 1 (30-40% lower RTT, lowest camera drop rates).

```python
from kachaka_core.camera import CameraStreamer
from kachaka_core.connection import KachakaConnection

conn = KachakaConnection.get("192.168.1.100")
streamer = CameraStreamer(conn, interval=1.0, camera="front")
streamer.start()

# Main loop does status queries without camera blocking
while patrolling:
    status = queries.get_status()
    frame = streamer.latest_frame  # non-blocking, returns latest captured frame
    if frame:
        process(frame["image_base64"])
    time.sleep(1.0)

streamer.stop()
print(streamer.stats)  # {"total_frames": 120, "dropped": 3, "drop_rate_pct": 2.4}
```

### With callback

```python
def on_new_frame(frame: dict):
    save_to_disk(frame["image_base64"])

streamer = CameraStreamer(conn, interval=0.5, on_frame=on_new_frame)
streamer.start()
```

### Back camera

```python
streamer = CameraStreamer(conn, camera="back")
```
```

Also update the anti-patterns table — add a new row:

```markdown
| Call `get_front_camera_image()` in tight loop | Use `CameraStreamer` for continuous capture |
```

**Step 2: Commit**

```bash
cd /home/snaken/CodeBase/kachaka-sdk-toolkit
git add skill/SKILL.md
git commit -m "docs: add CameraStreamer usage to SKILL.md"
```

---

### Task 5: Git Initial Commit (existing code)

This task commits all existing code that predates our changes. Do this BEFORE the feature commits to preserve clean history.

**Step 1: Check if there are existing commits**

Run: `cd /home/snaken/CodeBase/kachaka-sdk-toolkit && git log --oneline 2>&1`
Expected: Error or empty (no commits yet)

**Step 2: Create initial commit with all existing files**

```bash
cd /home/snaken/CodeBase/kachaka-sdk-toolkit
git add -A
git commit -m "feat: initial kachaka-sdk-toolkit

Core layer (kachaka_core): connection pool, commands, queries, error handling.
MCP Server: 30+ tools for robot control via Claude.
Skill: SKILL.md documentation for development agents.
Tests: connection, commands, queries."
```

**Important:** This task should be done FIRST before Tasks 1-4, since the repo has no commits yet. The task ordering in execution should be: **Task 5 → Task 1 → Task 2 → Task 3 → Task 4 → Task 6 → Task 7 → Task 8**.

---

### Task 6: MCP Server Registration

**Files:**
- Modify: `~/.claude/settings.json`

**Step 1: Check if venv python exists**

Run: `ls -la /home/snaken/CodeBase/kachaka-sdk-toolkit/.venv/bin/python`
Expected: File exists

**Step 2: Verify server can be imported**

Run: `cd /home/snaken/CodeBase/kachaka-sdk-toolkit && .venv/bin/python -c "import mcp_server.server; print('Server OK')"`
Expected: `Server OK`

**Step 3: Add MCP server config to settings.json**

Read `~/.claude/settings.json`, then add `mcpServers` key:

```json
{
  "env": { ... },
  "enabledPlugins": { ... },
  "mcpServers": {
    "kachaka": {
      "command": "/home/snaken/CodeBase/kachaka-sdk-toolkit/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/home/snaken/CodeBase/kachaka-sdk-toolkit"
    }
  }
}
```

**Step 4: Verify JSON is valid**

Run: `python3 -c "import json; json.load(open('/home/snaken/.claude/settings.json')); print('Valid JSON')"`
Expected: `Valid JSON`

---

### Task 7: Skill Deployment + CLAUDE.md

**Files:**
- Create: `~/.claude/CLAUDE.md`
- Create: `~/.claude/skills/kachaka-sdk` (symlink)

**Step 1: Create skills directory and symlink**

```bash
mkdir -p ~/.claude/skills
ln -sf /home/snaken/CodeBase/kachaka-sdk-toolkit/skill ~/.claude/skills/kachaka-sdk
```

**Step 2: Verify symlink**

Run: `ls -la ~/.claude/skills/kachaka-sdk/SKILL.md`
Expected: File accessible

**Step 3: Create global CLAUDE.md**

Write `~/.claude/CLAUDE.md`:

```markdown
# Global Configuration

## Kachaka Robot

- **Conversation control**: Use MCP tools (ping_robot, move_to_location, speak, etc.)
- **Development reference**: Read skill kachaka-sdk for API usage patterns
- **Core principle**: All robot operations go through kachaka_core — never use KachakaApiClient directly
- **Camera best practice**: Use CameraStreamer (background thread) for continuous capture, not single-shot in loops
- **Robot IP**: Passed as parameter to every tool — do not hard-code
```

---

### Task 8: Final Verification

**Step 1: Run full test suite**

Run: `cd /home/snaken/CodeBase/kachaka-sdk-toolkit && .venv/bin/python -m pytest tests/ -v`
Expected: ALL PASS

**Step 2: Verify git history**

Run: `cd /home/snaken/CodeBase/kachaka-sdk-toolkit && git log --oneline`
Expected: Multiple commits (initial + test + impl + mcp + docs)

**Step 3: Verify MCP config**

Run: `python3 -c "import json; cfg=json.load(open('/home/snaken/.claude/settings.json')); print(cfg['mcpServers']['kachaka'])" 2>&1`
Expected: Shows kachaka MCP config

**Step 4: Verify Skill accessible**

Run: `head -5 ~/.claude/skills/kachaka-sdk/SKILL.md`
Expected: Shows SKILL.md header

**Step 5: Verify CLAUDE.md exists**

Run: `head -5 ~/.claude/CLAUDE.md`
Expected: Shows CLAUDE.md header

---

## Execution Order

```
Task 5: Git initial commit (existing code)
  ↓
Task 1: CameraStreamer tests (red phase)
  ↓
Task 2: CameraStreamer implementation (green phase)
  ↓
Task 3: MCP Server streaming tools
  ↓
Task 4: Skill documentation update
  ↓
Task 6: MCP Server registration
  ↓
Task 7: Skill deployment + CLAUDE.md
  ↓
Task 8: Final verification
```
