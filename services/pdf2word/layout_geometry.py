"""
layout_geometry.py — pure geometry rules used by the layout engine.

Kept separate so the alignment / margin / column-split heuristics are easy to
test and tune independently of the orchestration in :mod:`layout_engine`.
"""
from __future__ import annotations

from .model import Alignment, Paragraph
from .utils import median


def content_margins(paragraphs, tables, images, page_w: float, page_h: float):
    """Estimate (left, top, right, bottom) margins in points from content extent."""
    xs0, ys0, xs1, ys1 = [], [], [], []
    for coll in (paragraphs, tables, images):
        for e in coll:
            b = e.bbox
            if b == (0.0, 0.0, 0.0, 0.0):
                continue
            xs0.append(b[0]); ys0.append(b[1]); xs1.append(b[2]); ys1.append(b[3])
    if not xs0:
        return 72.0, 72.0, 72.0, 72.0
    left = min(max(min(xs0), 12.0), 144.0)
    top = min(max(min(ys0), 12.0), 144.0)
    right = min(max(page_w - max(xs1), 12.0), 144.0)
    bottom = min(max(page_h - max(ys1), 12.0), 144.0)
    return left, top, right, bottom


def alignment_for(p: Paragraph, page_w: float, left_m: float, right_m: float):
    """Infer paragraph alignment from horizontal position within the text column."""
    x0, _, x1, _ = p.bbox
    content_l, content_r = left_m, page_w - right_m
    width = content_r - content_l
    if width <= 0:
        return None
    gap_l = x0 - content_l
    gap_r = content_r - x1
    if abs(gap_l - gap_r) < width * 0.08 and gap_l > width * 0.12:
        return Alignment.CENTER
    if gap_l > width * 0.30 and gap_r < width * 0.08:
        return Alignment.RIGHT
    return None  # left (default); true justification isn't reliably detectable


def detect_column_split(lines, page_w: float):
    """Return the x of a 2-column split, or ``None`` for single-column pages."""
    if len(lines) < 8:
        return None
    mid = page_w / 2.0
    left = sum(1 for l in lines if l.bbox[2] < mid)
    right = sum(1 for l in lines if l.bbox[0] > mid)
    crossing = sum(1 for l in lines if l.bbox[0] < mid < l.bbox[2])
    if left >= 3 and right >= 3 and crossing < 0.15 * len(lines):
        return mid
    return None
