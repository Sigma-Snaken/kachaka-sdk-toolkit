"""Tests for kachaka_core.detection — ObjectDetector."""

from __future__ import annotations

import base64
import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from kachaka_core.detection import ObjectDetector
from kachaka_core.camera import CameraStreamer
from kachaka_core.connection import KachakaConnection


# ── Helpers ───────────────────────────────────────────────────────────


def _make_test_jpeg(width: int = 640, height: int = 480) -> bytes:
    """Create a small real JPEG image for testing."""
    img = Image.new("RGB", (width, height), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestObjectDetector:
    """Tests for ObjectDetector.get_detections and capture_with_detections."""

    def _make_detection(
        self,
        label: int = 1,
        x: int = 100,
        y: int = 50,
        w: int = 200,
        h: int = 300,
        score: float = 0.95,
        distance: float = 2.3,
    ):
        """Helper to create a mock ObjectDetection."""
        det = MagicMock()
        det.label = label
        det.roi.x_offset = x
        det.roi.y_offset = y
        det.roi.width = w
        det.roi.height = h
        det.score = score
        det.distance_median = distance
        return det

    def _make_conn(self, detections=None, image_bytes: bytes = b"fake-jpeg"):
        """Create mock conn with sdk."""
        conn = MagicMock(spec=KachakaConnection)
        conn.client = MagicMock()
        if detections is not None:
            conn.client.get_object_detection.return_value = (
                MagicMock(),
                detections,
            )
        img = MagicMock()
        img.data = image_bytes
        img.format = "jpeg"
        conn.client.get_front_camera_ros_compressed_image.return_value = img
        conn.client.get_back_camera_ros_compressed_image.return_value = img
        return conn

    # ── get_detections ────────────────────────────────────────────────

    def test_get_detections_empty(self):
        """No objects detected — should return ok=True with empty list."""
        conn = self._make_conn(detections=[])
        det = ObjectDetector(conn)

        result = det.get_detections()

        assert result["ok"] is True
        assert result["objects"] == []

    def test_get_detections_single_person(self):
        """One person detection — verify all dict fields."""
        conn = self._make_conn(
            detections=[self._make_detection(label=1, x=10, y=20, w=30, h=40, score=0.87, distance=1.5)]
        )
        det = ObjectDetector(conn)

        result = det.get_detections()

        assert result["ok"] is True
        assert len(result["objects"]) == 1

        obj = result["objects"][0]
        assert obj["label"] == "person"
        assert obj["label_id"] == 1
        assert obj["roi"] == {"x": 10, "y": 20, "width": 30, "height": 40}
        assert obj["score"] == 0.87
        assert obj["distance"] == 1.5

    def test_get_detections_multiple_labels(self):
        """One of each label — person, shelf, charger, door, unknown."""
        detections = [
            self._make_detection(label=1),  # person
            self._make_detection(label=2),  # shelf
            self._make_detection(label=3),  # charger
            self._make_detection(label=4),  # door
            self._make_detection(label=0),  # unknown
        ]
        conn = self._make_conn(detections=detections)
        det = ObjectDetector(conn)

        result = det.get_detections()

        assert result["ok"] is True
        assert len(result["objects"]) == 5

        labels = [obj["label"] for obj in result["objects"]]
        assert labels == ["person", "shelf", "charger", "door", "unknown"]

    # ── _detection_to_dict ────────────────────────────────────────────

    def test_detection_to_dict_label_mapping(self):
        """Verify all 5 label IDs map to the correct human-readable string."""
        expected = {
            0: "unknown",
            1: "person",
            2: "shelf",
            3: "charger",
            4: "door",
        }
        for label_id, label_name in expected.items():
            mock_det = self._make_detection(label=label_id)
            d = ObjectDetector._detection_to_dict(mock_det)
            assert d["label"] == label_name, f"label_id={label_id}"
            assert d["label_id"] == label_id

    def test_detection_to_dict_zero_distance(self):
        """distance_median=0 should produce distance: None."""
        mock_det = self._make_detection(distance=0.0)
        d = ObjectDetector._detection_to_dict(mock_det)
        assert d["distance"] is None

    # ── capture_with_detections ───────────────────────────────────────

    def test_capture_with_detections_front(self):
        """Front camera — verify image_base64 and objects returned."""
        person = self._make_detection(label=1, score=0.99, distance=3.0)
        conn = self._make_conn(detections=[person], image_bytes=b"front-jpeg")
        det = ObjectDetector(conn)

        result = det.capture_with_detections(camera="front")

        assert result["ok"] is True
        assert result["image_base64"] == base64.b64encode(b"front-jpeg").decode()
        assert result["format"] == "jpeg"
        assert len(result["objects"]) == 1
        assert result["objects"][0]["label"] == "person"

        conn.client.get_front_camera_ros_compressed_image.assert_called_once()

    def test_capture_with_detections_back(self):
        """camera='back' should use back camera."""
        conn = self._make_conn(detections=[], image_bytes=b"back-jpeg")
        det = ObjectDetector(conn)

        result = det.capture_with_detections(camera="back")

        assert result["ok"] is True
        assert result["image_base64"] == base64.b64encode(b"back-jpeg").decode()
        conn.client.get_back_camera_ros_compressed_image.assert_called_once()

    def test_capture_with_detections_invalid_camera(self):
        """camera='side' should return ok=False."""
        conn = self._make_conn(detections=[])
        det = ObjectDetector(conn)

        result = det.capture_with_detections(camera="side")

        assert result["ok"] is False
        assert "side" in result["error"]


class TestAnnotateFrame:
    """Tests for ObjectDetector.annotate_frame."""

    def test_annotate_empty_objects(self):
        """No objects — should still produce valid JPEG."""
        conn = MagicMock(spec=KachakaConnection)
        conn.client = MagicMock()
        det = ObjectDetector(conn)

        jpeg_in = _make_test_jpeg()
        jpeg_out = det.annotate_frame(jpeg_in, [])

        assert len(jpeg_out) > 0
        # Verify it is a valid JPEG by opening it
        img = Image.open(io.BytesIO(jpeg_out))
        assert img.format == "JPEG"

    def test_annotate_single_object(self):
        """One object — output is valid JPEG and different from input."""
        conn = MagicMock(spec=KachakaConnection)
        conn.client = MagicMock()
        det = ObjectDetector(conn)

        jpeg_in = _make_test_jpeg()
        objects = [
            {
                "label": "person",
                "roi": {"x": 10, "y": 10, "width": 100, "height": 200},
                "score": 0.95,
                "distance": 2.3,
            }
        ]
        jpeg_out = det.annotate_frame(jpeg_in, objects)

        assert len(jpeg_out) > 0
        # Output should differ from input (bounding box was drawn)
        assert jpeg_out != jpeg_in
        # Verify it is a valid JPEG
        img = Image.open(io.BytesIO(jpeg_out))
        assert img.format == "JPEG"

    def test_annotate_preserves_jpeg_format(self):
        """Output starts with JPEG magic bytes (0xFF 0xD8)."""
        conn = MagicMock(spec=KachakaConnection)
        conn.client = MagicMock()
        det = ObjectDetector(conn)

        jpeg_in = _make_test_jpeg()
        objects = [
            {
                "label": "shelf",
                "roi": {"x": 50, "y": 50, "width": 80, "height": 80},
                "score": 0.80,
                "distance": None,
            }
        ]
        jpeg_out = det.annotate_frame(jpeg_in, objects)

        assert jpeg_out[:2] == b"\xff\xd8"


class TestCameraStreamerDetection:
    """Tests for CameraStreamer detection/annotate integration flags."""

    def test_streamer_default_no_detection(self):
        """detect=False (default) — _detector should be None."""
        conn = MagicMock(spec=KachakaConnection)
        conn.client = MagicMock()

        streamer = CameraStreamer(conn, interval=1.0, camera="front")

        assert streamer._detector is None
        assert streamer._detect is False

    def test_streamer_detect_creates_detector(self):
        """detect=True should create an ObjectDetector instance."""
        conn = MagicMock(spec=KachakaConnection)
        conn.client = MagicMock()

        streamer = CameraStreamer(conn, interval=1.0, camera="front", detect=True)

        assert streamer._detector is not None
        assert isinstance(streamer._detector, ObjectDetector)
        assert streamer._detect is True

    def test_streamer_annotate_forces_detect(self):
        """annotate=True without detect should set _detect=True."""
        conn = MagicMock(spec=KachakaConnection)
        conn.client = MagicMock()

        streamer = CameraStreamer(
            conn, interval=1.0, camera="front", detect=False, annotate=True
        )

        assert streamer._detect is True
        assert streamer._annotate is True
        assert streamer._detector is not None
        assert isinstance(streamer._detector, ObjectDetector)
