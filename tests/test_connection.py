"""Tests for kachaka_core.connection — pool, normalisation, ping."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kachaka_core.connection import KachakaConnection


@pytest.fixture(autouse=True)
def _clean_pool():
    """Ensure each test starts with an empty connection pool."""
    KachakaConnection.clear_pool()
    yield
    KachakaConnection.clear_pool()


class TestNormaliseTarget:
    def test_adds_default_port(self):
        assert KachakaConnection._normalise_target("192.168.1.1") == "192.168.1.1:26400"

    def test_preserves_explicit_port(self):
        assert KachakaConnection._normalise_target("10.0.0.1:9999") == "10.0.0.1:9999"

    def test_mdns_hostname(self):
        assert KachakaConnection._normalise_target("kachaka-abc.local") == "kachaka-abc.local:26400"


class TestPool:
    @patch("kachaka_core.connection.KachakaApiClient")
    def test_same_ip_returns_same_instance(self, mock_cls):
        mock_cls.return_value = MagicMock()
        a = KachakaConnection.get("1.2.3.4")
        b = KachakaConnection.get("1.2.3.4")
        assert a is b

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_different_ip_returns_different(self, mock_cls):
        mock_cls.return_value = MagicMock()
        a = KachakaConnection.get("1.2.3.4")
        b = KachakaConnection.get("5.6.7.8")
        assert a is not b

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_port_normalised_for_pool_key(self, mock_cls):
        mock_cls.return_value = MagicMock()
        a = KachakaConnection.get("1.2.3.4")
        b = KachakaConnection.get("1.2.3.4:26400")
        assert a is b

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_remove(self, mock_cls):
        mock_cls.return_value = MagicMock()
        KachakaConnection.get("1.2.3.4")
        KachakaConnection.remove("1.2.3.4")
        assert "1.2.3.4:26400" not in KachakaConnection._pool

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_clear_pool(self, mock_cls):
        mock_cls.return_value = MagicMock()
        KachakaConnection.get("1.2.3.4")
        KachakaConnection.get("5.6.7.8")
        KachakaConnection.clear_pool()
        assert len(KachakaConnection._pool) == 0


class TestPing:
    @patch("kachaka_core.connection.KachakaApiClient")
    def test_ping_success(self, mock_cls):
        mock_client = MagicMock()
        mock_client.get_robot_serial_number.return_value = "KCK-001"
        mock_pose = MagicMock(x=1.0, y=2.0, theta=0.5)
        mock_client.get_robot_pose.return_value = mock_pose
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        result = conn.ping()

        assert result["ok"] is True
        assert result["serial"] == "KCK-001"
        assert result["pose"] == {"x": 1.0, "y": 2.0, "theta": 0.5}

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_ping_grpc_error(self, mock_cls):
        import grpc

        mock_client = MagicMock()
        mock_client.get_robot_serial_number.return_value = "KCK-001"
        rpc_error = grpc.RpcError()
        rpc_error.code = lambda: grpc.StatusCode.UNAVAILABLE
        rpc_error.details = lambda: "Connection refused"
        mock_client.get_robot_pose.side_effect = rpc_error
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        result = conn.ping()

        assert result["ok"] is False
        assert "UNAVAILABLE" in result["error"]


class TestResolver:
    @patch("kachaka_core.connection.KachakaApiClient")
    def test_ensure_resolver_calls_update(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        conn.ensure_resolver()

        mock_client.update_resolver.assert_called_once()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_ensure_resolver_idempotent(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        conn.ensure_resolver()
        conn.ensure_resolver()

        # Only called once because _resolver_ready is cached
        mock_client.update_resolver.assert_called_once()
