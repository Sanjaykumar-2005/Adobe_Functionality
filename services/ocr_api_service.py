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


def list_providers() -> dict:
    """Return the configured OCR providers (+ default) for the UI dropdown.

    Shape: ``{"providers": [{"id","label","configured"}, ...], "default": "<id>"}``.
    No subscription keys are ever included.
    """
    return {
        "providers": AppSettings.ocr_providers_public(),
        "default": AppSettings.OCR_DEFAULT_PROVIDER,
    }


def extract_text(pdf_path: str, provider: str | None = None) -> str:
    """Base64-encode the PDF, call the chosen OCR provider, return its text.

    ``provider`` is an id from ``AppSettings.OCR_PROVIDERS`` (Chandra, PaddleOCR,
    ...); ``None`` uses the default provider. Raises :class:`ValueError` for an
    unknown/unconfigured provider (→ HTTP 400), :class:`RuntimeError` otherwise.
    """
    if requests is None:
        raise RuntimeError("The 'requests' package is required for OCR — pip install requests.")

    try:
        prov = AppSettings.ocr_provider(provider)
    except KeyError as exc:
        raise ValueError(str(exc)) from exc

    endpoint = prov.get("endpoint")
    key = prov.get("key")
    if not (endpoint and key):
        name = provider or AppSettings.OCR_DEFAULT_PROVIDER
        raise ValueError(
            f"OCR provider '{name}' is not configured. Paste its endpoint URL + "
            f"subscription key in app_settings.py (OCR_PROVIDERS)."
        )
    input_key = prov.get("input_key", "base64_file")
    output_key = prov.get("output_key", "extracted_text")

    with open(pdf_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")

    resp = requests.post(
        endpoint,
        headers={
            "Content-Type": "application/json",
            AppSettings.SUBSCRIPTION_KEY_HEADER: key,
        },
        json={input_key: b64},
        timeout=AppSettings.HTTP_TIMEOUT,
    )
    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError("OCR API did not return JSON.")

    text = data.get(output_key)
    if text is None:
        raise RuntimeError(f"OCR API response did not contain '{output_key}'.")
    return text
