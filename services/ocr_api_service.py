"""
services/ocr_api_service.py — remote OCR via the Azure OCR endpoint.

Flow:  read the PDF -> base64-encode -> POST {"base64_file": <b64>} ->
       the service replies {"extracted_text": <text>}.

Flask-free: takes an absolute path, returns the extracted text as a string.
Endpoint + subscription key come from app_settings.AppSettings.
"""
from __future__ import annotations

import base64

# `requests` is guarded so the app still boots if it isn't installed;
# the call raises a clear error instead of crashing at import time.
try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

from app_settings import AppSettings


def extract_text(pdf_path: str) -> str:
    """Base64-encode the PDF, call the OCR API, and return its extracted text."""
    if requests is None:
        raise RuntimeError("The 'requests' package is required for OCR — pip install requests.")
    if not AppSettings.ocr_configured():
        raise RuntimeError(
            "OCR API is not configured. Set OCR_API_ENDPOINT and OCR_API_KEY "
            "(see app_settings.py)."
        )

    with open(pdf_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")

    resp = requests.post(
        AppSettings.OCR_API_ENDPOINT,
        headers={
            "Content-Type": "application/json",
            AppSettings.SUBSCRIPTION_KEY_HEADER: AppSettings.OCR_API_KEY,
        },
        json={"base64_file": b64},
        timeout=AppSettings.HTTP_TIMEOUT,
    )
    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError("OCR API did not return JSON.")

    text = data.get("extracted_text")
    if text is None:
        raise RuntimeError("OCR API response did not contain 'extracted_text'.")
    return text
