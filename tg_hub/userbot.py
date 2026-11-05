"""Userbot: обгортка Telethon + движок авто-відповідей («людське» надсилання)."""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import Any

from telethon import TelegramClient, events

from .config import Config
from .db import Database
from .llm import BaseLLMClient, LLMError, create_client
from .persona.styles import DEFAULT_BASE_PROMPT

logger = logging.getLogger(__name__)

OWNER_LABEL = "Я:"
OTHER_LABEL = "Співрозмовник:"

_DISCLAIMER_RE = re.compile(
    r"^\s*(я(?:к)?\s+(?:штучний\s+)?інтелект|я\s*—\s*(?:штучний\s+)?інтелект|"
    r"as\s+an\s+ai|я\s+не\s+людина)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
_MD_ARTIFACTS = (
    ("```", ""),
    ("**", ""),
    ("__", ""),
    ("###", ""),
    ("##", ""),
)
TYPING_CHARS_PER_SECOND = 15.0
TYPING_MIN_SECONDS = 1.0
TYPING_MAX_SECONDS = 6.0
ERROR_NOTIFY_INTERVAL = 600.0  # не спамити адміна частіше ніж раз на 10 хв


def strip_markdown_artifacts(text: str) -> str:
    """Прибрати markdown-артефакти та дислеймери про ШІ."""
    for old, new in _MD_ARTIFACTS:
        text = text.replace(old, new)
    text = _DISCLAIMER_RE.sub("", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def split_reply(text: str, max_chunks: int = 3) -> list[str]:
    """Розбити довгу відповідь на 1..max_chunks частин по реченнях."""
    text = text.strip()
    if not text:
        return []
    if max_chunks <= 1 or len(text) <= 600:
        return [text]

    sentences = re.split(r"(?<=[.!?…])\s+", text)
    if len(sentences) < 2:
        return [text]

    target = len(text) / max_chunks
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        current.append(sentence)
        current_len += len(sentence) + 1
        if current_len >= target and len(chunks) < max_chunks - 1:
            chunks.append(" ".join(current))
            current, current_len = [], 0
    if current:
        if chunks and len(" ".join(current)) < 30:
            chunks[-1] += " " + " ".join(current)
        else:
            chunks.append(" ".join(current))
    return chunks[:max_chunks]


def format_context(pairs: list[tuple[bool, str]], max_messages: int = 20) -> str:
    """Історія → текст з мітками «Я:» / «Співрозмовник:» (хронологічно, останні N).

    ``pairs`` — список (is_outgoing, text) у хронологічному порядку.
    """
    recent = [p for p in pairs if p[1] and p[1].strip()][-max_messages:]
    lines = [f"{OWNER_LABEL if out else OTHER_LABEL} {text.strip()}" for out, text in recent]
    return "\n".join(lines)


class Userbot:
    """Клієнт Telethon власника + движок авто-відповідей."""

    def __init__(self, config: Config, db: Database, owner_user_id: int | None = None) -> None:
        self._config = config
        self._db = db
        self._owner_user_id = owner_user_id
        self.client: TelegramClient | None = None
        self._cooldowns: dict[int, float] = {}
        self._last_error_notify = 0.0
        self._own_username = ""
        self._error_notifier: Any = None

    # --- життєвий цикл ---

    async def start(self) -> bool:
        """Підключити клієнт, якщо в БД (або env) є акаунт. True — підключено."""
        account = None
        if self._owner_user_id is not None:
            account = await self._db.get_account(self._owner_user_id)
        if account is None:
            account = await self._db.get_any_account()

        api_id = account["api_id"] if account else self._config.telegram_api_id
        api_hash = account["api_hash"] if account else self._config.telegram_api_hash
        if not api_id or not api_hash:
            logger.info("Telethon-акаунт не налаштовано — userbot не запускається")
            return False

        user_id = account["user_id"] if account else self._owner_user_id
        session_path = self._config.sessions_dir / f"{user_id}.session"
        self._config.sessions_dir.mkdir(parents=True, exist_ok=True)

        self.client = TelegramClient(
            str(session_path), int(api_id), str(api_hash),
            device_model="TelegramHub",
        )
        await self.client.connect()
        if not await self.client.is_user_authorized():
            logger.error("Сесія %s не авторизована — запустіть підключення через контрольний бот", session_path)
            await self.client.disconnect()
            self.client = None
            return False

        me = await self.client.get_me()
        self._owner_user_id = user_id
        self._own_username = (getattr(me, "username", None) or "").lower()
        logger.info("Userbot підключено як %s (id=%s)", getattr(me, "first_name", "?"), user_id)
        self.client.add_event_handler(self._on_new_message, events.NewMessage(incoming=True))
        return True

    async def stop(self) -> None:
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:  # noqa: BLE001 — при зупинці процесу все одно ігноруємо
                logger.exception("Помилка під час від'єднання Telethon")
            self.client = None

    @property
    def connected(self) -> bool:
        return self.client is not None

    # --- обробник вхідних повідомлень ---

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        """Вхідне повідомлення → фільтри → LLM → «людське» надсилання."""
        message = event.message
        if message.out or getattr(message, "action", None) is not None:
            return
        if not (message.message or "").strip():
            return
        if self._owner_user_id is None or message.sender_id == self._owner_user_id:
            return

        try:
            user_id = self._owner_user_id
            settings = await self._db.get_settings(user_id)
            if not settings["auto_reply"]:
                return
            if not self._passes_filters(message, settings):
                return
            if not self._cooldown_ok(message.chat_id, int(settings["cooldown_seconds"])):
                return

            reply = await self._generate_reply(user_id, settings, message)
            if not reply:
                return
            self._cooldowns[message.chat_id] = time.monotonic()
            await self._humanized_send(message, reply, settings)
        except LLMError as exc:
            await self._record_error(message.chat_id, f"LLM: {exc}")
        except Exception as exc:  # noqa: BLE001 — не даємо обробнику впасти
            await self._record_error(message.chat_id, f"{type(exc).__name__}: {exc}")

    # --- фільтри ---

    def _passes_filters(self, message: Any, settings: dict[str, Any]) -> bool:
        chat_id = str(message.chat_id)
        username = getattr(message.chat, "username", None) if message.chat else None
        entries = {str(e).strip().lstrip("@").lower() for e in settings["ignore_list"] + settings["whitelist_chats"]}

        candidates = {chat_id.lower()}
        if username:
            candidates.add(username.lower())

        if settings["whitelist_mode"]:
            if not (candidates & entries):
                return False
        else:
            ignore = {str(e).strip().lstrip("@").lower() for e in settings["ignore_list"]}
            if candidates & ignore:
                return False

        if settings["only_private_or_mention"]:
            is_private = bool(getattr(message, "is_private", False))
            mentioned = bool(getattr(message, "mentioned", False))
            own_handle = self._own_username.lstrip("@")
            text_hit = bool(own_handle) and own_handle in (message.message or "").lower()
            if not is_private and not mentioned and not text_hit:
                return False
        return True

    def _cooldown_ok(self, chat_id: int, cooldown_seconds: int) -> bool:
        last = self._cooldowns.get(chat_id)
        if last is not None and (time.monotonic() - last) < cooldown_seconds:
            return False
        return True

    # --- генерація відповіді ---

    async def _build_context(self, chat_ref: Any, limit: int) -> str:
        assert self.client is not None
        messages = await self.client.get_messages(chat_ref, limit=max(1, limit))
        pairs: list[tuple[bool, str]] = []
        for msg in reversed(messages):  # get_messages повертає від нових до старих
            text = (msg.message or "").strip() if msg.message else ""
            if text:
                pairs.append((bool(msg.out), text))
        return format_context(pairs, max_messages=limit)

    def _system_prompt(self, base_prompt: str, style_profile: str) -> str:
        system = base_prompt or DEFAULT_BASE_PROMPT
        if "{style_profile}" in system:
            system = system.replace("{style_profile}", style_profile or "—")
        elif style_profile:
            system += f"\n\nПрофіль стилю:\n{style_profile}"
        return system

    async def _generate_reply(self, user_id: int, settings: dict[str, Any], message: Any) -> str:
        key = await self._db.get_active_key(user_id)
        if key is None:
            logger.info("Немає активного LLM-ключа — авто-відповідь пропущено")
            return ""
        llm: BaseLLMClient = create_client(
            provider=key["provider"],
            base_url=key["base_url"],
            api_key=key["api_key"],
            model=key["model"],
            timeout=self._config.llm_timeout,
        )
        prompts = await self._db.get_prompts(user_id)
        context = await self._build_context(message.chat_id, int(settings["context_messages"]))
        system = self._system_prompt(prompts["base_prompt"], prompts["style_profile"])
        user_content = (
            f"Історія діалогу:\n{context}\n\n"
            f"Нове повідомлення співрозмовника: «{message.message.strip()}»\n"
            "Напиши МОЮ відповідь (лише текст повідомлення, без лапок і пояснень)."
        )
        return await llm.chat([{"role": "user", "content": user_content}], system=system)

    # --- «людське» надсилання ---

    async def _humanized_send(self, message: Any, reply: str, settings: dict[str, Any]) -> None:
        assert self.client is not None
        reply = strip_markdown_artifacts(reply)
        chunks = split_reply(reply, max_chunks=max(1, int(settings.get("max_chunks", 3))))
        delay_min = float(settings["reply_delay_min"])
        delay_max = float(settings["reply_delay_max"])
        if delay_max < delay_min:
            delay_min, delay_max = delay_max, delay_min

        await asyncio.sleep(random.uniform(delay_min, delay_max))  # noqa: S311 — імітація людини

        for index, chunk in enumerate(chunks):
            typing_seconds = min(
                TYPING_MAX_SECONDS,
                max(TYPING_MIN_SECONDS, len(chunk) / TYPING_CHARS_PER_SECOND),
            )
            try:
                await self.client.send_chat_action(message.chat_id, "typing")
            except Exception:  # noqa: BLE001 — chat_action не критичний
                logger.debug("send_chat_action не вдався", exc_info=True)
            await asyncio.sleep(typing_seconds)
            await self.client.send_message(message.chat_id, chunk)
            if index < len(chunks) - 1:
                await asyncio.sleep(random.uniform(0.8, 2.5))  # noqa: S311 — пауза між частинами

    # --- помилки ---

    async def _record_error(self, chat_id: int, error: str) -> None:
        short = error[:200]
        logger.error("Помилка авто-відповіді в чаті %s: %s", chat_id, short)
        try:
            await self._db.update_settings(self._owner_user_id or 0, last_error=short)
        except Exception:  # noqa: BLE001
            logger.exception("Не вдалося зберегти останню помилку")

        now = time.monotonic()
        if now - self._last_error_notify > ERROR_NOTIFY_INTERVAL:
            self._last_error_notify = now
            callback = getattr(self, "_error_notifier", None)
            if callback is not None:
                try:
                    await callback(f"⚠️ Userbot: {short}")
                except Exception:  # noqa: BLE001
                    logger.exception("Не вдалося повідомити адміна про помилку")

    def set_error_notifier(self, callback: Any) -> None:
        """Реєструє async callback(text) для сповіщення адмін-чату про помилки."""
        self._error_notifier = callback