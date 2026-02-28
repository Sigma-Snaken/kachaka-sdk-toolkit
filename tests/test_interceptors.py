"""Tests for gRPC timeout interceptor."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import grpc
import pytest

from kachaka_core.interceptors import TimeoutInterceptor


class FakeCallDetails:
    """Minimal ClientCallDetails for testing."""

    def __init__(self, method="/test", timeout=None):
        self.method = method
        self.timeout = timeout
        self.metadata = None
        self.credentials = None
        self.wait_for_ready = None
        self.compression = None


class TestTimeoutInterceptor:
    def test_adds_timeout_when_none(self):
        """Interceptor should add default timeout to calls with no timeout."""
        interceptor = TimeoutInterceptor(default_timeout=10.0)
        original = FakeCallDetails(timeout=None)
        captured = {}

        def fake_continuation(call_details, request):
            captured["timeout"] = call_details.timeout
            return "ok"

        result = interceptor.intercept_unary_unary(
            fake_continuation, original, b"request"
        )
        assert result == "ok"
        assert captured["timeout"] == 10.0

    def test_preserves_explicit_timeout(self):
        """Interceptor should NOT override an explicitly set timeout."""
        interceptor = TimeoutInterceptor(default_timeout=10.0)
        original = FakeCallDetails(timeout=3.0)
        captured = {}

        def fake_continuation(call_details, request):
            captured["timeout"] = call_details.timeout
            return "ok"

        result = interceptor.intercept_unary_unary(
            fake_continuation, original, b"request"
        )
        assert result == "ok"
        assert captured["timeout"] == 3.0

    def test_preserves_method_and_metadata(self):
        """Interceptor should preserve all other call details."""
        interceptor = TimeoutInterceptor(default_timeout=5.0)
        original = FakeCallDetails(method="/kachaka/GetPose", timeout=None)
        original.metadata = [("key", "value")]
        captured = {}

        def fake_continuation(call_details, request):
            captured["method"] = call_details.method
            captured["metadata"] = call_details.metadata
            return "ok"

        interceptor.intercept_unary_unary(fake_continuation, original, b"req")
        assert captured["method"] == "/kachaka/GetPose"
        assert captured["metadata"] == [("key", "value")]

    def test_custom_default_timeout(self):
        """Different default_timeout values should be applied."""
        for timeout_val in [1.0, 5.0, 30.0]:
            interceptor = TimeoutInterceptor(default_timeout=timeout_val)
            original = FakeCallDetails(timeout=None)
            captured = {}

            def fake_continuation(call_details, request):
                captured["timeout"] = call_details.timeout
                return "ok"

            interceptor.intercept_unary_unary(
                fake_continuation, original, b"req"
            )
            assert captured["timeout"] == timeout_val


class TestTimeoutInterceptorIntegration:
    """Integration test with real gRPC channel (localhost, no server)."""

    def test_intercepted_channel_applies_timeout(self):
        """Calls through intercepted channel should have timeout applied."""
        # Create a channel to a non-existent target
        plain_channel = grpc.insecure_channel("localhost:19999")
        interceptor = TimeoutInterceptor(default_timeout=1.0)
        intercepted = grpc.intercept_channel(plain_channel, interceptor)

        # Make a unary call — should fail with DEADLINE_EXCEEDED (timeout)
        # or UNAVAILABLE (can't connect), but NOT hang forever
        method = "/test.Service/Method"
        try:
            response_future = intercepted.unary_unary(
                method,
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x,
            )(b"test")
        except grpc.RpcError as exc:
            # Either DEADLINE_EXCEEDED or UNAVAILABLE is acceptable
            assert exc.code() in (
                grpc.StatusCode.DEADLINE_EXCEEDED,
                grpc.StatusCode.UNAVAILABLE,
            )
        finally:
            plain_channel.close()

    def test_intercepted_call_does_not_hang(self):
        """A call to unreachable target must return within timeout + margin."""
        plain_channel = grpc.insecure_channel("192.0.2.1:26400")  # RFC 5737 TEST-NET
        interceptor = TimeoutInterceptor(default_timeout=2.0)
        intercepted = grpc.intercept_channel(plain_channel, interceptor)

        start = time.monotonic()
        try:
            intercepted.unary_unary(
                "/test/Method",
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x,
            )(b"test")
        except grpc.RpcError:
            pass
        elapsed = time.monotonic() - start

        # Should complete within timeout + 1s margin (not hang for minutes)
        assert elapsed < 4.0, f"Call took {elapsed:.1f}s, expected < 4s"
        plain_channel.close()
