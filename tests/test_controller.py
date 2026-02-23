"""Tests for kachaka_core.controller — RobotController."""

from __future__ import annotations

import copy
import time
from unittest.mock import MagicMock, patch

import pytest

from kachaka_core.controller import (
    ControllerMetrics,
    RobotController,
    RobotState,
    _call_with_retry,
)
from kachaka_core.connection import KachakaConnection


class TestRobotState:
    def test_default_values(self):
        state = RobotState()
        assert state.battery_pct == 0
        assert state.pose_x == 0.0
        assert state.pose_y == 0.0
        assert state.pose_theta == 0.0
        assert state.is_command_running is False
        assert state.last_updated == 0.0

    def test_snapshot_is_independent_copy(self):
        state = RobotState(battery_pct=85, pose_x=1.0)
        snapshot = copy.copy(state)
        snapshot.battery_pct = 50
        assert state.battery_pct == 85


class TestControllerMetrics:
    def test_default_values(self):
        m = ControllerMetrics()
        assert m.poll_rtt_list == []
        assert m.poll_count == 0
        assert m.poll_success_count == 0
        assert m.poll_failure_count == 0

    def test_reset(self):
        m = ControllerMetrics()
        m.poll_rtt_list.append(12.3)
        m.poll_count = 5
        m.poll_success_count = 4
        m.poll_failure_count = 1
        m.reset()
        assert m.poll_rtt_list == []
        assert m.poll_count == 0
        assert m.poll_success_count == 0
        assert m.poll_failure_count == 0


class TestCallWithRetry:
    def test_success_first_try(self):
        func = MagicMock(return_value=42)
        deadline = time.perf_counter() + 5
        result = _call_with_retry(func, deadline=deadline)
        assert result == 42
        assert func.call_count == 1

    def test_retries_on_failure(self):
        func = MagicMock(side_effect=[Exception("fail"), Exception("fail"), 42])
        deadline = time.perf_counter() + 10
        with patch("kachaka_core.controller.time.sleep"):
            result = _call_with_retry(func, deadline=deadline, retry_delay=0.1)
        assert result == 42
        assert func.call_count == 3

    def test_raises_last_error_after_deadline(self):
        func = MagicMock(side_effect=Exception("always fails"))
        deadline = time.perf_counter() + 0.05  # expires quickly after first attempt
        with pytest.raises(Exception, match="always fails"):
            _call_with_retry(func, deadline=deadline, retry_delay=0.01)

    def test_raises_timeout_if_no_attempt(self):
        func_never_called = MagicMock()
        deadline = time.perf_counter() - 1
        with pytest.raises(TimeoutError):
            _call_with_retry(func_never_called, deadline=deadline)

    def test_max_attempts_respected(self):
        func = MagicMock(side_effect=Exception("fail"))
        deadline = time.perf_counter() + 60
        with patch("kachaka_core.controller.time.sleep"):
            with pytest.raises(Exception, match="fail"):
                _call_with_retry(func, deadline=deadline, max_attempts=2, retry_delay=0.01)
        assert func.call_count == 2

    def test_passes_args_and_kwargs(self):
        func = MagicMock(return_value="ok")
        deadline = time.perf_counter() + 5
        _call_with_retry(func, "a", "b", deadline=deadline, key="val")
        func.assert_called_once_with("a", "b", key="val")


# ── Helpers ───────────────────────────────────────────────────────


def _make_mock_conn():
    """Create a KachakaConnection with a fully mocked client."""
    mock_client = MagicMock()
    # Default stub responses for state polling
    pose = MagicMock()
    pose.x, pose.y, pose.theta = 1.0, 2.0, 0.5
    mock_client.get_robot_pose.return_value = pose

    battery = (85, "DISCHARGING")
    mock_client.get_battery_info.return_value = battery

    mock_client.is_command_running.return_value = False

    with patch("kachaka_core.connection.KachakaApiClient", return_value=mock_client):
        conn = KachakaConnection.get(f"mock-{id(mock_client)}")
    return conn, mock_client


# ── RobotController lifecycle tests ──────────────────────────────


class TestRobotControllerLifecycle:
    def setup_method(self):
        KachakaConnection.clear_pool()

    def teardown_method(self):
        KachakaConnection.clear_pool()

    def test_init(self):
        conn, _ = _make_mock_conn()
        ctrl = RobotController(conn)
        assert ctrl.state.battery_pct == 0  # not yet started

    def test_start_stop(self):
        conn, mock_client = _make_mock_conn()
        ctrl = RobotController(conn, fast_interval=0.05, slow_interval=0.05)
        ctrl.start()
        time.sleep(0.2)  # let state thread run a few cycles
        state = ctrl.state
        assert state.battery_pct == 85
        assert state.pose_x == 1.0
        assert state.pose_y == 2.0
        assert state.is_command_running is False
        assert state.last_updated > 0
        ctrl.stop()

    def test_start_is_idempotent(self):
        conn, _ = _make_mock_conn()
        ctrl = RobotController(conn, fast_interval=0.05)
        ctrl.start()
        ctrl.start()  # should not crash
        ctrl.stop()

    def test_stop_is_idempotent(self):
        conn, _ = _make_mock_conn()
        ctrl = RobotController(conn, fast_interval=0.05)
        ctrl.start()
        ctrl.stop()
        ctrl.stop()  # should not crash

    def test_state_survives_grpc_error(self):
        conn, mock_client = _make_mock_conn()
        mock_client.get_robot_pose.side_effect = Exception("network error")
        ctrl = RobotController(conn, fast_interval=0.05, slow_interval=0.05)
        ctrl.start()
        time.sleep(0.2)
        # Thread should survive errors, state may have partial updates
        ctrl.stop()  # should not hang
