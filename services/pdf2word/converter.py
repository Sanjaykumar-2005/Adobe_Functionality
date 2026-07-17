"""
converter.py — top-level orchestration.

Ties the whole pipeline together and drives it in a streaming, page-at-a-time
fashion so 300-500+ page PDFs convert with bounded memory:

    Reader -> (text-based check) -> header/footer scan
           -> for each page: LayoutEngine.build_page -> WordGenerator.add_page
           -> save

Emits comprehensive logs and calls an optional ``progress_cb(page, total, msg)``
after each page so a frontend can show live progress.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

from .headerfooter import HeaderFooterDetector
from .layout_engine import LayoutEngine
from .reader import PdfReader
from .word_generator import WordGenerator

log = logging.getLogger("pdf2word.converter")

ProgressCb = Callable[[int, int, str], None]


class PdfToWordConverter:
    """Convert a text-based PDF to an editable .docx via the document model."""

    def convert(self, input_pdf_path: str, output_docx_path: str, *,
                remove_borders: bool = False,
                page_breaks: bool = True,
                progress_cb: Optional[ProgressCb] = None) -> dict:
        """Run the full pipeline.

        ``page_breaks=False`` suppresses the forced page break between PDF pages
        (continuous flow, no manufactured blank/half-empty pages) — used by auto mode.

        Returns ``{"success", "output_path", "error", "pages"}``.
        """
        if not input_pdf_path or not os.path.exists(input_pdf_path):
            return self._fail("Input PDF not found.")

        reader = None
        try:
            reader = PdfReader(input_pdf_path)

            ok, reason = reader.is_text_based()
            if not ok:
                log.warning("rejected (not text-based): %s", input_pdf_path)
                return self._fail(reason)

            total = reader.page_count
            log.info("layout analysis starting (%d pages)", total)

            # Header/footer detection needs a cheap cross-page pre-scan.
            hf = HeaderFooterDetector().detect(reader.band_lines())

            gen = WordGenerator(remove_borders=remove_borders, page_breaks=page_breaks)
            gen.set_header_footer(hf["header"], hf["footer"])
            engine = LayoutEngine()

            for raw in reader.pages():                     # streaming
                page_model = engine.build_page(raw, exclude=hf["exclude"])
                gen.add_page(page_model, first=(raw.index == 0))
                if progress_cb:
                    pct = int(raw.number * 100 / total) if total else 100
                    progress_cb(raw.number, total, f"Processing page {raw.number}/{total} ({pct}%)")

            log.info("document model complete; writing DOCX")
            gen.save(output_docx_path)
            log.info("conversion completed: %s", output_docx_path)
            return {"success": True, "output_path": output_docx_path,
                    "error": None, "pages": total}

        except Exception as exc:  # noqa: BLE001
            log.exception("conversion failed")
            return self._fail(f"PDF to Word conversion failed: {exc}")
        finally:
            if reader is not None:
                reader.close()

    @staticmethod
    def _fail(msg: str) -> dict:
        return {"success": False, "output_path": None, "error": msg, "pages": 0}
