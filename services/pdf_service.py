"""
PDF page-operation service — the workhorse of the application.

Every public function is *pure Python* (no Flask). Inputs are absolute paths +
plain parameters; outputs are written under the job's OUTPUT dir via
``output_path`` and each function returns a ``list`` of absolute output paths
(``render_thumbnails`` returns a richer ``list[dict]``).

Engine: PyMuPDF (``import fitz``). Pillow is used only for image re-encoding
during :func:`compress` and watermark opacity; it is imported defensively so the
rest of the module keeps working if Pillow is somehow unavailable.

Page-number convention
----------------------
All ``pages`` / ``order`` parameters coming from the UI are **1-based**. They are
converted to 0-based indices internally and validated against the document
length; out-of-range values raise :class:`ValueError`.
"""
from __future__ import annotations

import io
import os
from typing import Iterable, Sequence

import fitz  # PyMuPDF

from config import Config
from utils.file_utils import (
    download_url,
    output_path,
    stem,
    with_suffix,
)

# Pillow is optional at runtime; only image-heavy ops need it.
try:  # pragma: no cover - trivial guard
    from PIL import Image  # type: ignore

    _PIL_OK = True
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    _PIL_OK = False


# --------------------------------------------------------------------------- #
# Private helpers
# --------------------------------------------------------------------------- #
def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    """Convert ``"#rrggbb"`` (or ``"rrggbb"`` / ``"#rgb"``) to RGB floats 0..1.

    Falls back to mid-grey on malformed input so an overlay never crashes a job.
    """
    if not hex_color:
        return (0.5, 0.5, 0.5)
    s = hex_color.strip().lstrip("#")
    if len(s) == 3:  # shorthand #rgb -> #rrggbb
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        return (0.5, 0.5, 0.5)
    try:
        r = int(s[0:2], 16) / 255.0
        g = int(s[2:4], 16) / 255.0
        b = int(s[4:6], 16) / 255.0
    except ValueError:
        return (0.5, 0.5, 0.5)
    return (r, g, b)


def _parse_ranges(spec: str, total: int) -> list[list[int]]:
    """Expand a range spec like ``"1-3,5,8-10"`` into groups of 0-based indices.

    Each comma-separated token becomes one group: ``"1-3"`` -> ``[0, 1, 2]`` and
    ``"5"`` -> ``[4]``. Page numbers are 1-based and validated against *total*.
    Raises :class:`ValueError` on malformed tokens or out-of-range pages.
    """
    if not spec or not str(spec).strip():
        raise ValueError("Empty range specification.")
    groups: list[list[int]] = []
    for raw in str(spec).split(","):
        token = raw.strip()
        if not token:
            continue
        if "-" in token:
            a, _, b = token.partition("-")
            try:
                start, end = int(a), int(b)
            except ValueError:
                raise ValueError(f"Invalid range token: '{token}'.")
            if start > end:
                start, end = end, start
            page_nums = range(start, end + 1)
        else:
            try:
                page_nums = [int(token)]
            except ValueError:
                raise ValueError(f"Invalid page token: '{token}'.")
        group: list[int] = []
        for p in page_nums:
            if p < 1 or p > total:
                raise ValueError(
                    f"Page {p} out of range (document has {total} pages)."
                )
            group.append(p - 1)
        if group:
            groups.append(group)
    if not groups:
        raise ValueError("No valid ranges parsed.")
    return groups


def _validate_pages(pages: Iterable[int] | None, total: int) -> list[int]:
    """Convert a 1-based page list to validated, de-duplicated 0-based indices.

    ``None`` -> all pages. Raises :class:`ValueError` on out-of-range values.
    """
    if pages is None:
        return list(range(total))
    seen: list[int] = []
    for p in pages:
        try:
            pi = int(p)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid page number: {p!r}.")
        if pi < 1 or pi > total:
            raise ValueError(f"Page {pi} out of range (document has {total} pages).")
        idx = pi - 1
        if idx not in seen:
            seen.append(idx)
    return seen


def _subset_doc(src: "fitz.Document", indices: Sequence[int]) -> "fitz.Document":
    """Build a new in-memory document from *src* containing only *indices*.

    Pages are copied in the exact order given (supports reordering / repetition).
    """
    out = fitz.open()
    for i in indices:
        out.insert_pdf(src, from_page=i, to_page=i)
    return out


