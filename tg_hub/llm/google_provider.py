"""Провайдер Google Gemini (Generative Language API)."""
from __future__ import annotations

from typing import Any

import httpx

from .base import BaseLLMClient, LLMError

GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def build_google_url(model: str, api_key: str) -> str:
    return f"{GOOGLE_BASE_URL}/{model}:generateContent?key={api_key}"


def _to_google_role(role: str) -> str:
    return "model" if role == "assistant" else "user"


def build_google_payload(messages: list[dict[str, str]], system: str) -> dict[str, Any]:
    """Формат contents + system_instruction; роль assistant -> model."""
    contents = [
        {"role": _to_google_role(m["role"]), "parts": [{"text": m["content"]}]}
        for m in messages
    ]
    payload: dict[str, Any] = {"contents": contents}
    if system:
        payload["system_instruction"] = {"parts": [{"text": system}]}
    return payload


def parse_google_response(data: dict[str, Any]) -> str:
    """Витягти текст із candidates[0].content.parts."""
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(part.get("text", "") for part in parts)
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Неочікувана відповідь Google: {exc}") from exc
    if not text:
        raise LLMError("Google повернув порожню відповідь")
    return text


class GoogleClient(BaseLLMClient):
    """Клієнт generativelanguage.googleapis.com (ключ у query-параметрі)."""

    async def chat(self, messages: list[dict[str, str]], system: str = "") -> str:
        url = build_google_url(self.model, self.api_key)
        payload = build_google_payload(messages, system)
        async with httpx.AsyncClient() as client:
            data = await self._post_json(client, url, {}, payload)
        return parse_google_response(data)
