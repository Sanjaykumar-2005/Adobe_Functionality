"""
headerfooter.py — Header & footer detection.

Repeated content in the top band across pages becomes the Word header; repeated
content in the bottom band becomes the footer. Page numbers are normalised (digits
-> '#') so "Page 1"/"Page 2" count as the same repeated string. Detected
header/footer text is also returned so the layout engine can drop it from the body
(avoiding duplication).
"""
from __future__ import annotations

import logging
import re
from collections import Counter

log = logging.getLogger("pdf2word.headerfooter")

_TOP_BAND = 0.10          # top 10% of page height
_BOTTOM_BAND = 0.90       # below 90% of page height
_REPEAT_RATIO = 0.4       # must appear on >= 40% of (multi-)pages
_DIGITS = re.compile(r"\d+")


def _norm(text: str) -> str:
    return _DIGITS.sub("#", text.strip())


class HeaderFooterDetector:
    """Find repeated header/footer text across pages."""

    def detect(self, band_pages: list[dict]) -> dict:
        """``band_pages`` = ``[{"height": h, "lines": [(x0,y0,x1,y1,text), ...]}]``.

        Returns ``{"header": str|None, "footer": str|None, "exclude": set[str]}``
        where *exclude* holds normalised strings the body should drop.
        """
        n = len(band_pages)
        if n < 2:
            return {"header": None, "footer": None, "exclude": set()}

        header_counts: Counter = Counter()
        footer_counts: Counter = Counter()
        samples: dict[str, str] = {}

        for pg in band_pages:
            h = pg.get("height", 842) or 842
            top_limit = h * _TOP_BAND
            bot_limit = h * _BOTTOM_BAND
            seen_top, seen_bot = set(), set()
            for (x0, y0, x1, y1, text) in pg.get("lines", []):
                if not text:
                    continue
                key = _norm(text)
                samples.setdefault(key, text)
                if y1 <= top_limit and key not in seen_top:
                    header_counts[key] += 1
                    seen_top.add(key)
                elif y0 >= bot_limit and key not in seen_bot:
                    footer_counts[key] += 1
                    seen_bot.add(key)

        threshold = max(2, int(n * _REPEAT_RATIO))
        header_key = self._top_repeated(header_counts, threshold)
        footer_key = self._top_repeated(footer_counts, threshold)

        exclude = {k for k in (header_key, footer_key) if k}
        result = {
            "header": samples.get(header_key) if header_key else None,
            "footer": samples.get(footer_key) if footer_key else None,
            "exclude": exclude,
        }
        if result["header"] or result["footer"]:
            log.info("header=%r footer=%r", result["header"], result["footer"])
        return result

    @staticmethod
    def _top_repeated(counts: Counter, threshold: int):
        for key, cnt in counts.most_common():
            if cnt >= threshold:
                return key
        return None


def normalize(text: str) -> str:
    """Public helper: normalise a body line for comparison with exclude set."""
    return _norm(text)
