"""LLM-провайдери (тільки httpx, без SDK) та фабрика."""
from __future__ import annotations

from .base import DEFAULT_BACKOFF_BASE, DEFAULT_MAX_RETRIES, BaseLLMClient, LLMError
from .openai_compat import OpenAICompatClient
from .anthropic_provider import AnthropicClient
from .google_provider import GoogleClient

PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_GOOGLE = "google"

_KNOWN_PROVIDERS = frozenset({PROVIDER_OPENAI, PROVIDER_ANTHROPIC, PROVIDER_GOOGLE})


def create_client(provider: str, base_url: str | None, api_key: str, model: str,
                  timeout: float = 60.0, **kwargs: object) -> BaseLLMClient:
    """Створити клієнт провайдера за назвою.

    ``provider``: ``openai`` (OpenAI-сумісний, потрібен base_url), ``anthropic``, ``google``.
    """
    provider = (provider or "").strip().lower()
    if provider in ("openai", "openai-compatible", "openai_compat"):
        return OpenAICompatClient(
            base_url=base_url or "https://api.openai.com/v1",
            api_key=api_key, model=model, timeout=timeout,
            **kwargs,
        )
    if provider == PROVIDER_ANTHROPIC:
        return AnthropicClient(api_key=api_key, model=model, timeout=timeout, **kwargs)
    if provider == PROVIDER_GOOGLE:
        return GoogleClient(api_key=api_key, model=model, timeout=timeout, **kwargs)
    raise ValueError(
        f"Невідомий провайдер: {provider!r}. Доступні: {', '.join(sorted(_KNOWN_PROVIDERS))}"
    )


__all__ = [
    "BaseLLMClient",
    "LLMError",
    "OpenAICompatClient",
    "AnthropicClient",
    "GoogleClient",
    "create_client",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_BACKOFF_BASE",
    "PROVIDER_OPENAI",
    "PROVIDER_ANTHROPIC",
    "PROVIDER_GOOGLE",
]
