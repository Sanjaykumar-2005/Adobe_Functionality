"""
whitespace.py — White-space / vertical-gap analysis.

Converts the vertical gaps between consecutive page elements into paragraph
spacing so the Word output visually tracks the PDF. Small gaps are ignored
(normal leading); larger gaps become ``space_before`` on the following
paragraph; very large gaps additionally request a blank spacer paragraph.
"""
from __future__ import annotations

from .model import Element, Paragraph

# points
_IGNORE_BELOW = 4.0          # normal line leading — no extra spacing
_SPACER_ABOVE = 90.0         # gap beyond this also gets a blank spacer paragraph
_MAX_SPACE = 240.0           # cap so a huge gap can't explode spacing


def _top(el: Element) -> float:
    return el.bbox[1]


def _bottom(el: Element) -> float:
    return el.bbox[3]


class WhitespaceAnalyzer:
    """Assign inter-element spacing from measured vertical gaps."""

    def apply(self, elements: list[Element]) -> list[Element]:
        """Return elements with spacing set; may insert blank spacer paragraphs."""
        if not elements:
            return elements
        out: list[Element] = []
        prev_bottom = None
        for el in elements:
            if prev_bottom is not None:
                gap = _top(el) - prev_bottom
                if gap > _IGNORE_BELOW:
                    space = min(gap, _MAX_SPACE)
                    if isinstance(el, Paragraph):
                        el.space_before = max(el.space_before, space)
                    elif gap > _SPACER_ABOVE:
                        out.append(self._spacer(space))
            out.append(el)
            prev_bottom = _bottom(el)
        return out

    @staticmethod
    def _spacer(points: float) -> Paragraph:
        p = Paragraph(runs=[])
        p.space_after = min(points, _MAX_SPACE)
        return p
