"""Robot action commands — movement, shelf ops, speech, and manual control.

Every public method returns a ``dict`` with at minimum an ``ok`` key.
Network-retryable errors are handled by ``@with_retry``.

Patterns extracted from:
- bio-patrol FleetAPI: move_to_location, move_shelf, return_shelf, speak,
  dock/undock, return_home, cancel_command
- visual-patrol RobotService: move_to_pose, move_forward, rotate_in_place,
  return_home, cancel_command
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from .connection import KachakaConnection
from .error_handling import with_retry

logger = logging.getLogger(__name__)


class KachakaCommands:
    """High-level command interface for a single Kachaka robot."""

    def __init__(self, conn: KachakaConnection):
        self.conn = conn
        self.sdk = conn.client

    # ── Movement ─────────────────────────────────────────────────────

    @with_retry()
    def move_to_location(
        self,
        location_name: str,
        *,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
    ) -> dict:
        """Move to a registered location by name or ID.

        The resolver is initialised on first use so that name lookups work.
        """
        self.conn.ensure_resolver()
        location_id = self.conn.resolve_location(location_name)
        result = self.sdk.move_to_location(
            location_id,
            cancel_all=cancel_all,
            tts_on_success=tts_on_success,
            title=title,
        )
        return self._result_to_dict(result, action="move_to_location", target=location_name)

    @with_retry()
    def move_to_pose(
        self,
        x: float,
        y: float,
        yaw: float,
        *,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
    ) -> dict:
        """Move to an absolute map coordinate ``(x, y, yaw)``."""
        result = self.sdk.move_to_pose(
            x, y, yaw,
            cancel_all=cancel_all,
            tts_on_success=tts_on_success,
            title=title,
        )
        return self._result_to_dict(result, action="move_to_pose", target=f"({x}, {y}, {yaw})")

    @with_retry()
    def move_forward(self, distance_meter: float, *, speed: float = 0.0) -> dict:
        """Move forward (positive) or backward (negative) by *distance_meter*.

        ``speed=0.0`` lets the robot decide. Max is 0.3 m/s.
        """
        result = self.sdk.move_forward(distance_meter, speed=speed)
        return self._result_to_dict(result, action="move_forward", target=f"{distance_meter}m")

    @with_retry()
    def rotate_in_place(self, angle_radian: float) -> dict:
        """Rotate in place by *angle_radian* (positive = counter-clockwise)."""
        result = self.sdk.rotate_in_place(angle_radian)
        return self._result_to_dict(result, action="rotate_in_place", target=f"{angle_radian}rad")

    @with_retry()
    def return_home(
        self,
        *,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
    ) -> dict:
        """Return to charger."""
        result = self.sdk.return_home(
            cancel_all=cancel_all,
            tts_on_success=tts_on_success,
            title=title,
        )
        return self._result_to_dict(result, action="return_home")

    # ── Shelf operations ─────────────────────────────────────────────

    @with_retry()
    def move_shelf(
        self,
        shelf_name: str,
        location_name: str,
        *,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
    ) -> dict:
        """Pick up *shelf_name* and deliver it to *location_name*."""
        self.conn.ensure_resolver()
        shelf_id = self.conn.resolve_shelf(shelf_name)
        location_id = self.conn.resolve_location(location_name)
        result = self.sdk.move_shelf(
            shelf_id,
            location_id,
            cancel_all=cancel_all,
            tts_on_success=tts_on_success,
            title=title,
        )
        return self._result_to_dict(
            result, action="move_shelf", target=f"{shelf_name} -> {location_name}"
        )

    @with_retry()
    def return_shelf(self, shelf_name: str = "", **kwargs) -> dict:
        """Return the shelf to its home location."""
        self.conn.ensure_resolver()
        shelf_id = self.conn.resolve_shelf(shelf_name) if shelf_name else ""
        result = self.sdk.return_shelf(shelf_id, **kwargs)
        return self._result_to_dict(result, action="return_shelf", target=shelf_name or "(current)")

    @with_retry()
    def dock_shelf(self, **kwargs) -> dict:
        """Dock the currently held shelf."""
        result = self.sdk.dock_shelf(**kwargs)
        return self._result_to_dict(result, action="dock_shelf")

    @with_retry()
    def undock_shelf(self, **kwargs) -> dict:
        """Undock the currently held shelf."""
        result = self.sdk.undock_shelf(**kwargs)
        return self._result_to_dict(result, action="undock_shelf")

    @with_retry()
    def reset_shelf_pose(self, shelf_name: str) -> dict:
        """Reset the recorded pose of a shelf."""
        self.conn.ensure_resolver()
        shelf_id = self.conn.resolve_shelf(shelf_name)
        result = self.sdk.reset_shelf_pose(shelf_id)
        return self._result_to_dict(result, action="reset_shelf_pose", target=shelf_name)

    # ── Speech ───────────────────────────────────────────────────────

    @with_retry()
    def speak(
        self,
        text: str,
        *,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
    ) -> dict:
        """Text-to-speech on the robot's speaker."""
        result = self.sdk.speak(
            text,
            cancel_all=cancel_all,
            tts_on_success=tts_on_success,
            title=title,
        )
        return self._result_to_dict(result, action="speak", target=text[:40])

    @with_retry()
    def set_speaker_volume(self, volume: int) -> dict:
        """Set speaker volume (0–10)."""
        volume = max(0, min(10, volume))
        result = self.sdk.set_speaker_volume(volume)
        return self._result_to_dict(result, action="set_speaker_volume", target=str(volume))

    # ── Command control ──────────────────────────────────────────────

    @with_retry()
    def cancel_command(self) -> dict:
        """Cancel the currently running command."""
        result, cmd = self.sdk.cancel_command()
        return {
            "ok": result.success,
            "error_code": result.error_code if not result.success else 0,
            "cancelled_command": str(cmd) if cmd else None,
        }

    @with_retry()
    def proceed(self) -> dict:
        """Resume a command that is waiting for user confirmation."""
        result = self.sdk.proceed()
        return self._result_to_dict(result, action="proceed")

    # ── Manual control ───────────────────────────────────────────────

    @with_retry()
    def set_manual_control(self, enabled: bool) -> dict:
        """Enable or disable manual velocity control mode."""
        result = self.sdk.set_manual_control_enabled(enabled)
        return self._result_to_dict(result, action="set_manual_control", target=str(enabled))

    @with_retry()
    def set_velocity(self, linear: float, angular: float) -> dict:
        """Send velocity command (requires manual-control mode).

        Max linear: 0.3 m/s, max angular: 1.57 rad/s.
        """
        linear = max(-0.3, min(0.3, linear))
        angular = max(-1.57, min(1.57, angular))
        result = self.sdk.set_robot_velocity(linear, angular)
        return self._result_to_dict(
            result, action="set_velocity", target=f"lin={linear}, ang={angular}"
        )

    def stop(self) -> dict:
        """Emergency stop — sets velocity to zero and disables manual control."""
        try:
            self.sdk.set_robot_stop()
            return {"ok": True, "action": "stop"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── Polling ──────────────────────────────────────────────────────

    def poll_until_complete(
        self,
        timeout: float = 120.0,
        interval: float = 0.5,
    ) -> dict:
        """Block until the current command finishes or *timeout* expires.

        Returns the final command state.
        """
        start = time.time()
        while time.time() - start < timeout:
            try:
                if not self.sdk.is_command_running():
                    result, cmd = self.sdk.get_last_command_result()
                    return {
                        "ok": result.success,
                        "error_code": result.error_code,
                        "command": str(cmd) if cmd else None,
                        "elapsed": round(time.time() - start, 1),
                    }
            except Exception as exc:
                logger.debug("poll error: %s", exc)
            time.sleep(interval)
        return {"ok": False, "error": "timeout", "timeout": timeout}

    # ── Internal ─────────────────────────────────────────────────────

    def _resolve_error_description(self, error_code: int) -> str:
        """Fetch error description from robot. Returns empty string on failure."""
        try:
            definitions = self.sdk.get_robot_error_code()
            if error_code in definitions:
                info = definitions[error_code]
                return getattr(info, "title_en", "") or getattr(info, "title", "") or ""
        except Exception:
            logger.debug("Failed to fetch error description for %d", error_code)
        return ""

    def _result_to_dict(self, result, *, action: str = "", target: str = "") -> dict:
        """Convert a ``pb2.Result`` into a standardised response dict."""
        d: dict = {"ok": result.success}
        if not result.success:
            ec = result.error_code
            d["error_code"] = ec
            desc = self._resolve_error_description(ec)
            d["error"] = f"error_code={ec}" + (f": {desc}" if desc else "")
        if action:
            d["action"] = action
        if target:
            d["target"] = target
        return d
