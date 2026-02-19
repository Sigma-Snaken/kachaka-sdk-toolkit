"""Background camera capture for Kachaka robots.

Runs a daemon thread that periodically grabs a JPEG frame from the
front or back camera, encodes it as base64, and stores it for
thread-safe retrieval.  Errors are logged but never crash the thread.

Pattern derived from sync_camera_separate in connection-test Round 1.
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
    """Background thread camera capture — does not block main loop.

    Usage::

        conn = KachakaConnection.get("192.168.1.100")
        cam = CameraStreamer(conn, interval=1.0, camera="front")
        cam.start()
        ...
        frame = cam.latest_frame   # thread-safe read
        cam.stop()
    """

    def __init__(
        self,
        conn: KachakaConnection,
        interval: float = 1.0,
        camera: str = "front",
        on_frame: Optional[Callable[[dict], None]] = None,
    ) -> None:
        if camera not in _VALID_CAMERAS:
            raise ValueError(
                f"Invalid camera {camera!r}; must be one of {_VALID_CAMERAS}"
            )

        self._conn = conn
        self._interval = interval
        self._camera = camera
        self._on_frame = on_frame

        # Thread machinery
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Frame storage (protected by lock)
        self._lock = threading.Lock()
        self._latest_frame: Optional[dict] = None

        # Counters (only written by the capture thread, reads are atomic on CPython)
        self._total_frames: int = 0
        self._dropped: int = 0

    # ── Public API ───────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background capture thread.  No-op if already running."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("CameraStreamer started (camera=%s, interval=%.2fs)", self._camera, self._interval)

    def stop(self) -> None:
        """Signal the thread to stop and wait for it to finish.  No-op if not running."""
        if self._thread is None or not self._thread.is_alive():
            return

        self._stop_event.set()
        self._thread.join(timeout=self._interval * 3)
        if self._thread.is_alive():
            logger.warning("CameraStreamer thread did not stop within timeout")
        else:
            logger.info("CameraStreamer stopped")

    @property
    def latest_frame(self) -> Optional[dict]:
        """Return the most recently captured frame (thread-safe)."""
        with self._lock:
            return self._latest_frame

    @property
    def is_running(self) -> bool:
        """Whether the capture thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> dict:
        """Capture statistics: total_frames, dropped, drop_rate_pct."""
        total = self._total_frames
        dropped = self._dropped
        rate = (dropped / total * 100.0) if total > 0 else 0.0
        return {
            "total_frames": total,
            "dropped": dropped,
            "drop_rate_pct": rate,
        }

    # ── Internal ─────────────────────────────────────────────────────

    def _run(self) -> None:
        """Main loop executed in the daemon thread."""
        sdk = self._conn.client

        if self._camera == "front":
            capture_fn = sdk.get_front_camera_ros_compressed_image
        else:
            capture_fn = sdk.get_back_camera_ros_compressed_image

        while not self._stop_event.is_set():
            self._total_frames += 1
            try:
                img = capture_fn()
                b64 = base64.b64encode(img.data).decode()
                frame: dict = {
                    "ok": True,
                    "image_base64": b64,
                    "format": img.format or "jpeg",
                    "timestamp": time.time(),
                }
                with self._lock:
                    self._latest_frame = frame

                # Fire callback (errors in callback must not kill the thread)
                if self._on_frame is not None:
                    try:
                        self._on_frame(frame)
                    except Exception:
                        logger.warning("on_frame callback raised an exception", exc_info=True)

            except Exception:
                self._dropped += 1
                logger.debug(
                    "CameraStreamer capture error (camera=%s, dropped=%d)",
                    self._camera,
                    self._dropped,
                    exc_info=True,
                )

            # Interruptible sleep — returns immediately if stop_event is set
            self._stop_event.wait(self._interval)