def _font_name(base: str | None, bold: bool = False, italic: bool = False) -> str:
    """Map a friendly font name + bold/italic flags to a PyMuPDF base-14 code."""
    b = (base or "helv").lower()
    if b.startswith("cour") or b.startswith("mono"):
        fam = {(False, False): "cour", (True, False): "cobo",
               (False, True): "coit", (True, True): "cobi"}
    elif b.startswith("tim") or b.startswith("tiro") or b.startswith("serif"):
        fam = {(False, False): "tiro", (True, False): "tibo",
               (False, True): "tiit", (True, True): "tibi"}
    else:  # helvetica / sans / default
        fam = {(False, False): "helv", (True, False): "hebo",
               (False, True): "heit", (True, True): "hebi"}
    return fam[(bool(bold), bool(italic))]


def _out(job_id: str, src_path: str, suffix: str) -> str:
    """Convenience: output path named ``<stem><suffix>.pdf`` in the job dir."""
    return output_path(job_id, with_suffix(src_path, suffix))


# --------------------------------------------------------------------------- #
# Inspection / rendering
# --------------------------------------------------------------------------- #
def page_count(path: str) -> int:
    """Return the number of pages in the PDF at *path*."""
    with fitz.open(path) as doc:
        return doc.page_count


def render_thumbnails(path: str, job_id: str, pages: list[int] | None = None,
                      dpi: int = 72) -> list[dict]:
    """Render requested pages to PNG thumbnails in the job OUTPUT dir.

    Parameters
    ----------
    pages : 1-based page numbers, or ``None`` for every page.
    dpi   : render resolution; capped at 150 for speed/memory.

    Returns a list of ``{"page", "url", "width", "height"}`` (page is 1-based).
    """
    dpi = max(36, min(int(dpi or 72), 150))
    results: list[dict] = []
    with fitz.open(path) as doc:
        indices = _validate_pages(pages, doc.page_count)
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        base = stem(path)
        for idx in indices:
            page = doc[idx]
            pix = page.get_pixmap(matrix=mat, alpha=False)
            name = f"{base}_p{idx + 1}.png"
            pix.save(output_path(job_id, name))
            results.append({
                "page": idx + 1,
                "url": download_url(job_id, name),
                "width": pix.width,
                "height": pix.height,
            })
    return results


# --------------------------------------------------------------------------- #
# Merge / split / reorder
# --------------------------------------------------------------------------- #
def merge(paths: list[str], job_id: str, out_name: str = "merged.pdf") -> list[str]:
    """Concatenate the given PDFs (in order) into a single output PDF."""
    if not paths:
        raise ValueError("No input files to merge.")
    out = fitz.open()
    try:
        for p in paths:
            with fitz.open(p) as src:
                out.insert_pdf(src)
        dest = output_path(job_id, out_name)
        out.save(dest, deflate=True, garbage=4)
    finally:
        out.close()
    return [dest]


def split(path: str, job_id: str, mode: str, ranges: str | None = None,
          n: int | None = None, pages: list[int] | None = None) -> list[str]:
    """Split a PDF according to *mode*.

    Modes
    -----
    ``"pages"``    one PDF per page.
    ``"ranges"``   ``ranges="1-3,5,8-10"`` -> one PDF per comma group.
    ``"every_n"``  ``n`` -> consecutive chunks of *n* pages.
    ``"custom"``   ``pages=[1,3,5]`` -> a single PDF of exactly those pages.

    Returns every produced PDF path.
    """
    outputs: list[str] = []
    base = stem(path)
    with fitz.open(path) as doc:
        total = doc.page_count

        if mode == "pages":
            groups = [[i] for i in range(total)]
        elif mode == "ranges":
            groups = _parse_ranges(ranges or "", total)
        elif mode == "every_n":
            if not n or int(n) < 1:
                raise ValueError("'every_n' split requires n >= 1.")
            n = int(n)
            groups = [list(range(i, min(i + n, total))) for i in range(0, total, n)]
        elif mode == "custom":
            idxs = _validate_pages(pages, total)
            if not idxs:
                raise ValueError("'custom' split requires at least one page.")
            groups = [idxs]
        else:
            raise ValueError(f"Unknown split mode: '{mode}'.")

        for gi, group in enumerate(groups, start=1):
            if not group:
                continue
            sub = _subset_doc(doc, group)
            try:
                if mode == "pages":
                    name = f"{base}_p{group[0] + 1}.pdf"
                elif mode == "custom":
                    name = with_suffix(path, "_selected")
                else:
                    name = f"{base}_part{gi}.pdf"
                dest = output_path(job_id, name)
                sub.save(dest, deflate=True, garbage=4)
                outputs.append(dest)
            finally:
                sub.close()
    return outputs


