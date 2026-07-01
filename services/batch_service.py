"""
Batch service: apply one operation across many files with per-file isolation.

Pure Python (no Flask). Dispatches to the other services, tolerates individual
file failures, tallies success/failure counts, packages multiple outputs into a
ZIP, and returns a structured result dict ready for the JSON envelope.
"""
from __future__ import annotations

import os

from config import Config
from utils.file_utils import ext_of, file_descriptor, make_zip

from services import (
    conversion_service,
    ocr_service,
    pdf_service,
    security_service,
)


# Which input extensions each operation accepts.
_OP_ALLOWED = {
    "word_to_pdf": Config.ALLOWED_WORD,
    "image_to_pdf": Config.ALLOWED_IMAGE,
    "pdf_to_word": Config.ALLOWED_PDF,
    "merge": Config.ALLOWED_PDF,
    "split": Config.ALLOWED_PDF,
    "compress": Config.ALLOWED_PDF,
    "ocr": Config.ALLOWED_PDF,
    "rotate": Config.ALLOWED_PDF,
    "watermark": Config.ALLOWED_PDF,
    "page_numbers": Config.ALLOWED_PDF,
    "protect": Config.ALLOWED_PDF,
}


def _process_one(operation: str, path: str, options: dict, job_id: str) -> list[str]:
    """Run a single-file *operation* and return its produced output paths."""
    opt = options or {}

    if operation == "word_to_pdf":
        return conversion_service.word_to_pdf([path], job_id)
    if operation == "image_to_pdf":
        return conversion_service.image_to_pdf(
            [path], job_id,
            page_size=opt.get("page_size", "A4"),
            merge=True,
        )
    if operation == "pdf_to_word":
        return conversion_service.pdf_to_word([path], job_id)
    if operation == "split":
        return pdf_service.split(
            path, job_id,
            mode=opt.get("mode", "pages"),
            ranges=opt.get("ranges"),
            n=opt.get("n"),
            pages=opt.get("pages"),
        )
    if operation == "compress":
        return pdf_service.compress(path, job_id, level=opt.get("level", "medium"))
    if operation == "ocr":
        return ocr_service.ocr_pdf(
            path, job_id, lang=opt.get("lang"), force=bool(opt.get("force", False))
        )
    if operation == "rotate":
        return pdf_service.rotate(
            path, job_id, rotation=int(opt.get("rotation", 90)), pages=opt.get("pages")
        )
    if operation == "watermark":
        return pdf_service.add_watermark(
            path, job_id,
            wm_type=opt.get("wm_type", "text"),
            text=opt.get("text"),
            image_path=opt.get("image_path"),
            opacity=float(opt.get("opacity", 0.3)),
            rotation=float(opt.get("rotation", 0)),
            font=opt.get("font", "helv"),
            font_size=int(opt.get("font_size", 48)),
            color=opt.get("color", "#888888"),
            position=opt.get("position", "center"),
        )
    if operation == "page_numbers":
        return pdf_service.add_page_numbers(
            path, job_id,
            position=opt.get("position", "bottom-center"),
            start=int(opt.get("start", 1)),
            font=opt.get("font", "helv"),
            font_size=int(opt.get("font_size", 12)),
            color=opt.get("color", "#000000"),
            prefix=opt.get("prefix", ""),
            suffix=opt.get("suffix", ""),
        )
    if operation == "protect":
        return security_service.protect(
            path, job_id,
            user_pw=opt.get("user_pw", ""),
            owner_pw=opt.get("owner_pw", ""),
            permissions=opt.get("permissions"),
        )
    raise ValueError(f"Unsupported batch operation: '{operation}'.")


def _finalize(results: list[dict], all_outputs: list[str], job_id: str) -> dict:
    """Build descriptors, count successes/failures, and ZIP multiple outputs."""
    success_count = sum(1 for r in results if r["status"] == "success")
    failure_count = len(results) - success_count
    files = [file_descriptor(job_id, p) for p in all_outputs]

    zip_desc = None
    if len(all_outputs) > 1:
        zip_path = make_zip(job_id, all_outputs, "batch_results.zip")
        zip_desc = file_descriptor(job_id, zip_path)

    return {
        "results": results,
        "success_count": success_count,
        "failure_count": failure_count,
        "files": files,
        "zip": zip_desc,
    }


def run_batch(file_paths: list[str], operation: str, options: dict, job_id: str,
              progress_cb=None) -> dict:
    """Apply *operation* to every suitable file in *file_paths*.

    Files whose extension does not suit the operation are recorded as failures
    with a reason (and never abort the batch). ``"merge"`` is special: all PDFs
    are combined into a single output.

    Parameters
    ----------
    progress_cb : optional ``callable(done, total, current_name)`` invoked after
                  each file is handled.

    Returns
    -------
    dict with keys ``results``, ``success_count``, ``failure_count``, ``files``,
    ``zip`` (see module/SPEC docs for the exact shape).
    """
    if operation not in _OP_ALLOWED:
        raise ValueError(f"Unsupported batch operation: '{operation}'.")
    options = options or {}
    allowed = _OP_ALLOWED[operation]
    total = len(file_paths)
    results: list[dict] = []
    all_outputs: list[str] = []

    # ---- Special case: merge everything into one PDF. -------------------- #
    if operation == "merge":
        valid = [p for p in file_paths if ext_of(p) in allowed]
        invalid = [p for p in file_paths if ext_of(p) not in allowed]
        merged_desc = None
        if len(valid) >= 1:
            try:
                outputs = pdf_service.merge(valid, job_id)
                all_outputs.extend(outputs)
                merged_desc = file_descriptor(job_id, outputs[0])
            except Exception as exc:  # whole-merge failure -> mark all valid failed
                for done, p in enumerate(valid, start=1):
                    results.append({
                        "file": os.path.basename(p), "status": "failed",
                        "reason": str(exc), "outputs": [],
                    })
                    if progress_cb:
                        progress_cb(done, total, os.path.basename(p))
                valid = []
        for done, p in enumerate(valid, start=1):
            results.append({
                "file": os.path.basename(p), "status": "success",
                "reason": None, "outputs": [merged_desc] if merged_desc else [],
            })
            if progress_cb:
                progress_cb(done, total, os.path.basename(p))
        for p in invalid:
            results.append({
                "file": os.path.basename(p), "status": "failed",
                "reason": "Not a PDF; skipped for merge.", "outputs": [],
            })
            if progress_cb:
                progress_cb(len(results), total, os.path.basename(p))
        return _finalize(results, all_outputs, job_id)

    # ---- General per-file processing. ------------------------------------ #
    for done, path in enumerate(file_paths, start=1):
        name = os.path.basename(path)
        if ext_of(path) not in allowed:
            results.append({
                "file": name, "status": "failed",
                "reason": f"Unsupported file type for '{operation}'.", "outputs": [],
            })
        else:
            try:
                outputs = _process_one(operation, path, options, job_id)
                all_outputs.extend(outputs)
                results.append({
                    "file": name, "status": "success", "reason": None,
                    "outputs": [file_descriptor(job_id, p) for p in outputs],
                })
            except Exception as exc:
                results.append({
                    "file": name, "status": "failed",
                    "reason": str(exc), "outputs": [],
                })
        if progress_cb:
            progress_cb(done, total, name)

    return _finalize(results, all_outputs, job_id)
