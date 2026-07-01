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


def _word_to_pdf_word_com(src: str, dest: str) -> str | None:
    """Convert via Microsoft Word using COM automation (Windows + Word only).

    Driven through PowerShell, so NO extra Python package (docx2pdf / pywin32) is
    required — only Windows with Word installed. This uses Word's own rendering
    engine, so the PDF is a FULL-FIDELITY copy of the .docx (backgrounds, images,
    fonts, layout). Returns the produced PDF path, or ``None`` if unavailable.
    """
    if os.name != "nt":
        return None
    # Escape single quotes for the single-quoted PowerShell string literals.
    s = src.replace("'", "''")
    d = dest.replace("'", "''")
    ps = (
        "$ErrorActionPreference='Stop';"
        "$word=New-Object -ComObject Word.Application;"
        "$word.Visible=$false;"
        "$doc=$null;"
        "try{"
        f"$doc=$word.Documents.Open('{s}',$false,$true);"   # (path, ConfirmConversions=false, ReadOnly=true)
        f"$doc.ExportAsFixedFormat('{d}',17);"               # 17 = wdExportFormatPDF
        "}finally{"
        "if($doc){$doc.Close($false)};"
        "$word.Quit()"
        "}"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            check=True, capture_output=True, timeout=180,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return dest if os.path.exists(dest) else None


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


def word_to_pdf(paths: list[str], job_id: str,
                engines_out: list[str] | None = None) -> list[str]:
    """Convert each .docx/.doc to PDF, trying LibreOffice -> docx2pdf -> reportlab.

    Returns one PDF per input (in order). The pure-python fallback guarantees a
    result even with no office suite installed — but it is TEXT-ONLY (drops
    backgrounds, images, colors, complex layout).

    If ``engines_out`` is provided, the engine used for each file is appended to
    it ("libreoffice" / "word" / "reportlab") so callers can warn on fallback.
    """
    if not paths:
        raise ValueError("No input documents provided.")
    out_dir = os.path.dirname(output_path(job_id, "x.pdf"))
    results: list[str] = []
    for src in paths:
        dest = output_path(job_id, with_suffix(src, "", ".pdf"))
        engine = "libreoffice"
        produced = _word_to_pdf_libreoffice(src, out_dir)
        if produced and os.path.abspath(produced) != os.path.abspath(dest):
            shutil.move(produced, dest)
            produced = dest
        if not produced:
            engine = "word"
            produced = _word_to_pdf_word_com(src, dest)
        if not produced:
            engine = "word"
            produced = _word_to_pdf_docx2pdf(src, dest)
        if not produced:
            engine = "reportlab"
            produced = _word_to_pdf_reportlab(src, dest)
        results.append(produced)
        if engines_out is not None:
            engines_out.append(engine)
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
def _pdf_to_word_word_com(src: str, dest: str) -> str | None:
    """Convert a PDF to .docx using Microsoft Word's own PDF import (Windows + Word).

    Word reconstructs the document faithfully (all columns/rows, page framing, no
    right-shift) — far better than pdf2docx. The obstacle on this machine: opening a
    PDF in Word raises a modal "Word will now convert your PDF" dialog that freezes
    headless automation. We work around it by opening the PDF in a VISIBLE Word (in a
    background job) while a foreground loop repeatedly activates the Word window and
    presses Enter to dismiss that dialog. NO registry/security change is needed.

    Trade-off: Word appears briefly and grabs keyboard focus for a few seconds while
    converting — so this is best on a local/desktop run, not a shared server. Returns
    the produced .docx path, or ``None`` if Word is unavailable / it times out (caller
    then falls back to pdf2docx).
    """
    if os.name != "nt":
        return None
    s = src.replace("'", "''")
    d = dest.replace("'", "''")
    script = _WORD_PDF_PS1.replace("__SRC__", s).replace("__DEST__", d)
    ps1 = dest + ".convert.ps1"
    try:
        with open(ps1, "w", encoding="utf-8") as fh:
            fh.write(script)
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1],
            check=False, capture_output=True, timeout=240,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        try:
            os.remove(ps1)
        except OSError:
            pass
    return dest if os.path.exists(dest) else None


