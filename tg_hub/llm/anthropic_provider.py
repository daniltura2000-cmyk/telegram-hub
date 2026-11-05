"""Провайдер Anthropic (Messages API)."""
from __future__ import annotations

from typing import Any

import httpx

from .base import BaseLLMClient, LLMError

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 1024


def build_anthropic_payload(model: str, messages: list[dict[str, str]],
                            system: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> dict[str, Any]:
    """Системний промпт передається окремим полем ``system``."""
    payload_messages = [{"role": m["role"], "content": m["content"]} for m in messages]
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": payload_messages,
    }
    if system:
        payload["system"] = system
    return payload


def parse_anthropic_response(data: dict[str, Any]) -> str:
    """Склеїти текстові блоки з ``content``."""
    try:
        blocks = data["content"]
        text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
    except (KeyError, TypeError) as exc:
        raise LLMError(f"Неочікувана відповідь Anthropic: {exc}") from exc
    if not text:
        raise LLMError("Anthropic повернув порожню відповідь")
    return text


class AnthropicClient(BaseLLMClient):
    """Клієнт api.anthropic.com/v1/messages."""

    async def chat(self, messages: list[dict[str, str]], system: str = "") -> str:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        payload = build_anthropic_payload(self.model, messages, system)
        async with httpx.AsyncClient() as client:
            data = await self._post_json(client, ANTHROPIC_URL, headers, payload)
        return parse_anthropic_response(data)
