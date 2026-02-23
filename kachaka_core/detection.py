"""Object detection for Kachaka robots.

Wraps the built-in on-device detector — returns structured dicts and
optionally annotates camera frames with bounding boxes using PIL.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Optional

from .connection import KachakaConnection
from .error_handling import with_retry

logger = logging.getLogger(__name__)

# Label mapping (proto enum -> human-readable)
_LABEL_NAMES = {
    0: "unknown",
    1: "person",
    2: "shelf",
    3: "charger",
    4: "door",
}

# Colors for bbox drawing (RGB)
_LABEL_COLORS = {
    "person": (0, 200, 0),      # green
    "shelf": (0, 100, 255),     # blue
    "charger": (0, 220, 220),   # cyan
    "door": (220, 0, 0),        # red
    "unknown": (220, 100, 200), # pink
}


class ObjectDetector:
    """Object detection queries and frame annotation for a single Kachaka robot.

    Usage::

        conn = KachakaConnection.get("192.168.1.100")
        det = ObjectDetector(conn)
        result = det.get_detections()     # {"ok": True, "objects": [...]}
        snap = det.capture_with_detections(camera="front")
    """

    def __init__(self, conn: KachakaConnection):
        self.conn = conn
        self.sdk = conn.client

    # ── Public API ────────────────────────────────────────────────────

    @with_retry()
    def get_detections(self) -> dict:
        """Get current object detection results.

        Returns:
            {"ok": True, "objects": [{"label": "person", "label_id": 1,
             "roi": {"x": ..., "y": ..., "width": ..., "height": ...},
             "score": 0.95, "distance": 2.3}, ...]}
        """
        header, objects = self.sdk.get_object_detection()
        return {
            "ok": True,
            "objects": [self._detection_to_dict(obj) for obj in objects],
        }

    @with_retry()
    def capture_with_detections(self, camera: str = "front") -> dict:
        """Capture camera frame + detection results simultaneously (no bbox drawn).

        Args:
            camera: ``"front"`` or ``"back"``.

        Returns:
            {"ok": True, "image_base64": "...", "format": "jpeg",
             "objects": [...]}
        """
        # Capture image
        if camera == "front":
            img = self.sdk.get_front_camera_ros_compressed_image()
        elif camera == "back":
            img = self.sdk.get_back_camera_ros_compressed_image()
        else:
            return {"ok": False, "error": f"Invalid camera: {camera!r}"}

        # Get detections
        header, objects = self.sdk.get_object_detection()

        return {
            "ok": True,
            "image_base64": base64.b64encode(img.data).decode(),
            "format": img.format or "jpeg",
            "objects": [self._detection_to_dict(obj) for obj in objects],
        }

    def annotate_frame(self, image_bytes: bytes, objects: list) -> bytes:
        """Draw bounding boxes on JPEG bytes.

        Args:
            image_bytes: raw JPEG bytes (not base64).
            objects: list of detection dicts from :meth:`get_detections`.

        Returns:
            Annotated JPEG bytes (not base64).

        Uses PIL ``ImageDraw``.  Rectangle 4 px, label text at top-left:
        ``"label, score=0.95, 2.3m"``
        """
        from PIL import Image, ImageDraw, ImageFont

        img = Image.open(io.BytesIO(image_bytes))
        draw = ImageDraw.Draw(img)

        # Try to get a font; fall back to default
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16
            )
        except (IOError, OSError):
            font = ImageFont.load_default()

        for obj in objects:
            roi = obj.get("roi", {})
            x = roi.get("x", 0)
            y = roi.get("y", 0)
            w = roi.get("width", 0)
            h = roi.get("height", 0)

            label = obj.get("label", "unknown")
            score = obj.get("score", 0.0)
            distance = obj.get("distance")

            color = _LABEL_COLORS.get(label, _LABEL_COLORS["unknown"])

            # Draw rectangle (4px width)
            for i in range(4):
                draw.rectangle(
                    [x - i, y - i, x + w + i, y + h + i],
                    outline=color,
                )

            # Label text
            text = f"{label}, score={score:.2f}"
            if distance is not None:
                text += f", {distance:.1f}m"

            # Text background
            bbox = draw.textbbox((x, y - 20), text, font=font)
            draw.rectangle(bbox, fill=color)
            draw.text((x, y - 20), text, fill=(255, 255, 255), font=font)

        # Encode back to JPEG
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()

    # ── Internal ─────────────────────────────────────────────────────

    @staticmethod
    def _detection_to_dict(obj) -> dict:
        """Convert a proto ``ObjectDetection`` to a plain dict."""
        return {
            "label": _LABEL_NAMES.get(obj.label, "unknown"),
            "label_id": obj.label,
            "roi": {
                "x": obj.roi.x_offset,
                "y": obj.roi.y_offset,
                "width": obj.roi.width,
                "height": obj.roi.height,
            },
            "score": round(obj.score, 4),
            "distance": (
                round(obj.distance_median, 3)
                if obj.distance_median > 0
                else None
            ),
        }
