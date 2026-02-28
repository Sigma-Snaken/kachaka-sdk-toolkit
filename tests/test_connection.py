"""Tests for kachaka_core.connection — pool, normalisation, ping, monitoring."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import grpc
import pytest

from kachaka_core.connection import ConnectionState, KachakaConnection


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
    def test_ensure_resolver_fetches_shelves_and_locations(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        conn.ensure_resolver()

        mock_client.get_shelves.assert_called_once()
        mock_client.get_locations.assert_called_once()
        mock_client.update_resolver.assert_not_called()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_ensure_resolver_idempotent(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        conn.ensure_resolver()
        conn.ensure_resolver()

        # Only called once because _resolver_ready is cached
        mock_client.get_shelves.assert_called_once()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_resolve_shelf_by_name_and_id(self, mock_cls):
        mock_client = MagicMock()
        shelf = MagicMock()
        shelf.name = "ShelfA"
        shelf.id = "S01"
        mock_client.get_shelves.return_value = [shelf]
        mock_client.get_locations.return_value = []
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        conn.ensure_resolver()

        assert conn.resolve_shelf("ShelfA") == "S01"
        assert conn.resolve_shelf("S01") == "S01"
        assert conn.resolve_shelf("unknown") == "unknown"

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_resolve_location_by_name_and_id(self, mock_cls):
        mock_client = MagicMock()
        loc = MagicMock()
        loc.name = "Kitchen"
        loc.id = "L01"
        mock_client.get_shelves.return_value = []
        mock_client.get_locations.return_value = [loc]
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        conn.ensure_resolver()

        assert conn.resolve_location("Kitchen") == "L01"
        assert conn.resolve_location("L01") == "L01"
        assert conn.resolve_location("unknown") == "unknown"


class TestMonitoring:
    @patch("kachaka_core.connection.KachakaApiClient")
    def test_state_initially_connected(self, mock_cls):
        mock_cls.return_value = MagicMock()
        conn = KachakaConnection.get("1.2.3.4")
        assert conn.state == ConnectionState.CONNECTED

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_no_monitoring_by_default(self, mock_cls):
        mock_cls.return_value = MagicMock()
        conn = KachakaConnection.get("1.2.3.4")
        assert conn._monitor_thread is None

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_monitoring_detects_disconnect(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        # Make ping fail
        rpc_error = grpc.RpcError()
        rpc_error.code = lambda: grpc.StatusCode.UNAVAILABLE
        rpc_error.details = lambda: "gone"
        mock_client.get_robot_serial_number.side_effect = rpc_error

        conn.start_monitoring(interval=0.05)
        try:
            reached = conn.wait_for_state(ConnectionState.DISCONNECTED, timeout=2.0)
            assert reached
            assert conn.state == ConnectionState.DISCONNECTED
        finally:
            conn.stop_monitoring()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_monitoring_detects_reconnect(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")

        # Start disconnected
        rpc_error = grpc.RpcError()
        rpc_error.code = lambda: grpc.StatusCode.UNAVAILABLE
        rpc_error.details = lambda: "gone"
        mock_client.get_robot_serial_number.side_effect = rpc_error

        conn.start_monitoring(interval=0.05)
        try:
            conn.wait_for_state(ConnectionState.DISCONNECTED, timeout=2.0)

            # Restore connection
            mock_client.get_robot_serial_number.side_effect = None
            mock_client.get_robot_serial_number.return_value = "KCK-001"
            mock_client.get_robot_pose.return_value = MagicMock(x=0, y=0, theta=0)

            reached = conn.wait_for_state(ConnectionState.CONNECTED, timeout=2.0)
            assert reached
            assert conn.state == ConnectionState.CONNECTED
        finally:
            conn.stop_monitoring()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_state_change_callback_called(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        transitions = []

        rpc_error = grpc.RpcError()
        rpc_error.code = lambda: grpc.StatusCode.UNAVAILABLE
        rpc_error.details = lambda: "gone"
        mock_client.get_robot_serial_number.side_effect = rpc_error

        conn.start_monitoring(interval=0.05, on_state_change=transitions.append)
        try:
            conn.wait_for_state(ConnectionState.DISCONNECTED, timeout=2.0)
            assert ConnectionState.DISCONNECTED in transitions
        finally:
            conn.stop_monitoring()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_stop_monitoring_cleans_up(self, mock_cls):
        mock_client = MagicMock()
        mock_client.get_robot_serial_number.return_value = "KCK-001"
        mock_client.get_robot_pose.return_value = MagicMock(x=0, y=0, theta=0)
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        conn.start_monitoring(interval=0.05)
        assert conn._monitor_thread is not None
        conn.stop_monitoring()
        assert conn._monitor_thread is None

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_wait_for_state_timeout(self, mock_cls):
        mock_client = MagicMock()
        mock_client.get_robot_serial_number.return_value = "KCK-001"
        mock_client.get_robot_pose.return_value = MagicMock(x=0, y=0, theta=0)
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        # Don't start monitoring — state stays CONNECTED
        reached = conn.wait_for_state(ConnectionState.DISCONNECTED, timeout=0.1)
        assert reached is False

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_start_monitoring_idempotent(self, mock_cls):
        mock_client = MagicMock()
        mock_client.get_robot_serial_number.return_value = "KCK-001"
        mock_client.get_robot_pose.return_value = MagicMock(x=0, y=0, theta=0)
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        conn.start_monitoring(interval=0.1)
        thread1 = conn._monitor_thread
        conn.start_monitoring(interval=0.1)
        thread2 = conn._monitor_thread
        assert thread1 is thread2
        conn.stop_monitoring()
