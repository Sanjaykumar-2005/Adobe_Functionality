"""
reader.py — the PDF Reader stage.

Owns the PyMuPDF (and pdfplumber) handles, decides whether a PDF is text-based,
exposes document metadata, and yields pages ONE AT A TIME so very large PDFs
(300-500+ pages) are processed with bounded memory (streaming). No layout logic
lives here — it only surfaces raw page handles for the layout engine.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator, Optional

import fitz  # PyMuPDF

try:
    import pdfplumber  # type: ignore
    _PDFPLUMBER_OK = True
except Exception:  # pragma: no cover
    pdfplumber = None  # type: ignore
    _PDFPLUMBER_OK = False

log = logging.getLogger("pdf2word.reader")

# A page needs at least this many extractable chars (summed) to count as "text".
_MIN_TEXT_CHARS = 15
# Fraction of pages that must carry real text for the PDF to be "text-based".
_TEXT_PAGE_RATIO = 0.34


@dataclass
class RawPage:
    """A single page's live handles + geometry, handed to the layout engine."""
    index: int                       # 0-based
    number: int                      # 1-based
    fitz_page: "fitz.Page"
    plumber_page: object             # pdfplumber page or None
    width: float
    height: float
    rotation: int


class PdfReader:
    """Streaming reader over a PDF file (context-manager friendly)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._doc = fitz.open(path)
        self._plumber = pdfplumber.open(path) if _PDFPLUMBER_OK else None
        log.info("PDF loaded: %s (%d pages)", path, self._doc.page_count)

    # ------------------------------------------------------------------ meta #
    @property
    def page_count(self) -> int:
        return self._doc.page_count

    def metadata(self) -> dict:
        md = dict(self._doc.metadata or {})
        md["page_count"] = self._doc.page_count
        return md

    def is_text_based(self) -> tuple[bool, str]:
        """Return ``(ok, reason)``; reject scanned / image-only PDFs.

        A page is "text" when it yields more than a small char threshold. If too
        few pages have real text, the PDF is image-based and unsupported.
        """
        if self._doc.page_count == 0:
            return False, "This PDF is image-based. Only text-based PDFs are supported."
        text_pages = 0
        for page in self._doc:
            if len((page.get_text("text") or "").strip()) >= _MIN_TEXT_CHARS:
                text_pages += 1
        if text_pages == 0 or (text_pages / self._doc.page_count) < _TEXT_PAGE_RATIO:
            return False, "This PDF is image-based. Only text-based PDFs are supported."
        return True, ""

    # --------------------------------------------------------------- scanning #
    def band_lines(self) -> list[dict]:
        """Cheap per-page ``{"height", "lines":[(x0,y0,x1,y1,text)]}`` for HF scan.

        Uses ``get_text("blocks")`` (fast, positioned) rather than the full dict.
        """
        out: list[dict] = []
        for page in self._doc:
            try:
                blocks = page.get_text("blocks")  # (x0,y0,x1,y1,text,no,type)
                lines = [(b[0], b[1], b[2], b[3], (b[4] or "").strip())
                         for b in blocks if b[6] == 0 and (b[4] or "").strip()]
                out.append({"height": page.rect.height, "lines": lines})
            except Exception:
                out.append({"height": page.rect.height if page else 842, "lines": []})
        return out

    # ------------------------------------------------------------- streaming #
    def pages(self) -> Iterator[RawPage]:
        """Yield each page's handles one at a time (bounded memory)."""
        for i in range(self._doc.page_count):
            fp = self._doc[i]
            pp = None
            if self._plumber is not None and i < len(self._plumber.pages):
                pp = self._plumber.pages[i]
            rect = fp.rect
            yield RawPage(
                index=i, number=i + 1, fitz_page=fp, plumber_page=pp,
                width=rect.width, height=rect.height, rotation=fp.rotation,
            )

    # -------------------------------------------------------------- lifecycle #
    def close(self) -> None:
        for handle in (self._plumber, self._doc):
            try:
                if handle is not None:
                    handle.close()
            except Exception:
                pass

    def __enter__(self) -> "PdfReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
