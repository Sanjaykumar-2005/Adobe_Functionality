"""
tables.py — Table detection.

Uses pdfplumber's geometry-based table finder (preferred) with a PyMuPDF
``find_tables`` fallback. Produces :class:`Table` models with the cell grid and
bounding box; cells are never flattened into paragraphs (the layout engine
excludes any text inside a table's bbox).
"""
from __future__ import annotations

import logging

from .model import BBox, Table

log = logging.getLogger("pdf2word.tables")


class TableDetector:
    """Detect tables on a page and return :class:`Table` models."""

    def detect(self, plumber_page, fitz_page) -> list[Table]:
        tables = self._from_plumber(plumber_page)
        if not tables:
            tables = self._from_fitz(fitz_page)
        if tables:
            log.info("tables detected: %d", len(tables))
        return tables

    # ------------------------------------------------------------ pdfplumber #
    def _from_plumber(self, plumber_page) -> list[Table]:
        if plumber_page is None:
            return []
        out: list[Table] = []
        try:
            found = plumber_page.find_tables()
        except Exception:
            return []
        for t in found:
            try:
                data = t.extract()
            except Exception:
                continue
            rows = self._clean(data)
            if not rows:
                continue
            out.append(Table(rows=rows, bbox=tuple(t.bbox),
                             has_borders=self._has_borders(plumber_page, t.bbox)))
        return out

    @staticmethod
    def _has_borders(plumber_page, bbox: BBox) -> bool:
        """True if ruled lines exist inside the table's box (bordered table)."""
        try:
            x0, top, x1, bottom = bbox
            for ln in list(plumber_page.lines) + list(plumber_page.rects):
                cx = (ln.get("x0", 0) + ln.get("x1", 0)) / 2
                cy = (ln.get("top", 0) + ln.get("bottom", 0)) / 2
                if x0 - 2 <= cx <= x1 + 2 and top - 2 <= cy <= bottom + 2:
                    return True
        except Exception:
            pass
        return True  # default to bordered (most tables are)

    # --------------------------------------------------------------- PyMuPDF #
    def _from_fitz(self, fitz_page) -> list[Table]:
        out: list[Table] = []
        try:
            finder = fitz_page.find_tables()
        except Exception:
            return []
        for t in getattr(finder, "tables", []):
            try:
                data = t.extract()
            except Exception:
                continue
            rows = self._clean(data)
            if rows:
                out.append(Table(rows=rows, bbox=tuple(t.bbox), has_borders=True))
        return out

    # ----------------------------------------------------------------- utils #
    @staticmethod
    def _clean(data) -> list[list[str]]:
        """Normalise a raw grid: drop empty rows, stringify cells, pad ragged rows."""
        rows = [r for r in (data or []) if r is not None]
        if not rows:
            return []
        ncols = max((len(r) for r in rows), default=0)
        if ncols == 0:
            return []
        clean: list[list[str]] = []
        for r in rows:
            row = [("" if c is None else str(c).strip()) for c in r]
            row += [""] * (ncols - len(row))
            clean.append(row)
        # Drop fully-empty rows.
        clean = [r for r in clean if any(c for c in r)]
        return clean