def rotate(path: str, job_id: str, rotation: int, pages: list[int] | None = None) -> list[str]:
    """Rotate pages by *rotation* degrees (90/180/270), additive to existing rotation.

    ``pages=None`` rotates the whole document.
    """
    if int(rotation) not in (90, 180, 270):
        raise ValueError("rotation must be one of 90, 180, 270.")
    with fitz.open(path) as doc:
        indices = _validate_pages(pages, doc.page_count)
        for idx in indices:
            page = doc[idx]
            page.set_rotation((page.rotation + int(rotation)) % 360)
        dest = _out(job_id, path, "_rotated")
        doc.save(dest, deflate=True, garbage=4)
    return [dest]


def rearrange(path: str, job_id: str, order: list[int]) -> list[str]:
    """Reorder pages. *order* is a full 1-based permutation of every page."""
    with fitz.open(path) as doc:
        total = doc.page_count
        if not order or len(order) != total:
            raise ValueError(
                f"order must list all {total} pages exactly once; got {len(order or [])}."
            )
        indices = _validate_pages(order, total)
        if sorted(indices) != list(range(total)):
            raise ValueError("order must be a permutation of all pages (no repeats/gaps).")
        out = _subset_doc(doc, indices)
        try:
            dest = _out(job_id, path, "_reordered")
            out.save(dest, deflate=True, garbage=4)
        finally:
            out.close()
    return [dest]


def extract(path: str, job_id: str, pages: list[int]) -> list[str]:
    """Create a new PDF containing only *pages* (1-based). Original untouched."""
    with fitz.open(path) as doc:
        indices = _validate_pages(pages, doc.page_count)
        if not indices:
            raise ValueError("No pages selected to extract.")
        out = _subset_doc(doc, indices)
        try:
            dest = _out(job_id, path, "_extracted")
            out.save(dest, deflate=True, garbage=4)
        finally:
            out.close()
    return [dest]


def delete_pages(path: str, job_id: str, pages: list[int]) -> list[str]:
    """Create a new PDF with *pages* (1-based) removed."""
    with fitz.open(path) as doc:
        total = doc.page_count
        remove = set(_validate_pages(pages, total))
        keep = [i for i in range(total) if i not in remove]
        if not keep:
            raise ValueError("Refusing to delete every page.")
        out = _subset_doc(doc, keep)
        try:
            dest = _out(job_id, path, "_trimmed")
            out.save(dest, deflate=True, garbage=4)
        finally:
            out.close()
    return [dest]


def crop(path: str, job_id: str, box: dict, pages: list[int] | None = None) -> list[str]:
    """Crop pages to a normalized *box* ``{x0,y0,x1,y1}`` (0..1 of each page rect)."""
    try:
        x0, y0, x1, y1 = (float(box["x0"]), float(box["y0"]),
                          float(box["x1"]), float(box["y1"]))
    except (KeyError, TypeError, ValueError):
        raise ValueError("box must contain numeric x0, y0, x1, y1.")
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("box coords must satisfy 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1.")
    with fitz.open(path) as doc:
        indices = _validate_pages(pages, doc.page_count)
        for idx in indices:
            page = doc[idx]
            r = page.rect
            new_rect = fitz.Rect(
                r.x0 + x0 * r.width, r.y0 + y0 * r.height,
                r.x0 + x1 * r.width, r.y0 + y1 * r.height,
            )
            page.set_cropbox(new_rect)
        dest = _out(job_id, path, "_cropped")
        doc.save(dest, deflate=True, garbage=4)
    return [dest]


