"""Tests for @with_retry decorator, including deadline mode."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import grpc
import pytest

from kachaka_core.error_handling import with_retry, RETRYABLE_CODES


def _make_rpc_error(code: grpc.StatusCode, details: str = "") -> grpc.RpcError:
    """Create a mock gRPC RpcError."""
    exc = grpc.RpcError()
    exc.code = MagicMock(return_value=code)
    exc.details = MagicMock(return_value=details)
    return exc


class TestWithRetryExisting:
    """Verify existing count-based behavior is unchanged."""

    def test_success_first_try(self):
        @with_retry(max_attempts=3)
        def op():
            return {"ok": True, "value": 42}

        result = op()
        assert result == {"ok": True, "value": 42}

    def test_retries_on_unavailable(self):
        call_count = 0

        @with_retry(max_attempts=3, base_delay=0.01)
        def op():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise _make_rpc_error(grpc.StatusCode.UNAVAILABLE, "transient")
            return {"ok": True}

        result = op()
        assert result["ok"] is True
        assert call_count == 3

    def test_no_retry_on_non_retryable(self):
        @with_retry(max_attempts=3, base_delay=0.01)
        def op():
            raise _make_rpc_error(grpc.StatusCode.INVALID_ARGUMENT, "bad arg")

        result = op()
        assert result["ok"] is False
        assert result["retryable"] is False
        assert "INVALID_ARGUMENT" in result["error"]

    def test_exhausted_retries(self):
        @with_retry(max_attempts=2, base_delay=0.01)
        def op():
            raise _make_rpc_error(grpc.StatusCode.UNAVAILABLE, "down")

        result = op()
        assert result["ok"] is False
        assert result["retryable"] is True
        assert result["attempts"] == 2


class TestWithRetryDeadline:
    """Test new deadline mode."""

    def test_deadline_retries_until_deadline(self):
        """deadline mode should retry until time runs out."""
        call_count = 0

        @with_retry(deadline=0.5, base_delay=0.05, max_delay=0.1)
        def op():
            nonlocal call_count
            call_count += 1
            raise _make_rpc_error(grpc.StatusCode.UNAVAILABLE, "down")

        result = op()
        assert result["ok"] is False
        assert result["retryable"] is True
        # With 0.5s deadline and ~0.05-0.1s delays, should get multiple attempts
        assert call_count >= 3

    def test_deadline_succeeds_after_transient_failures(self):
        """Should succeed if function recovers before deadline."""
        call_count = 0

        @with_retry(deadline=2.0, base_delay=0.05)
        def op():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise _make_rpc_error(grpc.StatusCode.DEADLINE_EXCEEDED, "timeout")
            return {"ok": True, "recovered": True}

        result = op()
        assert result["ok"] is True
        assert result["recovered"] is True
        assert call_count == 4

    def test_deadline_overrides_max_attempts(self):
        """When deadline is set, max_attempts is ignored."""
        call_count = 0

        @with_retry(max_attempts=2, deadline=0.5, base_delay=0.05, max_delay=0.1)
        def op():
            nonlocal call_count
            call_count += 1
            raise _make_rpc_error(grpc.StatusCode.UNAVAILABLE, "down")

        result = op()
        assert result["ok"] is False
        # max_attempts=2 would only allow 2, deadline should allow more
        assert call_count > 2

    def test_deadline_non_retryable_fails_immediately(self):
        """Non-retryable errors should still fail immediately in deadline mode."""
        call_count = 0

        @with_retry(deadline=2.0, base_delay=0.05)
        def op():
            nonlocal call_count
            call_count += 1
            raise _make_rpc_error(grpc.StatusCode.INVALID_ARGUMENT, "bad")

        result = op()
        assert result["ok"] is False
        assert result["retryable"] is False
        assert call_count == 1

    def test_deadline_unexpected_exception_fails_immediately(self):
        """Non-gRPC exceptions should still fail immediately."""
        @with_retry(deadline=2.0, base_delay=0.05)
        def op():
            raise ValueError("not a gRPC error")

        result = op()
        assert result["ok"] is False
        assert "not a gRPC error" in result["error"]

    def test_deadline_respects_time_limit(self):
        """Deadline mode should not run significantly past the deadline."""
        @with_retry(deadline=0.3, base_delay=0.05, max_delay=0.1)
        def op():
            raise _make_rpc_error(grpc.StatusCode.UNAVAILABLE, "down")

        start = time.perf_counter()
        result = op()
        elapsed = time.perf_counter() - start

        assert result["ok"] is False
        # Should finish within deadline + one max_delay margin
        assert elapsed < 0.6, f"Took {elapsed:.2f}s, expected < 0.6s"

    def test_deadline_backoff_capped_by_remaining_time(self):
        """Sleep should not exceed remaining time before deadline."""
        call_times = []

        @with_retry(deadline=0.4, base_delay=0.05, max_delay=10.0)
        def op():
            call_times.append(time.perf_counter())
            raise _make_rpc_error(grpc.StatusCode.UNAVAILABLE, "down")

        start = time.perf_counter()
        op()
        elapsed = time.perf_counter() - start

        # Even though max_delay=10s, should finish around deadline
        assert elapsed < 0.8

    def test_deadline_includes_attempts_in_result(self):
        """Result should include attempt count."""
        call_count = 0

        @with_retry(deadline=0.3, base_delay=0.05)
        def op():
            nonlocal call_count
            call_count += 1
            raise _make_rpc_error(grpc.StatusCode.UNAVAILABLE, "down")

        result = op()
        assert result["attempts"] == call_count

    def test_without_deadline_unchanged(self):
        """Existing behavior preserved when deadline not set."""
        call_count = 0

        @with_retry(max_attempts=3, base_delay=0.01)
        def op():
            nonlocal call_count
            call_count += 1
            raise _make_rpc_error(grpc.StatusCode.UNAVAILABLE, "down")

        result = op()
        assert call_count == 3
        assert result["attempts"] == 3
