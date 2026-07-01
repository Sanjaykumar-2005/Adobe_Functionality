"""
services/llm_service.py — text summarization via the Azure Qwen32B chat endpoint.

Sends an OpenAI-style chat/completions payload and returns the assistant's
message text. Endpoint + subscription key come from app_settings.AppSettings.

Reference request shape (from the API owner):
    POST {LLM_API_ENDPOINT}
    Content-Type: application/json
    ocp-apim-subscription-key: <key>
    { "messages": [ {role, content}, ... ], "max_tokens": 5000 }
"""
from __future__ import annotations

# `requests` is guarded so the app still boots if it isn't installed.
try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

from app_settings import AppSettings

_DEFAULT_INSTRUCTION = (
    "Summarize the following document text. Capture the key points, decisions, "
    "and any action items in clear, concise language.\n\n"
)


def summarize(text: str, instruction: str | None = None) -> str:
    """Summarize `text` with the LLM and return the summary string."""
    if requests is None:
        raise RuntimeError("The 'requests' package is required for the LLM — pip install requests.")
    if not text or not text.strip():
        raise ValueError("No text to summarize.")
    if not AppSettings.llm_configured():
        raise RuntimeError(
            "LLM API is not configured. Set LLM_API_ENDPOINT and LLM_API_KEY "
            "(see app_settings.py)."
        )

    prompt = (instruction or _DEFAULT_INSTRUCTION) + text
    resp = requests.post(
        AppSettings.LLM_API_ENDPOINT,
        headers={
            "Content-Type": "application/json",
            AppSettings.SUBSCRIPTION_KEY_HEADER: AppSettings.LLM_API_KEY,
        },
        json={
            "messages": [
                {"role": "system", "content": AppSettings.LLM_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": AppSettings.LLM_MAX_TOKENS,
        },
        timeout=AppSettings.HTTP_TIMEOUT,
    )
    resp.raise_for_status()

    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except ValueError:
        raise RuntimeError("LLM API did not return JSON.")
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("LLM response was not in the expected chat-completions format.")
