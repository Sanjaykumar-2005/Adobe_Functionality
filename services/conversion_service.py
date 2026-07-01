"""
Format-conversion service: Word <-> PDF and Image -> PDF.

Pure Python (no Flask). Functions take absolute input paths + plain params,
write outputs under the job OUTPUT dir, and return lists of absolute paths.

External engines are imported defensively. None of them are required to import
this module; a missing engine surfaces as a clear :class:`RuntimeError` only when
the relevant function is actually called and no fallback succeeds.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from config import Config
from utils.file_utils import output_path, stem, with_suffix

# ---- Optional engines (guarded) ------------------------------------------- #
try:
    from PIL import Image  # type: ignore
    _PIL_OK = True
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    _PIL_OK = False

try:
    from pdf2docx import Converter  # type: ignore
    _PDF2DOCX_OK = True
except Exception:  # pragma: no cover
    Converter = None  # type: ignore
    _PDF2DOCX_OK = False


# --------------------------------------------------------------------------- #
# Word -> PDF
# --------------------------------------------------------------------------- #
def _soffice_binary() -> str | None:
    """Locate a LibreOffice/OpenOffice headless binary, if installed."""
    for name in ("soffice", "libreoffice", "soffice.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _word_to_pdf_libreoffice(src: str, out_dir: str) -> str | None:
    """Convert via LibreOffice headless. Returns the PDF path or ``None``."""
    binary = _soffice_binary()
    if not binary:
        return None
    try:
        subprocess.run(
            [binary, "--headless", "--convert-to", "pdf", "--outdir", out_dir, src],
            check=True, capture_output=True, timeout=180,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    produced = os.path.join(out_dir, stem(src) + ".pdf")
    return produced if os.path.exists(produced) else None


def _word_to_pdf_docx2pdf(src: str, dest: str) -> str | None:
    """Convert via the docx2pdf package (needs MS Word, Windows/macOS)."""
    try:
        from docx2pdf import convert as _convert  # type: ignore
    except Exception:
        return None
    try:
        _convert(src, dest)
    except Exception:
        return None
    return dest if os.path.exists(dest) else None


def _word_to_pdf_reportlab(src: str, dest: str) -> str:
    """Pure-Python best-effort fallback: python-docx -> reportlab.

    Renders paragraphs (with heading sizing + basic bold/italic) and simple
    tables. Always produces a PDF. Raises :class:`RuntimeError` only if the
    pure-python toolchain itself is missing.
    """
    try:
        from docx import Document  # python-docx
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Word->PDF fallback needs python-docx. Install LibreOffice or "
            "python-docx + reportlab."
        ) from exc
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
        )
        from reportlab.lib import colors
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Word->PDF fallback needs reportlab. Install LibreOffice or reportlab."
        ) from exc

    docx = Document(src)
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    flow = []

    def _inline(par) -> str:
        """Reassemble a paragraph's runs with crude bold/italic markup."""
        parts = []
        for run in par.runs:
            txt = (run.text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if not txt:
                continue
            if run.bold:
                txt = f"<b>{txt}</b>"
            if run.italic:
                txt = f"<i>{txt}</i>"
            parts.append(txt)
        return "".join(parts) or (par.text or "")

    for par in docx.paragraphs:
        text = _inline(par)
        if not text.strip():
            flow.append(Spacer(1, 6))
            continue
        name = (par.style.name or "").lower() if par.style else ""
        if name.startswith("heading") or name == "title":
            size = 18 if "1" in name or name == "title" else 14
            style = ParagraphStyle("h", parent=body, fontSize=size,
                                   leading=size + 4, spaceAfter=8, spaceBefore=8)
            flow.append(Paragraph(text, style))
        else:
            flow.append(Paragraph(text, body))

    for table in docx.tables:
        data = [[(cell.text or "") for cell in row.cells] for row in table.rows]
        if not data:
            continue
        tbl = Table(data)
        tbl.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
        ]))
        flow.append(Spacer(1, 6))
        flow.append(tbl)

    if not flow:
        flow.append(Paragraph("(empty document)", body))

    SimpleDocTemplate(dest, pagesize=A4,
                      leftMargin=inch, rightMargin=inch,
                      topMargin=inch, bottomMargin=inch).build(flow)
    return dest