# --------------------------------------------------------------------------- #
# Compression
# --------------------------------------------------------------------------- #
def _recompress_image(doc: "fitz.Document", page: "fitz.Page", xref: int,
                      dpi: int, quality: int) -> None:
    """Downsample + re-encode a single image xref in place. Best-effort.

    Targets *dpi* relative to the image's on-page display size and JPEG *quality*.
    Images with transparency are kept as optimized PNG; everything else becomes
    JPEG. Silently skipped (by the caller) if anything goes wrong.
    """
    if not _PIL_OK:
        return
    pix = fitz.Pixmap(doc, xref)
    try:
        if pix.colorspace and pix.colorspace.name not in ("DeviceRGB", "DeviceGray"):
            pix = fitz.Pixmap(fitz.csRGB, pix)  # normalize CMYK/other -> RGB
        im = Image.open(io.BytesIO(pix.tobytes("png")))
    finally:
        pix = None  # release C-level pixmap promptly

    # Downsample to the resolution actually needed at the display size.
    rects = page.get_image_rects(xref)
    if rects:
        disp = rects[0]
        target_w = max(1, int((disp.width / 72.0) * dpi))
        target_h = max(1, int((disp.height / 72.0) * dpi))
        if im.width > target_w or im.height > target_h:
            im.thumbnail((target_w, target_h), Image.LANCZOS)

    buf = io.BytesIO()
    has_alpha = im.mode in ("RGBA", "LA", "P")
    if has_alpha:
        im.save(buf, format="PNG", optimize=True)
    else:
        im.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    new_bytes = buf.getvalue()
    page.replace_image(xref, stream=new_bytes)


def compress(path: str, job_id: str, level: str = "medium") -> list[str]:
    """Shrink a PDF by re-encoding images and deflating/garbage-collecting.

    *level* selects a preset from ``Config.COMPRESSION_LEVELS`` (low/medium/high).
    If per-image re-encoding fails (or Pillow is missing) the function still
    produces a deflated, garbage-collected PDF.
    """
    preset = Config.COMPRESSION_LEVELS.get(level)
    if preset is None:
        raise ValueError(
            f"Unknown compression level '{level}'. "
            f"Choose from: {', '.join(Config.COMPRESSION_LEVELS)}."
        )
    dpi, quality = preset["dpi"], preset["quality"]

    with fitz.open(path) as doc:
        if _PIL_OK:
            for pno in range(doc.page_count):
                page = doc[pno]
                seen: set[int] = set()
                for img in page.get_images(full=True):
                    xref = img[0]
                    if xref in seen:
                        continue
                    seen.add(xref)
                    try:
                        _recompress_image(doc, page, xref, dpi, quality)
                    except Exception:
                        continue  # leave this image untouched, keep going
        dest = _out(job_id, path, "_compressed")
        doc.save(dest, deflate=True, deflate_images=True, deflate_fonts=True,
                 garbage=4, clean=True)
    return [dest]


# --------------------------------------------------------------------------- #
# Watermark / page numbers
# --------------------------------------------------------------------------- #
def _rotation_matrix(angle: float, pivot: "fitz.Point") -> "fitz.Matrix":
    """Rotation matrix about *pivot* (degrees, anti-clockwise in PDF space)."""
    return fitz.Matrix(1, 0, 0, 1, 0, 0).prerotate(angle)


def _faded_image_bytes(image_path: str, opacity: float) -> bytes | None:
    """Return PNG bytes of *image_path* with alpha scaled by *opacity*.

    ``None`` if Pillow is unavailable (caller inserts the raw image instead).
    """
    if not _PIL_OK:
        return None
    im = Image.open(image_path).convert("RGBA")
    alpha = im.split()[3].point(lambda a: int(a * max(0.0, min(opacity, 1.0))))
    im.putalpha(alpha)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _anchor_point(position: str, page_rect: "fitz.Rect", tw: float, th: float,
                  margin: float = 36.0) -> "fitz.Point":
    """Baseline insertion point for text of width *tw*/height *th* at *position*."""
    w, h = page_rect.width, page_rect.height
    cx, cy = w / 2.0, h / 2.0
    spots = {
        "center": (cx - tw / 2.0, cy + th / 2.0),
        "top-left": (margin, margin + th),
        "top-right": (w - margin - tw, margin + th),
        "bottom-left": (margin, h - margin),
        "bottom-right": (w - margin - tw, h - margin),
        "top-center": (cx - tw / 2.0, margin + th),
        "bottom-center": (cx - tw / 2.0, h - margin),
    }
    x, y = spots.get(position, spots["center"])
    return fitz.Point(x, y)


