"""
hyperlinks.py — Hyperlink extraction.

Pulls clickable link rectangles from a page so the paragraph builder can tag the
text runs that fall inside them, and the word generator can recreate real,
editable hyperlinks in the DOCX.
"""
from __future__ import annotations

from typing import Optional

from .model import BBox
from .utils import bbox_center, point_in_bbox


def page_links(fitz_page) -> list[dict]:
    """Return ``[{"uri": str, "rect": BBox}, ...]`` for external links on a page."""
    out: list[dict] = []
    try:
        for lk in fitz_page.get_links() or []:
            uri = lk.get("uri")
            rect = lk.get("from")
            if uri and rect is not None:
                out.append({"uri": uri, "rect": (rect.x0, rect.y0, rect.x1, rect.y1)})
    except Exception:
        pass
    return out


def href_for_span(span_bbox: Optional[BBox], links: list[dict]) -> Optional[str]:
    """Return the URL whose rectangle contains the span's centre, if any."""
    if not span_bbox or not links:
        return None
    cx, cy = bbox_center(span_bbox)
    for lk in links:
        if point_in_bbox(cx, cy, lk["rect"]):
            return lk["uri"]
    return None
