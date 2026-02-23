"""Tests for MCP server controller tools."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# Stub out the ``mcp`` package before importing the server module,
# since the MCP SDK may not be installed in the test environment.
if "mcp" not in sys.modules:
    _mcp_mod = ModuleType("mcp")
    _mcp_server_mod = ModuleType("mcp.server")
    _mcp_fastmcp_mod = ModuleType("mcp.server.fastmcp")

    class _FakeFastMCP:
        def __init__(self, *a, **kw): pass
        def tool(self):
            """No-op decorator — return the original function unchanged."""
            return lambda fn: fn
        def run(self, **kw): pass

    _mcp_fastmcp_mod.FastMCP = _FakeFastMCP
    _mcp_mod.server = _mcp_server_mod
    _mcp_server_mod.fastmcp = _mcp_fastmcp_mod
    sys.modules["mcp"] = _mcp_mod
    sys.modules["mcp.server"] = _mcp_server_mod
    sys.modules["mcp.server.fastmcp"] = _mcp_fastmcp_mod

from kachaka_core.connection import KachakaConnection
from kachaka_core.controller import RobotController
from mcp_server.server import (
    _controller_key,
    _controllers,
    controller_move_shelf,
    controller_move_to_location,
    controller_return_shelf,
    get_controller_state,
    start_controller,
    stop_controller,
)


def _make_mock_conn():
    mock_client = MagicMock()
    pose = MagicMock()
    pose.x, pose.y, pose.theta = 1.0, 2.0, 0.5
    mock_client.get_robot_pose.return_value = pose
    mock_client.get_battery_info.return_value = (85, "DISCHARGING")
    mock_client.is_command_running.return_value = False
    with patch("kachaka_core.connection.KachakaApiClient", return_value=mock_client):
        conn = KachakaConnection.get(f"mock-{id(mock_client)}")
    return conn, mock_client


@pytest.fixture(autouse=True)
def _clean_state():
    """Clear controller dict and connection pool before/after each test."""
    _controllers.clear()
    KachakaConnection.clear_pool()
    yield
    for ctrl in _controllers.values():
        ctrl.stop()
    _controllers.clear()
    KachakaConnection.clear_pool()


class TestStartStopController:
    def test_start_creates_entry(self):
        conn, _ = _make_mock_conn()
        ip = conn.target
        with patch("mcp_server.server.KachakaConnection.get", return_value=conn):
            result = start_controller(ip)
        assert result["ok"] is True
        assert result["message"] == "controller started"
        key = _controller_key(ip)
        assert key in _controllers
        assert isinstance(_controllers[key], RobotController)
        _controllers[key].stop()

    def test_stop_removes_entry(self):
        conn, _ = _make_mock_conn()
        ip = conn.target
        with patch("mcp_server.server.KachakaConnection.get", return_value=conn):
            start_controller(ip)
        key = _controller_key(ip)
        assert key in _controllers
        result = stop_controller(ip)
        assert result["ok"] is True
        assert result["message"] == "controller stopped"
        assert key not in _controllers

    def test_stop_when_not_started(self):
        result = stop_controller("10.0.0.1")
        assert result["ok"] is True
        assert result["message"] == "no controller to stop"


class TestStartControllerIdempotent:
    def test_second_call_returns_existing(self):
        conn, _ = _make_mock_conn()
        ip = conn.target
        with patch("mcp_server.server.KachakaConnection.get", return_value=conn):
            result1 = start_controller(ip)
            result2 = start_controller(ip)
        assert result1["ok"] is True
        assert result2["ok"] is True
        assert result2["message"] == "controller already running"
        key = _controller_key(ip)
        assert key in _controllers
        _controllers[key].stop()


class TestControllerCommandWithoutStart:
    def test_move_shelf_without_start(self):
        result = controller_move_shelf("10.0.0.1", "ShelfA", "Room1")
        assert result["ok"] is False
        assert result["error"] == "controller not started"

    def test_return_shelf_without_start(self):
        result = controller_return_shelf("10.0.0.1", "ShelfA")
        assert result["ok"] is False
        assert result["error"] == "controller not started"

    def test_move_to_location_without_start(self):
        result = controller_move_to_location("10.0.0.1", "Kitchen")
        assert result["ok"] is False
        assert result["error"] == "controller not started"


class TestGetControllerState:
    def test_returns_error_when_not_started(self):
        result = get_controller_state("10.0.0.1")
        assert result["ok"] is False
        assert result["error"] == "controller not started"

    def test_returns_state_dict(self):
        conn, _ = _make_mock_conn()
        ip = conn.target
        with patch("mcp_server.server.KachakaConnection.get", return_value=conn):
            start_controller(ip)
        result = get_controller_state(ip)
        assert result["ok"] is True
        expected_keys = {
            "ok", "battery_pct", "pose_x", "pose_y", "pose_theta",
            "is_command_running", "last_updated", "moving_shelf_id",
            "shelf_dropped",
        }
        assert expected_keys == set(result.keys())
        _controllers[_controller_key(ip)].stop()
