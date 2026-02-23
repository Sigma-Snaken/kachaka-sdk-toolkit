"""RobotController — background state polling + non-blocking command execution.

Provides a unified interface for all movement commands (move_to_location,
return_home, move_shelf, return_shelf) with:
- Background thread continuously reading battery, pose, command state
- Non-blocking gRPC command execution with command_id verification
- Deadline-based retry on transient failures
- Built-in metrics collection (RTT, poll counts)
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field

from kachaka_api.generated import kachaka_api_pb2 as pb2

from .connection import KachakaConnection

logger = logging.getLogger(__name__)


@dataclass
class RobotState:
    """Snapshot of robot state, updated by the background polling thread."""
    battery_pct: int = 0
    pose_x: float = 0.0
    pose_y: float = 0.0
    pose_theta: float = 0.0
    is_command_running: bool = False
    last_updated: float = 0.0


@dataclass
class ControllerMetrics:
    """Metrics collected during command execution polling."""
    poll_rtt_list: list[float] = field(default_factory=list)
    poll_count: int = 0
    poll_success_count: int = 0
    poll_failure_count: int = 0

    def reset(self) -> None:
        self.poll_rtt_list.clear()
        self.poll_count = 0
        self.poll_success_count = 0
        self.poll_failure_count = 0


def _call_with_retry(
    func,
    *args,
    deadline: float,
    retry_delay: float = 1.0,
    max_attempts: int = 0,
    **kwargs,
):
    """Call func with retry until deadline or max_attempts.

    Args:
        func: Callable to invoke.
        deadline: Absolute time (perf_counter) after which to stop.
        retry_delay: Seconds between retries.
        max_attempts: Max attempts (0 = unlimited, deadline only).

    Returns:
        The return value of func on success.

    Raises:
        The last exception if all retries fail.
        TimeoutError if deadline passed without any attempt.
    """
    last_err = None
    attempt = 0
    while time.perf_counter() < deadline:
        attempt += 1
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            logger.debug("_call_with_retry %s attempt %d: %s", getattr(func, "__name__", func), attempt, e)
            if max_attempts > 0 and attempt >= max_attempts:
                break
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            time.sleep(min(retry_delay, remaining))
    if last_err is not None:
        raise last_err
    raise TimeoutError("deadline exceeded without any attempt")
