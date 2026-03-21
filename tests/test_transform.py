"""Tests for kachaka_core.transform — background TF streaming."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from kachaka_core.connection import ConnectionState, KachakaConnection
from kachaka_core.transform import TransformStreamer, _parse_transform


@pytest.fixture(autouse=True)
def _clean_pool():
    KachakaConnection.clear_pool()
    yield
    KachakaConnection.clear_pool()


def _make_conn(mock_client):
    with patch("kachaka_core.connection.KachakaApiClient", return_value=mock_client):
        return KachakaConnection.get("test-robot")


def _make_tf(frame_id="odom", child_frame_id="base_link",
             tx=1.0, ty=2.0, tz=0.0, rx=0.0, ry=0.0, rz=0.0, rw=1.0,
             stamp_nsec=0):
    tf = MagicMock()
    tf.header.frame_id = frame_id
    tf.header.stamp_nsec = stamp_nsec
    tf.child_frame_id = child_frame_id
    tf.translation.x = tx
    tf.translation.y = ty
    tf.translation.z = tz
    tf.rotation.x = rx
    tf.rotation.y = ry
    tf.rotation.z = rz
    tf.rotation.w = rw
    return tf


class TestParseTransform:
    def test_parse(self):
        tf = _make_tf(frame_id="odom", child_frame_id="base_link",
                       tx=1.5, ty=2.5, tz=0.1)
        result = _parse_transform(tf)
        assert result["frame_id"] == "odom"
        assert result["child_frame_id"] == "base_link"
        assert result["translation"]["x"] == 1.5
        assert result["translation"]["y"] == 2.5
        assert result["rotation"]["w"] == 1.0
        assert "theta" in result
        assert abs(result["theta"]) < 0.01  # identity quat → theta ≈ 0

    def test_parse_rotated(self):
        import math
        # 90° yaw → quat (0,0,0.7071,0.7071)
        tf = _make_tf(rz=0.7071, rw=0.7071)
        result = _parse_transform(tf)
        assert abs(result["theta"] - math.pi / 2) < 0.01


class TestLifecycle:
    def test_start_sets_running(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = MagicMock()
        # Return an iterator that blocks forever
        mock_stub.GetDynamicTransform.return_value = iter([])
        mock_client.stub = mock_stub

        streamer = TransformStreamer(conn)
        streamer.start()
        assert streamer.is_running is True
        streamer.stop()

    def test_double_start_is_noop(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = MagicMock()
        mock_stub.GetDynamicTransform.return_value = iter([])
        mock_client.stub = mock_stub

        streamer = TransformStreamer(conn)
        streamer.start()
        thread1 = streamer._thread
        streamer.start()
        assert streamer._thread is thread1
        streamer.stop()

    def test_stop_without_start_is_noop(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        streamer = TransformStreamer(conn)
        streamer.stop()  # should not raise

    def test_thread_is_daemon(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = MagicMock()
        mock_stub.GetDynamicTransform.return_value = iter([])
        mock_client.stub = mock_stub

        streamer = TransformStreamer(conn)
        streamer.start()
        assert streamer._thread.daemon is True
        streamer.stop()


class TestStreamConsumption:
    def test_receives_transforms(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = MagicMock()

        tf1 = _make_tf(frame_id="odom", child_frame_id="base_link", tx=1.0)
        response = MagicMock()
        response.transforms = [tf1]

        # The stream yields one response then ends (triggering reconnect)
        received = threading.Event()

        def fake_stream(request):
            yield response
            received.set()
            # Block until stopped
            while not streamer._stop_event.is_set():
                time.sleep(0.01)

        mock_stub.GetDynamicTransform.side_effect = fake_stream
        mock_client.stub = mock_stub

        streamer = TransformStreamer(conn, reconnect_delay=0.1)
        streamer.start()
        received.wait(timeout=2.0)
        time.sleep(0.05)  # let lock update

        transforms = streamer.latest_transforms
        assert transforms is not None
        assert len(transforms) == 1
        assert transforms[0]["frame_id"] == "odom"
        assert transforms[0]["translation"]["x"] == 1.0
        assert streamer._total_updates >= 1

        streamer.stop()

    def test_callback_called(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = MagicMock()

        tf1 = _make_tf()
        response = MagicMock()
        response.transforms = [tf1]

        received = threading.Event()
        callback_data = []

        def on_tf(transforms):
            callback_data.append(transforms)
            received.set()

        def fake_stream(request):
            yield response
            while not streamer._stop_event.is_set():
                time.sleep(0.01)

        mock_stub.GetDynamicTransform.side_effect = fake_stream
        mock_client.stub = mock_stub

        streamer = TransformStreamer(conn, on_transform=on_tf, reconnect_delay=0.1)
        streamer.start()
        received.wait(timeout=2.0)

        assert len(callback_data) >= 1
        assert callback_data[0][0]["frame_id"] == "odom"
        streamer.stop()

    def test_callback_exception_does_not_crash_thread(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = MagicMock()

        tf1 = _make_tf()
        response = MagicMock()
        response.transforms = [tf1]

        received = threading.Event()

        def bad_callback(transforms):
            received.set()
            raise RuntimeError("boom")

        def fake_stream(request):
            yield response
            while not streamer._stop_event.is_set():
                time.sleep(0.01)

        mock_stub.GetDynamicTransform.side_effect = fake_stream
        mock_client.stub = mock_stub

        streamer = TransformStreamer(conn, on_transform=bad_callback, reconnect_delay=0.1)
        streamer.start()
        received.wait(timeout=2.0)
        time.sleep(0.1)

        # Thread should still be alive despite callback exception
        assert streamer.is_running is True
        streamer.stop()


class TestErrorRecovery:
    def test_reconnects_after_stream_error(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = MagicMock()

        tf1 = _make_tf()
        response = MagicMock()
        response.transforms = [tf1]

        call_count = 0
        received = threading.Event()

        def fake_stream(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("connection lost")
            yield response
            received.set()
            while not streamer._stop_event.is_set():
                time.sleep(0.01)

        mock_stub.GetDynamicTransform.side_effect = fake_stream
        mock_client.stub = mock_stub

        streamer = TransformStreamer(conn, reconnect_delay=0.1)
        streamer.start()
        received.wait(timeout=3.0)

        assert streamer._errors >= 1
        assert streamer._total_updates >= 1
        assert streamer.latest_transforms is not None
        streamer.stop()

    def test_skips_while_disconnected(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = MagicMock()
        mock_stub.GetDynamicTransform.return_value = iter([])
        mock_client.stub = mock_stub

        # Force disconnected state
        conn._set_state(ConnectionState.DISCONNECTED)

        streamer = TransformStreamer(conn, reconnect_delay=0.1)
        streamer.start()
        time.sleep(0.3)

        # Should not have called GetDynamicTransform
        mock_stub.GetDynamicTransform.assert_not_called()
        streamer.stop()


class TestStats:
    def test_initial_stats(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        streamer = TransformStreamer(conn)

        stats = streamer.stats
        assert stats["total_updates"] == 0
        assert stats["errors"] == 0
        assert stats["last_update_time"] is None
