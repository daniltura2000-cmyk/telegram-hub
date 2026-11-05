"""Перевірка ключа та отримання реального списку доступних моделей провайдера.

Використовується контрольним ботом: ключ зберігається ЛИШЕ якщо провайдер
його приймає, а модель обирається з фактично наявних моделей (за потреби
можна ввести вручну). Жодних повторів — один запит із коротким таймаутом.
"""
from __future__ import annotations

from typing import Any

import httpx

PROBE_TIMEOUT = 15.0

_NON_CHAT_MARKERS = (
    "embedding", "whisper", "tts", "dall-e", "moderation", "image",
    "audio", "transcribe", "aqa", "vision-only",
)


class ProbeError(Exception):
    """Ключ недійсний/недоступний; ``message`` — готовий український текст."""


def _friendly_http_error(status: int) -> str:
    if status in (401, 403):
        return "❌ Ключ недійсний або не активований (HTTP %d). Перевірте ключ на сайті провайдера." % status
    if status == 429:
        return (
            "❌ HTTP 429 — квота або ліміт запитів вичерпані. "
            "Для OpenAI переконайтеся, що на акаунті є кредити; "
            "для безкоштовних провайдерів — зачекайте і спробуйте пізніше."
        )
    return f"❌ Провайдер повернув HTTP {status}. Спробуйте пізніше."
# ---------------------------------------------------------------------------
# OpenAI-сумісний


def parse_openai_models(data: dict[str, Any]) -> list[str]:
    ids = [item.get("id", "") for item in data.get("data", []) if isinstance(item, dict)]
    cleaned = sorted(
        i for i in ids
        if i and not any(marker in i.lower() for marker in _NON_CHAT_MARKERS)
    )
    return cleaned


async def _probe_openai(base_url: str, api_key: str) -> list[str]:
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, timeout=PROBE_TIMEOUT)
    if response.status_code != 200:
        raise ProbeError(_friendly_http_error(response.status_code))
    return parse_openai_models(response.json())


async def _probe_google(api_key: str) -> list[str]:
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    params = {"key": api_key}
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, timeout=PROBE_TIMEOUT)
    if response.status_code != 200:
        raise ProbeError(_friendly_http_error(response.status_code))
    names: list[str] = []
    for item in response.json().get("models", []):
        name = str(item.get("name", "")).removeprefix("models/")
        if name and not any(marker in name.lower() for marker in _NON_CHAT_MARKERS):
            names.append(name)
    return sorted(set(names))


async def _probe_anthropic(api_key: str) -> list[str]:
    url = "https://api.anthropic.com/v1/models"
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, timeout=PROBE_TIMEOUT)
    if response.status_code != 200:
        raise ProbeError(_friendly_http_error(response.status_code))
    ids = [item.get("id", "") for item in response.json().get("data", []) if isinstance(item, dict)]
    return sorted(i for i in ids if i)


async def probe_key(provider: str, base_url: str, api_key: str) -> list[str]:
    """Повернути список доступних моделей або кинути ProbeError."""
    if provider == "openai":
        if not base_url:
            raise ProbeError("❌ Для OpenAI-сумісного провайдера потрібен базовий URL.")
        return await _probe_openai(base_url, api_key)
    if provider == "google":
        return await _probe_google(api_key)
    if provider == "anthropic":
        return await _probe_anthropic(api_key)
    raise ProbeError(f"❌ Невідомий провайдер: {provider}")
