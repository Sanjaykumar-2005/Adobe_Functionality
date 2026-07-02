"""
headings.py — Heading detection (rule-based, no ML).

Assigns heading levels using font size relative to body text, bold weight,
centering and shortness. Larger/bolder/short/centered paragraphs become headings;
levels are bucketed by how much bigger than body text they are.
"""
from __future__ import annotations

from .model import Alignment, Paragraph
from .paragraphs import ParagraphBuilder


class HeadingDetector:
    """Assign ``heading_level`` (1..4) to paragraphs that look like headings."""

    def assign(self, paragraphs: list[Paragraph], body_size: float) -> None:
        if body_size <= 0:
            body_size = self._estimate_body_size(paragraphs)
        if body_size <= 0:
            return
        for p in paragraphs:
            if p.list_type is not None or p.is_toc:
                continue
            size = ParagraphBuilder.dominant_size(p)
            text = p.text.strip()
            if not text or len(text) > 200:          # long => body, not a heading
                continue
            bold = bool(p.runs) and all(r.style.bold for r in p.runs if r.text.strip())
            ratio = size / body_size if body_size else 1.0
            centered = p.alignment == Alignment.CENTER

            level = None
            if ratio >= 1.6:
                level = 1
            elif ratio >= 1.35:
                level = 2
            elif ratio >= 1.15:
                level = 3
            elif (bold or centered) and len(text) < 80 and size >= body_size:
                level = 4
            if level:
                p.heading_level = level

    @staticmethod
    def _estimate_body_size(paragraphs: list[Paragraph]) -> float:
        """Most common run size across paragraphs ~= body text size."""
        from collections import Counter
        sizes = Counter()
        for p in paragraphs:
            for r in p.runs:
                if r.style.size and r.text.strip():
                    sizes[round(r.style.size)] += len(r.text)
        return float(sizes.most_common(1)[0][0]) if sizes else 0.0
