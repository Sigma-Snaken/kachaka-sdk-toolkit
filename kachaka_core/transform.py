"""Background TF transform streaming for Kachaka robots.

Runs a daemon thread that consumes the ``GetDynamicTransform`` server-
streaming RPC, storing the latest transforms for thread-safe retrieval.
On stream errors the thread automatically reconnects with backoff.

Pattern derived from :class:`CameraStreamer`.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Callable, Optional

from kachaka_api.generated import kachaka_api_pb2 as pb2

from .connection import ConnectionState, KachakaConnection

logger = logging.getLogger(__name__)


def _quat_to_yaw(x: float, y: float, z: float, w: float) -> float:
    """Extract yaw (theta) from a quaternion. Same as ROS ``tf2`` convention."""
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def _parse_transform(tf) -> dict:
    """Convert a ``RosTransformStamped`` proto into a plain dict."""
    rx, ry, rz, rw = tf.rotation.x, tf.rotation.y, tf.rotation.z, tf.rotation.w
    return {
        "frame_id": tf.header.frame_id,
        "child_frame_id": tf.child_frame_id,
        "stamp_nsec": tf.header.stamp_nsec,
        "translation": {
            "x": tf.translation.x,
            "y": tf.translation.y,
            "z": tf.translation.z,
        },
        "rotation": {"x": rx, "y": ry, "z": rz, "w": rw},
        "theta": _quat_to_yaw(rx, ry, rz, rw),
    }


class TransformStreamer:
    """Background thread for streaming dynamic TF transforms.

    ``GetDynamicTransform`` is a server-streaming gRPC RPC — the robot
    pushes new transforms continuously.  This class consumes the stream
    in a daemon thread and exposes the latest snapshot via
    :attr:`latest_transforms`.

    Usage::

        conn = KachakaConnection.get("192.168.1.100")
        tf = TransformStreamer(conn)
        tf.start()
        ...
        transforms = tf.latest_transforms  # thread-safe read
        tf.stop()
    """

    def __init__(
        self,
        conn: KachakaConnection,
        on_transform: Optional[Callable[[list[dict]], None]] = None,
        reconnect_delay: float = 2.0,
    ) -> None:
        self._conn = conn
        self._on_transform = on_transform
        self._reconnect_delay = reconnect_delay

        # Thread machinery
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Transform storage (protected by lock)
        self._lock = threading.Lock()
        self._latest_transforms: Optional[list[dict]] = None

        # Counters
        self._total_updates: int = 0
        self._errors: int = 0
        self._last_update_time: float | None = None

    # ── Public API ───────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background stream consumer.  No-op if already running."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("TransformStreamer started")

    def stop(self) -> None:
        """Signal the thread to stop and wait for it to finish."""
        if self._thread is None or not self._thread.is_alive():
            return

        self._stop_event.set()
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            logger.warning("TransformStreamer thread did not stop within timeout")
        else:
            logger.info("TransformStreamer stopped")

    @property
    def latest_transforms(self) -> Optional[list[dict]]:
        """Most recently received dynamic transforms (thread-safe)."""
        with self._lock:
            return self._latest_transforms

    @property
    def is_running(self) -> bool:
        """Whether the stream consumer thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> dict:
        """Stream statistics."""
        return {
            "total_updates": self._total_updates,
            "errors": self._errors,
            "last_update_time": self._last_update_time,
        }

    # ── Internal ─────────────────────────────────────────────────────

    def _run(self) -> None:
        """Main loop: open stream, consume, reconnect on error."""
        stub = self._conn.client.stub

        while not self._stop_event.is_set():
            # Skip while disconnected
            if self._conn.state == ConnectionState.DISCONNECTED:
                self._stop_event.wait(self._reconnect_delay)
                continue

            try:
                stream = stub.GetDynamicTransform(pb2.EmptyRequest())
                for response in stream:
                    if self._stop_event.is_set():
                        break

                    transforms = [
                        _parse_transform(tf) for tf in response.transforms
                    ]

                    with self._lock:
                        self._latest_transforms = transforms

                    self._total_updates += 1
                    self._last_update_time = time.time()

                    if self._on_transform is not None:
                        try:
                            self._on_transform(transforms)
                        except Exception:
                            logger.debug(
                                "on_transform callback error", exc_info=True,
                            )

            except Exception:
                self._errors += 1
                logger.debug(
                    "TransformStreamer stream error (errors=%d)",
                    self._errors,
                    exc_info=True,
                )
                # Backoff before reconnecting
                self._stop_event.wait(self._reconnect_delay)
