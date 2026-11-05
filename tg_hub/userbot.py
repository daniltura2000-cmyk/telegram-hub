"""Userbot: обгортка Telethon + движок авто-відповідей («людське» надсилання)."""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from datetime import datetime
from typing import Any

from telethon import TelegramClient, events
from telethon.tl import types as tl_types

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
# Фрази, якими «палиться» ШІ: канцелярит, assistant-стартери, звернення-прощання.
_AI_TELLS_PATTERNS = (
    re.compile(r"^\s*(Сподіва(ємся|юсь|юся)[^\n.!?\n]*[.!?\n]?\s*|"
               r"(Радий|Готовий|Рада|Готова) (допомогти|відповісти)[^\n.!?\n]*[.!?\n]?\s*|"
               r"(Гарне|Чудове) запитання[.!]?\s*|"
               r"Звісно[,!.]?\s*|Звичайно[,!.]?\s*|"
               r"Дава(й|йте) роз(бер|глянем|беремо|глядемо)[^\n.!?\n]*[.!?\n]?\s*|"
               r"(Резюмуючи|Підсумовуючи)[^\n.:!?\n]*[:.]?\s*|"
               r"Отже[,;.]\s*|Висновок[:.]?\s*|У висновку[,:]?\s*)", re.IGNORECASE),
    re.compile(r"\b(резюмуючи|підсумовуючи)\b[^\n.:!?]*[:.]?\s*", re.IGNORECASE),
    re.compile(r"\b(як штучний інтелект|як ШІ|як мовна модель|як асистент)\b", re.IGNORECASE),
    re.compile(r"\s*\b(не соромтеся звертатись|не соромся звертатись|звертайтеся якщо|"
               r"звертайся якщо|якщо будуть питання|якщо виникнуть питання)[^\n]*", re.IGNORECASE),
)
# Довге тире « — » у телеграм-балачці видає ШІ: живі люди майже не пишуть ем-дефіси.
_EM_DASH_RE = re.compile(r"\s*[—–]\s*")
TYPING_CHARS_PER_SECOND = 15.0
TYPING_MIN_SECONDS = 1.0
TYPING_MAX_SECONDS = 6.0
ERROR_NOTIFY_INTERVAL = 600.0  # не спамити адміна частіше ніж раз на 10 хв

# --- Burst-логіка: людина часто пише кілька повідомлень підряд ---
DEBOUNCE_SECONDS = 2.0      # скільки чекати тиші перед відповіддю
TYPING_GRACE = 2.5          # скільки рахувати «ще друкує» після останьої дії
MAX_BURST_WAIT = 30.0       # стеля очікування, щоб не зависати назавжди
BURST_REPLY_THRESHOLD = 3   # ≥3 повідомлень — контекст зрозумілий, відповідаємо раніше

MEDIA_PLACEHOLDERS: tuple[tuple[str, str], ...] = (
    ("sticker", "[стикер]"),
    ("photo", "[фото]"),
    ("voice", "[голосове]"),
    ("video_note", "[відео-повідомлення]"),
    ("video", "[відео]"),
    ("gif", "[гіф]"),
    ("audio", "[аудіо]"),
)

KB_SECTION_HEADER = "БАЗА ЗНАНЬ (правила поведінки, факти про мене)"


def media_placeholder(message: Any) -> str | None:
    """Короткий опис медіа-повідомлення (стікер, фото...) або None для тексту."""
    for attr, label in MEDIA_PLACEHOLDERS:
        if getattr(message, attr, None):
            return label
    return "[медіа]"


def strip_ai_tells(text: str) -> str:
    """Прибрати «відкриття-помічника», канцелярит і штучні ем-дефіси."""
    # старт-фрази можуть іти поспіль («Сподіваюсь,... Давай розберемо...»)
    for _ in range(3):
        new = _AI_TELLS_PATTERNS[0].sub("", text)
        if new == text:
            break
        text = new
    for pattern in _AI_TELLS_PATTERNS[1:]:
        text = pattern.sub("", text)
    # « — » → три крапки з пробілами; кінцеві пробіли прибирає нормалізація
    text = _EM_DASH_RE.sub(" ... ", text)
    # зайві пропуски після чисток
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(" \t\n,;")


