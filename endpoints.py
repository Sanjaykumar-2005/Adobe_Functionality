"""
endpoints.py — the single, central registry of every HTTP endpoint.

WHY THIS FILE EXISTS
--------------------
All routes live here in one place so you can see, edit, or ADD an endpoint
without hunting through multiple files.

TO ADD A NEW ENDPOINT
---------------------
  1. Write a small handler function in the matching section below. It should:
       - parse the request (request.files / request.form),
       - call into a service in `services/`,
       - return ok(...) / fail(...) (the standard JSON envelope).
  2. Add ONE row to the ROUTES table at the bottom:
       (endpoint_name, methods, url_rule, handler_function)
  That's it — register(app) wires every row onto the Flask app in a loop.

CONVENTIONS (kept identical across handlers)
  - Each request gets a fresh job_id; uploads are validated + saved under it.
  - User-error -> fail(msg, 400) (FileValidationError / ValueError).
    Server-error -> logged + fail(msg, 500).
  - HTTP only. The real PDF logic is in services/; shared helpers in utils/.
"""
from __future__ import annotations

import json
import os
import re

from flask import abort, current_app, render_template, request, send_from_directory

from config import Config
from utils.file_utils import (
    FileValidationError,
    ext_of,
    file_descriptor,
    job_output_dir,
    make_zip,
    new_job_id,
    output_path,
    save_upload,
    save_uploads,
    validate_upload,
    with_suffix,
)
from utils.responses import fail, ok
from services import (
    batch_service,
    conversion_service,
    llm_service,
    ocr_api_service,
    ocr_engines,
    ocr_service,
    pdf_service,
    pdf_to_word_service,
    security_service,
)


# =========================================================================== #
# Shared helpers
# =========================================================================== #
def parse_page_list(spec: str) -> list[int]:
    """Expand a UI page spec ("1-3,5") into a sorted, de-duplicated 1-based list."""
    if not spec:
        return []
    pages: set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a.strip()), int(b.strip())
            if start > end:
                start, end = end, start
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    return sorted(pages)


def _single_pdf(job_id: str) -> str:
    """Validate + persist the single `file` upload (a PDF); return its abs path."""
    storage = request.files.get("file")
    if storage is None:
        raise FileValidationError("No file provided.")
    validate_upload(storage, Config.ALLOWED_PDF)
    return save_upload(storage, job_id)


def _zip_extra(job_id: str, outputs: list[str]) -> dict:
    """Add a `zip` descriptor to the envelope when >1 output was produced."""
    if len(outputs) > 1:
        return {"zip": file_descriptor(job_id, make_zip(job_id, outputs))}
    return {}


