"""OpenAI-сумісний провайдер (довільний base_url + /chat/completions)."""
from __future__ import annotations

from typing import Any

import httpx

from .base import BaseLLMClient, LLMError


def build_openai_payload(model: str, messages: list[dict[str, str]], system: str,
                         max_tokens: int = 200) -> dict[str, Any]:
    """Зібрати тілло запиту /chat/completions (системний промпт — першим)."""
    payload_messages: list[dict[str, str]] = []
    if system:
        payload_messages.append({"role": "system", "content": system})
    payload_messages.extend(messages)
    payload: dict[str, Any] = {
        "model": model, "messages": payload_messages, "max_tokens": max_tokens,
    }
    # Reasoning-моделі (GLM тощо): вимикаємо приховане «думання» через
    # reasoning_effort=none — інакше воно з'їдає весь max_tokens і
    # content приходить порожнім (finish_reason=length)
    lower = model.lower()
    if "glm" in lower:
        payload["reasoning_effort"] = "none"
    return payload


def parse_openai_response(data: dict[str, Any]) -> str:
    """Витягти текст відповіді зі стандартної відповіді OpenAI."""
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Неочікувана відповідь OpenAI: {exc}") from exc
    if not isinstance(content, str):
        raise LLMError("OpenAI повернув порожню або не текстову відповідь")
    return content


class OpenAICompatClient(BaseLLMClient):
    """Клієнт для будь-якого OpenAI-сумісного API (base_url + ключ + модель)."""

    def __init__(self, base_url: str, api_key: str, model: str, **kwargs: Any) -> None:
        super().__init__(api_key, model, **kwargs)
        if not base_url:
            raise ValueError("base_url обов'язковий для OpenAI-сумісного провайдера")
        self.base_url = base_url.rstrip("/")

    async def chat(self, messages: list[dict[str, str]], system: str = "") -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = build_openai_payload(self.model, messages, system, max_tokens=self.max_tokens)
        async with httpx.AsyncClient() as client:
            data = await self._post_json(client, url, headers, payload)
        return parse_openai_response(data)