def add_watermark(path: str, job_id: str, *, wm_type: str, text: str | None = None,
                  image_path: str | None = None, opacity: float = 0.3,
                  rotation: float = 0, font: str = "helv", font_size: int = 48,
                  color: str = "#888888", position: str = "center") -> list[str]:
    """Stamp a text or image watermark on every page.

    Parameters
    ----------
    wm_type   : ``"text"`` or ``"image"``.
    position  : center / top-left / top-right / bottom-left / bottom-right / tile.
    opacity   : 0..1 fill opacity. ``rotation`` in degrees.
    color     : hex string for text watermarks.
    """
    opacity = max(0.0, min(float(opacity), 1.0))
    rgb = _hex_to_rgb(color)

    if wm_type == "text":
        if not text:
            raise ValueError("Text watermark requires non-empty 'text'.")
    elif wm_type == "image":
        if not image_path or not os.path.exists(image_path):
            raise ValueError("Image watermark requires a valid 'image_path'.")
    else:
        raise ValueError("wm_type must be 'text' or 'image'.")

    faded = _faded_image_bytes(image_path, opacity) if wm_type == "image" else None

    with fitz.open(path) as doc:
        for page in doc:
            rect = page.rect
            if wm_type == "text":
                _stamp_text_watermark(page, rect, text, font, font_size, rgb,
                                      opacity, rotation, position)
            else:
                _stamp_image_watermark(page, rect, image_path, faded, opacity,
                                       rotation, position)
        dest = _out(job_id, path, "_watermarked")
        doc.save(dest, deflate=True, garbage=4)
    return [dest]


def _stamp_text_watermark(page, rect, text, font, font_size, rgb, opacity,
                          rotation, position) -> None:
    """Draw a single text watermark (or a tiled grid) on one page."""
    fontname = _font_name(font)
    tw = fitz.get_text_length(text, fontname=fontname, fontsize=font_size)
    th = font_size

    if position == "tile":
        step_x = max(tw + 80, 160)
        step_y = max(th * 4, 120)
        y = step_y / 2
        while y < rect.height + step_y:
            x = step_x / 2
            while x < rect.width + step_x:
                pivot = fitz.Point(x, y)
                page.insert_text(
                    fitz.Point(x - tw / 2, y), text, fontname=fontname,
                    fontsize=font_size, color=rgb, fill_opacity=opacity,
                    morph=(pivot, _rotation_matrix(rotation or 45, pivot)),
                )
                x += step_x
            y += step_y
        return

    point = _anchor_point(position, rect, tw, th)
    pivot = fitz.Point(point.x + tw / 2, point.y - th / 2)
    morph = (pivot, _rotation_matrix(rotation, pivot)) if rotation else None
    page.insert_text(point, text, fontname=fontname, fontsize=font_size,
                     color=rgb, fill_opacity=opacity, morph=morph)


def _stamp_image_watermark(page, rect, image_path, faded, opacity, rotation,
                           position) -> None:
    """Place an image watermark (sized to ~40% of the page) on one page."""
    iw = rect.width * 0.4
    ih = rect.height * 0.4
    if position == "tile":
        # Single centered placement is used for tiled image requests too,
        # to avoid pathological page bloat from many embedded copies.
        position = "center"
    cx, cy = rect.width / 2, rect.height / 2
    anchors = {
        "center": (cx - iw / 2, cy - ih / 2),
        "top-left": (24, 24),
        "top-right": (rect.width - iw - 24, 24),
        "bottom-left": (24, rect.height - ih - 24),
        "bottom-right": (rect.width - iw - 24, rect.height - ih - 24),
    }
    x, y = anchors.get(position, anchors["center"])
    target = fitz.Rect(x, y, x + iw, y + ih)
    kwargs = {"overlay": True, "keep_proportion": True}
    if rotation:
        kwargs["rotate"] = int(rotation) % 360 // 90 * 90  # nearest 90 for image
    if faded is not None:
        page.insert_image(target, stream=faded, **kwargs)
    else:
        page.insert_image(target, filename=image_path, **kwargs)


