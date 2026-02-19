# Camera-Separate Integration + Deployment Design

**Date:** 2026-02-19
**Status:** Approved

## Context

The `kachaka-connection-test` project completed Round 1 experiments (240 runs across 4 communication modes x 6 network conditions x 10 repeats). Key finding: **Sync + Camera-Separate** is optimal for mesh network robot operations — 30-40% lower RTT, lowest camera drop rates, consistent 0% message loss under all disconnect scenarios.

The current `kachaka-sdk-toolkit` has a complete core layer but:
1. Does not implement the camera-separate pattern
2. Has no git history (0 commits)
3. MCP Server is not registered with Claude
4. Skill is not deployed to `~/.claude/skills/`
5. No global `CLAUDE.md` exists

## Goals

1. Add `CameraStreamer` to `kachaka_core` implementing the camera-separate best practice
2. Update MCP Server with streaming camera tools
3. Update Skill documentation with CameraStreamer usage
4. Complete full deployment: git init, MCP registration, Skill symlink, CLAUDE.md

## Design

### 1. New Module: `kachaka_core/camera.py`

```python
class CameraStreamer:
    """Background thread camera capture — does not block main loop.

    Based on sync_camera_separate pattern from connection-test Round 1.

    Args:
        conn: KachakaConnection instance
        interval: Seconds between captures (default 1.0)
        camera: "front" or "back"
        on_frame: Optional callback(frame_dict) called on each new frame
    """

    def __init__(self, conn, interval=1.0, camera="front", on_frame=None): ...
    def start(self) -> None: ...
    def stop(self) -> None: ...

    @property
    def latest_frame(self) -> dict | None: ...

    @property
    def is_running(self) -> bool: ...

    @property
    def stats(self) -> dict: ...
    # Returns: {"total_frames": int, "dropped": int, "drop_rate_pct": float}
```

**Implementation details:**
- `threading.Thread(daemon=True)` — auto-stops when main program exits
- `threading.Lock` protects `_latest_frame` — no blocking on read
- gRPC errors logged but do not crash the thread; retry on next interval
- `stop_event = threading.Event()` for clean shutdown
- Thread calls `sdk.get_front_camera_ros_compressed_image()` or back equivalent
- Each frame stored as `{"ok": True, "image_base64": "...", "format": "jpeg", "timestamp": float}`

**Existing `queries.py` methods unchanged** — `get_front_camera_image()` and `get_back_camera_image()` remain for single-shot use cases.

### 2. MCP Server Updates

New tools in `mcp_server/server.py`:

| Tool | Purpose |
|------|---------|
| `start_camera_stream(ip, interval, camera)` | Start background camera capture |
| `get_camera_frame(ip, camera)` | Get latest frame from stream |
| `stop_camera_stream(ip, camera)` | Stop background capture |
| `get_camera_stats(ip, camera)` | Get stream statistics |

Module-level `_streamers: dict[str, CameraStreamer]` cache, keyed by `"{target}:{camera}"`.

Existing `capture_front_camera` and `capture_back_camera` tools remain unchanged.

### 3. Skill Documentation Update

Update `skill/SKILL.md` to add:
- CameraStreamer usage section with examples
- Best practice note: prefer CameraStreamer over single-shot for continuous monitoring
- Anti-pattern: don't call `get_front_camera_image()` in tight loops

### 4. Deployment

#### 4a. Git
- First commit with all existing + new code

#### 4b. MCP Server Registration
Add to `~/.claude/settings.json` `mcpServers`:
```json
{
  "kachaka": {
    "command": "/home/snaken/CodeBase/kachaka-sdk-toolkit/.venv/bin/python",
    "args": ["-m", "mcp_server.server"],
    "cwd": "/home/snaken/CodeBase/kachaka-sdk-toolkit"
  }
}
```

#### 4c. Skill Symlink
```bash
mkdir -p ~/.claude/skills
ln -s /home/snaken/CodeBase/kachaka-sdk-toolkit/skill ~/.claude/skills/kachaka-sdk
```

#### 4d. Global CLAUDE.md
Create `~/.claude/CLAUDE.md` with Kachaka section guiding agent to use MCP tools for conversation control and Skill for development reference.

## Files Changed

| File | Action |
|------|--------|
| `kachaka_core/camera.py` | **New** — CameraStreamer class |
| `kachaka_core/__init__.py` | **Edit** — export CameraStreamer |
| `mcp_server/server.py` | **Edit** — add 4 streaming tools |
| `skill/SKILL.md` | **Edit** — add CameraStreamer docs |
| `tests/test_camera.py` | **New** — CameraStreamer tests |
| `~/.claude/settings.json` | **Edit** — add MCP server config |
| `~/.claude/CLAUDE.md` | **New** — global agent guidance |
| `~/.claude/skills/kachaka-sdk` | **New** — symlink |

## Out of Scope

- Async camera streaming (wait for Round 2 results)
- Deadline-based retry (wait for Round 2 results)
- Polling mode task execution (wait for Round 2 results)
