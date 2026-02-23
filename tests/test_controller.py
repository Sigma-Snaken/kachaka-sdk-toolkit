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
        # Thread should still be alive despite fast-cycle errors
        assert ctrl._thread is not None and ctrl._thread.is_alive()
        # Battery (slow cycle) should still update since only pose errors
        state = ctrl.state
        assert state.battery_pct == 85
        ctrl.stop()


# ── _execute_command and movement command tests ──────────────────


class TestExecuteCommand:
    """Tests for _execute_command engine and movement command wrappers."""

    def setup_method(self):
        KachakaConnection.clear_pool()

    def teardown_method(self):
        KachakaConnection.clear_pool()

    def _make_ctrl(self, mock_client):
        """Create a RobotController with custom mock client.

        Uses high intervals (60s) so the state polling thread never
        interferes with tests.  The controller is NOT started — command
        execution doesn't require the state thread.
        """
        conn, _ = _make_mock_conn()
        conn._client = mock_client  # Override with our custom mock
        ctrl = RobotController(
            conn, fast_interval=60, slow_interval=60, poll_interval=0.05
        )
        return ctrl

    # ── helpers for building mock stub responses ─────────────

    @staticmethod
    def _start_cmd_response(success=True, command_id="cmd-abc", error_code=0):
        resp = MagicMock()
        resp.result.success = success
        resp.result.error_code = error_code
        resp.command_id = command_id
        return resp

    @staticmethod
    def _cmd_state_response(state, command_id="cmd-abc"):
        resp = MagicMock()
        resp.state = state
        resp.command_id = command_id
        return resp

    @staticmethod
    def _last_result_response(success=True, command_id="cmd-abc", error_code=0):
        resp = MagicMock()
        resp.result.success = success
        resp.result.error_code = error_code
        resp.command_id = command_id
        return resp

    # ── test_command_success ─────────────────────────────────

    def test_command_success(self):
        mock_client = MagicMock()
        stub = mock_client.stub

        # StartCommand → success, command_id="cmd-abc"
        stub.StartCommand.return_value = self._start_cmd_response(
            success=True, command_id="cmd-abc"
        )

        # GetCommandState: RUNNING first, then UNSPECIFIED (command done)
        stub.GetCommandState.side_effect = [
            # registration poll(s)
            self._cmd_state_response(2, "cmd-abc"),  # RUNNING → registered
            # main poll: still RUNNING
            self._cmd_state_response(2, "cmd-abc"),
            # main poll: no longer RUNNING (UNSPECIFIED = 0)
            self._cmd_state_response(0, "cmd-abc"),
        ]

        # GetLastCommandResult → success, matching command_id
        stub.GetLastCommandResult.return_value = self._last_result_response(
            success=True, command_id="cmd-abc"
        )

        # Resolver for move_to_location
        mock_client.resolver.resolve_location_id_or_name.return_value = "loc-123"

        ctrl = self._make_ctrl(mock_client)
        # ensure_resolver is on the connection
        ctrl._conn._resolver_ready = True

        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.move_to_location("Kitchen", timeout=10.0)

        assert result["ok"] is True
        assert result["action"] == "move_to_location"
        assert "elapsed" in result

    # ── test_command_start_failure ────────────────────────────

    def test_command_start_failure(self):
        mock_client = MagicMock()
        stub = mock_client.stub

        stub.StartCommand.return_value = self._start_cmd_response(
            success=False, command_id="", error_code=13
        )

        ctrl = self._make_ctrl(mock_client)

        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.return_home(timeout=10.0)

        assert result["ok"] is False
        assert result["error_code"] == 13

    # ── test_command_timeout ─────────────────────────────────

    def test_command_timeout(self):
        mock_client = MagicMock()
        stub = mock_client.stub

        stub.StartCommand.return_value = self._start_cmd_response(
            success=True, command_id="cmd-timeout"
        )

        # Always return RUNNING so the command never finishes
        stub.GetCommandState.return_value = self._cmd_state_response(
            2, "cmd-timeout"  # COMMAND_STATE_RUNNING = 2
        )

        ctrl = self._make_ctrl(mock_client)

        # Use a very short timeout so the test completes quickly
        # We patch time.sleep to avoid real waits, but perf_counter
        # advances naturally through the loop overhead... so we patch it.
        call_count = 0
        base_time = time.perf_counter()

        def fake_perf_counter():
            nonlocal call_count
            call_count += 1
            # After a few calls, jump past the deadline
            if call_count > 10:
                return base_time + 100.0  # way past any deadline
            return base_time + call_count * 0.01

        with patch("kachaka_core.controller.time.sleep"):
            with patch("kachaka_core.controller.time.perf_counter", side_effect=fake_perf_counter):
                result = ctrl.return_home(timeout=0.5)

        assert result["ok"] is False
        assert result["error"] == "TIMEOUT"

    # ── test_metrics_recorded ────────────────────────────────

    def test_metrics_recorded(self):
        mock_client = MagicMock()
        stub = mock_client.stub

        stub.StartCommand.return_value = self._start_cmd_response(
            success=True, command_id="cmd-met"
        )

        # Registration poll: RUNNING (registered)
        # Main poll: RUNNING, then UNSPECIFIED
        stub.GetCommandState.side_effect = [
            self._cmd_state_response(2, "cmd-met"),   # registered
            self._cmd_state_response(2, "cmd-met"),   # still running
            self._cmd_state_response(0, "cmd-met"),   # done
        ]

        stub.GetLastCommandResult.return_value = self._last_result_response(
            success=True, command_id="cmd-met"
        )

        ctrl = self._make_ctrl(mock_client)
        ctrl.reset_metrics()

        with patch("kachaka_core.controller.time.sleep"):
            ctrl.return_home(timeout=10.0)

        assert ctrl.metrics.poll_count >= 1
        assert ctrl.metrics.poll_success_count >= 1
