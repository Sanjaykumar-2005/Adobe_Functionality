"""
images.py — Embedded image extraction.

Pulls raster images from a page (with their on-page rectangle) so the word
generator can insert them inline at approximately the right position and size.
"""
from __future__ import annotations

import logging

from .model import Image

log = logging.getLogger("pdf2word.images")

# Ignore hairline/again-noise images below this size (points).
_MIN_DIM = 6.0


class ImageExtractor:
    """Extract embedded images from a page as :class:`Image` models."""

    def extract(self, fitz_page) -> list[Image]:
        images: list[Image] = []
        try:
            data = fitz_page.get_text("dict")
        except Exception:
            return images
        for block in data.get("blocks", []):
            if block.get("type", 0) != 1:            # 1 = image block
                continue
            raw = block.get("image")
            if not raw:
                continue
            bbox = tuple(block.get("bbox", (0, 0, 0, 0)))
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            if w < _MIN_DIM or h < _MIN_DIM:
                continue
            images.append(Image(
                data=raw, ext=block.get("ext", "png") or "png",
                width=w, height=h, bbox=bbox,
            ))
        if images:
            log.info("images extracted: %d", len(images))
        return images
