"""
analyze.py — pick the best PDF->Word mode for a document.

The two conversion paths have opposite strengths:

* **faithful** (LibreOffice / MS Word import) preserves the visual look
  (backgrounds, borders, shading, images) but scatters text into positioned
  frames — and it MANGLES data tables and can HIDE white text on dark fills.
* **editable** (the native pdf2word engine) rebuilds real, editable paragraphs
  and Word tables and keeps light-on-dark text readable, at the cost of not
  reproducing page backgrounds/borders.

``recommend_mode`` samples a PDF and returns ``"editable"`` when the faithful
path would likely LOSE content — i.e. the doc has meaningful white-text-on-dark
regions, or it is table-dominated — and ``"faithful"`` otherwise (where the
office import's visual fidelity is the bigger win).
"""
from __future__ import annotations

import logging

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover - import-guarded like the rest of the engine
    fitz = None

log = logging.getLogger("pdf2word.analyze")

_LIGHT = 200                 # channel value above which a colour counts as "light"
_LIGHT_ON_DARK_MIN = 40      # chars of white-on-dark text that make faithful risky
_TABLE_RATIO_MIN = 0.35      # table text / all text above which a doc is "table-heavy"
_SAMPLE_PAGES = 15           # cap the scan so huge PDFs stay fast


def _near_white(color_int: int) -> bool:
    r, g, b = (color_int >> 16) & 255, (color_int >> 8) & 255, color_int & 255
    return r > _LIGHT and g > _LIGHT and b > _LIGHT


def _dark_rects(page) -> list:
    """Bounding rects of near-black / dark filled shapes on the page."""
    rects = []
    try:
        for d in page.get_drawings():
            fill = d.get("fill")
            rect = d.get("rect")
            if not fill or rect is None:
                continue
            lum = 0.299 * fill[0] + 0.587 * fill[1] + 0.114 * fill[2]
            if lum <= 0.5:
                rects.append(rect)
    except Exception:
        pass
    return rects


def recommend_mode(path: str, sample_pages: int = _SAMPLE_PAGES) -> str:
    """Return ``"editable"`` or ``"faithful"`` for the PDF at *path*.

    Never raises — any failure degrades to ``"faithful"`` (the historical default).
    """
    if fitz is None:
        return "faithful"
    try:
        doc = fitz.open(path)
    except Exception:
        return "faithful"
    try:
        total_text = 0
        table_text = 0
        light_on_dark = 0
        n = min(doc.page_count, max(1, sample_pages))
        for i in range(n):
            page = doc[i]
            dark = _dark_rects(page)

            try:
                data = page.get_text("dict")
            except Exception:
                data = {"blocks": []}
            for blk in data.get("blocks", []):
                if blk.get("type") != 0:
                    continue
                for line in blk.get("lines", []):
                    for span in line.get("spans", []):
                        nchar = len((span.get("text") or "").strip())
                        if not nchar:
                            continue
                        total_text += nchar
                        if dark and _near_white(span.get("color", 0)):
                            x0, y0, x1, y1 = span["bbox"]
                            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                            if any(r.x0 - 2 <= cx <= r.x1 + 2 and r.y0 - 2 <= cy <= r.y1 + 2
                                   for r in dark):
                                light_on_dark += nchar

            try:
                for t in page.find_tables().tables:
                    for row in t.extract():
                        for cell in row:
                            if cell and str(cell).strip():
                                table_text += len(str(cell).strip())
            except Exception:
                pass

        ratio = (table_text / total_text) if total_text else 0.0
        mode = "editable" if (light_on_dark >= _LIGHT_ON_DARK_MIN
                              or ratio >= _TABLE_RATIO_MIN) else "faithful"
        log.info("recommend_mode(%s) -> %s (light_on_dark=%d chars, table_ratio=%.2f, "
                 "pages_sampled=%d)", path, mode, light_on_dark, ratio, n)
        return mode
    finally:
        doc.close()
