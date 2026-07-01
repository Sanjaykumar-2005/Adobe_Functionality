"""
OCR service: detect scanned PDFs and produce searchable (text-layer) PDFs.

Pure Python (no Flask). The heavy lifting is delegated to ``ocrmypdf`` (which
wraps the Tesseract + Ghostscript binaries). Those are *runtime* dependencies of
the host, not of this module — imports are guarded so the module loads fine on a
box without them, and a clear :class:`RuntimeError` is raised only when OCR is
actually requested but the toolchain is missing.
"""
from __future__ import annotations

import fitz  # PyMuPDF

from config import Config
from utils.file_utils import output_path, with_suffix

# ocrmypdf is optional; it (and Tesseract/Ghostscript) may be absent.
try:
    import ocrmypdf  # type: ignore
    _OCRMYPDF_OK = True
except Exception:  # pragma: no cover
    ocrmypdf = None  # type: ignore
    _OCRMYPDF_OK = False


# Below this many characters a page is treated as having "no real text".
_MIN_TEXT_CHARS = 12


def is_scanned(path: str) -> dict:
    """Heuristically decide whether a PDF is scanned (image-only).

    A page counts as "text" if it yields more than a small threshold of
    extractable characters. A page counts as a scan candidate if it has little
    text *and* contains at least one image. The document is flagged ``scanned``
    when more than half of its pages look like scans.

    Returns ``{"scanned", "text_pages", "total_pages", "ratio"}`` where *ratio*
    is the fraction of pages that DO have real text.
    """
    text_pages = 0
    scanned_pages = 0
    with fitz.open(path) as doc:
        total = doc.page_count
        for page in doc:
            text = page.get_text("text").strip()
            has_text = len(text) >= _MIN_TEXT_CHARS
            has_images = bool(page.get_images(full=True))
            if has_text:
                text_pages += 1
            elif has_images:
                scanned_pages += 1
    ratio = (text_pages / total) if total else 0.0
    scanned = total > 0 and (scanned_pages / total) > 0.5
    return {
        "scanned": scanned,
        "text_pages": text_pages,
        "total_pages": total,
        "ratio": round(ratio, 4),
    }


def ocr_pdf(path: str, job_id: str, lang: str | None = None,
            force: bool = False) -> list[str]:
    """Produce a searchable PDF with an OCR text layer.

    Uses ``ocrmypdf`` to preserve layout. ``force=True`` re-OCRs pages that
    already contain text; otherwise existing text pages are skipped.

    Raises :class:`RuntimeError` with installation guidance if ocrmypdf or its
    Tesseract/Ghostscript backends are unavailable.
    """
    lang = lang or Config.OCR_DEFAULT_LANG
    dest = output_path(job_id, with_suffix(path, "_ocr"))

    if not _OCRMYPDF_OK:
        raise RuntimeError(
            "OCR is unavailable: the 'ocrmypdf' package (and the Tesseract OCR "
            "engine + Ghostscript) are not installed on this server. Install "
            "Tesseract and Ghostscript, then `pip install ocrmypdf`, to enable OCR."
        )

    try:
        ocrmypdf.ocr(
            path, dest,
            language=lang,
            skip_text=not force,
            force_ocr=force,
            progress_bar=False,
            deskew=True,
        )
    except Exception as exc:
        # Translate the common "binary missing" failures into a friendly message.
        name = type(exc).__name__
        msg = str(exc)
        if "Tesseract" in msg or "tesseract" in msg or name == "MissingDependencyError":
            raise RuntimeError(
                "OCR failed: the Tesseract engine could not be found. Please "
                "install Tesseract OCR (and Ghostscript) on the server."
            ) from exc
        raise RuntimeError(f"OCR failed: {msg}") from exc
    return [dest]
