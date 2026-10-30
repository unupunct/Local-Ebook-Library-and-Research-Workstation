"""Cover thumbnail cache (Pillow)."""
from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)
THUMB_SIZE = (320, 480)
Image.MAX_IMAGE_PIXELS = 100_000_000  # guard against decompression bombs


def save_thumbnail(data: bytes, dest: Path) -> bool:
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            im = im.convert("RGB")
            im.thumbnail(THUMB_SIZE, Image.LANCZOS)
            dest.parent.mkdir(parents=True, exist_ok=True)
            im.save(dest, "JPEG", quality=82)
        return True
    except Exception as exc:
        log.info("Cover could not be processed: %s", exc)
        return False
