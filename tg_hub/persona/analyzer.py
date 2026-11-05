"""Аналіз стилю: збір власних вихідних повідомлень і генерація профілю через LLM.

Функції приймають уже підключений Telethon-клієнт — жодної власної авторизації.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from ..llm import BaseLLMClient
from .styles import DEFAULT_CHAT_PROFILE_META_PROMPT, DEFAULT_STYLE_META_PROMPT

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


def build_dialogue_user_prompt(
    pairs: list[tuple[bool, str, str | None]],
    char_limit: int = DEFAULT_SAMPLE_CHAR_LIMIT,
) -> str:
    """Пер-чат профіль: діалог з мітками «Я»/«Інший» і датами (чиста функція).

    ``pairs`` — (is_mine, text, date_label «ДД.ММ» або None).
    """
    lines: list[str] = []
    used = 0
    for is_mine, text, date_label in pairs:
        text = text.strip()
        if not text:
            continue
        stamp = f"[{date_label}] " if date_label else ""
        line = f"{stamp}{'Я' if is_mine else 'Інший'}: {text}"
        remaining = char_limit - used
        if remaining <= 0:
            break
        if len(line) > remaining:
            line = line[:remaining]
        lines.append(line)
        used += len(line) + 1
    return DEFAULT_CHAT_PROFILE_META_PROMPT.format(samples="\n".join(lines))


async def collect_dialogue_pairs(
    client: _TelethonLike,
    chat_ref: Any,
    limit: int = DEFAULT_MESSAGES_PER_CHAT,
) -> list[tuple[bool, str, str | None]]:
    """Отримати діалог як трійки (is_mine, text, date_label «ДД.ММ» або None).

    Для профілю «пари з людиною» важлива не лише моя мова, а й те, як саме
    ця людина листується зі мною (її capitalization, тон, теми) і КОЛИ —
    давні повідомлення дають «старий контекст», свіжі — «актуальний».
    """
    entity = await client.get_entity(chat_ref)
    pairs: list[tuple[bool, str, str | None]] = []
    async for message in client.iter_messages(entity, limit=limit):
        text = getattr(message, "message", None)
        if text and text.strip():
            date_label = None
            try:
                date_label = message.date.astimezone().strftime("%d.%m")
            except Exception:  # noqa: BLE001 — без дати теж ок
                pass
            pairs.append((bool(message.out), text.strip(), date_label))
    return pairs


async def collect_outgoing_messages(
    client: _TelethonLike,
    chat_ref: Any,
    limit: int = DEFAULT_MESSAGES_PER_CHAT,
) -> list[str]:
    """Отримати ТІЛЬКИ власні вихідні текстові повідомлення з чату (глобальний профіль)."""
    triples = await collect_dialogue_pairs(client, chat_ref, limit=limit)
    return [text for is_mine, text, _date in triples if is_mine]


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


async def generate_chat_profile(
    client: _TelethonLike,
    llm: BaseLLMClient,
    chat_ref: Any,
    limit: int = DEFAULT_MESSAGES_PER_CHAT,
    char_limit: int = DEFAULT_SAMPLE_CHAR_LIMIT,
) -> str:
    """Пер-чат профіль: аналіз ВЗАЄМОДІЇ власника з конкретною людиною.

    Аналізується діалог (обидві сторони, з датами) — як власник пише саме
    з цією людиною і як листується вона сама.
    """
    raw = await collect_dialogue_pairs(client, chat_ref, limit=limit)
    if not raw:
        raise ValueError("Не знайдено повідомлень у чаті для аналізу")
    # Telethon повертає від нових до старих — реверсуємо до хронології
    raw.reverse()
    prompt = build_dialogue_user_prompt(raw, char_limit=char_limit)
    return await llm.chat(
        [{"role": "user", "content": prompt}],
        system="Ти — експерт зі стилістики тексту. Відповідай українською.",
    )