# PowerShell used by _pdf_to_word_word_com. Opens the PDF in a visible Word (in a
# background job) and, from the foreground, keeps pressing Enter on Word's modal
# "convert PDF" dialog until the .docx is written. Cleans up any invisible orphan
# Word instances at the end.
_WORD_PDF_PS1 = r"""
$src  = '__SRC__'
$dest = '__DEST__'
if (Test-Path $dest) { Remove-Item $dest -Force -ErrorAction SilentlyContinue }
$job = Start-Job -ScriptBlock {
    param($src, $dest)
    try {
        $w = New-Object -ComObject Word.Application
        $w.Visible = $true
        $w.DisplayAlerts = 0
        $d = $w.Documents.Open($src, $false, $true)
        $d.SaveAs2($dest, 16)
        $d.Close($false)
        $w.Quit()
    } catch {}
} -ArgumentList $src, $dest
Add-Type -AssemblyName System.Windows.Forms
$sh = New-Object -ComObject WScript.Shell
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 700
    try {
        if ($sh.AppActivate('Microsoft Word')) {
            Start-Sleep -Milliseconds 150
            [System.Windows.Forms.SendKeys]::SendWait('~')
        }
    } catch {}
    if (Test-Path $dest) { break }
}
Wait-Job $job -Timeout 5 | Out-Null
Stop-Job $job -ErrorAction SilentlyContinue
Remove-Job $job -Force -ErrorAction SilentlyContinue
Get-Process WINWORD -ErrorAction SilentlyContinue |
    Where-Object { [string]::IsNullOrWhiteSpace($_.MainWindowTitle) } |
    Stop-Process -Force -ErrorAction SilentlyContinue
"""