def strip_markdown_artifacts(text: str) -> str:
    """Прибрати markdown-артефакти, дислеймери про ШІ та «шІ-зманери»."""
    for old, new in _MD_ARTIFACTS:
        text = text.replace(old, new)
    text = _DISCLAIMER_RE.sub("", text)
    text = strip_ai_tells(text)
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


def format_context(pairs: list[tuple[bool, str, str | None]], max_messages: int = 20) -> str:
    """Історія → текст з мітками, часом і «Я:»/«Співрозмовник:» (хронологічно, останні N).

    ``pairs`` — список (is_outgoing, text, time_label) у хронологічному порядку;
    ``time_label`` — «HH:MM» або None. Час дає моделі опору: «я подзвоню за 5 хв»
    має сенс тільки якщо відомо, котра зараз година.
    """
    recent = [p for p in pairs if p[1] and p[1].strip()][-max_messages:]
    lines = []
    for out, text, time_label in recent:
        stamp = f"[{time_label}] " if time_label else ""
        lines.append(f"{stamp}{OWNER_LABEL if out else OTHER_LABEL} {text.strip()}")
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
        # burst-стан: накопичені повідомлення чату + активні задачі дебаунсу
        self._pending: dict[int, list[str]] = {}
        self._debounce_tasks: dict[int, asyncio.Task[None]] = {}
        self._typing_until: dict[int, float] = {}  # chat_id -> до коли «друкує»
        # кеш об'єктів чатів (мережевий get_chat — раз на 5 хв на чат)
        self.CHAT_CACHE_TTL = 300.0
        self._chat_cache: dict[int, tuple[Any, float]] = {}

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
        self.client.add_event_handler(self._on_user_update, events.UserUpdate)
        return True

    async def stop(self) -> None:
        for task in list(self._debounce_tasks.values()):
            task.cancel()
        self._debounce_tasks.clear()
        self._pending.clear()
        self._typing_until.clear()
        self._cooldowns.clear()
        self._chat_cache.clear()
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
        """Вхідне повідомлення → фільтри → черга burst → (пауза тиші) → одна відповідь."""
        message = event.message
        if message.out or getattr(message, "action", None) is not None:
            return
        if self._owner_user_id is None or message.sender_id == self._owner_user_id:
            return

        try:
            user_id = self._owner_user_id
            settings = await self._db.get_settings(user_id)
            if not settings["auto_reply"]:
                return
            if not await self._passes_filters(message, settings):
                return

            # текст або зрозумілий плейсхолдер для медіа (стікер/фото/голосове…)
            text = (message.message or "").strip()
            if not text:
                text = media_placeholder(message)

            chat_id = message.chat_id
            self._pending.setdefault(chat_id, []).append(text)
            # нове повідомлення скидає таймер тиші: попередню задачу — скасувати
            old_task = self._debounce_tasks.get(chat_id)
            if old_task is not None and not old_task.done():
                old_task.cancel()
            self._debounce_tasks[chat_id] = asyncio.create_task(
                self._debounced_reply(chat_id, user_id, settings),
            )
        except Exception as exc:  # noqa: BLE001 — не даємо обробнику впасти
            await self._record_error(getattr(message, "chat_id", 0), f"{type(exc).__name__}: {exc}")

    async def _on_user_update(self, event: Any) -> None:
        """Співрозмовник друкує → продовжуємо чекати (максимум MAX_BURST_WAIT)."""
        try:
            if not getattr(event, "typing", False):
                return
            chat_id = getattr(event, "chat_id", None) or getattr(event, "user_id", None)
            if chat_id in self._pending:
                self._typing_until[chat_id] = time.monotonic() + TYPING_GRACE
        except Exception:  # noqa: BLE001 — подія «друкує» не повинна падати в диспетчер
            logger.exception("Помилка обробки UserUpdate")

    async def _debounced_reply(self, chat_id: int, user_id: int, settings: dict[str, Any]) -> None:
        """Чекає тишу (і паузи «друкує»), потім ОДНА відповідь на весь burst.

        Виняток: якщо накопичилось BURST_REPLY_THRESHOLD повідомлень — відповідаємо
        одразу (з невеликою паузою), бо контекст уже достатній.
        """
        try:
            deadline = time.monotonic() + MAX_BURST_WAIT
            while True:
                await asyncio.sleep(DEBOUNCE_SECONDS)
                now = time.monotonic()
                if now >= deadline:
                    break
                # ≥3 повідомлень — контекст зрозумілий, не змушуємо чекати
                if len(self._pending.get(chat_id, [])) >= BURST_REPLY_THRESHOLD:
                    break
                # людина друкує? зачекаемо ще одне вікно
                if now < self._typing_until.get(chat_id, 0.0):
                    continue
                break

            burst = self._pending.pop(chat_id, [])
            self._debounce_tasks.pop(chat_id, None)
            if not burst:
                return
            if not settings["auto_reply"]:
                return
            if not self._cooldown_ok(chat_id, int(settings["cooldown_seconds"])):
                return

            reply = await self._generate_reply(user_id, settings, chat_id, burst)
            if not reply:
                return
            await self._humanized_send(chat_id, reply, settings)
            # кулдаун стартує лише після УСПІШНОГО надсилання: якщо send_message
            # впав — наступне повідомлення не блокується «порожнім» кулдауном
            self._cooldowns[chat_id] = time.monotonic()
            self._typing_until.pop(chat_id, None)  # не накопичуємо застарілі записи
            await self._record_qa_entry(user_id, chat_id, burst, reply)
        except LLMError as exc:
            await self._record_error(chat_id, f"LLM: {exc}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            await self._record_error(chat_id, f"{type(exc).__name__}: {exc}")

    # --- фільтри ---

    async def _get_chat_cached(self, message: Any) -> Any | None:
        """get_chat() з кешем на 5 хвилин: мережевий виклик — раз на чат, не на кожне повідомлення серії."""
        chat_id = message.chat_id
        now = time.monotonic()
        cached = self._chat_cache.get(chat_id)
        if cached is not None and now - cached[1] < self.CHAT_CACHE_TTL:
            return cached[0]
        try:
            chat = await message.get_chat()
        except Exception:  # noqa: BLE001 — чат недоступний → не ризикуємо
            return None
        self._chat_cache[chat_id] = (chat, now)
        if len(self._chat_cache) > 500:  # захист від безмежного росту
            stale = sorted(self._chat_cache.items(), key=lambda kv: kv[1][1])[:100]
            for key, _ in stale:
                self._chat_cache.pop(key, None)
        return chat

    async def _passes_filters(self, message: Any, settings: dict[str, Any]) -> bool:
        # message.chat — це Peer без username; повний об'єкт беремо через get_chat()
        chat = await self._get_chat_cached(message)
        if chat is None:
            return False
        username = getattr(chat, "username", None)
        chat_id = str(chat.id if chat is not None else message.chat_id)
        entries = {str(e).strip().lstrip("@").lower() for e in settings["ignore_list"] + settings["whitelist_chats"]}

        candidates = {chat_id.lower(), str(message.chat_id).lower()}
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
            # Telethon Message не має атрибута is_private — приватність визначаємо
            # за типом чату: особистий чат — це завжди User (не Chat/Channel).
            is_private = isinstance(chat, tl_types.User)
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
        pairs: list[tuple[bool, str, str | None]] = []
        today = datetime.now().astimezone().date()
        for msg in reversed(messages):  # get_messages повертає від нових до старих
            text = (msg.message or "").strip() if msg.message else ""
            if text:
                time_label = None
                try:
                    local_date = msg.date.astimezone().date()
                    if local_date == today:
                        time_label = msg.date.astimezone().strftime("%H:%M")
                    else:
                        # день обов'язковий: промпт просить модель розрізняти
                        # «старий контекст» (інші дні) і сьогоднішні повідомлення
                        time_label = msg.date.astimezone().strftime("%d.%m %H:%M")
                except Exception:  # noqa: BLE001 — без часу теж ок
                    pass
                pairs.append((bool(msg.out), text, time_label))
        return format_context(pairs, max_messages=limit)

    def _system_prompt(self, base_prompt: str, style_profile: str) -> str:
        system = base_prompt or DEFAULT_BASE_PROMPT
        if "{style_profile}" in system:
            system = system.replace("{style_profile}", style_profile or "—")
        elif style_profile:
            system += f"\n\nПрофіль стилю:\n{style_profile}"
        return system

    async def _generate_reply(self, user_id: int, settings: dict[str, Any],
                              chat_id: int, burst: list[str]) -> str:
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
            max_tokens=self._config.llm_max_tokens,
        )
        prompts = await self._db.get_prompts(user_id)
        context = await self._build_context(chat_id, int(settings["context_messages"]))
        # пер-чат профіль має пріоритет над глобальним
        chat_profile = await self._db.get_chat_profile(user_id, str(chat_id))
        style = chat_profile or prompts["style_profile"]
        system = self._system_prompt(prompts["base_prompt"], style)
        kb_text = await self._db.get_full_kb(user_id)
        if kb_text:
            system += (
                f"\n\n{'=' * 12} {KB_SECTION_HEADER} {'=' * 12}\n{kb_text}\n{'=' * 12}"
            )
        latest = "\n".join(f"• {item}" for item in burst)
        current_time = datetime.now().strftime("%H:%M")
        user_content = (
            f"Історія діалогу (у квадратних дужках — місцевий час):\n{context}\n\n"
            f"Зараз: [{current_time}]\n\n"
            "ЯК АНАЛІЗУВАТИ КОНТЕКСТ:\n"
            "1. Подивись на часові мітки (сьогоднішні — [ГГ:ХХ], давніші — "
            "[ДД.ММ ГГ:ХХ]): повідомлення з інших днів або давно "
            "завершені теми — це СТАРИЙ КОНТЕКСТ. Тримай його на увазі (згадай, "
            "якщо співрозмовник повертається до нього), але не відповідай на нього.\n"
            "2. Останні повідомлення співрозмовника порівняй зі старим контекстом: "
            "якщо це ПРОДОВЖЕННЯ попередньої теми — відповідай у її руслі, "
            "як НОВА ТЕМА — відповідай на нову тему, не тягни стару.\n"
            "3. Відповідай природно, як жива людина: коротко, без офіційності, "
            "варіюй формулювання. Не переказуй питання — відповідай на нього.\n\n"
            f"Щойно надійшло від співрозмовника (може бути кілька повідомлень підряд — "
            f"прочитай їх як одне ціле і відповіси на все разом):\n{latest}\n\n"
            "Напиши МОЮ відповідь (лише текст повідомлення, без лапок і пояснень). "
            "Одна відповідь на весь блок, не окремо на кожне повідомлення."
        )
        return await llm.chat([{"role": "user", "content": user_content}], system=system)

    # --- «людське» надсилання ---

    async def _humanized_send(self, chat_id: int, reply: str, settings: dict[str, Any]) -> None:
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
                await self.client.send_chat_action(chat_id, "typing")
            except Exception:  # noqa: BLE001 — chat_action не критичний
                logger.debug("send_chat_action не вдався", exc_info=True)
            await asyncio.sleep(typing_seconds)
            await self.client.send_message(chat_id, chunk)
            if index < len(chunks) - 1:
                await asyncio.sleep(random.uniform(0.8, 2.5))  # noqa: S311 — пауза між частинами

    # --- журнал Q&A ---

    async def _record_qa_entry(self, user_id: int, chat_id: int,
                               burst: list[str], reply: str) -> None:
        """Записати запис журналу «питання-відповідь» після успішної відповіді.

        Кожна нова відповідь — окремий запис (burst не зливаються з попередніми).
        """
        try:
            chat_ref = str(chat_id)
            chat_title = ""
            if self.client is not None:
                try:
                    chat = await self.client.get_entity(chat_id)
                    chat_title = (
                        getattr(chat, "title", None)
                        or getattr(chat, "first_name", None)
                        or ""
                    )
                    username = getattr(chat, "username", None)
                    if username:
                        chat_ref = f"@{username}"
                except Exception:  # noqa: BLE001 — назва чату не критична
                    logger.debug("Не вдалося отримати назву чату %s", chat_id, exc_info=True)
            await self._db.add_qa_entry(
                user_id, chat_ref, chat_title, "\n".join(burst), reply,
            )
        except Exception:  # noqa: BLE001 — журнал не повинен ламати відповідь
            logger.exception("Не вдалося записати запис журналу Q&A")

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