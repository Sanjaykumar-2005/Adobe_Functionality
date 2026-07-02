"""
toc.py — Table-of-Contents detection.

Rule-based detection of TOC rows: a line whose text has a dotted leader and/or
ends with a page number (e.g. "1.2  API Details ........... 5"). Marks the
paragraph as a TOC entry, stores the trailing page number, and cleans the title
so the word generator can rebuild it with a right tab stop + dot leader.
"""
from __future__ import annotations

import re

from .model import Paragraph

# "Title ......... 12"  or  "Title      12"  (dotted or wide gap then a number)
_TOC_RE = re.compile(r"^(.*?)[\.\s]{2,}\.*\s*(\d{1,4})\s*$")
_HAS_LEADER = re.compile(r"\.{3,}")


class TocDetector:
    """Detect + normalise Table-of-Contents rows within a page's paragraphs."""

    def apply(self, paragraphs: list[Paragraph]) -> None:
        for p in paragraphs:
            text = p.text.strip()
            if not text or len(text) > 200:
                continue
            m = _TOC_RE.match(text)
            if not m:
                continue
            title, page = m.group(1).strip(" ."), m.group(2)
            # Require a dot leader OR a short heading-like title to avoid false hits.
            if not (_HAS_LEADER.search(text) or len(title) < 80):
                continue
            if not title:
                continue
            p.is_toc = True
            p.toc_page = page
            self._set_title(p, title)

    @staticmethod
    def _set_title(paragraph: Paragraph, title: str) -> None:
        """Replace the paragraph's text with just the cleaned title (keep 1st style)."""
        style = paragraph.runs[0].style if paragraph.runs else None
        href = paragraph.runs[0].href if paragraph.runs else None
        from .model import Run, Style
        paragraph.runs = [Run(text=title, style=style or Style(), href=href)]