def _repair_docx_table_layout(path: str) -> None:
    """Fix pdf2docx tables whose column grid doesn't match their cell widths.

    pdf2docx frequently emits a ``<w:tblGrid>`` of equal columns (e.g. three 2.76in
    thirds) while the cells declare very different widths (e.g. 0.34in / 7.59in /
    0.34in) and leaves ``tblW="auto"``. Word then lays the table out on the *grid*,
    so a wide content cell starts on the wrong column — the whole block shifts right
    and overflows the page (the classic "converted document is pushed to the right").
    Because pdf2docx wraps each page's content in such layout tables (often nested),
    this hits richly-formatted templates hard.

    For every table (including nested ones) whose grid disagrees with its first row's
    real cell widths, we: rewrite the grid to match the cells, set an explicit
    ``tblW`` = sum of cell widths, force ``tblLayout=fixed`` (so Word stops
    auto-fitting onto the bad grid), and zero any stray table indent. Tables whose
    grid already matches (normal Word data tables) are left untouched. Best-effort:
    never raises.
    """
    try:
        from docx import Document
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
    except Exception:
        return

    def tw(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    try:
        doc = Document(path)
        body = doc.element.body
        changed = False
        for tbl in body.iter(qn("w:tbl")):
            tblPr = tbl.find(qn("w:tblPr"))
            grid = tbl.find(qn("w:tblGrid"))
            tr = tbl.find(qn("w:tr"))
            if tblPr is None or grid is None or tr is None:
                continue
            # Intended column widths from the first row's cells (twips), honoring gridSpan.
            cell_w, ok = [], True
            for tc in tr.findall(qn("w:tc")):
                tcPr = tc.find(qn("w:tcPr"))
                span, w = 1, None
                if tcPr is not None:
                    gs = tcPr.find(qn("w:gridSpan"))
                    if gs is not None:
                        try:
                            span = max(1, int(gs.get(qn("w:val"))))
                        except (TypeError, ValueError):
                            span = 1
                    tcW = tcPr.find(qn("w:tcW"))
                    if tcW is not None and tcW.get(qn("w:type")) in (None, "dxa"):
                        w = tw(tcW.get(qn("w:w")))
                if w is None:
                    ok = False
                    break
                per = int(round(w / span))
                cell_w.extend([per] * span)
            cols = grid.findall(qn("w:gridCol"))
            if not ok or not cell_w or len(cols) != len(cell_w):
                continue
            cur = [tw(c.get(qn("w:w"))) for c in cols]
            if cur == cell_w:
                continue  # grid already consistent — leave normal tables alone
            for c, nw in zip(cols, cell_w):
                c.set(qn("w:w"), str(nw))
            total = sum(cell_w)
            tblW = tblPr.find(qn("w:tblW"))
            if tblW is None:
                tblW = OxmlElement("w:tblW")
                tblPr.append(tblW)
            tblW.set(qn("w:w"), str(total))
            tblW.set(qn("w:type"), "dxa")
            lay = tblPr.find(qn("w:tblLayout"))
            if lay is None:
                lay = OxmlElement("w:tblLayout")
                tblPr.append(lay)
            lay.set(qn("w:type"), "fixed")
            ind = tblPr.find(qn("w:tblInd"))
            if ind is not None:
                ind.set(qn("w:w"), "0")
                ind.set(qn("w:type"), "dxa")
            changed = True
        if changed:
            doc.save(path)
    except Exception:
        return


def _normalize_docx_left_shift(path: str) -> None:
    """Undo the uniform right-shift pdf2docx adds to a converted .docx.

    pdf2docx preserves each block's absolute x-position by adding a left indent to
    every paragraph/table, which makes the whole page look shifted to the right.

    We estimate the uniform shift as the MOST COMMON positive left-indent across
    all text (body paragraphs *and* paragraphs inside tables — pdf2docx often wraps
    content in invisible layout tables), then subtract it from every paragraph and
    table. Content clamped below zero snaps back to the page margin, so lines that
    were already at the margin stay put while intentional *relative* indentation
    (bullets, quotes, deeper nesting) is preserved. No-op unless a real chunk of the
    text shares the same positive indent, so clean docs are untouched. Best-effort:
    never raises (a failure just leaves the doc as-is).
    """
    try:
        from collections import Counter
        from docx import Document
        from docx.shared import Emu
        from docx.oxml.ns import qn
    except Exception:
        return

    def emu(v) -> int:
        return int(v) if v is not None else 0

    def all_paragraphs(doc):
        # Body paragraphs + every table-cell paragraph (covers layout tables).
        yield from doc.paragraphs
        for tbl in doc.tables:
            for row in tbl.rows:
                for cell in row.cells:
                    yield from cell.paragraphs

    try:
        doc = Document(path)
        indents = [emu(p.paragraph_format.left_indent)
                   for p in all_paragraphs(doc) if (p.text or "").strip()]
        pos = [i for i in indents if i > 45720]  # ignore < ~0.05in
        if not pos:
            return
        # Bucket to ~0.02in and take the most common indent as the uniform shift.
        bucket = 18288  # 0.02in in EMU
        counts = Counter(round(i / bucket) * bucket for i in pos)
        delta, freq = counts.most_common(1)[0]
        # Only correct when the shift affects a real share of the text.
        if delta <= 45720 or freq < max(2, len(pos) * 0.3):
            return
        for p in all_paragraphs(doc):
            new = emu(p.paragraph_format.left_indent) - delta
            p.paragraph_format.left_indent = Emu(new) if new > 0 else None
        # Shift tables by the same amount (w:tblInd is in twips; 1in = 1440 twips).
        delta_tw = int(round(delta / 635))
        for tbl in doc.tables:
            tblPr = tbl._tbl.tblPr
            ind = tblPr.find(qn("w:tblInd")) if tblPr is not None else None
            if ind is None:
                continue
            try:
                w = int(ind.get(qn("w:w")))
            except (TypeError, ValueError):
                w = 0
            ind.set(qn("w:w"), str(max(0, w - delta_tw)))
            ind.set(qn("w:type"), "dxa")
        doc.save(path)
    except Exception:
        return


def pdf_to_word(paths: list[str], job_id: str) -> list[str]:
    """Convert each PDF to .docx (one per input).

    Fidelity strategy (best first):

    1. **Microsoft Word's own PDF import** (``_pdf_to_word_word_com``) — Windows +
       Word only. Reconstructs the document faithfully (all columns/rows, page
       framing, correct margins, no right-shift). Briefly opens Word and takes
       keyboard focus while it converts, so it's best on a local/desktop run.
    2. **pdf2docx fallback** — pure-Python, no Word needed. Works everywhere but is a
       best-effort *guess* at the layout (PDF has no logical structure), so on
       richly-formatted templates it can shift content and even drop hard-to-parse
       cells. We post-process it with ``_repair_docx_table_layout`` (fixes the
       right-shift/overflow from pdf2docx's broken table grids) and
       ``_normalize_docx_left_shift`` (residual paragraph indent).

    Raises :class:`RuntimeError` only if BOTH paths are unavailable, or the fallback
    conversion itself fails.
    """
    if not paths:
        raise ValueError("No input PDFs provided.")
    outputs: list[str] = []
    for src in paths:
        dest = output_path(job_id, with_suffix(src, "", ".docx"))

        # 1) Preferred: Word's own converter (faithful). None if unavailable/timeout.
        produced = _pdf_to_word_word_com(src, dest)
        if produced:
            outputs.append(produced)
            continue

        # 2) Fallback: pdf2docx + layout repairs.
        if not _PDF2DOCX_OK:
            raise RuntimeError(
                "PDF->Word needs either Microsoft Word (Windows) or the 'pdf2docx' "
                "package, neither of which is available."
            )
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
        _repair_docx_table_layout(dest)   # fix pdf2docx's broken table grids (right-shift/overflow)
        _normalize_docx_left_shift(dest)  # undo any residual uniform paragraph indent shift
        outputs.append(dest)
    return outputs
