"""Аналіз стилю: збір власних вихідних повідомлень і генерація профілю через LLM.

Функції приймають уже підключений Telethon-клієнт — жодної власної авторизації.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from ..llm import BaseLLMClient
from .styles import DEFAULT_STYLE_META_PROMPT

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_CHAR_LIMIT = 15000
DEFAULT_MESSAGES_PER_CHAT = 500


class _TelethonLike(Protocol):
    """Мінімальний інтерфейс Telethon-клієнта, який використовує аналізатор."""

    async def get_entity(self, entity: Any) -> Any: ...
    def iter_messages(self, entity: Any, limit: int, from_user: Any = ...): ...


def build_style_user_prompt(samples: list[str], char_limit: int = DEFAULT_SAMPLE_CHAR_LIMIT) -> str:
    """Зібрати користувацький запит для мета-промпту (чиста функція — тестується)."""
    chunks: list[str] = []
    used = 0
    for sample in samples:
        sample = sample.strip()
        if not sample:
            continue
        remaining = char_limit - used
        if remaining <= 0:
            break
        if len(sample) > remaining:
            sample = sample[:remaining]
        chunks.append(sample)
        used += len(sample) + 1
    return DEFAULT_STYLE_META_PROMPT.format(samples="\n".join(chunks))


async def collect_outgoing_messages(
    client: _TelethonLike,
    chat_ref: Any,
    limit: int = DEFAULT_MESSAGES_PER_CHAT,
) -> list[str]:
    """Отримати ТІЛЬКИ власні вихідні текстові повідомлення з чату.

    ``chat_ref`` — id, @username або об'єкт діалогу Telethon.
    """
    entity = await client.get_entity(chat_ref)
    texts: list[str] = []
    async for message in client.iter_messages(entity, limit=limit, from_user="me"):
        text = getattr(message, "message", None)
        if text and text.strip():
            texts.append(text.strip())
    return texts


async def generate_style_profile(
    client: _TelethonLike,
    llm: BaseLLMClient,
    chat_refs: list[Any],
    limit_per_chat: int = DEFAULT_MESSAGES_PER_CHAT,
    char_limit: int = DEFAULT_SAMPLE_CHAR_LIMIT,
) -> str:
    """Зібрати повідомлення з чатів і згенерувати профіль стилю через LLM.

    Повертає текст профілю українською. Кидає виняток, якщо власних
    повідомлень не знайдено.
    """
    all_samples: list[str] = []
    for chat_ref in chat_refs:
        try:
            texts = await collect_outgoing_messages(client, chat_ref, limit=limit_per_chat)
        except Exception:
            logger.exception("Не вдалося зібрати повідомлення з %r", chat_ref)
            continue
        logger.info("Зібрано %d власних повідомлень з %r", len(texts), chat_ref)
        all_samples.extend(texts)

    if not all_samples:
        raise ValueError("Не знайдено власних вихідних повідомлень у обраних чатах")

    user_prompt = build_style_user_prompt(all_samples, char_limit=char_limit)
    return await llm.chat(
        [{"role": "user", "content": user_prompt}],
        system="Ти — експерт зі стилістики тексту. Відповідай українською.",
    )