def word_to_pdf(paths: list[str], job_id: str) -> list[str]:
    """Convert each .docx/.doc to PDF, trying LibreOffice -> docx2pdf -> reportlab.

    Returns one PDF per input (in order). The pure-python fallback guarantees a
    result even with no office suite installed.
    """
    if not paths:
        raise ValueError("No input documents provided.")
    out_dir = os.path.dirname(output_path(job_id, "x.pdf"))
    results: list[str] = []
    for src in paths:
        dest = output_path(job_id, with_suffix(src, "", ".pdf"))
        produced = _word_to_pdf_libreoffice(src, out_dir)
        if produced and os.path.abspath(produced) != os.path.abspath(dest):
            shutil.move(produced, dest)
            produced = dest
        if not produced:
            produced = _word_to_pdf_docx2pdf(src, dest)
        if not produced:
            produced = _word_to_pdf_reportlab(src, dest)
        results.append(produced)
    return results


# --------------------------------------------------------------------------- #
# Image -> PDF
# --------------------------------------------------------------------------- #
def _prep_image(path: str):
    """Open an image and flatten transparency/palette onto white RGB."""
    im = Image.open(path)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return im.convert("RGB")


def _place_on_page(im, page_size):
    """Return an RGB page-sized canvas with *im* centered, aspect preserved.

    ``page_size`` is a ``(w, h)`` point tuple, or ``None`` (== "Fit", page equals
    image size at 1px=1pt).
    """
    if page_size is None:
        return im  # Fit: page is the image itself
    pw, ph = page_size
    canvas = Image.new("RGB", (int(pw), int(ph)), (255, 255, 255))
    scale = min(pw / im.width, ph / im.height)
    new_w, new_h = max(1, int(im.width * scale)), max(1, int(im.height * scale))
    resized = im.resize((new_w, new_h), Image.LANCZOS)
    canvas.paste(resized, ((int(pw) - new_w) // 2, (int(ph) - new_h) // 2))
    return canvas


def image_to_pdf(paths: list[str], job_id: str, *, page_size: str = "A4",
                 merge: bool = True, out_name: str = "images.pdf") -> list[str]:
    """Convert images to PDF using Pillow.

    *page_size* is a key of ``Config.PAGE_SIZES`` ("Fit" makes each page match its
    image). Images keep aspect ratio, centered on white. ``merge=True`` yields one
    multi-page PDF; otherwise one PDF per image.
    """
    if not _PIL_OK:
        raise RuntimeError("Image->PDF requires Pillow, which is not installed.")
    if not paths:
        raise ValueError("No input images provided.")
    if page_size not in Config.PAGE_SIZES:
        raise ValueError(
            f"Unknown page_size '{page_size}'. "
            f"Choose from: {', '.join(Config.PAGE_SIZES)}."
        )
    size = Config.PAGE_SIZES[page_size]

    pages = [_place_on_page(_prep_image(p), size) for p in paths]

    if merge:
        dest = output_path(job_id, out_name)
        first, rest = pages[0], pages[1:]
        first.save(dest, "PDF", resolution=72.0, save_all=True, append_images=rest)
        return [dest]

    outputs: list[str] = []
    for src, page in zip(paths, pages):
        dest = output_path(job_id, with_suffix(src, "", ".pdf"))
        page.save(dest, "PDF", resolution=72.0)
        outputs.append(dest)
    return outputs


# --------------------------------------------------------------------------- #
# PDF -> Word
# --------------------------------------------------------------------------- #
def pdf_to_word(paths: list[str], job_id: str) -> list[str]:
    """Convert each PDF to .docx via pdf2docx (one .docx per input).

    Raises :class:`RuntimeError` with a clear message if pdf2docx is unavailable
    or a conversion fails.
    """
    if not _PDF2DOCX_OK:
        raise RuntimeError(
            "PDF->Word requires the 'pdf2docx' package, which is not installed."
        )
    if not paths:
        raise ValueError("No input PDFs provided.")
    outputs: list[str] = []
    for src in paths:
        dest = output_path(job_id, with_suffix(src, "", ".docx"))
        try:
            conv = Converter(src)
            try:
                conv.convert(dest)
            finally:
                conv.close()
        except Exception as exc:
            raise RuntimeError(
                f"Failed to convert '{os.path.basename(src)}' to Word: {exc}"
            ) from exc
        outputs.append(dest)
    return outputs
