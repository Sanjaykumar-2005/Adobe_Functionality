"""Uniform JSON envelope helpers so every endpoint speaks the same language."""
from __future__ import annotations

from flask import jsonify


def ok(job_id: str, files: list[dict] | None = None, **extra):
    """Success envelope.

    files: list of {name, url, size} descriptors (see file_utils.file_descriptor).
    extra: any feature-specific fields (e.g. pages, scanned, results).
    """
    payload = {"success": True, "job_id": job_id}
    if files is not None:
        payload["files"] = files
    payload.update(extra)
    return jsonify(payload)


def fail(message: str, status: int = 400, **extra):
    payload = {"success": False, "error": message}
    payload.update(extra)
    return jsonify(payload), status
