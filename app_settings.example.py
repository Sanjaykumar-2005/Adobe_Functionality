"""
app_settings.example.py — TEMPLATE. Copy this to `app_settings.py` and fill it in.

    cp app_settings.example.py app_settings.py      # Linux / RHEL
    copy app_settings.example.py app_settings.py    # Windows

`app_settings.py` is the real config and is **git-ignored**, so the subscription
keys you paste into it can never be committed or pushed. This template holds only
empty placeholders and IS committed, so the shape of the config stays in the repo.

This is the ONE place to configure the external Azure endpoints + subscription
keys. Edit the values directly (no .env / environment variables needed). Both Azure
APIM services authenticate with the SAME header name
(`ocp-apim-subscription-key`); only the URL + key differ per service.

Leave a key blank and its feature simply reports "not configured" — the app still
boots and every non-OCR tool works.
"""
from __future__ import annotations  # allow `str | None` hints on Python 3.7–3.9 (RHEL)


class AppSettings:
    # ---- OCR providers: selectable OCR engines (Chandra, PaddleOCR, ...) ----
    # Each provider is one remote OCR endpoint. The user picks one in the UI.
    # To ADD a provider: copy a block below, give it a new id (the dict key),
    # paste its endpoint URL + subscription key, and (if its JSON differs) set
    # "input_key"/"output_key". That's it — it shows up in the frontend dropdown.
    #
    #   input_key  : request JSON field that carries the base64 PDF  (default "base64_file")
    #   output_key : response JSON field that holds the extracted text (default "extracted_text")
    OCR_PROVIDERS = {
        "chandra": {
            "label": "Chandra",
            "endpoint": "",  # <-- paste the Chandra endpoint URL here
            "key": "",       # <-- paste the Chandra subscription key here
            "input_key": "base64_file",
            "output_key": "extracted_text",
        },
        "paddle": {
            "label": "PaddleOCR",
            "endpoint": "",  # <-- paste the PaddleOCR endpoint URL here
            "key": "",       # <-- paste the PaddleOCR subscription key here
            "input_key": "base64_file",
            "output_key": "extracted_text",
        },
    }
    # Provider selected by default (must be a key of OCR_PROVIDERS).
    OCR_DEFAULT_PROVIDER = "chandra"

    # ---- LLM service: Qwen32B chat/completions (used by the Summarize feature) ----
    LLM_API_ENDPOINT = ""  # <-- paste the Qwen (LLM) endpoint URL here
    LLM_API_KEY = ""       # <-- paste the Qwen (LLM) subscription key here
    LLM_MAX_TOKENS = 5000
    LLM_SYSTEM_PROMPT = (
        "You are a helpful assistant that writes clear, concise summaries of documents."
    )

    # ---- Shared HTTP ----
    HTTP_TIMEOUT = 120  # seconds per OCR/LLM request
    # Header name both Azure APIM services expect for the subscription key.
    SUBSCRIPTION_KEY_HEADER = "ocp-apim-subscription-key"

    @classmethod
    def ocr_provider(cls, name: str | None = None) -> dict:
        """Return the config dict for an OCR provider (default when name is None).

        Raises :class:`KeyError` if the name is not a known provider.
        """
        name = name or cls.OCR_DEFAULT_PROVIDER
        prov = cls.OCR_PROVIDERS.get(name)
        if prov is None:
            raise KeyError(
                f"Unknown OCR provider '{name}'. "
                f"Choose from: {', '.join(cls.OCR_PROVIDERS)}."
            )
        return prov

    @classmethod
    def ocr_provider_configured(cls, name: str | None = None) -> bool:
        """True when the given provider (default if None) has both endpoint + key."""
        try:
            prov = cls.ocr_provider(name)
        except KeyError:
            return False
        return bool(prov.get("endpoint") and prov.get("key"))

    @classmethod
    def ocr_configured(cls) -> bool:
        """True when the DEFAULT OCR provider is configured (endpoint + key set)."""
        return cls.ocr_provider_configured(cls.OCR_DEFAULT_PROVIDER)

    @classmethod
    def ocr_providers_public(cls) -> list:
        """Provider list for the frontend dropdown — ids/labels only, NO keys."""
        return [
            {
                "id": pid,
                "label": p.get("label", pid),
                "configured": bool(p.get("endpoint") and p.get("key")),
            }
            for pid, p in cls.OCR_PROVIDERS.items()
        ]

    @classmethod
    def llm_configured(cls) -> bool:
        """True when the LLM endpoint URL and key are both set."""
        return bool(cls.LLM_API_ENDPOINT and cls.LLM_API_KEY)
