"""Tests for kachaka_core.controller — RobotController."""

from __future__ import annotations

import copy
import time
from unittest.mock import MagicMock, patch

import pytest

from kachaka_core.controller import ControllerMetrics, RobotState, _call_with_retry


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
