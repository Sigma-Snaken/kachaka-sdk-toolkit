"""Tests for kachaka_core.queries — status, locations, camera, map."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kachaka_core.connection import KachakaConnection
from kachaka_core.queries import KachakaQueries


@pytest.fixture(autouse=True)
def _clean_pool():
    KachakaConnection.clear_pool()
    yield
    KachakaConnection.clear_pool()


def _make_conn(mock_client):
    with patch("kachaka_core.connection.KachakaApiClient", return_value=mock_client):
        return KachakaConnection.get("test-robot")


class TestGetStatus:
    def test_full_status(self):
        mock = MagicMock()
        mock.get_robot_pose.return_value = MagicMock(x=1.0, y=2.0, theta=0.5)
        mock.get_battery_info.return_value = (85.0, "CHARGING")
        mock.get_command_state.return_value = ("PENDING", None)
        mock.get_error.return_value = []
        mock.get_moving_shelf_id.return_value = ""
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_status()

        assert result["ok"] is True
        assert result["pose"]["x"] == 1.0
        assert result["battery"]["percentage"] == 85.0
        assert result["errors"] == []
        assert result["moving_shelf_id"] is None


class TestLocations:
    def test_list_locations(self):
        mock = MagicMock()
        loc = MagicMock()
        loc.id = "loc-1"
        loc.name = "Kitchen"
        loc.type = "CHARGER"
        loc.pose = MagicMock(x=0.0, y=0.0, theta=0.0)
        mock.get_locations.return_value = [loc]
        conn = _make_conn(mock)

        result = KachakaQueries(conn).list_locations()

        assert result["ok"] is True
        assert len(result["locations"]) == 1
        assert result["locations"][0]["name"] == "Kitchen"


class TestShelves:
    def test_list_shelves(self):
        mock = MagicMock()
        shelf = MagicMock()
        shelf.id = "shelf-1"
        shelf.name = "Shelf A"
        shelf.home_location_id = "loc-2"
        mock.get_shelves.return_value = [shelf]
        conn = _make_conn(mock)

        result = KachakaQueries(conn).list_shelves()

        assert result["ok"] is True
        assert result["shelves"][0]["name"] == "Shelf A"

    def test_get_moving_shelf_empty(self):
        mock = MagicMock()
        mock.get_moving_shelf_id.return_value = ""
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_moving_shelf()
        assert result["shelf_id"] is None

    def test_get_moving_shelf_with_id(self):
        mock = MagicMock()
        mock.get_moving_shelf_id.return_value = "shelf-1"
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_moving_shelf()
        assert result["shelf_id"] == "shelf-1"


class TestCamera:
    def test_front_camera(self):
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.return_value = MagicMock(
            data=b"\xff\xd8test-jpeg", format="jpeg"
        )
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_front_camera_image()

        assert result["ok"] is True
        assert result["format"] == "jpeg"
        assert len(result["image_base64"]) > 0

    def test_back_camera(self):
        mock = MagicMock()
        mock.get_back_camera_ros_compressed_image.return_value = MagicMock(
            data=b"\xff\xd8back", format="jpeg"
        )
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_back_camera_image()
        assert result["ok"] is True


class TestMap:
    def test_get_map(self):
        mock = MagicMock()
        png_map = MagicMock()
        png_map.data = b"\x89PNGtest"
        png_map.name = "Floor1"
        png_map.resolution = 0.05
        png_map.width = 200
        png_map.height = 200
        mock.get_png_map.return_value = png_map
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_map()

        assert result["ok"] is True
        assert result["format"] == "png"
        assert result["name"] == "Floor1"

    def test_list_maps(self):
        mock = MagicMock()
        m = MagicMock()
        m.id = "map-1"
        m.name = "Floor1"
        mock.get_map_list.return_value = [m]
        mock.get_current_map_id.return_value = "map-1"
        conn = _make_conn(mock)

        result = KachakaQueries(conn).list_maps()

        assert result["ok"] is True
        assert result["current_map_id"] == "map-1"


class TestErrors:
    def test_no_errors(self):
        mock = MagicMock()
        mock.get_error.return_value = []
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_errors()
        assert result["errors"] == []

    def test_error_definitions(self):
        mock = MagicMock()
        err_info = MagicMock()
        err_info.title_en = "Shelf dropped"
        err_info.description_en = "The shelf was dropped during movement"
        mock.get_robot_error_code.return_value = {14606: err_info}
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_error_definitions()

        assert result["ok"] is True
        assert 14606 in result["definitions"]
        assert result["definitions"][14606]["title"] == "Shelf dropped"


class TestRobotInfo:
    def test_serial_number(self):
        mock = MagicMock()
        mock.get_robot_serial_number.return_value = "KCK-001"
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_serial_number()
        assert result["serial"] == "KCK-001"

    def test_version(self):
        mock = MagicMock()
        mock.get_robot_version.return_value = "3.15.1"
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_version()
        assert result["version"] == "3.15.1"
