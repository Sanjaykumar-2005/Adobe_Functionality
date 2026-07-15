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
from .model import BBox, Page, Paragraph
from .paragraphs import ParagraphBuilder, extract_lines
from .reader import RawPage
from .tables import TableDetector
from .toc import TocDetector
from .utils import point_in_any
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

        # 3b) White/light text on a dark fill (code blocks, coloured banners) would
        #     be invisible on the white page. Re-attach the dark fill as paragraph
        #     shading so it stays readable (light text on the dark block, as in the PDF).
        self._shade_light_on_dark(paragraphs, fp, table_boxes)

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
    def _has_light_text(p: Paragraph) -> bool:
        """True if any run is near-white (would vanish on a white page)."""
        for r in p.runs:
            c = r.style.color or (0, 0, 0)
            if c[0] > 200 and c[1] > 200 and c[2] > 200 and r.text.strip():
                return True
        return False

    @staticmethod
    def _dark_fills(fp, exclude_boxes: list[BBox]) -> list[tuple[BBox, tuple[int, int, int]]]:
        """Near-black / dark filled rectangles from the page's vector graphics.

        Returns (bbox, rgb) pairs. Table regions are skipped — table cells own
        their own backgrounds and are already rendered legibly.
        """
        out: list[tuple[BBox, tuple[int, int, int]]] = []
        try:
            drawings = fp.get_drawings()
        except Exception:
            return out
        for d in drawings:
            fill = d.get("fill")
            rect = d.get("rect")
            if not fill or rect is None:
                continue
            r, g, b = fill[0], fill[1], fill[2]
            lum = 0.299 * r + 0.587 * g + 0.114 * b     # perceived brightness 0..1
            if lum > 0.5:
                continue                                 # not a dark fill
            cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
            if point_in_any(cx, cy, exclude_boxes, pad=2.0):
                continue                                 # inside a table
            out.append(((rect.x0, rect.y0, rect.x1, rect.y1),
                        (int(r * 255), int(g * 255), int(b * 255))))
        return out

    def _shade_light_on_dark(self, paragraphs, fp, table_boxes) -> None:
        """Tag paragraphs whose light text sits on a dark fill with that fill colour."""
        candidates = [p for p in paragraphs if self._has_light_text(p)]
        if not candidates:
            return
        fills = self._dark_fills(fp, table_boxes)
        if not fills:
            return
        for p in candidates:
            cx = (p.bbox[0] + p.bbox[2]) / 2
            cy = (p.bbox[1] + p.bbox[3]) / 2
            for (x0, y0, x1, y1), col in fills:
                if x0 - 2 <= cx <= x1 + 2 and y0 - 2 <= cy <= y1 + 2:
                    p.shading = col
                    break

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
