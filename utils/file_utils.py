"""
File-handling utilities shared by every route and service.

Responsibilities
----------------
* Validate uploaded files (extension + emptiness).
* Persist uploads into a per-request job directory under TEMP_DIR.
* Produce output paths under OUTPUT_DIR keyed by a job id so downloads are
  namespaced and collision-free.
* Build ZIP archives for multi-file results.
* Run a background janitor that deletes stale job directories.

The single source of truth for "where does a job's files live" is the job id
(a short uuid). A download URL is always:  /download/<job_id>/<filename>
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
import zipfile
from typing import Iterable

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from config import Config


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
class FileValidationError(Exception):
    """Raised when an uploaded file fails validation. Routes map this to HTTP 400."""


def ext_of(filename: str) -> str:
    """Lower-cased extension including the dot, e.g. '.pdf'. Empty string if none."""
    return os.path.splitext(filename or "")[1].lower()


def validate_upload(storage: FileStorage, allowed: Iterable[str]) -> None:
    """Validate a single FileStorage against an allowed-extension set."""
    if storage is None or not storage.filename:
        raise FileValidationError("No file provided.")
    ext = ext_of(storage.filename)
    allowed = set(allowed)
    if ext not in allowed:
        nice = ", ".join(sorted(e.lstrip(".") for e in allowed))
        raise FileValidationError(
            f"Unsupported file type '{ext or '?'}'. Allowed: {nice}."
        )


# --------------------------------------------------------------------------- #
# Job directories & persistence
# --------------------------------------------------------------------------- #
def new_job_id() -> str:
    return uuid.uuid4().hex[:16]


def job_input_dir(job_id: str) -> str:
    p = os.path.join(Config.TEMP_DIR, job_id)
    os.makedirs(p, exist_ok=True)
    return p


def job_output_dir(job_id: str) -> str:
    p = os.path.join(Config.OUTPUT_DIR, job_id)
    os.makedirs(p, exist_ok=True)
    return p


def save_upload(storage: FileStorage, job_id: str) -> str:
    """Persist an upload into the job's input dir; return the absolute path."""
    name = secure_filename(storage.filename) or f"upload{ext_of(storage.filename)}"
    dest = os.path.join(job_input_dir(job_id), name)
    # Avoid clobbering duplicate names within one request.
    base, ext = os.path.splitext(dest)
    i = 1
    while os.path.exists(dest):
        dest = f"{base}_{i}{ext}"
        i += 1
    storage.save(dest)
    if os.path.getsize(dest) == 0:
        os.remove(dest)
        raise FileValidationError(f"Uploaded file '{name}' is empty.")
    return dest


def save_uploads(storages: list[FileStorage], allowed: Iterable[str], job_id: str) -> list[str]:
    """Validate + persist a list of uploads. Returns absolute input paths in order."""
    if not storages:
        raise FileValidationError("No files provided.")
    if len(storages) > Config.MAX_FILES_PER_REQUEST:
        raise FileValidationError(
            f"Too many files ({len(storages)}); max {Config.MAX_FILES_PER_REQUEST}."
        )
    paths = []
    for s in storages:
        validate_upload(s, allowed)
        paths.append(save_upload(s, job_id))
    return paths


def output_path(job_id: str, filename: str) -> str:
    """Absolute path for a result file inside the job's output dir."""
    return os.path.join(job_output_dir(job_id), secure_filename(filename))


def download_url(job_id: str, filename: str) -> str:
    return f"/download/{job_id}/{secure_filename(filename)}"


def file_descriptor(job_id: str, abs_path: str) -> dict:
    """Standard JSON descriptor for a produced file."""
    name = os.path.basename(abs_path)
    size = os.path.getsize(abs_path) if os.path.exists(abs_path) else 0
    return {"name": name, "url": download_url(job_id, name), "size": size}


# --------------------------------------------------------------------------- #
# ZIP packaging
# --------------------------------------------------------------------------- #
def make_zip(job_id: str, file_paths: list[str], zip_name: str = "results.zip") -> str:
    """Bundle the given files into a ZIP inside the job output dir; return its path."""
    zip_path = output_path(job_id, zip_name)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in file_paths:
            if os.path.exists(p):
                zf.write(p, arcname=os.path.basename(p))
    return zip_path


# --------------------------------------------------------------------------- #
# Naming helpers
# --------------------------------------------------------------------------- #
def stem(path: str) -> str:
    """Filename without directory or extension."""
    return os.path.splitext(os.path.basename(path))[0]


def with_suffix(path: str, suffix: str, ext: str = ".pdf") -> str:
    """Build 'name<suffix><ext>' from a source path (basename only)."""
    return f"{stem(path)}{suffix}{ext}"


# --------------------------------------------------------------------------- #
# Background janitor
# --------------------------------------------------------------------------- #
def _purge_dir_tree(root: str, max_age: float) -> None:
    now = time.time()
    if not os.path.isdir(root):
        return
    for entry in os.listdir(root):
        full = os.path.join(root, entry)
        try:
            if now - os.path.getmtime(full) > max_age:
                if os.path.isdir(full):
                    shutil.rmtree(full, ignore_errors=True)
                else:
                    os.remove(full)
        except OSError:
            pass


def _janitor_loop(app_logger=None) -> None:
    while True:
        try:
            _purge_dir_tree(Config.OUTPUT_DIR, Config.FILE_RETENTION_SECONDS)
            _purge_dir_tree(Config.TEMP_DIR, Config.FILE_RETENTION_SECONDS)
            if app_logger:
                app_logger.debug("janitor: cleanup pass complete")
        except Exception as exc:  # never let the janitor die
            if app_logger:
                app_logger.warning("janitor error: %s", exc)
        time.sleep(Config.CLEANUP_INTERVAL_SECONDS)


def start_janitor(app_logger=None) -> None:
    """Spawn the cleanup thread once (idempotent via a module flag)."""
    if getattr(start_janitor, "_started", False):
        return
    start_janitor._started = True
    t = threading.Thread(target=_janitor_loop, args=(app_logger,), daemon=True, name="janitor")
    t.start()
