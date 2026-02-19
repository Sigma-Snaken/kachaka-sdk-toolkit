"""MCP Server for Kachaka Robot — thin wrapper around kachaka_core.

Each tool is a one-liner delegation to the shared core layer.
Run with: ``python -m mcp_server.server`` or ``python mcp_server/server.py``

Transport: stdio (default for Claude Desktop / Claude Code).
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from kachaka_core.commands import KachakaCommands
from kachaka_core.connection import KachakaConnection
from kachaka_core.queries import KachakaQueries

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

mcp = FastMCP(
    "kachaka-robot",
    instructions=(
        "Kachaka Robot control tools. All tools require an ``ip`` parameter "
        "(e.g. '192.168.1.100' or '192.168.1.100:26400'). "
        "Port 26400 is appended automatically when omitted."
    ),
)


# ── Connection ───────────────────────────────────────────────────────

@mcp.tool()
def ping_robot(ip: str) -> dict:
    """Test gRPC connectivity and return serial number + current pose."""
    return KachakaConnection.get(ip).ping()


@mcp.tool()
def disconnect_robot(ip: str) -> dict:
    """Remove robot from connection pool (useful after IP change)."""
    KachakaConnection.remove(ip)
    return {"ok": True, "message": f"Removed {ip} from pool"}


# ── Status queries ───────────────────────────────────────────────────

@mcp.tool()
def get_robot_status(ip: str) -> dict:
    """Full snapshot: pose, battery, command state, errors, moving shelf."""
    return KachakaQueries(KachakaConnection.get(ip)).get_status()


@mcp.tool()
def get_robot_pose(ip: str) -> dict:
    """Current robot position on the map (x, y, theta)."""
    return KachakaQueries(KachakaConnection.get(ip)).get_pose()


@mcp.tool()
def get_battery(ip: str) -> dict:
    """Battery percentage and charging status."""
    return KachakaQueries(KachakaConnection.get(ip)).get_battery()


@mcp.tool()
def get_errors(ip: str) -> dict:
    """Active error codes on the robot."""
    return KachakaQueries(KachakaConnection.get(ip)).get_errors()


@mcp.tool()
def get_robot_info(ip: str) -> dict:
    """Serial number and firmware version."""
    conn = KachakaConnection.get(ip)
    q = KachakaQueries(conn)
    serial = q.get_serial_number()
    version = q.get_version()
    if serial["ok"] and version["ok"]:
        return {"ok": True, "serial": serial["serial"], "version": version["version"]}
    return serial if not serial["ok"] else version


# ── Locations & shelves ──────────────────────────────────────────────

@mcp.tool()
def list_locations(ip: str) -> dict:
    """All registered locations (name, id, type, pose)."""
    return KachakaQueries(KachakaConnection.get(ip)).list_locations()


@mcp.tool()
def list_shelves(ip: str) -> dict:
    """All registered shelves (name, id, home location)."""
    return KachakaQueries(KachakaConnection.get(ip)).list_shelves()


@mcp.tool()
def get_moving_shelf(ip: str) -> dict:
    """ID of the shelf the robot is currently carrying."""
    return KachakaQueries(KachakaConnection.get(ip)).get_moving_shelf()


# ── Movement ─────────────────────────────────────────────────────────

@mcp.tool()
def move_to_location(ip: str, location_name: str) -> dict:
    """Move robot to a registered location by name or ID.

    Use ``list_locations`` first to see available destinations.
    This is a blocking call — returns when movement completes.
    """
    return KachakaCommands(KachakaConnection.get(ip)).move_to_location(location_name)


@mcp.tool()
def move_to_pose(ip: str, x: float, y: float, yaw: float) -> dict:
    """Move robot to absolute map coordinates (x, y, yaw in radians)."""
    return KachakaCommands(KachakaConnection.get(ip)).move_to_pose(x, y, yaw)


@mcp.tool()
def move_forward(ip: str, distance_meter: float) -> dict:
    """Move forward (positive) or backward (negative) by a distance in meters."""
    return KachakaCommands(KachakaConnection.get(ip)).move_forward(distance_meter)


@mcp.tool()
def rotate(ip: str, angle_radian: float) -> dict:
    """Rotate in place. Positive = counter-clockwise."""
    return KachakaCommands(KachakaConnection.get(ip)).rotate_in_place(angle_radian)


@mcp.tool()
def return_home(ip: str) -> dict:
    """Send robot back to its charger."""
    return KachakaCommands(KachakaConnection.get(ip)).return_home()


# ── Shelf operations ─────────────────────────────────────────────────

@mcp.tool()
def move_shelf(ip: str, shelf_name: str, location_name: str) -> dict:
    """Pick up a shelf and deliver it to a location (by name or ID)."""
    return KachakaCommands(KachakaConnection.get(ip)).move_shelf(shelf_name, location_name)


@mcp.tool()
def return_shelf(ip: str, shelf_name: str = "") -> dict:
    """Return the currently held (or named) shelf to its home location."""
    return KachakaCommands(KachakaConnection.get(ip)).return_shelf(shelf_name)


@mcp.tool()
def dock_shelf(ip: str) -> dict:
    """Dock the currently held shelf onto the robot."""
    return KachakaCommands(KachakaConnection.get(ip)).dock_shelf()


@mcp.tool()
def undock_shelf(ip: str) -> dict:
    """Undock the currently held shelf from the robot."""
    return KachakaCommands(KachakaConnection.get(ip)).undock_shelf()


# ── Speech ───────────────────────────────────────────────────────────

@mcp.tool()
def speak(ip: str, text: str) -> dict:
    """Make the robot speak text via TTS."""
    return KachakaCommands(KachakaConnection.get(ip)).speak(text)


@mcp.tool()
def set_volume(ip: str, volume: int) -> dict:
    """Set speaker volume (0–10)."""
    return KachakaCommands(KachakaConnection.get(ip)).set_speaker_volume(volume)


@mcp.tool()
def get_volume(ip: str) -> dict:
    """Get current speaker volume."""
    return KachakaQueries(KachakaConnection.get(ip)).get_speaker_volume()


# ── Command control ──────────────────────────────────────────────────

@mcp.tool()
def cancel_command(ip: str) -> dict:
    """Cancel the currently running command."""
    return KachakaCommands(KachakaConnection.get(ip)).cancel_command()


@mcp.tool()
def get_command_state(ip: str) -> dict:
    """Check whether a command is running and its current state."""
    return KachakaQueries(KachakaConnection.get(ip)).get_command_state()


@mcp.tool()
def get_last_result(ip: str) -> dict:
    """Result of the most recently completed command."""
    return KachakaQueries(KachakaConnection.get(ip)).get_last_command_result()


# ── Camera ───────────────────────────────────────────────────────────

@mcp.tool()
def capture_front_camera(ip: str) -> dict:
    """Capture a JPEG from the front camera (returned as base64)."""
    return KachakaQueries(KachakaConnection.get(ip)).get_front_camera_image()


@mcp.tool()
def capture_back_camera(ip: str) -> dict:
    """Capture a JPEG from the back camera (returned as base64)."""
    return KachakaQueries(KachakaConnection.get(ip)).get_back_camera_image()


# ── Map ──────────────────────────────────────────────────────────────

@mcp.tool()
def get_map(ip: str) -> dict:
    """Current map as base64 PNG with metadata."""
    return KachakaQueries(KachakaConnection.get(ip)).get_map()


@mcp.tool()
def list_maps(ip: str) -> dict:
    """All available maps and the currently active map ID."""
    return KachakaQueries(KachakaConnection.get(ip)).list_maps()


# ── Shortcuts ────────────────────────────────────────────────────────

@mcp.tool()
def list_shortcuts(ip: str) -> dict:
    """All registered shortcuts (id -> name)."""
    return KachakaQueries(KachakaConnection.get(ip)).list_shortcuts()


# ── History ──────────────────────────────────────────────────────────

@mcp.tool()
def get_history(ip: str) -> dict:
    """Recent command execution history."""
    return KachakaQueries(KachakaConnection.get(ip)).get_history()


# ── Manual control ───────────────────────────────────────────────────

@mcp.tool()
def enable_manual_control(ip: str, enabled: bool) -> dict:
    """Enable or disable manual velocity control mode."""
    return KachakaCommands(KachakaConnection.get(ip)).set_manual_control(enabled)


@mcp.tool()
def set_velocity(ip: str, linear: float, angular: float) -> dict:
    """Set robot velocity (requires manual-control mode). Max: 0.3 m/s, 1.57 rad/s."""
    return KachakaCommands(KachakaConnection.get(ip)).set_velocity(linear, angular)


@mcp.tool()
def emergency_stop(ip: str) -> dict:
    """Immediately stop the robot and disable manual control."""
    return KachakaCommands(KachakaConnection.get(ip)).stop()


# ── Entry point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
