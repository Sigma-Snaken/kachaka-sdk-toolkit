"""Tests for kachaka_core.commands — movement, shelf, speech, retry."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import grpc
import pytest

from kachaka_core.commands import KachakaCommands
from kachaka_core.connection import KachakaConnection


@pytest.fixture(autouse=True)
def _clean_pool():
    KachakaConnection.clear_pool()
    yield
    KachakaConnection.clear_pool()


def _make_result(success: bool = True, error_code: int = 0):
    r = MagicMock()
    r.success = success
    r.error_code = error_code
    return r


def _make_conn(mock_client):
    """Create a KachakaConnection with a mocked client."""
    with patch("kachaka_core.connection.KachakaApiClient", return_value=mock_client):
        conn = KachakaConnection.get("test-robot")
    return conn


class TestMovement:
    def test_move_to_location_success(self):
        mock_client = MagicMock()
        mock_client.move_to_location.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        cmds = KachakaCommands(conn)
        result = cmds.move_to_location("Kitchen")

        assert result["ok"] is True
        assert result["action"] == "move_to_location"
        assert result["target"] == "Kitchen"

    def test_move_to_location_failure(self):
        mock_client = MagicMock()
        mock_client.move_to_location.return_value = _make_result(False, error_code=101)
        conn = _make_conn(mock_client)

        cmds = KachakaCommands(conn)
        result = cmds.move_to_location("Nowhere")

        assert result["ok"] is False
        assert result["error_code"] == 101

    def test_move_to_pose(self):
        mock_client = MagicMock()
        mock_client.move_to_pose.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        cmds = KachakaCommands(conn)
        result = cmds.move_to_pose(1.0, 2.0, 0.5)

        assert result["ok"] is True
        mock_client.move_to_pose.assert_called_once_with(
            1.0, 2.0, 0.5, cancel_all=True, tts_on_success="", title=""
        )

    def test_return_home(self):
        mock_client = MagicMock()
        mock_client.return_home.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        cmds = KachakaCommands(conn)
        result = cmds.return_home()

        assert result["ok"] is True
        assert result["action"] == "return_home"


class TestShelfOps:
    def test_move_shelf(self):
        mock_client = MagicMock()
        mock_client.move_shelf.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        cmds = KachakaCommands(conn)
        result = cmds.move_shelf("Shelf A", "Room 1")

        assert result["ok"] is True
        assert "Shelf A" in result["target"]

    def test_dock_shelf(self):
        mock_client = MagicMock()
        mock_client.dock_shelf.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).dock_shelf()
        assert result["ok"] is True


class TestSpeech:
    def test_speak(self):
        mock_client = MagicMock()
        mock_client.speak.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).speak("Hello")
        assert result["ok"] is True
        assert result["target"] == "Hello"

    def test_set_volume_clamped(self):
        mock_client = MagicMock()
        mock_client.set_speaker_volume.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        KachakaCommands(conn).set_speaker_volume(15)
        mock_client.set_speaker_volume.assert_called_once_with(10)


class TestRetry:
    def test_retries_on_unavailable(self):
        mock_client = MagicMock()

        rpc_error = grpc.RpcError()
        rpc_error.code = lambda: grpc.StatusCode.UNAVAILABLE
        rpc_error.details = lambda: "transient"

        mock_client.speak.side_effect = [rpc_error, rpc_error, _make_result(True)]
        conn = _make_conn(mock_client)

        with patch("kachaka_core.error_handling.time.sleep"):
            result = KachakaCommands(conn).speak("test")

        assert result["ok"] is True
        assert mock_client.speak.call_count == 3

    def test_no_retry_on_invalid_argument(self):
        mock_client = MagicMock()

        rpc_error = grpc.RpcError()
        rpc_error.code = lambda: grpc.StatusCode.INVALID_ARGUMENT
        rpc_error.details = lambda: "bad param"

        mock_client.speak.side_effect = rpc_error
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).speak("test")

        assert result["ok"] is False
        assert result["retryable"] is False
        assert mock_client.speak.call_count == 1

    def test_exhausted_retries(self):
        mock_client = MagicMock()

        rpc_error = grpc.RpcError()
        rpc_error.code = lambda: grpc.StatusCode.UNAVAILABLE
        rpc_error.details = lambda: "down"

        mock_client.speak.side_effect = rpc_error
        conn = _make_conn(mock_client)

        with patch("kachaka_core.error_handling.time.sleep"):
            result = KachakaCommands(conn).speak("test")

        assert result["ok"] is False
        assert result["retryable"] is True
        assert result["attempts"] == 3


class TestCancelCommand:
    def test_cancel_success(self):
        mock_client = MagicMock()
        mock_client.cancel_command.return_value = (_make_result(True), MagicMock())
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).cancel_command()
        assert result["ok"] is True


class TestStop:
    def test_emergency_stop(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).stop()
        assert result["ok"] is True
        mock_client.set_robot_stop.assert_called_once()


class TestPollUntilComplete:
    def test_immediate_completion(self):
        mock_client = MagicMock()
        mock_client.is_command_running.return_value = False
        mock_client.get_last_command_result.return_value = (
            _make_result(True),
            MagicMock(),
        )
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).poll_until_complete(timeout=5.0)
        assert result["ok"] is True

    def test_timeout(self):
        mock_client = MagicMock()
        mock_client.is_command_running.return_value = True
        conn = _make_conn(mock_client)

        with patch("kachaka_core.commands.time.sleep"):
            with patch("kachaka_core.commands.time.time", side_effect=[0, 0, 999]):
                result = KachakaCommands(conn).poll_until_complete(timeout=1.0)

        assert result["ok"] is False
        assert result["error"] == "timeout"
