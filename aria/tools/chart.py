"""
ARIA Tool: chart analysis utilities
Encodes chart images for Claude vision API.
"""

import base64
from pathlib import Path


SUPPORTED_FORMATS = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".webp": "image/webp",
    ".gif":  "image/gif",
}


def encode_chart_image(path: Path) -> dict:
    suffix = path.suffix.lower()
    media_type = SUPPORTED_FORMATS.get(suffix)
    if not media_type:
        return None
    try:
        image_bytes = path.read_bytes()
        encoded = base64.standard_b64encode(image_bytes).decode("utf-8")
        return {
            "type": "base64",
            "media_type": media_type,
            "data": encoded,
        }
    except Exception:
        return None