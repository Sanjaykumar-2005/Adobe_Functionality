"""
pdf2word — a modular, rule-based, layout-aware PDF -> Word (.docx) engine.

Text-based PDFs only. No OCR, AI, LLMs or cloud APIs. Open-source only
(PyMuPDF, pdfplumber, python-docx, Pillow).

Architecture:
    PDF -> Reader -> Layout Extraction -> Document Model -> Layout Engine
        -> Word Generator -> DOCX

Modules:
    reader            PDF opening, text-based check, streaming pages, metadata
    model             intermediate Document Model (dataclasses)
    layout_engine     orchestrates detectors -> a Page model (reading order)
    layout_geometry   alignment / margin / column-split rules
    paragraphs        span/line -> paragraph merging
    headings          heading-level detection
    lists             bullet / number / roman / alpha list detection
    whitespace        vertical-gap -> spacing
    tables            pdfplumber (+ PyMuPDF) table detection
    images            embedded image extraction
    drawings          rule-line / vector extraction
    headerfooter      repeated header/footer detection
    toc               table-of-contents (dot-leader) detection
    hyperlinks        link extraction + span tagging
    word_generator    Document Model -> .docx
    converter         streaming orchestration + logging + progress

Public API:
    convert_pdf_to_word(input_pdf_path, output_docx_path,
                        remove_borders=False, progress_cb=None) -> dict
    is_text_based(input_pdf_path) -> (bool, reason)
"""
from __future__ import annotations

from typing import Callable, Optional

from .analyze import recommend_mode
from .converter import PdfToWordConverter
from .reader import PdfReader

__all__ = ["convert_pdf_to_word", "is_text_based", "recommend_mode",
           "PdfToWordConverter"]


def convert_pdf_to_word(input_pdf_path: str, output_docx_path: str,
                        remove_borders: bool = False,
                        page_breaks: bool = True,
                        progress_cb: Optional[Callable[[int, int, str], None]] = None) -> dict:
    """Convenience wrapper around :class:`PdfToWordConverter`.

    ``page_breaks=False`` produces one continuously-flowing document (no forced
    break per PDF page, so no manufactured blank/half-empty pages) — used by auto mode.

    Returns ``{"success": bool, "output_path": str|None, "error": str|None,
    "pages": int}``.
    """
    return PdfToWordConverter().convert(
        input_pdf_path, output_docx_path,
        remove_borders=remove_borders, page_breaks=page_breaks, progress_cb=progress_cb,
    )


def is_text_based(input_pdf_path: str) -> tuple[bool, str]:
    """Return ``(ok, reason)`` — reject scanned/image-only PDFs before converting."""
    reader = None
    try:
        reader = PdfReader(input_pdf_path)
        return reader.is_text_based()
    except Exception as exc:  # noqa: BLE001
        # Some libraries raise exceptions with an EMPTY str() (pdfminer's
        # PdfminerException is one), which used to produce a bare, useless
        # "Could not open the PDF:" with no reason. Always name the type.
        detail = str(exc).strip() or f"{type(exc).__name__} (no message)"
        return False, f"Could not open the PDF: {detail}"
    finally:
        if reader is not None:
            reader.close()
