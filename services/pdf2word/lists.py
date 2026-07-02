"""
lists.py — List detection.

Rule-based detection of bullet, numbered, Roman-numeral and alphabetic list
items from the leading marker of a paragraph. Sets ``list_type`` and strips the
marker so the word generator can apply real Word list formatting.
"""
from __future__ import annotations

import re

from .model import ListType, Paragraph

_BULLETS = {"•", "●", "▪", "‣", "⁃", "◦", "·", "-", "*", "o"}
# 1.  1)  (1)   a.  a)   iv.  IV)   etc.
_NUM_RE = re.compile(r"^\(?([0-9]{1,3}|[ivxlcdm]{1,6}|[IVXLCDM]{1,6}|[a-zA-Z])\)?[.)]\s+")


class ListDetector:
    """Detect and normalise list markers on a paragraph (mutates in place)."""

    def apply(self, paragraph: Paragraph) -> None:
        if not paragraph.runs:
            return
        text = paragraph.text.lstrip()
        if not text:
            return

        # Bullet markers.
        first = text[0]
        if first in _BULLETS and (len(text) == 1 or text[1] == " "):
            paragraph.list_type = ListType.BULLET
            self._strip_prefix(paragraph, len(text) - len(text[1:].lstrip()) + 1)
            return

        # Numbered / roman / alphabetic markers.
        m = _NUM_RE.match(text)
        if m:
            paragraph.list_type = ListType.NUMBER
            self._strip_prefix(paragraph, m.end())

    @staticmethod
    def _strip_prefix(paragraph: Paragraph, n: int) -> None:
        """Remove the first *n* visible characters (the marker) from the runs."""
        remaining = n
        # account for leading whitespace already skipped by lstrip in caller
        lead = len(paragraph.text) - len(paragraph.text.lstrip())
        remaining += lead
        for run in paragraph.runs:
            if remaining <= 0:
                break
            if len(run.text) <= remaining:
                remaining -= len(run.text)
                run.text = ""
            else:
                run.text = run.text[remaining:]
                remaining = 0
        paragraph.runs = [r for r in paragraph.runs if r.text]