def _as_bool(value, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


_SIZE_UNITS = {"b": 1, "kb": 1024, "k": 1024, "mb": 1024 ** 2, "m": 1024 ** 2}


def _parse_size(raw, unit=None) -> int | None:
    """Parse a file size -> bytes. ``None`` when *raw* is absent/blank.

    Accepts a bare number plus a separate *unit* ("2", "MB") or a combined
    string ("2 MB", "500kb"). A unit inside *raw* wins over the *unit* arg;
    MB is assumed when neither says.
    """
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip().lower().replace(" ", "")
    match = re.fullmatch(r"(\d*\.?\d+)([a-z]*)", text)
    if not match:
        raise ValueError("Target size must be a number, e.g. 2 or 500.")
    value = float(match.group(1))
    suffix = match.group(2) or (unit or "mb").strip().lower()
    if suffix not in _SIZE_UNITS:
        raise ValueError("Target size unit must be KB or MB.")
    if value <= 0:
        raise ValueError("Target size must be greater than zero.")
    return int(value * _SIZE_UNITS[suffix])


def _human_size(num: int | None) -> str:
    """Bytes -> a short human string ('1.4 MB'). Mirrors the frontend's format."""
    if not num:
        return "0 B"
    size = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _collect_image_map(job_id: str) -> dict:
    """Persist uploads named image_0, image_1, ... into {index: abs_path}."""
    image_map: dict[int, str] = {}
    for key in request.files:
        if not key.startswith("image_"):
            continue
        try:
            idx = int(key.split("_", 1)[1])
        except (ValueError, IndexError):
            continue
        storage = request.files[key]
        if storage is None or not storage.filename:
            continue
        validate_upload(storage, Config.ALLOWED_IMAGE)
        image_map[idx] = save_upload(storage, job_id)
    return image_map


# =========================================================================== #
# Infrastructure (UI page, file download, health)
# =========================================================================== #
def index():
    """Serve the single-page UI."""
    return render_template("index.html")


def download(job_id: str, filename: str):
    """Serve a produced file. job_id is a hex token; reject anything else."""
    if not job_id.isalnum():
        abort(404)
    directory = job_output_dir(job_id)
    if not os.path.isfile(os.path.join(directory, filename)):
        abort(404)
    return send_from_directory(directory, filename, as_attachment=True)


def health():
    return {"status": "ok"}


# =========================================================================== #
# Common: preview thumbnails / page info
# =========================================================================== #
def preview():
    """Render page thumbnails for a PDF (basic info for images)."""
    try:
        storage = request.files.get("file")
        if storage is None:
            raise FileValidationError("No file provided.")
        job_id = new_job_id()
        validate_upload(storage, Config.ALLOWED_PDF | Config.ALLOWED_IMAGE)
        path = save_upload(storage, job_id)
        if ext_of(storage.filename) in Config.ALLOWED_PDF:
            dpi = int(request.form.get("dpi", 72))
            pages_spec = request.form.get("pages")
            pages = parse_page_list(pages_spec) if pages_spec else None
            n = pdf_service.page_count(path)
            thumbnails = pdf_service.render_thumbnails(path, job_id, pages=pages, dpi=dpi)
            return ok(job_id, files=[], pages=n, thumbnails=thumbnails)
        return ok(job_id, files=[], pages=1, thumbnails=[])
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("preview failed")
        return fail(str(e), 500)


def info():
    """Quick page count for an uploaded PDF."""
    try:
        storage = request.files.get("file")
        if storage is None:
            raise FileValidationError("No file provided.")
        job_id = new_job_id()
        validate_upload(storage, Config.ALLOWED_PDF)
        path = save_upload(storage, job_id)
        return ok(job_id, pages=pdf_service.page_count(path))
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("info failed")
        return fail(str(e), 500)


# =========================================================================== #
# Convert: Word<->PDF, Image->PDF
# =========================================================================== #
def convert_word_to_pdf():
    """Convert one or more Word documents to PDF."""
    try:
        job_id = new_job_id()
        paths = save_uploads(request.files.getlist("files"), Config.ALLOWED_WORD, job_id)
        engines: list[str] = []
        outputs = conversion_service.word_to_pdf(paths, job_id, engines_out=engines)
        files = [file_descriptor(job_id, p) for p in outputs]
        extra = _zip_extra(job_id, outputs)
        if "reportlab" in engines:
            extra["warning"] = (
                "No LibreOffice or Microsoft Word was found, so a text-only fallback "
                "was used. Backgrounds, images, colors, and complex layout are NOT "
                "preserved. Install LibreOffice for full-fidelity conversion."
            )
        return ok(job_id, files, **extra)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("word_to_pdf failed")
        return fail(str(e), 500)


def convert_image_to_pdf():
    """Convert images to a single (or per-image) PDF."""
    try:
        job_id = new_job_id()
        paths = save_uploads(request.files.getlist("files"), Config.ALLOWED_IMAGE, job_id)
        page_size = request.form.get("page_size", "A4")
        merge = _as_bool(request.form.get("merge"), True)
        outputs = conversion_service.image_to_pdf(paths, job_id, page_size=page_size, merge=merge)
        files = [file_descriptor(job_id, p) for p in outputs]
        return ok(job_id, files, **_zip_extra(job_id, outputs))
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("image_to_pdf failed")
        return fail(str(e), 500)


def convert_pdf_to_word():
    """Convert one or more TEXT-BASED PDFs to editable Word (.docx), no OCR.

    The engine is chosen automatically per file by ``recommend_mode``: table-heavy
    or white-text-on-dark documents go to the native ``pdf2word`` engine (the office
    import mangles tables and hides that text); everything else takes the faithful
    office path, best first:
      1. LibreOffice's Writer PDF import — the PRIMARY engine. Runs on the server
         (Linux/Docker) with no MS Word: preserves backgrounds, page borders, shaded
         headers, images and complex layout. Its text sits in positioned frames, so
         the result is faithful to look at but awkward to re-edit.
      2. Microsoft Word's own importer — only when LibreOffice is not installed
         (Windows desktop fallback).
      3. Native rule-based ``pdf2word`` engine — last resort with no office suite at
         all. Genuinely editable text, but backgrounds / borders are approximated.

    Scanned / image-only PDFs are rejected up front (no OCR in this feature).
    """
    try:
        job_id = new_job_id()
        paths = save_uploads(request.files.getlist("files"), Config.ALLOWED_PDF, job_id)

        # Reject scanned / image-only PDFs before any engine runs.
        for src in paths:
            ok_txt, reason = pdf_to_word_service.is_text_based(src)
            if not ok_txt:
                return fail(reason, 400)

        outputs, engines, used_native = [], [], False
        for src in paths:
            dest = output_path(job_id, with_suffix(src, "", ".docx"))

            # Let the analyzer pick per file (table-heavy / white-on-dark ->
            # native engine; otherwise the faithful office path).
            mode = pdf_to_word_service.recommend_mode(src)

            # Native engine: every text span read into real paragraphs — nothing
            # hidden in overlapping frames. Continuous flow (no forced break per PDF
            # page) so no blank/half-empty pages are manufactured.
            if mode == "editable":
                result = pdf_to_word_service.convert_pdf_to_word(
                    src, dest, page_breaks=False)
                if not result.get("success"):
                    return fail(result.get("error") or "Conversion failed.", 400)
                outputs.append(result["output_path"])
                engines.append("native")
                continue

            # Faithful office path --------------------------------------------
            # 1) LibreOffice (server default).
            produced = conversion_service._pdf_to_word_libreoffice(src, dest)
            engine = "libreoffice"

            # 2) MS Word's importer, only if LibreOffice is absent.
            if not produced:
                produced = conversion_service._pdf_to_word_word_com(src, dest)
                engine = "word"

            if produced:
                outputs.append(produced)
                engines.append(engine)
                continue

            # 3) Portable native rule-based engine.
            result = pdf_to_word_service.convert_pdf_to_word(
                src, dest, page_breaks=False)
            if not result.get("success"):
                return fail(result.get("error") or "Conversion failed.", 400)
            outputs.append(result["output_path"])
            engines.append("native")
            used_native = True

        files = [file_descriptor(job_id, p) for p in outputs]
        extra = _zip_extra(job_id, outputs)
        extra["engines"] = engines
        if used_native:
            extra["warning"] = (
                "No office suite was available, so a portable converter was used. "
                "The text, tables, fonts and images are preserved, but backgrounds, "
                "page borders and complex visual layout may not be fully reproduced. "
                "Install LibreOffice on the server for an exact-format conversion."
            )
        return ok(job_id, files, **extra)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("pdf_to_word failed")
        return fail(str(e), 500)


# =========================================================================== #
# Organize: merge / split / rotate / rearrange / extract / delete / crop / compress
# =========================================================================== #
def organize_merge():
    """Merge several PDFs, optionally reordering by an `order` index list."""
    try:
        job_id = new_job_id()
        paths = save_uploads(request.files.getlist("files"), Config.ALLOWED_PDF, job_id)
        order_raw = request.form.get("order")
        if order_raw:
            order = json.loads(order_raw)
            try:
                paths = [paths[i] for i in order]
            except (IndexError, TypeError):
                raise ValueError("`order` contains an out-of-range file index.")
        outputs = pdf_service.merge(paths, job_id)
        files = [file_descriptor(job_id, p) for p in outputs]
        return ok(job_id, files)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("merge failed")
        return fail(str(e), 500)


def organize_split():
    """Split a PDF by pages / ranges / every-N / custom selection."""
    try:
        job_id = new_job_id()
        path = _single_pdf(job_id)
        mode = request.form.get("mode", "pages")
        ranges = request.form.get("ranges")
        n_raw = request.form.get("n")
        n = int(n_raw) if n_raw else None
        pages_spec = request.form.get("pages")
        pages = parse_page_list(pages_spec) if pages_spec else None
        outputs = pdf_service.split(path, job_id, mode, ranges=ranges, n=n, pages=pages)
        files = [file_descriptor(job_id, p) for p in outputs]
        return ok(job_id, files, **_zip_extra(job_id, outputs))
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("split failed")
        return fail(str(e), 500)


def organize_rotate():
    """Rotate the whole document or specific pages."""
    try:
        job_id = new_job_id()
        path = _single_pdf(job_id)
        rotation = int(request.form.get("rotation", 90))
        pages_spec = request.form.get("pages")
        pages = parse_page_list(pages_spec) if pages_spec else None
        outputs = pdf_service.rotate(path, job_id, rotation, pages=pages)
        files = [file_descriptor(job_id, p) for p in outputs]
        return ok(job_id, files)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("rotate failed")
        return fail(str(e), 500)


def organize_rearrange():
    """Reorder pages by a full 1-based permutation list."""
    try:
        job_id = new_job_id()
        path = _single_pdf(job_id)
        order_raw = request.form.get("order")
        if not order_raw:
            raise ValueError("`order` is required.")
        order = json.loads(order_raw)
        outputs = pdf_service.rearrange(path, job_id, order)
        files = [file_descriptor(job_id, p) for p in outputs]
        return ok(job_id, files)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("rearrange failed")
        return fail(str(e), 500)


def organize_extract():
    """Build a new PDF from only the selected pages."""
    try:
        job_id = new_job_id()
        path = _single_pdf(job_id)
        pages = parse_page_list(request.form.get("pages"))
        if not pages:
            raise ValueError("`pages` is required.")
        outputs = pdf_service.extract(path, job_id, pages)
        files = [file_descriptor(job_id, p) for p in outputs]
        return ok(job_id, files)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("extract failed")
        return fail(str(e), 500)


def organize_delete():
    """Build a new PDF without the selected pages."""
    try:
        job_id = new_job_id()
        path = _single_pdf(job_id)
        pages = parse_page_list(request.form.get("pages"))
        if not pages:
            raise ValueError("`pages` is required.")
        outputs = pdf_service.delete_pages(path, job_id, pages)
        files = [file_descriptor(job_id, p) for p in outputs]
        return ok(job_id, files)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("delete failed")
        return fail(str(e), 500)


def organize_crop():
    """Crop pages to normalized boxes (JSON body or form).

    `boxes` = per-page `[{page,x0,y0,x1,y1}]` — each page cropped to its own box,
    pages not listed left alone. This is what the UI sends. `box` (+ optional
    `pages`) = the original "one box on every chosen page" form, still accepted.
    """
    try:
        job_id = new_job_id()
        path = _single_pdf(job_id)
        data = request.get_json(silent=True)
        if data:
            boxes = data.get("boxes")
            box = data.get("box")
            pages = data.get("pages")
            if isinstance(pages, str):
                pages = parse_page_list(pages)
        else:
            boxes_raw = request.form.get("boxes")
            boxes = json.loads(boxes_raw) if boxes_raw else None
            box_raw = request.form.get("box")
            box = json.loads(box_raw) if box_raw else None
            pages_spec = request.form.get("pages")
            pages = parse_page_list(pages_spec) if pages_spec else None
        if not boxes and not box:
            raise ValueError("`boxes` (one per page) or `box` is required.")
        outputs = pdf_service.crop(path, job_id, box, pages=pages, boxes=boxes)
        files = [file_descriptor(job_id, p) for p in outputs]
        return ok(job_id, files)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("crop failed")
        return fail(str(e), 500)


def organize_compress():
    """Compress a PDF — at a quality `level`, or down to a `target_size`.

    `target_size` (+ optional `target_unit`, default MB) switches to target mode
    and ignores `level`. Targets are best-effort: the envelope carries a
    `compression` report and a `warning` when the target could not be reached.
    """
    try:
        job_id = new_job_id()
        path = _single_pdf(job_id)
        level = request.form.get("level", "medium")
        target_bytes = _parse_size(request.form.get("target_size"),
                                   request.form.get("target_unit"))
        report: dict | None = {} if target_bytes else None
        outputs = pdf_service.compress(path, job_id, level,
                                       target_bytes=target_bytes, report=report)
        files = [file_descriptor(job_id, p) for p in outputs]
        extra: dict = {}
        if report:
            extra["compression"] = report
            if not report["met"]:
                extra["warning"] = (
                    f"Could not reach {_human_size(report['target_bytes'])}. "
                    f"Squeezed {_human_size(report['original_bytes'])} down to "
                    f"{_human_size(report['achieved_bytes'])}, which is as small as "
                    "this PDF gets — only images can be re-encoded, so text, fonts "
                    "and page structure set a floor. Try a larger target."
                )
        return ok(job_id, files, **extra)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("compress failed")
        return fail(str(e), 500)


# =========================================================================== #
# Edit: text / watermark / page-numbers / redact / fill-sign
# =========================================================================== #
def edit_text():
    """Apply a list of text/region/image edits to a PDF."""
    try:
        job_id = new_job_id()
        path = _single_pdf(job_id)
        edits_raw = request.form.get("edits")
        if not edits_raw:
            raise ValueError("`edits` is required.")
        edits = json.loads(edits_raw)
        image_map = _collect_image_map(job_id)
        seq = 0
        for edit in edits:
            if isinstance(edit, dict) and edit.get("type") == "add_image":
                idx = edit.get("image_index", seq)
                if idx in image_map:
                    edit["image_path"] = image_map[idx]
                seq += 1
        outputs = pdf_service.edit_text(path, job_id, edits)
        files = [file_descriptor(job_id, p) for p in outputs]
        return ok(job_id, files)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("edit_text failed")
        return fail(str(e), 500)


def edit_watermark():
    """Overlay a text or image watermark on every page."""
    try:
        job_id = new_job_id()
        path = _single_pdf(job_id)
        image_path = None
        img = request.files.get("image")
        if img is not None and img.filename:
            validate_upload(img, Config.ALLOWED_IMAGE)
            image_path = save_upload(img, job_id)
        outputs = pdf_service.add_watermark(
            path, job_id,
            wm_type=request.form.get("wm_type", "text"),
            text=request.form.get("text"),
            image_path=image_path,
            opacity=float(request.form.get("opacity", 0.3)),
            rotation=float(request.form.get("rotation", 0)),
            font=request.form.get("font", "helv"),
            font_size=int(request.form.get("font_size", 48)),
            color=request.form.get("color", "#888888"),
            position=request.form.get("position", "center"),
        )
        files = [file_descriptor(job_id, p) for p in outputs]
        return ok(job_id, files)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("watermark failed")
        return fail(str(e), 500)


def edit_page_numbers():
    """Stamp page numbers onto every page."""
    try:
        job_id = new_job_id()
        path = _single_pdf(job_id)
        outputs = pdf_service.add_page_numbers(
            path, job_id,
            position=request.form.get("position", "bottom-center"),
            start=int(request.form.get("start", 1)),
            font=request.form.get("font", "helv"),
            font_size=int(request.form.get("font_size", 12)),
            color=request.form.get("color", "#000000"),
            prefix=request.form.get("prefix", ""),
            suffix=request.form.get("suffix", ""),
        )
        files = [file_descriptor(job_id, p) for p in outputs]
        return ok(job_id, files)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("page_numbers failed")
        return fail(str(e), 500)


def edit_redact():
    """Permanently remove content inside the given normalized boxes."""
    try:
        job_id = new_job_id()
        path = _single_pdf(job_id)
        data = request.get_json(silent=True)
        if data and "boxes" in data:
            boxes = data.get("boxes")
            pages = data.get("pages")
            if isinstance(pages, str):
                pages = parse_page_list(pages)
        else:
            boxes_raw = request.form.get("boxes")
            if not boxes_raw:
                raise ValueError("`boxes` is required.")
            boxes = json.loads(boxes_raw)
            pages_spec = request.form.get("pages")
            pages = parse_page_list(pages_spec) if pages_spec else None
        outputs = pdf_service.redact(path, job_id, boxes, pages=pages)
        files = [file_descriptor(job_id, p) for p in outputs]
        return ok(job_id, files)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("redact failed")
        return fail(str(e), 500)


def edit_fill_sign():
    """Fill form-like fields and apply signatures (text or image)."""
    try:
        job_id = new_job_id()
        path = _single_pdf(job_id)
        fields_raw = request.form.get("fields")
        if not fields_raw:
            raise ValueError("`fields` is required.")
        fields = json.loads(fields_raw)
        image_map = _collect_image_map(job_id)
        outputs = pdf_service.fill_sign(path, job_id, fields, image_map)
        files = [file_descriptor(job_id, p) for p in outputs]
        return ok(job_id, files)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("fill_sign failed")
        return fail(str(e), 500)


# =========================================================================== #
# Security: password protect
# =========================================================================== #
def security_protect():
    """Encrypt a PDF with user/owner passwords and permission flags."""
    try:
        storage = request.files.get("file")
        if storage is None:
            raise FileValidationError("No file provided.")
        job_id = new_job_id()
        validate_upload(storage, Config.ALLOWED_PDF)
        path = save_upload(storage, job_id)
        permissions = {
            "print": _as_bool(request.form.get("print"), True),
            "modify": _as_bool(request.form.get("modify"), True),
            "copy": _as_bool(request.form.get("copy"), True),
            "annotate": _as_bool(request.form.get("annotate"), True),
        }
        outputs = security_service.protect(
            path, job_id,
            user_pw=request.form.get("user_pw", ""),
            owner_pw=request.form.get("owner_pw", ""),
            permissions=permissions,
        )
        files = [file_descriptor(job_id, p) for p in outputs]
        return ok(job_id, files)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("protect failed")
        return fail(str(e), 500)


# =========================================================================== #
# OCR: list engines / detect / run (engine-selectable)
# =========================================================================== #
def ocr_engines_list():
    """List the OCR engines and which are currently available on this server."""
    return ok(None, engines=ocr_engines.list_engines(), default=Config.OCR_DEFAULT_ENGINE)


def ocr_detect():
    """Report whether the uploaded PDF appears to be scanned."""
    try:
        job_id = new_job_id()
        path = _single_pdf(job_id)
        return ok(job_id, **ocr_service.is_scanned(path))
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("ocr detect failed")
        return fail(str(e), 500)


def ocr_run():
    """Run OCR with the selected engine and return a searchable PDF."""
    try:
        job_id = new_job_id()
        path = _single_pdf(job_id)
        engine = request.form.get("engine") or Config.OCR_DEFAULT_ENGINE
        lang = request.form.get("lang") or Config.OCR_DEFAULT_LANG
        force = _as_bool(request.form.get("force"), False)
        outputs = ocr_engines.run(engine, path, job_id, lang=lang, force=force)
        files = [file_descriptor(job_id, p) for p in outputs]
        return ok(job_id, files, engine=engine)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("ocr run failed")
        return fail(str(e), 500)


def ocr_providers():
    """List the selectable OCR providers (Chandra, PaddleOCR, ...) for the UI."""
    return ok(None, **ocr_api_service.list_providers())


def ocr_extract():
    """Upload a PDF -> base64 -> selected OCR provider -> {"extracted_text": ...}."""
    try:
        job_id = new_job_id()
        path = _single_pdf(job_id)
        provider = request.form.get("engine") or request.form.get("provider") or None
        text = ocr_api_service.extract_text(path, provider=provider)
        return ok(job_id, extracted_text=text, engine=provider)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("ocr extract failed")
        return fail(str(e), 500)


def ocr_summarize():
    """OCR a PDF (or accept already-extracted `text`) then summarize it with the LLM.

    Send either a `file` (PDF, runs OCR first with the selected `engine`) or a
    `text` form field (skips OCR, summarizes directly). Returns extracted_text +
    summary.
    """
    try:
        job_id = new_job_id()
        text = (request.form.get("text") or "").strip()
        if not text:
            path = _single_pdf(job_id)
            provider = request.form.get("engine") or request.form.get("provider") or None
            text = ocr_api_service.extract_text(path, provider=provider)
        summary = llm_service.summarize(text)
        return ok(job_id, extracted_text=text, summary=summary)
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("ocr summarize failed")
        return fail(str(e), 500)


# =========================================================================== #
# Batch: one operation over many files
# =========================================================================== #
_BATCH_ALLOWED_ANY = Config.ALLOWED_PDF | Config.ALLOWED_WORD | Config.ALLOWED_IMAGE


def batch_run():
    """Run a batch operation over all uploaded files."""
    try:
        job_id = new_job_id()
        paths = save_uploads(request.files.getlist("files"), _BATCH_ALLOWED_ANY, job_id)
        operation = request.form.get("operation")
        if not operation:
            raise ValueError("`operation` is required.")
        options_raw = request.form.get("options")
        options = json.loads(options_raw) if options_raw else {}
        results = batch_service.run_batch(paths, operation, options, job_id)
        return ok(
            job_id,
            files=results["files"],
            results=results["results"],
            success_count=results["success_count"],
            failure_count=results["failure_count"],
            zip=results["zip"],
        )
    except (FileValidationError, ValueError) as e:
        return fail(str(e), 400)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("batch failed")
        return fail(str(e), 500)


# =========================================================================== #
# THE ROUTE TABLE  —  (endpoint_name, methods, url_rule, handler)
# Edit a path here, or append a row to add an endpoint. Names must be unique.
# =========================================================================== #
ROUTES = [
    # --- Infrastructure ---
    ("index",                 ["GET"],  "/",                                       index),
    ("download",              ["GET"],  "/download/<job_id>/<path:filename>",      download),
    ("health",                ["GET"],  "/health",                                 health),

    # --- Common ---
    ("preview",               ["POST"], "/api/preview",                            preview),
    ("info",                  ["POST"], "/api/info",                               info),

    # --- Convert ---
    ("convert_word_to_pdf",   ["POST"], "/api/convert/word-to-pdf",                convert_word_to_pdf),
    ("convert_image_to_pdf",  ["POST"], "/api/convert/image-to-pdf",               convert_image_to_pdf),
    ("convert_pdf_to_word",   ["POST"], "/api/convert/pdf-to-word",                convert_pdf_to_word),

    # --- Organize ---
    ("organize_merge",        ["POST"], "/api/organize/merge",                     organize_merge),
    ("organize_split",        ["POST"], "/api/organize/split",                     organize_split),
    ("organize_rotate",       ["POST"], "/api/organize/rotate",                    organize_rotate),
    ("organize_rearrange",    ["POST"], "/api/organize/rearrange",                 organize_rearrange),
    ("organize_extract",      ["POST"], "/api/organize/extract",                   organize_extract),
    ("organize_delete",       ["POST"], "/api/organize/delete",                    organize_delete),
    ("organize_crop",         ["POST"], "/api/organize/crop",                      organize_crop),
    ("organize_compress",     ["POST"], "/api/organize/compress",                  organize_compress),

    # --- Edit ---
    ("edit_text",             ["POST"], "/api/edit/text",                          edit_text),
    ("edit_watermark",        ["POST"], "/api/edit/watermark",                     edit_watermark),
    ("edit_page_numbers",     ["POST"], "/api/edit/page-numbers",                  edit_page_numbers),
    ("edit_redact",           ["POST"], "/api/edit/redact",                        edit_redact),
    ("edit_fill_sign",        ["POST"], "/api/edit/fill-sign",                     edit_fill_sign),

    # --- Security ---
    ("security_protect",      ["POST"], "/api/security/protect",                   security_protect),

    # --- OCR ---
    ("ocr_engines_list",      ["GET"],  "/api/ocr/engines",                        ocr_engines_list),
    ("ocr_providers",         ["GET"],  "/api/ocr/providers",                      ocr_providers),
    ("ocr_detect",            ["POST"], "/api/ocr/detect",                         ocr_detect),
    ("ocr_run",               ["POST"], "/api/ocr/run",                            ocr_run),
    ("ocr_extract",           ["POST"], "/api/ocr/extract",                        ocr_extract),
    ("ocr_summarize",         ["POST"], "/api/ocr/summarize",                      ocr_summarize),

    # --- Batch (registered under both /api/batch and /api/batch/) ---
    ("batch_run",             ["POST"], "/api/batch/",                             batch_run),
    ("batch_run_noslash",     ["POST"], "/api/batch",                              batch_run),
]


def register(app) -> None:
    """Wire every row of ROUTES onto the Flask app."""
    for name, methods, rule, view in ROUTES:
        app.add_url_rule(rule, endpoint=name, view_func=view, methods=methods)
