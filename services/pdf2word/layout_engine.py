"""
layout_engine.py — the rule-based Layout Engine.

Takes one raw page and produces a fully-analysed :class:`Page` model. This is the
brain of the converter: it runs every detector, decides reading order (including
multi-column), classifies paragraphs/headings/lists/TOC, drops repeated
header/footer text, and converts vertical gaps into spacing — all with geometry
rules, no ML.
"""
from __future__ import annotations

import logging

from .drawings import DrawingExtractor
from .headerfooter import normalize
from .headings import HeadingDetector
from .hyperlinks import page_links
from .images import ImageExtractor
from .layout_geometry import alignment_for, content_margins, detect_column_split
from .lists import ListDetector
from .model import Page, Paragraph
from .paragraphs import ParagraphBuilder, extract_lines
from .reader import RawPage
from .tables import TableDetector
from .toc import TocDetector
from .whitespace import WhitespaceAnalyzer

log = logging.getLogger("pdf2word.layout")


class LayoutEngine:
    """Assemble the intermediate model for a single page."""

    def __init__(self) -> None:
        self._tables = TableDetector()
        self._images = ImageExtractor()
        self._drawings = DrawingExtractor()
        self._paragraphs = ParagraphBuilder()
        self._lists = ListDetector()
        self._toc = TocDetector()
        self._headings = HeadingDetector()
        self._whitespace = WhitespaceAnalyzer()

    def build_page(self, raw: RawPage, exclude: set[str] | None = None) -> Page:
        exclude = exclude or set()
        fp, pp = raw.fitz_page, raw.plumber_page

        # 1) Structural detectors.
        tables = self._tables.detect(pp, fp)
        table_boxes = [t.bbox for t in tables]
        images = self._images.extract(fp)
        drawings = self._drawings.extract(fp, table_boxes)
        links = page_links(fp)

        # 2) Text -> lines -> paragraphs (column-aware merging).
        lines = extract_lines(fp, table_boxes, links)
        mid = detect_column_split(lines, raw.width)
        if mid is not None:
            left = sorted((l for l in lines if (l.bbox[0] + l.bbox[2]) / 2 < mid),
                          key=lambda l: (l.bbox[1], l.bbox[0]))
            right = sorted((l for l in lines if (l.bbox[0] + l.bbox[2]) / 2 >= mid),
                           key=lambda l: (l.bbox[1], l.bbox[0]))
            paragraphs = self._paragraphs.merge(left) + self._paragraphs.merge(right)
        else:
            lines.sort(key=lambda l: (l.bbox[1], l.bbox[0]))
            paragraphs = self._paragraphs.merge(lines)

        # 3) Classify paragraphs.
        left_m, top_m, right_m, bottom_m = content_margins(
            paragraphs, tables, images, raw.width, raw.height)
        for p in paragraphs:
            p.alignment = alignment_for(p, raw.width, left_m, right_m)
            self._lists.apply(p)
        self._toc.apply(paragraphs)
        self._headings.assign(paragraphs, body_size=0.0)

        # 4) Drop repeated header/footer text sitting in the page bands.
        if exclude:
            paragraphs = [p for p in paragraphs
                          if not self._is_running_hf(p, raw.height, exclude)]

        # 5) Merge everything into reading order (column-aware) + spacing.
        elements = self._order(paragraphs, tables, images, drawings, raw.width, mid)
        elements = self._whitespace.apply(elements)

        log.info("page %d parsed: %d paras, %d tables, %d images, %d rules",
                 raw.number, len(paragraphs), len(tables), len(images), len(drawings))

        return Page(
            number=raw.number, width=raw.width, height=raw.height,
            rotation=raw.rotation, margin_left=left_m, margin_top=top_m,
            margin_right=right_m, margin_bottom=bottom_m, elements=elements,
        )

    # ------------------------------------------------------------------ utils #
    @staticmethod
    def _is_running_hf(p: Paragraph, page_h: float, exclude: set[str]) -> bool:
        text = p.text.strip()
        if not text or normalize(text) not in exclude:
            return False
        cy = (p.bbox[1] + p.bbox[3]) / 2
        return cy <= page_h * 0.10 or cy >= page_h * 0.90

    @staticmethod
    def _order(paragraphs, tables, images, drawings, width, mid):
        elements = [*paragraphs, *tables, *images, *drawings]
        if mid is None:
            elements.sort(key=lambda e: (round(e.bbox[1], 1), e.bbox[0]))
            return elements
        left, right = [], []
        for e in elements:
            (left if (e.bbox[0] + e.bbox[2]) / 2 < mid else right).append(e)
        left.sort(key=lambda e: (e.bbox[1], e.bbox[0]))
        right.sort(key=lambda e: (e.bbox[1], e.bbox[0]))
        return left + right
