"""
app_settings.py — Azure API settings for the OCR and LLM (summarize) services.

This is the ONE place to configure the external Azure endpoints + subscription
keys. Edit the values below directly (no .env / environment variables needed).
Both Azure APIM services authenticate with the SAME header name
(`ocp-apim-subscription-key`); only the URL + key differ per service.

⚠ SECURITY: this file holds real subscription keys. It is committed to the repo,
so anyone with repo access can read the keys. Do NOT push to a public remote,
and rotate the keys if they ever leak.
"""


class AppSettings:
    # ---- OCR service: sends {"base64_file": <b64>} -> {"extracted_text": <text>} ----
    OCR_API_ENDPOINT = "https://ltceip4prod.azure-api.net/Chandra/extract-vector-chandra"
    OCR_API_KEY = ""  # <-- paste your Chandra (OCR) subscription key here

    # ---- LLM service: Qwen32B chat/completions (used by the Summarize feature) ----
    LLM_API_ENDPOINT = "https://ltceip4prod.azure-api.net/qwen32b/chat/completions"
    LLM_API_KEY = ""  # <-- paste your Qwen (LLM) subscription key here
    LLM_MAX_TOKENS = 5000
    LLM_SYSTEM_PROMPT = (
        "You are a helpful assistant that writes clear, concise summaries of documents."
    )

    # ---- Shared HTTP ----
    HTTP_TIMEOUT = 120  # seconds per OCR/LLM request
    # Header name both Azure APIM services expect for the subscription key.
    SUBSCRIPTION_KEY_HEADER = "ocp-apim-subscription-key"

    @classmethod
    def ocr_configured(cls) -> bool:
        """True when the OCR endpoint URL and key are both set."""
        return bool(cls.OCR_API_ENDPOINT and cls.OCR_API_KEY)

    @classmethod
    def llm_configured(cls) -> bool:
        """True when the LLM endpoint URL and key are both set."""
        return bool(cls.LLM_API_ENDPOINT and cls.LLM_API_KEY)
