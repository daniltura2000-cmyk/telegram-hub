"""Провайдер Google Gemini (Generative Language API)."""
from __future__ import annotations

import re
from typing import Any

import httpx

from .base import BaseLLMClient, LLMError

GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def build_google_url(model: str, api_key: str) -> str:
    return f"{GOOGLE_BASE_URL}/{model}:generateContent?key={api_key}"


def _to_google_role(role: str) -> str:
    return "model" if role == "assistant" else "user"


def build_google_payload(messages: list[dict[str, str]], system: str,
                         max_tokens: int = 200,
                         disable_thinking: bool = False) -> dict[str, Any]:
    """Формат contents + system_instruction; роль assistant -> model.

    ``disable_thinking`` — для flash-моделей 2.5/3.x, які «думають» за
    замовчуванням: думання з'їдає і час, і бюджет токенів.
    """
    contents = [
        {"role": _to_google_role(m["role"]), "parts": [{"text": m["content"]}]}
        for m in messages
    ]
    generation_config: dict[str, Any] = {"maxOutputTokens": max_tokens}
    if disable_thinking:
        generation_config["thinkingConfig"] = {"thinkingBudget": 0}
    payload: dict[str, Any] = {"contents": contents, "generationConfig": generation_config}
    if system:
        payload["system_instruction"] = {"parts": [{"text": system}]}
    return payload


_THINKING_RE = re.compile(r"gemini.*(2\.5|3)")


def _flash_model_can_disable_thinking(model: str) -> bool:
    """Thinking вимикається лише на flash-моделях 2.5/3.x (pro мінімум 128)."""
    lowered = model.lower()
    return "flash" in lowered and bool(_THINKING_RE.search(lowered))


def parse_google_response(data: dict[str, Any]) -> str:
    """Витягти текст із candidates[0].content.parts (блоки думання — пропускаємо)."""
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(
            part.get("text", "") for part in parts if not part.get("thought")
        )
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Неочікувана відповідь Google: {exc}") from exc
    if not text:
        raise LLMError("Google повернув порожню відповідь")
    return text


class GoogleClient(BaseLLMClient):
    """Клієнт generativelanguage.googleapis.com (ключ у query-параметрі)."""

    async def chat(self, messages: list[dict[str, str]], system: str = "") -> str:
        url = build_google_url(self.model, self.api_key)
        disable_thinking = _flash_model_can_disable_thinking(self.model)
        async with httpx.AsyncClient() as client:
            payload = build_google_payload(
                messages, system, max_tokens=self.max_tokens,
                disable_thinking=disable_thinking,
            )
            try:
                data = await self._post_json(client, url, {}, payload)
            except LLMError as exc:
                # стара модель може відхилити thinkingConfig — повтор без нього
                if disable_thinking and ("400" in str(exc) or "thinking" in str(exc).lower()):
                    logger.warning("thinkingConfig відхилено (%s) — повтор без нього", exc)
                    payload = build_google_payload(
                        messages, system, max_tokens=self.max_tokens,
                        disable_thinking=False,
                    )
                    data = await self._post_json(client, url, {}, payload)
                else:
                    raise
        return parse_google_response(data)