def add_page_numbers(path: str, job_id: str, *, position: str = "bottom-center",
                     start: int = 1, font: str = "helv", font_size: int = 12,
                     color: str = "#000000", prefix: str = "", suffix: str = "") -> list[str]:
    """Stamp page numbers on every page.

    Label text = ``f"{prefix}{number}{suffix}"`` where *number* starts at *start*.
    *position* one of bottom-/top- center/left/right.
    """
    rgb = _hex_to_rgb(color)
    fontname = _font_name(font)
    margin = 28.0
    with fitz.open(path) as doc:
        for i, page in enumerate(doc):
            number = int(start) + i
            label = f"{prefix}{number}{suffix}"
            rect = page.rect
            tw = fitz.get_text_length(label, fontname=fontname, fontsize=font_size)
            vert, _, horiz = position.partition("-")
            y = margin + font_size if vert == "top" else rect.height - margin
            if horiz == "left":
                x = margin
            elif horiz == "right":
                x = rect.width - margin - tw
            else:  # center
                x = (rect.width - tw) / 2.0
            page.insert_text(fitz.Point(x, y), label, fontname=fontname,
                             fontsize=font_size, color=rgb)
        dest = _out(job_id, path, "_numbered")
        doc.save(dest, deflate=True, garbage=4)
    return [dest]


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #
def redact(path: str, job_id: str, boxes: list[dict], pages: list[int] | None = None) -> list[str]:
    """Permanently remove rectangular regions and paint them black.

    Each *box* is normalized ``{"page"(1-based optional),"x0","y0","x1","y1"}``.
    A box without an explicit ``page`` is applied to every page in *pages*
    (or all pages if *pages* is ``None``). Uses ``add_redact_annot`` +
    ``apply_redactions`` so the underlying content is destroyed, not just hidden.
    """
    if not boxes:
        raise ValueError("No redaction boxes provided.")
    with fitz.open(path) as doc:
        total = doc.page_count
        default_pages = _validate_pages(pages, total)
        touched: set[int] = set()
        for box in boxes:
            try:
                x0, y0 = float(box["x0"]), float(box["y0"])
                x1, y1 = float(box["x1"]), float(box["y1"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("Each box needs numeric x0, y0, x1, y1.")
            if box.get("page") is not None:
                pi = int(box["page"])
                if pi < 1 or pi > total:
                    raise ValueError(f"Redaction page {pi} out of range.")
                target_idxs = [pi - 1]
            else:
                target_idxs = default_pages
            for idx in target_idxs:
                page = doc[idx]
                r = page.rect
                rect = fitz.Rect(
                    r.x0 + min(x0, x1) * r.width, r.y0 + min(y0, y1) * r.height,
                    r.x0 + max(x0, x1) * r.width, r.y0 + max(y0, y1) * r.height,
                )
                page.add_redact_annot(rect, fill=(0, 0, 0))
                touched.add(idx)
        for idx in touched:
            doc[idx].apply_redactions()
        dest = _out(job_id, path, "_redacted")
        doc.save(dest, deflate=True, garbage=4)
    return [dest]


# --------------------------------------------------------------------------- #
# Free-form editing (add text / erase region / add image)
# --------------------------------------------------------------------------- #
def _norm_rect(page: "fitz.Page", x0, y0, x1, y1) -> "fitz.Rect":
    """Build an absolute rect from normalized 0..1 coords on *page*."""
    r = page.rect
    return fitz.Rect(
        r.x0 + min(x0, x1) * r.width, r.y0 + min(y0, y1) * r.height,
        r.x0 + max(x0, x1) * r.width, r.y0 + max(y0, y1) * r.height,
    )


def _apply_edits(doc: "fitz.Document", edits: list[dict]) -> None:
    """Apply a list of edit operations to an open document in place.

    Supported ``type`` values: ``add_text``, ``delete_region``, ``add_image``.
    Region deletions are collected and flushed with one ``apply_redactions``
    pass per page so erasing and (re)adding text on the same page compose
    correctly.
    """
    total = doc.page_count
    erase_pages: set[int] = set()

    # Pass 1: erase regions (so later text isn't wiped out by redaction).
    for edit in edits:
        if edit.get("type") != "delete_region":
            continue
        idx = int(edit["page"]) - 1
        if idx < 0 or idx >= total:
            raise ValueError(f"edit page {edit.get('page')} out of range.")
        page = doc[idx]
        rect = _norm_rect(page, float(edit["x0"]), float(edit["y0"]),
                          float(edit["x1"]), float(edit["y1"]))
        page.add_redact_annot(rect, fill=(1, 1, 1))  # white -> visually erase
        erase_pages.add(idx)
    for idx in erase_pages:
        doc[idx].apply_redactions()

    # Pass 2: additive content.
    for edit in edits:
        etype = edit.get("type")
        if etype == "delete_region":
            continue
        idx = int(edit["page"]) - 1
        if idx < 0 or idx >= total:
            raise ValueError(f"edit page {edit.get('page')} out of range.")
        page = doc[idx]
        r = page.rect

        if etype == "add_text":
            x = float(edit.get("x", 0)) * r.width + r.x0
            y = float(edit.get("y", 0)) * r.height + r.y0
            fontname = _font_name(edit.get("font"), edit.get("bold", False),
                                  edit.get("italic", False))
            page.insert_text(
                fitz.Point(x, y), str(edit.get("text", "")),
                fontname=fontname, fontsize=float(edit.get("font_size", 12)),
                color=_hex_to_rgb(edit.get("color", "#000000")),
            )
        elif etype == "add_image":
            img = edit.get("image_path")
            if not img or not os.path.exists(img):
                raise ValueError("add_image edit requires a valid image_path.")
            rect = _norm_rect(page, float(edit["x0"]), float(edit["y0"]),
                              float(edit["x1"]), float(edit["y1"]))
            page.insert_image(rect, filename=img, overlay=True, keep_proportion=True)
        else:
            raise ValueError(f"Unsupported edit type: {etype!r}.")


def edit_text(path: str, job_id: str, edits: list[dict]) -> list[str]:
    """Apply add_text / delete_region / add_image edits and save a new PDF.

    Coordinates in each edit are normalized 0..1. "Editing" existing text from
    the UI is expressed as a ``delete_region`` plus an ``add_text`` for the same
    spot (the frontend sends both).
    """
    if not edits:
        raise ValueError("No edits provided.")
    with fitz.open(path) as doc:
        _apply_edits(doc, edits)
        dest = _out(job_id, path, "_edited")
        doc.save(dest, deflate=True, garbage=4)
    return [dest]


# --------------------------------------------------------------------------- #
# Fill & sign
# --------------------------------------------------------------------------- #
def fill_sign(path: str, job_id: str, fields: list[dict],
              image_map: dict | None = None) -> list[str]:
    """Flatten form-fill / signature *fields* onto a PDF.

    Each field has a ``type``:
      * ``text`` / ``date`` / ``signature_text`` -> rendered as text at (x, y).
      * ``signature_image`` -> image placed in box (x0,y0,x1,y1); the image is
        resolved from *image_map* (e.g. ``{"image_0": "/abs/path.png"}``) via the
        field's ``image`` / ``image_0`` key, or a direct ``image_path``.
      * ``checkbox`` -> draws an "X" at (x, y) when ``checked`` is truthy.

    Internally everything is translated to :func:`_apply_edits` primitives.
    """
    if not fields:
        raise ValueError("No fields provided.")
    image_map = image_map or {}
    edits: list[dict] = []
    for f in fields:
        ftype = f.get("type")
        page = f.get("page")
        if page is None:
            raise ValueError("Each fill/sign field needs a 'page'.")
        if ftype in ("text", "date", "signature_text"):
            edits.append({
                "type": "add_text", "page": page,
                "x": f.get("x", 0), "y": f.get("y", 0),
                "text": f.get("text", ""),
                "font": f.get("font", "helv"),
                "font_size": f.get("font_size", 14),
                "color": f.get("color", "#000000"),
                "bold": f.get("bold", False), "italic": f.get("italic", False),
            })
        elif ftype == "checkbox":
            if f.get("checked"):
                edits.append({
                    "type": "add_text", "page": page,
                    "x": f.get("x", 0), "y": f.get("y", 0), "text": "X",
                    "font": "hebo", "font_size": f.get("font_size", 14),
                    "color": f.get("color", "#000000"),
                })
        elif ftype == "signature_image":
            img_path = (f.get("image_path")
                        or image_map.get(f.get("image"))
                        or image_map.get(f.get("image_0")))
            if not img_path:
                # fall back to the first available uploaded signature image
                img_path = next(iter(image_map.values()), None)
            if not img_path:
                raise ValueError("signature_image field has no resolvable image.")
            edits.append({
                "type": "add_image", "page": page,
                "x0": f.get("x0", 0), "y0": f.get("y0", 0),
                "x1": f.get("x1", 0), "y1": f.get("y1", 0),
                "image_path": img_path,
            })
        else:
            raise ValueError(f"Unsupported fill/sign field type: {ftype!r}.")

    with fitz.open(path) as doc:
        _apply_edits(doc, edits)
        dest = _out(job_id, path, "_signed")
        doc.save(dest, deflate=True, garbage=4)
    return [dest]
