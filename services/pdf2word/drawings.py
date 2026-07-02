"""
drawings.py — Vector graphics extraction.

Reads ``page.get_drawings()`` and keeps the primitives that map usefully onto
Word: horizontal rule lines (separators under headings, TOC, header/footer) and
long vertical lines. Full arbitrary rectangles/fills don't have a clean flowing-
document equivalent, so they're detected but not recreated (kept in the model for
extensibility). Lines inside a table region are skipped (tables own those).
"""
from __future__ import annotations

import logging

from .model import BBox, Drawing, DrawingKind
from .utils import point_in_any, rgb_from_float

log = logging.getLogger("pdf2word.drawings")

_MIN_HLINE_LEN = 40.0     # a horizontal rule must be at least this wide (points)
_LINE_THICK = 3.0         # max thickness to still count as a "line"


class DrawingExtractor:
    """Extract horizontal/vertical rule lines from a page's vector graphics."""

    def extract(self, fitz_page, exclude_boxes: list[BBox]) -> list[Drawing]:
        out: list[Drawing] = []
        try:
            drawings = fitz_page.get_drawings()
        except Exception:
            return out
        for d in drawings:
            color = rgb_from_float(d.get("color"))
            width = float(d.get("width") or 1.0)
            for item in d.get("items", []):
                seg = self._item_to_drawing(item, color, width)
                if seg is None:
                    continue
                cx = (seg.bbox[0] + seg.bbox[2]) / 2
                cy = (seg.bbox[1] + seg.bbox[3]) / 2
                if point_in_any(cx, cy, exclude_boxes, pad=2.0):
                    continue                     # inside a table — skip
                out.append(seg)
        if out:
            log.info("rule lines detected: %d", len(out))
        return out

    @staticmethod
    def _item_to_drawing(item, color, width) -> Drawing | None:
        kind = item[0]
        if kind == "l":                          # line: ("l", p1, p2)
            p1, p2 = item[1], item[2]
            if abs(p1.y - p2.y) < 1.0 and abs(p2.x - p1.x) >= _MIN_HLINE_LEN:
                bbox = (min(p1.x, p2.x), p1.y, max(p1.x, p2.x), p1.y)
                return Drawing(kind=DrawingKind.HLINE, bbox=bbox, color=color, width=width)
            if abs(p1.x - p2.x) < 1.0 and abs(p2.y - p1.y) >= _MIN_HLINE_LEN:
                bbox = (p1.x, min(p1.y, p2.y), p1.x, max(p1.y, p2.y))
                return Drawing(kind=DrawingKind.VLINE, bbox=bbox, color=color, width=width)
        elif kind == "re":                       # rectangle: ("re", rect, ...)
            r = item[1]
            if min(r.width, r.height) <= _LINE_THICK and max(r.width, r.height) >= _MIN_HLINE_LEN:
                if r.width >= r.height:
                    bbox = (r.x0, (r.y0 + r.y1) / 2, r.x1, (r.y0 + r.y1) / 2)
                    return Drawing(kind=DrawingKind.HLINE, bbox=bbox, color=color, width=r.height or width)
                bbox = ((r.x0 + r.x1) / 2, r.y0, (r.x0 + r.x1) / 2, r.y1)
                return Drawing(kind=DrawingKind.VLINE, bbox=bbox, color=color, width=r.width or width)
        return None
