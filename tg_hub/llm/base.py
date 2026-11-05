"""Базовий клас LLM-провайдера та логіка повторів."""
from __future__ import annotations

import asyncio
import logging
import abc
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.5


class LLMError(Exception):
    """Помилка виклику LLM (після вичерпання повторів тощо)."""


class BaseLLMClient(abc.ABC):
    """Єдиний інтерфейс: ``await chat(messages, system) -> str``."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
    ) -> None:
        if not api_key:
            raise ValueError("API-ключ не може бути порожнім")
        if not model:
            raise ValueError("Назва моделі не може бути порожньою")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.backoff_base = backoff_base

    @abc.abstractmethod
    async def chat(self, messages: list[dict[str, str]], system: str = "") -> str:
        """Повернути текст відповіді моделі. ``messages`` — [{role, content}, ...]."""

    async def _post_json(self, client: httpx.AsyncClient, url: str,
                         headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        """POST з повторами та експоненційною затримкою на 429/5xx/таймаутах."""
        last_error = ""
        for attempt in range(self.max_retries):
            try:
                response = await client.post(url, headers=headers, json=payload, timeout=self.timeout)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"мережева помилка: {type(exc).__name__}"
                logger.warning("LLM %s (спроба %d/%d): %s", url, attempt + 1, self.max_retries, exc)
            else:
                if response.status_code in RETRYABLE_STATUS_CODES:
                    last_error = f"HTTP {response.status_code}"
                    logger.warning("LLM %s (спроба %d/%d): %s", url, attempt + 1, self.max_retries, last_error)
                else:
                    if response.status_code >= 400:
                        raise LLMError(
                            f"LLM повернув HTTP {response.status_code}: {response.text[:300]}"
                        )
                    return response.json()

            if attempt < self.max_retries - 1:
                await asyncio.sleep(self.backoff_base * (2 ** attempt))

        raise LLMError(f"LLM недоступний після {self.max_retries} спроб: {last_error}")
