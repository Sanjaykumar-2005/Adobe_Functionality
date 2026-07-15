"""
pdf_to_word_service.py — compatibility shim.

The native PDF->Word engine now lives in the modular package ``services.pdf2word``
(reader / model / layout_engine / word_generator / ... — see that package's
__init__ for the full architecture). This module is kept as a thin, stable
entry point so existing imports (e.g. endpoints.py) keep working.

Prefer importing from ``services.pdf2word`` directly in new code.
"""
from __future__ import annotations

from typing import Callable, Optional

from .pdf2word import convert_pdf_to_word as _convert
from .pdf2word import is_text_based, recommend_mode


def convert_pdf_to_word(input_pdf_path: str, output_docx_path: str,
                        remove_borders: bool = False,
                        progress_cb: Optional[Callable[[int, int, str], None]] = None) -> dict:
    """Delegate to the modular pdf2word engine. Returns
    ``{"success", "output_path", "error", "pages"}``."""
    return _convert(input_pdf_path, output_docx_path,
                    remove_borders=remove_borders, progress_cb=progress_cb)


__all__ = ["convert_pdf_to_word", "is_text_based", "recommend_mode"]
