"""Контрольний бот (Bot API, aiogram 3.x): меню, FSM-діалоги, налаштування.

Всі тексти — у :mod:`tg_hub.texts`, клавіатури — у :mod:`tg_hub.keyboards`.
Будь-який виняток у обробнику перетворюється на коротке українське
повідомлення адміну (декоратор :func:`error_guard`) — процес не падає.
"""
from __future__ import annotations

import asyncio
import functools
import html
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

import segno
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter, Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from telethon import TelegramClient
from telethon import errors as telethon_errors

from . import keyboards as kb
from . import texts
from .config import Config
from .db import Database
from .llm import create_client
from .llm.probe import ProbeError, probe_key, verify_model
from .persona import DEFAULT_BASE_PROMPT, generate_style_profile
from .persona.analyzer import generate_chat_profile
from .reminders import format_due, parse_reminder_time, serialize_snapshot
from .userbot import Userbot

logger = logging.getLogger(__name__)

DIALOGS_LIMIT = 25
ALL_DIALOGS_LIMIT = 50
MASKED_KEY_TAIL = 4

LIST_IGNORE = "ignore_list"
LIST_WHITELIST = "whitelist_chats"


class ApiKeyStates(StatesGroup):
    base_url = State()
    api_key = State()
    model = State()


class ConnectStates(StatesGroup):
    api_id = State()
    api_hash = State()
    phone = State()
    code = State()
    password = State()


class QrStates(StatesGroup):
    api_id = State()
    api_hash = State()


class PromptStates(StatesGroup):
    editing = State()


class ListStates(StatesGroup):
    adding = State()


class NumberStates(StatesGroup):
    waiting = State()


class RemindStates(StatesGroup):
    text = State()
    time = State()


class KbStates(StatesGroup):
    content = State()


class ProfileStates(StatesGroup):
    name = State()


class NextPromptStates(StatesGroup):
    text = State()


class AdminFilter(BaseFilter):
    """Пропускає лише адміністраторів з ADMIN_IDS."""

    def __init__(self, admin_ids: tuple[int, ...]) -> None:
        self.admin_ids = frozenset(admin_ids)

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user) and user.id in self.admin_ids  # type: ignore[union-attr]


_MENU_TEXTS = frozenset({
    texts.BTN_API_KEYS, texts.BTN_CONNECT, texts.BTN_PROMPT, texts.BTN_STYLE,
    texts.BTN_AUTOREPLY, texts.BTN_STATUS, texts.BTN_REMINDERS, texts.BTN_PROFILES,
    texts.BTN_BACK, texts.BTN_CANCEL,
})


class NotMenuText(BaseFilter):
    """Не пропускає кнопки головного меню.

    FSM-обробники «зловити все» не повинні з'їдати натискання кнопок меню:
    інакше в стані (наприклад RemindStates.text) кнопка «⚙️ Налаштування»
    зберігається як текст нагадування, а меню не відкривається.
    """

    async def __call__(self, message: Message) -> bool:
        return message.text not in _MENU_TEXTS


Handler = Callable[..., Awaitable[Any]]


async def _notify_error(event: Message | CallbackQuery, text: str) -> None:
    """Повідомити про помилку і зняти «годинник» завантаження на callback-кнопці."""
    target = getattr(event, "message", None) or event
    try:
        await target.answer(text)  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        logger.exception("Не вдалося надіслати повідомлення про помилку")
    if isinstance(event, CallbackQuery):
        try:
            await event.answer()  # прибираємо спінер, якщо адмін ще чекає
        except Exception:  # noqa: BLE001
            pass


def error_guard(handler: Handler) -> Handler:
    """Ловить будь-який виняток і повідомляє адміна коротким українським текстом."""

    @functools.wraps(handler)
    async def wrapper(event: Message | CallbackQuery, *args: Any, **kwargs: Any) -> Any:
        try:
            return await handler(event, *args, **kwargs)
        except TelegramAPIError:
            logger.exception("Помилка Telegram API в обробнику %s", handler.__name__)
            await _notify_error(event, texts.ERR_GENERIC)
        except Exception:  # noqa: BLE001 — головний захист від падіння процесу
            logger.exception("Помилка в обробнику %s", handler.__name__)
            await _notify_error(event, texts.ERR_GENERIC)

    return wrapper


def mask_key(api_key: str) -> str:
    """Маскувати ключ: показуємо лише останні 4 символи."""
    return f"…{api_key[-MASKED_KEY_TAIL:]}" if api_key else "…"


class ControlBot:
    """Збирає Router з усіма обробниками; володіє посиланнями на db/userbot."""

    def __init__(self, config: Config, db: Database, userbot: Userbot) -> None:
        self._config = config
        self._db = db
        self._userbot = userbot
        self._pending_clients: dict[int, TelegramClient] = {}
        self._qr_tasks: dict[int, asyncio.Task[None]] = {}
        self._entity_cache: dict[str, Any] = {}  # chat_ref -> InputPeer з get_dialogs
        self.router = Router(name="control")
        admin = AdminFilter(config.admin_ids)
        self.router.message.filter(admin)
        self.router.callback_query.filter(admin)
        self._register_handlers()

    # ------------------------------------------------------------------ утиліти

    async def _maybe_autoset_active(self, user_id: int, key_id: int) -> None:
        """Перший доданий ключ автоматично стає активним."""
        keys = await self._db.list_llm_keys(user_id)
        if all(not k["is_active"] for k in keys):
            await self._db.set_active_key(user_id, key_id)

    @staticmethod
    async def _safe_edit(message: Message, text: str,
                         reply_markup: InlineKeyboardMarkup | None = None) -> None:
        """edit_text із fallback: повідомлення старше 48 год уже не редагується."""
        try:
            await message.edit_text(text, reply_markup=reply_markup)
        except TelegramAPIError:
            await message.answer(text, reply_markup=reply_markup)

    @staticmethod
    async def _safe_edit_markup(message: Message, reply_markup: InlineKeyboardMarkup) -> None:
        """Оновити клавіатуру; для повідомлень старше 48 год — тихо пропустити."""
        try:
            await message.edit_reply_markup(reply_markup=reply_markup)
        except TelegramAPIError:
            logger.debug("Не вдалося оновити клавіатуру повідомлення", exc_info=True)

    async def _require_userbot(self, event: Message | CallbackQuery) -> bool:
        if not self._userbot.connected:
            await event.answer(texts.ERR_NOT_CONNECTED)  # type: ignore[union-attr]
            return False
        return True

    async def _list_dialogs(self, limit: int = DIALOGS_LIMIT) -> list[tuple[int, str, str]]:
        client = self._userbot.client
        assert client is not None
        dialogs = await client.get_dialogs(limit=limit)
        result: list[tuple[int, str, str]] = []
        for dialog in dialogs:
            title = (dialog.name or "Без назви")[:40]
            chat_id = dialog.id
            # кешуємо input_entity: get_entity(рядок-ID) падає, якщо сутності
            # немає в кеші сесії, а свіжі get_dialogs їх гарантовано мають
            try:
                self._entity_cache[str(chat_id)] = await dialog.get_input_entity()
            except Exception:  # noqa: BLE001 — фолбек на get_entity в місці виклику
                logger.debug("Не вдалося кешувати input_entity для %s", chat_id, exc_info=True)
            result.append((chat_id, f"{title} ({chat_id})", str(chat_id)))
        return result

    async def _resolve_chat_ref(self, ref: str) -> Any:
        """Перетворити рядок-референс на те, що iter_messages приймає надійно."""
        client = self._userbot.client
        assert client is not None
        cached = self._entity_cache.get(ref)
        if cached is not None:
            return cached
        return await client.get_entity(int(ref) if ref.lstrip("-").isdigit() else ref)

    async def _run_style_analysis(self, user_id: int, chat_refs: list[str],
                                  status_msg: Message) -> None:
        """Згенерувати профіль стилю і зберегти його."""
        key = await self._db.get_active_key(user_id)
        if key is None:
            await status_msg.answer(texts.ERR_NO_ACTIVE_KEY)
            return
        llm = create_client(
            provider=key["provider"], base_url=key["base_url"],
            api_key=key["api_key"], model=key["model"],
            timeout=self._config.llm_timeout,
            max_tokens=1500,  # профіль — довгий текст, ліміт лише для чат-відповідей
        )
        assert self._userbot.client is not None
        resolved: list[Any] = []
        resolved_refs: list[str] = []
        failed: list[str] = []
        for ref in chat_refs:
            try:
                resolved.append(await self._resolve_chat_ref(ref))
                resolved_refs.append(ref)
            except Exception:  # noqa: BLE001 — окремий чат не знайдено, збираємо решту
                logger.warning("Чат %s недоступний для аналізу", ref, exc_info=True)
                failed.append(ref)
        if not resolved:
            await status_msg.answer(texts.ERR_GENERIC)
            return
        profile = await generate_style_profile(
            self._userbot.client, llm, resolved,
            limit_per_chat=self._config.analysis_limit,
        )
        await self._db.set_style_profile(user_id, profile)
        for ref in resolved_refs:
            await self._db.mark_analyzed(user_id, ref)
        await status_msg.answer(texts.ANALYSIS_DONE, reply_markup=kb.style_menu())

    # ------------------------------------------------------------------ головне меню

    def _register_handlers(self) -> None:
        r = self.router

        @r.message(CommandStart())
        @error_guard
        async def start(message: Message, state: FSMContext) -> None:
            await state.clear()
            await self._db.ensure_user(message.from_user.id)
            await message.answer(texts.WELCOME, reply_markup=kb.main_menu())

        @r.message(F.text == texts.BTN_BACK)
        @error_guard
        async def back_to_menu(message: Message, state: FSMContext) -> None:
            await state.clear()
            await message.answer(texts.WELCOME, reply_markup=kb.main_menu())

        @r.message(Command("cancel"))
        @r.message(F.text == texts.BTN_CANCEL)
        @error_guard
        async def cancel_any(message: Message, state: FSMContext) -> None:
            # Універсальний вихід із ЛЮБОГО FSM-стану — /cancel або кнопка «❌ Скасувати».
            await state.clear()
            await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())

        @r.message(F.text == texts.BTN_STATUS)
        @error_guard
        async def status(message: Message, state: FSMContext) -> None:
            await state.clear()  # інакше FSM «застрягне»: наступний текст піде в активний діалог
            user_id = message.from_user.id
            account = await self._db.get_account(user_id)
            keys = await self._db.list_llm_keys(user_id)
            active = await self._db.get_active_key(user_id)
            settings = await self._db.get_settings(user_id)
            account_status = (
                texts.STATUS_CONNECTED.format(phone=account["phone"])
                if account and self._userbot.connected else texts.STATUS_NOT_CONNECTED
            )
            body = "\n".join([
                texts.STATUS_ACCOUNT.format(status=account_status),
                texts.STATUS_KEYS.format(
                    count=len(keys),
                    active=f"#{active['id']}" if active else texts.STATUS_NO_ACTIVE_KEY,
                ),
                texts.STATUS_AUTOREPLY.format(
                    state=texts.STATE_ON if settings["auto_reply"] else texts.STATE_OFF
                ),
                texts.STATUS_LAST_ERROR.format(
                    error=settings["last_error"] or texts.STATUS_NO_ERRORS
                ),
            ])
            await message.answer(texts.STATUS_HEADER.format(body=body), reply_markup=kb.main_menu())

        # ------------------------------------------------------ API ключі

        @r.message(F.text == texts.BTN_API_KEYS)
        @error_guard
        async def api_keys_menu(message: Message, state: FSMContext) -> None:
            await state.clear()
            await message.answer(texts.API_KEYS_MENU, reply_markup=kb.api_keys_menu())

        @r.callback_query(F.data == "key:add")
        @error_guard
        async def key_add(cb: CallbackQuery, state: FSMContext) -> None:
            await self._safe_edit(cb.message, texts.CHOOSE_PROVIDER, kb.provider_choice())  # type: ignore[arg-type]
            await cb.answer()

        @r.callback_query(F.data.startswith("keyprov:"))
        @error_guard
        async def key_provider_chosen(cb: CallbackQuery, state: FSMContext) -> None:
            parts = cb.data.split(":", 2)  # keyprov:<provider>[:<base_url>]
            provider = parts[1]
            await state.update_data(provider=provider)
            if len(parts) == 3 and parts[2]:
                # пресет: base_url вже відомий → одразу ключ
                await state.update_data(base_url=parts[2].rstrip("/"))
                await state.set_state(ApiKeyStates.api_key)
                await self._safe_edit(cb.message, texts.ASK_API_KEY, kb.cancel_inline())  # type: ignore[arg-type]
            elif provider == "openai":
                await state.set_state(ApiKeyStates.base_url)
                await self._safe_edit(cb.message, texts.ASK_BASE_URL, kb.cancel_inline())  # type: ignore[arg-type]
            else:
                await state.set_state(ApiKeyStates.api_key)
                await self._safe_edit(cb.message, texts.ASK_API_KEY, kb.cancel_inline())  # type: ignore[arg-type]
            await cb.answer()

        @r.message(NotMenuText(), ApiKeyStates.base_url)
        @error_guard
        async def key_base_url(message: Message, state: FSMContext) -> None:
            url = (message.text or "").strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                url = f"https://{url}"  # користувач забув схему — підставляємо
            if "." not in url.split("//", 1)[-1]:
                await message.answer(
                    "⚠️ Це не схоже на URL. Надішліть адресу, наприклад "
                    "https://api.openai.com/v1",
                )
                return
            await state.update_data(base_url=url)
            await state.set_state(ApiKeyStates.api_key)
            await message.answer(texts.ASK_API_KEY, reply_markup=kb.cancel_inline())

        @r.message(NotMenuText(), ApiKeyStates.api_key)
        @error_guard
        async def key_api_key(message: Message, state: FSMContext) -> None:
            api_key = (message.text or "").strip()
            if len(api_key) < 8:
                await message.answer("⚠️ Ключ виглядає занадто коротким. Спробуйте ще раз.")
                return
            await state.update_data(api_key=api_key)
            data = await state.get_data()
            wait = await message.answer("⏳ Перевіряю ключ і збираю доступні моделі…")
            try:
                models = await probe_key(data["provider"], data.get("base_url", ""), api_key)
            except ProbeError as exc:
                await wait.delete()
                await message.answer(str(exc))
                return  # лишаємося в стані api_key — можна ввести інший ключ
            except Exception:
                logger.exception("probe_key мережева помилка")
                await wait.delete()
                await message.answer(
                    "⚠️ Не вдалося зв'язатися з провайдером для перевірки ключа. "
                    "Спробуйте ще раз або перевірте інтернет.",
                )
                return
            await wait.delete()
            await state.update_data(models=models)
            await message.answer(
                texts.MODELS_FOUND.format(count=len(models)),
                reply_markup=kb.model_choice(models),
            )

        @r.callback_query(F.data.startswith("keymodel:"))
        @error_guard
        async def key_model_chosen(cb: CallbackQuery, state: FSMContext) -> None:
            chosen = cb.data.split(":", 1)[1]
            if chosen == "__manual__":
                await state.set_state(ApiKeyStates.model)
                await cb.message.answer(  # type: ignore[union-attr]
                    texts.ASK_MODEL, reply_markup=kb.cancel_inline(),
                )
                await cb.answer()
                return
            data = await state.get_data()
            wait = await cb.message.answer(  # type: ignore[union-attr]
                texts.MODEL_VERIFYING.format(model=chosen),
            )
            try:
                warning = await verify_model(
                    data["provider"], data.get("base_url", ""), data["api_key"], chosen
                )
            except ProbeError as exc:
                await wait.delete()
                await cb.answer()
                await cb.message.answer(str(exc))  # type: ignore[union-attr]
                return
            except Exception:
                logger.exception("verify_model мережева помилка")
                await wait.delete()
                await cb.answer()
                await cb.message.answer(  # type: ignore[union-attr]
                    "⚠️ Не вдалося зв'язатися з провайдером для перевірки моделі. Спробуйте ще раз.",
                )
                return
            await wait.delete()
            key_id = await self._db.add_llm_key(
                user_id=cb.from_user.id,
                provider=data["provider"],
                label=data.get("base_url", ""),
                base_url=data.get("base_url"),
                api_key=data["api_key"],
                model=chosen,
            )
            await state.clear()
            await self._maybe_autoset_active(cb.from_user.id, key_id)
            provider_name = {"openai": "OpenAI", "anthropic": "Anthropic", "google": "Google"}.get(
                data["provider"], data["provider"]
            )
            await cb.message.answer(  # type: ignore[union-attr]
                texts.KEY_ADDED_ACTIVE.format(key_id=key_id, provider=provider_name, model=chosen)
                + (f"\n\n{warning}" if warning else ""),
                reply_markup=kb.api_keys_menu(),
            )
            await cb.answer()

        @r.message(NotMenuText(), ApiKeyStates.model)
        @error_guard
        async def key_model(message: Message, state: FSMContext) -> None:
            model = (message.text or "").strip()
            if not model:
                await message.answer("⚠️ Назва моделі не може бути порожньою.")
                return
            data = await state.get_data()
            wait = await message.answer(texts.MODEL_VERIFYING.format(model=model))
            try:
                warning = await verify_model(
                    data["provider"], data.get("base_url", ""), data["api_key"], model
                )
            except ProbeError as exc:
                await wait.delete()
                await message.answer(str(exc))
                return  # лишаємося в стані model — можна ввести іншу назву
            except Exception:
                logger.exception("verify_model мережева помилка")
                await wait.delete()
                await message.answer(
                    "⚠️ Не вдалося зв'язатися з провайдером для перевірки моделі. Спробуйте ще раз.",
                )
                return
            await wait.delete()
            key_id = await self._db.add_llm_key(
                user_id=message.from_user.id,
                provider=data["provider"],
                label=data.get("base_url", ""),
                base_url=data.get("base_url"),
                api_key=data["api_key"],
                model=model,
            )
            await state.clear()
            await self._maybe_autoset_active(message.from_user.id, key_id)
            provider_name = {"openai": "OpenAI", "anthropic": "Anthropic", "google": "Google"}.get(
                data["provider"], data["provider"]
            )
            await message.answer(
                texts.KEY_ADDED_ACTIVE.format(key_id=key_id, provider=provider_name, model=model)
                + (f"\n\n{warning}" if warning else ""),
                reply_markup=kb.api_keys_menu(),
            )

        @r.callback_query(F.data == "key:list")
        @error_guard
        async def key_list_cb(cb: CallbackQuery) -> None:
            keys = await self._db.list_llm_keys(cb.from_user.id)
            if not keys:
                await self._safe_edit(cb.message, texts.KEY_LIST_EMPTY, kb.api_keys_menu())  # type: ignore[arg-type]
            else:
                listing = "\n".join(
                    texts.KEY_LABEL_FMT.format(
                        key_id=k["id"], provider=k["provider"], model=k["model"],
                        tail=mask_key(k["api_key"]),
                        active_mark=" ⭐" if k["is_active"] else "",
                    )
                    for k in keys
                )
                await self._safe_edit(  # type: ignore[arg-type]
                    cb.message,
                    f"📋 <b>Ключі</b> (⭐ = активний):\n{listing}",
                    kb.key_list(keys),
                )
            await cb.answer()

        @r.callback_query(F.data.startswith("key:activate:"))
        @error_guard
        async def key_activate(cb: CallbackQuery) -> None:
            key_id = int(cb.data.split(":")[2])
            if await self._db.set_active_key(cb.from_user.id, key_id):
                await cb.answer(texts.KEY_ACTIVATE_DONE.format(key_id=key_id), show_alert=True)
            else:
                await cb.answer("Ключ не знайдено", show_alert=True)
            keys = await self._db.list_llm_keys(cb.from_user.id)
            await self._safe_edit_markup(cb.message, kb.key_list(keys))  # type: ignore[arg-type]

        @r.callback_query(F.data.startswith("key:delete:"))
        @error_guard
        async def key_delete(cb: CallbackQuery) -> None:
            key_id = int(cb.data.split(":")[2])
            await self._db.delete_llm_key(cb.from_user.id, key_id)
            await cb.answer(texts.KEY_DELETED.format(key_id=key_id), show_alert=True)
            keys = await self._db.list_llm_keys(cb.from_user.id)
            await self._safe_edit_markup(cb.message, kb.key_list(keys))  # type: ignore[arg-type]

        # ------------------------------------------------------ підключення акаунта

        @r.message(F.text == texts.BTN_CONNECT)
        @error_guard
        async def connect_menu(message: Message, state: FSMContext) -> None:
            await state.clear()
            account = await self._db.get_account(message.from_user.id)
            status_text = (
                texts.STATUS_CONNECTED.format(phone=account["phone"])
                if account and self._userbot.connected else texts.STATUS_NOT_CONNECTED
            )
            await message.answer(
                texts.CONNECT_MENU.format(status=status_text),
                reply_markup=kb.connect_menu(),
            )

        @r.callback_query(F.data == "connect:start")
        @error_guard
        async def connect_start(cb: CallbackQuery, state: FSMContext) -> None:
            await state.set_state(ConnectStates.api_id)
            await cb.message.answer(  # type: ignore[union-attr]
                texts.ASK_API_ID, reply_markup=kb.cancel_inline(),
            )
            await cb.answer()

        @r.message(NotMenuText(), ConnectStates.api_id)
        @error_guard
        async def connect_api_id(message: Message, state: FSMContext) -> None:
            raw = (message.text or "").strip()
            if not raw.isdigit():
                await message.answer(texts.ERR_INVALID_API_ID)
                return
            await state.update_data(api_id=int(raw))
            await state.set_state(ConnectStates.api_hash)
            await message.answer(texts.ASK_API_HASH, reply_markup=kb.cancel_inline())

        @r.message(NotMenuText(), ConnectStates.api_hash)
        @error_guard
        async def connect_api_hash(message: Message, state: FSMContext) -> None:
            api_hash = (message.text or "").strip()
            if len(api_hash) < 30:
                await message.answer("⚠️ api_hash виглядає некоректно (зазвичай 32 символи).")
                return
            await state.update_data(api_hash=api_hash)
            await state.set_state(ConnectStates.phone)
            await message.answer(texts.ASK_PHONE, reply_markup=kb.cancel_inline())

        @r.message(NotMenuText(), ConnectStates.phone)
        @error_guard
        async def connect_phone(message: Message, state: FSMContext) -> None:
            phone = (message.text or "").strip()
            if not phone.startswith("+"):
                await message.answer("⚠️ Номер має бути у форматі +380...")
                return
            data = await state.get_data()
            user_id = message.from_user.id
            self._config.sessions_dir.mkdir(parents=True, exist_ok=True)
            session_path = self._config.sessions_dir / f"{user_id}.session"
            client = TelegramClient(
                str(session_path), data["api_id"], data["api_hash"],
                device_model="TelegramHub",
            )
            await client.connect()
            try:
                await client.send_code_request(phone)
            except telethon_errors.ApiIdInvalidError:
                await client.disconnect()
                self._pending_clients.pop(user_id, None)
                await state.clear()
                await message.answer(
                    "❌ Telegram відхилив пару api_id/api_hash — значення недійсні.\n\n"
                    "Перевірте на my.telegram.org/apps і почніть заново: "
                    "📱 Підключити акаунт → 🔌 Підключити.",
                    reply_markup=kb.connect_menu(),
                )
                return
            except telethon_errors.FloodWaitError as exc:
                await client.disconnect()
                self._pending_clients.pop(user_id, None)
                await state.clear()
                await message.answer(
                    f"⏳ Telegram обмежив частоту запитів коду. "
                    f"Спробуйте ще раз через {exc.seconds // 60 + 1} хв.",
                    reply_markup=kb.connect_menu(),
                )
                return
            except Exception:
                # неочікувана помилка: не залишаємо «вічно живий» клієнт і не тримаємо FSM
                await client.disconnect()
                await state.clear()
                raise
            self._pending_clients[user_id] = client
            await state.update_data(phone=phone)
            await state.set_state(ConnectStates.code)
            await message.answer(texts.CODE_SENT.format(phone=phone), reply_markup=kb.cancel_inline())

        @r.message(NotMenuText(), ConnectStates.code)
        @error_guard
        async def connect_code(message: Message, state: FSMContext) -> None:
            code = (message.text or "").strip().replace(" ", "")
            data = await state.get_data()
            client = self._pending_clients.get(message.from_user.id)
            if client is None:
                await state.clear()
                await message.answer(texts.CONNECT_FAILED.format(reason="сесію втрачено"),
                                     reply_markup=kb.connect_menu())
                return
            try:
                await client.sign_in(phone=data["phone"], code=code)
            except telethon_errors.SessionPasswordNeededError:
                await state.set_state(ConnectStates.password)
                await message.answer(texts.ASK_PASSWORD, reply_markup=kb.cancel_inline())
                return
            except telethon_errors.PhoneCodeInvalidError:
                await message.answer("⚠️ Невірний код. Спробуйте ще раз.")
                return
            await self._finish_connect(message, state)

        @r.message(NotMenuText(), ConnectStates.password)
        @error_guard
        async def connect_password(message: Message, state: FSMContext) -> None:
            password = (message.text or "").strip()
            client = self._pending_clients.get(message.from_user.id)
            if client is None:
                await state.clear()
                await message.answer(texts.CONNECT_FAILED.format(reason="сесію втрачено"),
                                     reply_markup=kb.connect_menu())
                return
            if password.lower() == "/skip" or not password:
                if not await client.is_user_authorized():
                    await message.answer("⚠️ Потрібен хмарний пароль 2FA. Надішліть його.")
                    return
            else:
                try:
                    await client.sign_in(password=password)
                except telethon_errors.PasswordHashInvalidError:
                    await message.answer("⚠️ Невірний пароль. Спробуйте ще раз.")
                    return
            await self._finish_connect(message, state)

        # ------------------------------------------------------ вхід через QR

        @r.callback_query(F.data == "connect:qr")
        @error_guard
        async def connect_qr(cb: CallbackQuery, state: FSMContext) -> None:
            await state.clear()
            account = await self._db.get_account(cb.from_user.id)
            if account and account.get("api_id") and account.get("api_hash"):
                await cb.message.answer(texts.QR_USING_SAVED)  # type: ignore[union-attr]
                await self._start_qr_login(
                    cb.message, state, cb.from_user.id,
                    int(account["api_id"]), str(account["api_hash"]),
                )
            else:
                await state.set_state(QrStates.api_id)
                await cb.message.answer(  # type: ignore[union-attr]
                    texts.ASK_API_ID, reply_markup=kb.cancel_inline(),
                )
            await cb.answer()

        @r.message(NotMenuText(), QrStates.api_id)
        @error_guard
        async def qr_api_id(message: Message, state: FSMContext) -> None:
            raw = (message.text or "").strip()
            if not raw.isdigit():
                await message.answer(texts.ERR_INVALID_API_ID)
                return
            await state.update_data(api_id=int(raw))
            await state.set_state(QrStates.api_hash)
            await message.answer(texts.ASK_API_HASH, reply_markup=kb.cancel_inline())

        @r.message(NotMenuText(), QrStates.api_hash)
        @error_guard
        async def qr_api_hash(message: Message, state: FSMContext) -> None:
            api_hash = (message.text or "").strip()
            if len(api_hash) < 30:
                await message.answer("⚠️ api_hash виглядає некоректно (зазвичай 32 символи).")
                return
            data = await state.get_data()
            api_id = int(data["api_id"])
            await self._start_qr_login(message, state, message.from_user.id, api_id, api_hash)

        @r.callback_query(F.data == "qr:cancel")
        @error_guard
        async def qr_cancel(cb: CallbackQuery, state: FSMContext) -> None:
            """Скасувати очікування QR-логіну й повернутись до меню підключення."""
            task = self._qr_tasks.pop(cb.from_user.id, None)
            if task and not task.done():
                task.cancel()
            client = self._pending_clients.pop(cb.from_user.id, None)
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    logger.debug("Не вдалося від'єднати клієнт QR-логіну", exc_info=True)
            await state.clear()
            try:
                await cb.message.edit_reply_markup(reply_markup=None)  # type: ignore[union-attr]
            except TelegramAPIError:
                logger.debug("Не вдалося зняти клавіатуру з QR-повідомлення", exc_info=True)
            text, markup = await self._connect_payload(cb.from_user.id)
            await cb.message.answer(text, reply_markup=markup)  # type: ignore[union-attr]
            await cb.answer(texts.CANCELLED)

        # ------------------------------------------------------ промпт (дві частини)

        @r.message(F.text == texts.BTN_PROMPT)
        @error_guard
        async def prompt_menu(message: Message, state: FSMContext) -> None:
            await state.clear()
            await self._db.ensure_user(message.from_user.id)
            await message.answer(texts.PROMPT_SPLIT_MENU, reply_markup=kb.prompt_menu())

        @r.callback_query(F.data == "prompt:short")
        @error_guard
        async def prompt_short(cb: CallbackQuery, state: FSMContext) -> None:
            await state.clear()
            await self._db.ensure_user(cb.from_user.id)
            prompts = await self._db.get_prompts(cb.from_user.id)
            current = prompts["base_prompt"] or DEFAULT_BASE_PROMPT
            shown = current if len(current) <= 1500 else current[:1500] + "…"
            await cb.message.answer(  # type: ignore[union-attr]
                texts.PROMPT_MENU.format(prompt=shown),
                reply_markup=kb.prompt_edit_menu(),
            )
            await cb.answer()

        @r.callback_query(F.data == "prompt:edit")
        @error_guard
        async def prompt_edit(cb: CallbackQuery, state: FSMContext) -> None:
            await state.set_state(PromptStates.editing)
            await cb.message.answer(  # type: ignore[union-attr]
                texts.ASK_PROMPT, reply_markup=kb.cancel_inline(),
            )
            await cb.answer()

        @r.callback_query(F.data == "prompt:reset")
        @error_guard
        async def prompt_reset(cb: CallbackQuery) -> None:
            await self._db.set_base_prompt(cb.from_user.id, DEFAULT_BASE_PROMPT)
            await cb.answer(texts.PROMPT_RESET_DONE, show_alert=True)

        @r.message(NotMenuText(), PromptStates.editing)
        @error_guard
        async def prompt_save(message: Message, state: FSMContext) -> None:
            text = (message.text or "").strip()
            if len(text) < 20:
                await message.answer("⚠️ Промпт занадто короткий (мінімум 20 символів).")
                return
            await self._db.set_base_prompt(message.from_user.id, text)
            await state.clear()
            await message.answer(texts.SAVED, reply_markup=kb.prompt_edit_menu())

        # ------------------------------------------------------ аналіз стилю

        @r.message(F.text == texts.BTN_STYLE)
        @error_guard
        async def style_menu(message: Message, state: FSMContext) -> None:
            await state.clear()
            await message.answer(texts.STYLE_MENU, reply_markup=kb.style_menu())

        @r.callback_query(F.data.in_({"style:one", "style:multi"}))
        @error_guard
        async def style_choose(cb: CallbackQuery, state: FSMContext) -> None:
            if not await self._require_userbot(cb.message):  # type: ignore[arg-type]
                await cb.answer()
                return
            dialogs = await self._list_dialogs()
            if not dialogs:
                await cb.message.answer(texts.NO_DIALOGS)  # type: ignore[union-attr]
                await cb.answer()
                return
            if cb.data == "style:one":
                await self._safe_edit(cb.message, texts.SELECT_ONE_CHAT, kb.dialogs_one(dialogs))  # type: ignore[arg-type]
            else:
                await state.update_data(selected=set())
                await self._safe_edit(  # type: ignore[arg-type]
                    cb.message, texts.SELECT_MULTI_CHAT, kb.dialogs_multi(dialogs, set()),
                )
            await cb.answer()

        @r.callback_query(F.data == "style:clearmulti")
        @error_guard
        async def style_clear_multi(cb: CallbackQuery, state: FSMContext) -> None:
            data = await state.get_data()
            data["selected"] = set()
            await state.set_data(data)
            dialogs = await self._list_dialogs()
            await self._safe_edit_markup(  # type: ignore[arg-type]
                cb.message, kb.dialogs_multi(dialogs, set())
            )
            await cb.answer(texts.MULTI_SELECTED.format(count=0))

        @r.callback_query(F.data.startswith("style:toggle:"))
        @error_guard
        async def style_toggle(cb: CallbackQuery, state: FSMContext) -> None:
            ref = cb.data.split(":", 2)[2]
            data = await state.get_data()
            selected: set[str] = data.get("selected", set())
            selected.symmetric_difference_update({ref})
            await state.update_data(selected=selected)
            dialogs = await self._list_dialogs()
            await self._safe_edit_markup(  # type: ignore[arg-type]
                cb.message, kb.dialogs_multi(dialogs, selected)
            )
            await cb.answer(texts.MULTI_SELECTED.format(count=len(selected)))

        @r.callback_query(F.data.startswith("style:pickone:"))
        @error_guard
        async def style_pick_one(cb: CallbackQuery) -> None:
            ref = cb.data.split(":", 2)[2]
            await cb.answer()  # зняти спінер одразу: аналіз триває до хвилини
            await cb.message.answer(texts.ANALYSIS_STARTED.format(limit=self._config.analysis_limit))  # type: ignore[union-attr]
            await self._run_style_analysis(cb.from_user.id, [ref], cb.message)  # type: ignore[arg-type]

        @r.callback_query(F.data == "style:donemulti")
        @error_guard
        async def style_done_multi(cb: CallbackQuery, state: FSMContext) -> None:
            data = await state.get_data()
            selected: list[str] = sorted(data.get("selected", set()))
            if not selected:
                await cb.answer("Нічого не обрано", show_alert=True)
                return
            await state.clear()
            await cb.answer()  # зняти спінер одразу: аналіз триває до хвилини
            await cb.message.answer(texts.ANALYSIS_STARTED.format(limit=self._config.analysis_limit))  # type: ignore[union-attr]
            await self._run_style_analysis(cb.from_user.id, selected, cb.message)  # type: ignore[arg-type]

        @r.callback_query(F.data == "style:all")
        @error_guard
        async def style_all(cb: CallbackQuery) -> None:
            if not await self._require_userbot(cb.message):  # type: ignore[arg-type]
                await cb.answer()
                return
            dialogs = await self._list_dialogs(limit=ALL_DIALOGS_LIMIT)
            if not dialogs:
                await cb.message.answer(texts.NO_DIALOGS)  # type: ignore[union-attr]
                await cb.answer()
                return
            refs = [ref for _cid, _title, ref in dialogs]
            await cb.answer()  # зняти спінер одразу: аналіз триває до хвилини
            await cb.message.answer(texts.ANALYSIS_STARTED.format(limit=self._config.analysis_limit))  # type: ignore[union-attr]
            await self._run_style_analysis(cb.from_user.id, refs, cb.message)  # type: ignore[arg-type]

        @r.callback_query(F.data.in_({"style:show", "style:regen"}))
        @error_guard
        async def style_show_or_regen(cb: CallbackQuery, state: FSMContext) -> None:
            if cb.data == "style:show":
                prompts = await self._db.get_prompts(cb.from_user.id)
                if not prompts["style_profile"]:
                    await cb.answer(texts.STYLE_PROFILE_EMPTY, show_alert=True)
                    return
                profile = prompts["style_profile"]
                if len(profile) > 3500:
                    profile = profile[:3500] + "…"
                await cb.message.answer(  # type: ignore[union-attr]
                    texts.STYLE_PROFILE_HEADER.format(profile=profile),
                    reply_markup=kb.style_menu(),
                )
                await cb.answer()
                return
            # regen — за списком проаналізованих чатів
            analyzed = await self._db.list_analyzed(cb.from_user.id)
            if not analyzed:
                await cb.answer("Немає проаналізованих чатів", show_alert=True)
                return
            if not await self._require_userbot(cb.message):  # type: ignore[arg-type]
                await cb.answer()
                return
            refs = [item["chat_ref"] for item in analyzed]
            await cb.answer()  # зняти спінер одразу: аналіз триває до хвилини
            await cb.message.answer(texts.ANALYSIS_STARTED.format(limit=self._config.analysis_limit))  # type: ignore[union-attr]
            await self._run_style_analysis(cb.from_user.id, refs, cb.message)  # type: ignore[arg-type]

        # ------------------------------------------------------ пер-чат профілі

        @r.callback_query(F.data == "style:perchat")
        @error_guard
        async def perchat_menu(cb: CallbackQuery) -> None:
            profiles = await self._db.list_chat_profiles(cb.from_user.id)
            if profiles:
                items = [(p["chat_ref"], f"{p['chat_ref']} · {p['updated_at'][:16]}")
                         for p in profiles]
                await self._safe_edit(  # type: ignore[arg-type]
                    cb.message, texts.CHAT_PROFILE_MENU, kb.chat_profile_list(items),
                )
            else:
                if not await self._require_userbot(cb.message):  # type: ignore[arg-type]
                    await cb.answer()
                    return
                dialogs = await self._list_dialogs()
                await self._safe_edit(  # type: ignore[arg-type]
                    cb.message, texts.CHAT_PROFILE_MENU, kb.dialogs_perchat(dialogs),
                )
            await cb.answer()

        @r.callback_query(F.data.startswith("cp:make:"))
        @error_guard
        async def cp_make(cb: CallbackQuery) -> None:
            if not await self._require_userbot(cb.message):  # type: ignore[arg-type]
                await cb.answer()
                return
            ref = cb.data.split(":", 2)[2]
            key = await self._db.get_active_key(cb.from_user.id)
            if key is None:
                await cb.message.answer(texts.ERR_NO_ACTIVE_KEY)  # type: ignore[union-attr]
                await cb.answer()
                return
            llm = create_client(
                provider=key["provider"], base_url=key["base_url"],
                api_key=key["api_key"], model=key["model"],
                timeout=self._config.llm_timeout,
                max_tokens=1500,  # профіль — довгий текст, ліміт лише для чат-відповідей
            )
            assert self._userbot.client is not None
            await cb.answer()  # зняти спінер одразу
            status = await cb.message.answer(  # type: ignore[union-attr]
                texts.ANALYSIS_STARTED.format(limit=self._config.analysis_limit),
            )
            try:
                profile = await generate_chat_profile(
                    self._userbot.client, llm, await self._resolve_chat_ref(ref),
                    limit=self._config.analysis_limit,
                )
                await self._db.save_chat_profile(cb.from_user.id, ref, profile)
                title = ref
                try:
                    entity = await self._resolve_chat_ref(ref)
                    title = getattr(entity, "first_name", None) or getattr(entity, "title", None) or ref
                except Exception:  # noqa: BLE001
                    logger.debug("Не вдалося отримати назву чату %s", ref, exc_info=True)
                await status.edit_text(
                    texts.CHAT_PROFILE_DONE.format(title=html.escape(title)),
                    reply_markup=kb.back_markup("nav:perchat"),
                )
            except ValueError as exc:
                await status.edit_text(f"⚠️ {exc}", reply_markup=kb.back_markup("nav:perchat"))
            except Exception:
                logger.exception("Помилка генерації пер-чат профілю")
                await status.edit_text(
                    texts.ERR_GENERIC, reply_markup=kb.back_markup("nav:perchat"),
                )

        @r.callback_query(F.data.startswith("cp:show:"))
        @error_guard
        async def cp_show(cb: CallbackQuery) -> None:
            ref = cb.data.split(":", 2)[2]
            profile = await self._db.get_chat_profile(cb.from_user.id, ref)
            if not profile:
                await cb.answer(texts.CHAT_PROFILE_EMPTY, show_alert=True)
                return
            if len(profile) > 3500:
                profile = profile[:3500] + "…"
            await cb.message.answer(  # type: ignore[union-attr]
                texts.CHAT_PROFILE_SHOW_HEADER.format(
                    title=html.escape(ref), profile=html.escape(profile),
                ),
                reply_markup=kb.back_markup("nav:perchat"),
            )
            await cb.answer()

        @r.callback_query(F.data.startswith("cp:del:"))
        @error_guard
        async def cp_del(cb: CallbackQuery) -> None:
            ref = cb.data.split(":", 2)[2]
            await self._db.delete_chat_profile(cb.from_user.id, ref)
            await cb.answer(texts.CHAT_PROFILE_DELETED, show_alert=True)
            profiles = await self._db.list_chat_profiles(cb.from_user.id)
            items = [(p["chat_ref"], f"{p['chat_ref']} · {p['updated_at'][:16]}")
                     for p in profiles]
            await self._safe_edit_markup(  # type: ignore[arg-type]
                cb.message, kb.chat_profile_list(items),
            )

        # ------------------------------------------------------ нагадування та журнал Q&A

        @r.message(F.text == texts.BTN_REMINDERS)
        @error_guard
        async def reminders_menu(message: Message, state: FSMContext) -> None:
            await state.clear()
            await self._send_journal_menu(message)

        @r.callback_query(F.data == "jr:clear")
        @error_guard
        async def jr_clear(cb: CallbackQuery) -> None:
            await self._db.clear_qa_journal(cb.from_user.id)
            await cb.answer(texts.JOURNAL_CLEARED, show_alert=True)
            await self._refresh_journal_menu(cb)

        @r.callback_query(F.data == "jr:add")
        @error_guard
        async def jr_add(cb: CallbackQuery, state: FSMContext) -> None:
            await state.set_state(RemindStates.text)
            await cb.message.answer(  # type: ignore[union-attr]
                texts.ASK_REMIND_TEXT, reply_markup=kb.cancel_inline(),
            )
            await cb.answer()

        @r.message(NotMenuText(), RemindStates.text)
        @error_guard
        async def remind_text_input(message: Message, state: FSMContext) -> None:
            text = (message.text or "").strip()
            if not text:
                await message.answer(texts.ASK_REMIND_TEXT, reply_markup=kb.cancel_inline())
                return
            await state.update_data(remind_text=text)
            await state.set_state(RemindStates.time)
            await message.answer(texts.ASK_REMIND_TIME, reply_markup=kb.cancel_inline())

        @r.message(NotMenuText(), RemindStates.time)
        @error_guard
        async def remind_time_input(message: Message, state: FSMContext) -> None:
            raw = (message.text or "").strip()
            due = parse_reminder_time(raw)
            if due is None:
                await message.answer(texts.REMINDER_BAD_TIME)
                return
            if due <= datetime.now():
                await message.answer(texts.REMINDER_BAD_PAST)
                return
            data = await state.get_data()
            text = str(data.get("remind_text", "")).strip() or "Нагадування"
            due_iso = due.isoformat(timespec="seconds")
            await self._db.add_reminder(message.from_user.id, due_iso, text)
            await state.clear()
            await message.answer(
                texts.REMINDER_ADDED.format(due_at=format_due(due_iso)),
                reply_markup=await self._journal_markup(message.from_user.id),
            )

        @r.callback_query(F.data.startswith("rem:del:"))
        @error_guard
        async def rem_del(cb: CallbackQuery) -> None:
            reminder_id = int(cb.data.split(":")[2])
            await self._db.delete_reminder(cb.from_user.id, reminder_id)
            await cb.answer(texts.REMINDER_DELETED, show_alert=True)
            await self._refresh_journal_menu(cb)

        # ------------------------------------------------------ база знань

        @r.callback_query(F.data == "kb:open")
        @error_guard
        async def kb_open(cb: CallbackQuery, state: FSMContext) -> None:
            await state.clear()
            text = await self._kb_menu_text(cb.from_user.id)
            try:
                await cb.message.edit_text(text, reply_markup=kb.kb_menu())  # type: ignore[union-attr]
            except TelegramAPIError:
                await cb.message.answer(text, reply_markup=kb.kb_menu())  # type: ignore[union-attr]
            await cb.answer()

        @r.callback_query(F.data == "kb:add")
        @error_guard
        async def kb_add(cb: CallbackQuery, state: FSMContext) -> None:
            await state.set_state(KbStates.content)
            await state.update_data(kb_buffer=[])
            await cb.message.answer(  # type: ignore[union-attr]
                texts.ASK_KB_CONTENT, reply_markup=kb.cancel_inline(),
            )
            await cb.answer()

        @r.message(KbStates.content, F.text == "/done")
        @error_guard
        async def kb_done(message: Message, state: FSMContext) -> None:
            data = await state.get_data()
            buffer: list[str] = list(data.get("kb_buffer", []))
            content = "\n".join(buffer).strip()
            if not content:
                await message.answer(texts.KB_EMPTY_BUFFER)
                return
            title = content.splitlines()[0].strip()[:60]
            await self._db.add_kb_entry(message.from_user.id, title, content)
            await state.clear()
            await message.answer(
                texts.KB_SAVED.format(title=html.escape(title), chars=len(content)),
                reply_markup=kb.kb_menu(),
            )

        @r.message(NotMenuText(), KbStates.content)
        @error_guard
        async def kb_chunk(message: Message, state: FSMContext) -> None:
            text = (message.text or "").strip()
            if not text:
                return
            total = await self._db.kb_total_chars(message.from_user.id)
            if total + len(text) > texts.KB_MAX_CHARS:
                await message.answer(texts.KB_TOO_LONG.format(limit=texts.KB_MAX_CHARS))
                return
            data = await state.get_data()
            buffer: list[str] = list(data.get("kb_buffer", []))
            buffer.append(text)
            await state.update_data(kb_buffer=buffer)
            await message.answer(
                texts.KB_CHUNK_SAVED.format(chars=len(text), total=total + len(text)),
                reply_markup=kb.cancel_inline(),
            )

        @r.callback_query(F.data == "kb:list")
        @error_guard
        async def kb_list(cb: CallbackQuery) -> None:
            entries = await self._db.list_kb_entries(cb.from_user.id)
            title, markup = self._kb_list_view(entries)
            try:
                await cb.message.edit_text(title, reply_markup=markup)  # type: ignore[union-attr]
            except TelegramAPIError:
                logger.debug("Не вдалося оновити список бази знань", exc_info=True)
            await cb.answer()

        @r.callback_query(F.data.startswith("kb:view:"))
        @error_guard
        async def kb_view(cb: CallbackQuery) -> None:
            entry_id = int(cb.data.split(":")[2])
            entry = await self._db.get_kb_entry(cb.from_user.id, entry_id)
            if entry is None:
                await cb.answer("Запис не знайдено", show_alert=True)
                return
            content = entry["content"] or ""
            if len(content) > 3500:
                content = content[:3500] + "…"
            await cb.message.answer(  # type: ignore[union-attr]
                texts.KB_ENTRY_FMT.format(
                    title=html.escape(entry["title"] or "Без назви"),
                    content=html.escape(content),
                ),
                reply_markup=kb.back_markup("nav:kblist"),
            )
            await cb.answer()

        @r.callback_query(F.data.startswith("kb:del:"))
        @error_guard
        async def kb_del(cb: CallbackQuery) -> None:
            entry_id = int(cb.data.split(":")[2])
            await self._db.delete_kb_entry(cb.from_user.id, entry_id)
            await cb.answer(texts.KB_ENTRY_DELETED, show_alert=True)
            entries = await self._db.list_kb_entries(cb.from_user.id)
            title, markup = self._kb_list_view(entries)
            try:
                await cb.message.edit_text(title, reply_markup=markup)  # type: ignore[union-attr]
            except TelegramAPIError:
                logger.debug("Не вдалося оновити список бази знань", exc_info=True)

        @r.callback_query(F.data == "kb:export")
        @error_guard
        async def kb_export(cb: CallbackQuery) -> None:
            entries = await self._db.list_kb_entries(cb.from_user.id)
            if not entries:
                await cb.answer(texts.KB_EXPORT_EMPTY, show_alert=True)
                return
            md = "\n\n".join(
                f"## {entry['title']}\n\n{entry['content']}" for entry in entries
            )
            file = BufferedInputFile(md.encode("utf-8"), filename=texts.KB_EXPORT_NAME)
            await cb.message.answer_document(file)  # type: ignore[union-attr]
            await cb.answer()

        # ------------------------------------------------------ профілі налаштувань

        @r.message(F.text == texts.BTN_PROFILES)
        @error_guard
        async def profiles_menu(message: Message, state: FSMContext) -> None:
            await state.clear()
            await self._db.ensure_user(message.from_user.id)
            settings = await self._db.get_settings(message.from_user.id)
            active = settings["profile_name"] or texts.PROFILE_NONE_ACTIVE
            await message.answer(
                texts.PROFILES_MENU.format(active=html.escape(active)),
                reply_markup=kb.profiles_menu(),
            )

        @r.callback_query(F.data == "prof:save")
        @error_guard
        async def prof_save(cb: CallbackQuery, state: FSMContext) -> None:
            await state.set_state(ProfileStates.name)
            await cb.message.answer(  # type: ignore[union-attr]
                texts.ASK_PROFILE_NAME, reply_markup=kb.cancel_inline(),
            )
            await cb.answer()

        @r.message(NotMenuText(), ProfileStates.name)
        @error_guard
        async def prof_name(message: Message, state: FSMContext) -> None:
            name = (message.text or "").strip()
            if not 1 <= len(name) <= 40:
                await message.answer(texts.PROFILE_BAD_NAME)
                return
            snapshot = await self._make_profile_snapshot(message.from_user.id)
            await self._db.save_profile(
                message.from_user.id, name, serialize_snapshot(snapshot)
            )
            await state.clear()
            await message.answer(
                texts.PROFILE_SAVED.format(name=html.escape(name)),
                reply_markup=kb.profiles_menu(),
            )

        @r.callback_query(F.data == "prof:list")
        @error_guard
        async def prof_list(cb: CallbackQuery) -> None:
            text, markup = await self._profiles_view(cb.from_user.id)
            try:
                await cb.message.edit_text(text, reply_markup=markup)  # type: ignore[union-attr]
            except TelegramAPIError:
                await cb.message.answer(text, reply_markup=markup)  # type: ignore[union-attr]
            await cb.answer()

        @r.callback_query(F.data.startswith("prof:apply:"))
        @error_guard
        async def prof_apply(cb: CallbackQuery) -> None:
            profile_id = int(cb.data.split(":")[2])
            profile = await self._db.get_profile(cb.from_user.id, profile_id)
            if profile is None or not await self._db.apply_profile(cb.from_user.id, profile_id):
                await cb.answer(texts.PROFILE_APPLY_FAILED, show_alert=True)
                return
            await cb.answer(
                texts.PROFILE_APPLIED.format(name=profile["name"]), show_alert=True
            )
            await self._refresh_profiles_view(cb)

        @r.callback_query(F.data.startswith("prof:del:"))
        @error_guard
        async def prof_del(cb: CallbackQuery) -> None:
            profile_id = int(cb.data.split(":")[2])
            profile = await self._db.get_profile(cb.from_user.id, profile_id)
            await self._db.delete_profile(cb.from_user.id, profile_id)
            if profile is not None:
                await cb.answer(
                    texts.PROFILE_DELETE_CONFIRM.format(
                        profile_id=profile_id, name=profile["name"]
                    ),
                    show_alert=True,
                )
            else:
                await cb.answer(texts.PROFILE_APPLY_FAILED, show_alert=True)
            await self._refresh_profiles_view(cb)

        # ------------------------------------------------------ налаштування авто-відповідей

        @r.message(F.text == texts.BTN_AUTOREPLY)
        @error_guard
        async def autoreply_menu(message: Message, state: FSMContext) -> None:
            await state.clear()
            await self._send_autoreply_menu(message)

        @r.callback_query(F.data == "ar:toggle")
        @error_guard
        async def ar_toggle(cb: CallbackQuery) -> None:
            s = await self._db.get_settings(cb.from_user.id)
            await self._db.update_settings(cb.from_user.id, auto_reply=0 if s["auto_reply"] else 1)
            state_text = texts.STATE_OFF if s["auto_reply"] else texts.STATE_ON
            await cb.answer(f"Авто-відповідь: {state_text}", show_alert=True)
            await self._refresh_autoreply_menu(cb)

        @r.callback_query(F.data == "ar:mode")
        @error_guard
        async def ar_mode(cb: CallbackQuery) -> None:
            s = await self._db.get_settings(cb.from_user.id)
            new_mode = 0 if s["whitelist_mode"] else 1
            await self._db.update_settings(cb.from_user.id, whitelist_mode=new_mode)
            await cb.answer(texts.MODE_WHITELIST if new_mode else texts.MODE_ALL_EXCEPT, show_alert=True)
            await self._refresh_autoreply_menu(cb)

        @r.callback_query(F.data == "ar:mention")
        @error_guard
        async def ar_mention(cb: CallbackQuery) -> None:
            s = await self._db.get_settings(cb.from_user.id)
            await self._db.update_settings(cb.from_user.id, only_private_or_mention=0 if s["only_private_or_mention"] else 1)
            await cb.answer()
            await self._refresh_autoreply_menu(cb)

        @r.callback_query(F.data.in_({"ar:ignore", "ar:whitelist"}))
        @error_guard
        async def ar_lists(cb: CallbackQuery) -> None:
            field = LIST_IGNORE if cb.data == "ar:ignore" else LIST_WHITELIST
            s = await self._db.get_settings(cb.from_user.id)
            items = list(s[field])
            title = texts.IGNORE_LIST_TITLE if field == LIST_IGNORE else texts.WHITELIST_TITLE
            await self._safe_edit(  # type: ignore[arg-type]
                cb.message,
                title.format(items="\n".join(items) or texts.EMPTY_LIST),
                kb.list_manage(items, "ilist" if field == LIST_IGNORE else "wlist"),
            )
            await cb.answer()

        @r.callback_query(F.data.in_({"ilist:add", "wlist:add"}))
        @error_guard
        async def list_add_start(cb: CallbackQuery, state: FSMContext) -> None:
            field = LIST_IGNORE if cb.data.startswith("ilist") else LIST_WHITELIST
            await state.update_data(list_field=field)
            await state.set_state(ListStates.adding)
            await cb.message.answer(  # type: ignore[union-attr]
                texts.ASK_LIST_ADD, reply_markup=kb.cancel_inline(),
            )
            await cb.answer()

        @r.message(NotMenuText(), ListStates.adding)
        @error_guard
        async def list_add_value(message: Message, state: FSMContext) -> None:
            value = (message.text or "").strip()
            if not value:
                await message.answer(texts.ERR_GENERIC)
                return
            data = await state.get_data()
            field = data.get("list_field", LIST_IGNORE)
            await self._db.add_to_list(message.from_user.id, field, value)
            await state.clear()
            settings = await self._db.get_settings(message.from_user.id)
            items = list(settings[field])
            prefix = "ilist" if field == LIST_IGNORE else "wlist"
            await message.answer(
                texts.LIST_ADD_DONE.format(value=value),
                reply_markup=kb.list_manage(items, prefix),
            )

        @r.callback_query(F.data.startswith("ilist:del:"))
        @error_guard
        async def ilist_del(cb: CallbackQuery) -> None:
            value = cb.data.split(":", 2)[2]
            await self._db.remove_from_list(cb.from_user.id, LIST_IGNORE, value)
            await cb.answer(texts.SAVED)
            await self._refresh_lists(cb, LIST_IGNORE, "ilist")

        @r.callback_query(F.data.startswith("wlist:del:"))
        @error_guard
        async def wlist_del(cb: CallbackQuery) -> None:
            value = cb.data.split(":", 2)[2]
            await self._db.remove_from_list(cb.from_user.id, LIST_WHITELIST, value)
            await cb.answer(texts.SAVED)
            await self._refresh_lists(cb, LIST_WHITELIST, "wlist")

        @r.callback_query(F.data.in_({"ilist:clear", "wlist:clear"}))
        @error_guard
        async def lists_clear(cb: CallbackQuery) -> None:
            field = LIST_IGNORE if cb.data.startswith("ilist") else LIST_WHITELIST
            prefix = "ilist" if field == LIST_IGNORE else "wlist"
            await self._db.clear_list(cb.from_user.id, field)
            await cb.answer(texts.LIST_CLEAR_DONE)
            await self._refresh_lists(cb, field, prefix)

        @r.callback_query(F.data == "ar:delay")
        @error_guard
        async def ar_delay(cb: CallbackQuery, state: FSMContext) -> None:
            await state.update_data(number_field="delay")
            await state.set_state(NumberStates.waiting)
            await cb.message.answer(  # type: ignore[union-attr]
                texts.ASK_REPLY_DELAY, reply_markup=kb.cancel_inline(),
            )
            await cb.answer()

        @r.callback_query(F.data == "ar:chunks")
        @error_guard
        async def ar_chunks(cb: CallbackQuery, state: FSMContext) -> None:
            await state.update_data(number_field="max_chunks")
            await state.set_state(NumberStates.waiting)
            await cb.message.answer(  # type: ignore[union-attr]
                texts.ASK_MAX_CHUNKS, reply_markup=kb.cancel_inline(),
            )
            await cb.answer()

        @r.callback_query(F.data == "ar:context")
        @error_guard
        async def ar_context(cb: CallbackQuery, state: FSMContext) -> None:
            await state.update_data(number_field="context_messages")
            await state.set_state(NumberStates.waiting)
            await cb.message.answer(  # type: ignore[union-attr]
                texts.ASK_CONTEXT_COUNT, reply_markup=kb.cancel_inline(),
            )
            await cb.answer()

        @r.callback_query(F.data == "ar:cooldown")
        @error_guard
        async def ar_cooldown(cb: CallbackQuery, state: FSMContext) -> None:
            await state.update_data(number_field="cooldown_seconds")
            await state.set_state(NumberStates.waiting)
            await cb.message.answer(  # type: ignore[union-attr]
                texts.ASK_COOLDOWN, reply_markup=kb.cancel_inline(),
            )
            await cb.answer()

        @r.callback_query(F.data == "ar:rudeness")
        @error_guard
        async def ar_rudeness(cb: CallbackQuery) -> None:
            await self._rudeness_menu(cb)
            await cb.answer()

        @r.callback_query(F.data == "ar:nextprompt")
        @error_guard
        async def ar_nextprompt(cb: CallbackQuery) -> None:
            s = await self._db.get_settings(cb.from_user.id)
            has_prompt = bool((s.get("next_prompt") or "").strip())
            snippet = ""
            if has_prompt:
                snippet = html.escape((s["next_prompt"] or "").strip()[:120])
                if len((s["next_prompt"] or "").strip()) > 120:
                    snippet += "…"
            text = texts.NEXT_PROMPT_MENU
            if snippet:
                text += texts.NEXT_PROMPT_ACTIVE_LINE.format(snippet=snippet)
            try:
                await cb.message.edit_text(  # type: ignore[union-attr]
                    text, reply_markup=kb.next_prompt_menu(has_prompt),
                )
            except TelegramAPIError:
                await cb.message.answer(  # type: ignore[union-attr]
                    text, reply_markup=kb.next_prompt_menu(has_prompt),
                )
            await cb.answer()

        @r.callback_query(F.data == "ar:np:set")
        @error_guard
        async def np_set(cb: CallbackQuery, state: FSMContext) -> None:
            await state.set_state(NextPromptStates.text)
            await cb.message.answer(  # type: ignore[union-attr]
                texts.NEXT_PROMPT_ASK, reply_markup=kb.cancel_inline(),
            )
            await cb.answer()

        @r.callback_query(F.data == "ar:np:clear")
        @error_guard
        async def np_clear(cb: CallbackQuery) -> None:
            await self._db.update_settings(cb.from_user.id, next_prompt="")
            try:
                await cb.message.edit_text(  # type: ignore[union-attr]
                    texts.NEXT_PROMPT_CLEARED,
                    reply_markup=kb.next_prompt_menu(False),
                )
            except TelegramAPIError:
                await cb.message.answer(  # type: ignore[union-attr]
                    texts.NEXT_PROMPT_CLEARED,
                    reply_markup=kb.next_prompt_menu(False),
                )
            await cb.answer()

        @r.message(NotMenuText(), NextPromptStates.text)
        @error_guard
        async def np_input(message: Message, state: FSMContext) -> None:
            text = (message.text or "").strip()
            if not text:
                await message.answer(texts.NEXT_PROMPT_ASK, reply_markup=kb.cancel_inline())
                return
            if len(text) > 2000:
                text = text[:2000]
            await self._db.update_settings(message.from_user.id, next_prompt=text)
            await state.clear()
            await message.answer(texts.NEXT_PROMPT_SAVED)

        @r.callback_query(F.data.startswith("ar:rude:"))
        @error_guard
        async def ar_rude_set(cb: CallbackQuery) -> None:
            try:
                level = int(cb.data.rsplit(":", 1)[1])
            except (IndexError, ValueError):
                await cb.answer()
                return
            if level not in texts.RUDENESS_NAMES:
                await cb.answer()
                return
            await self._db.update_settings(cb.from_user.id, rudeness_level=level)
            await self._rudeness_menu(cb)
            await cb.answer(f"Грубість: {texts.RUDENESS_NAMES[level]}")

        @r.message(NotMenuText(), NumberStates.waiting)
        @error_guard
        async def number_input(message: Message, state: FSMContext) -> None:
            data = await state.get_data()
            field = data.get("number_field", "")
            raw = (message.text or "").strip()
            if field == "delay":
                parts = raw.replace(",", " ").split()
                if len(parts) != 2:
                    await message.answer(texts.ASK_REPLY_DELAY, reply_markup=kb.cancel_inline())
                    return
                try:
                    delay_min, delay_max = float(parts[0]), float(parts[1])
                except ValueError:
                    await message.answer(texts.ASK_REPLY_DELAY, reply_markup=kb.cancel_inline())
                    return
                if not (0 <= delay_min <= delay_max <= 600):
                    await message.answer("⚠️ Значення мають бути в межах 0–600, min ≤ max.")
                    return
                await self._db.update_settings(
                    message.from_user.id, reply_delay_min=delay_min, reply_delay_max=delay_max
                )
                await state.clear()
                await message.answer(
                    texts.DELAY_SET_DONE.format(min=delay_min, max=delay_max),
                    reply_markup=await self._autoreply_markup(message.from_user.id),
                )
                return

            limits = {"max_chunks": (1, 5), "context_messages": (5, 50), "cooldown_seconds": (5, 3600)}
            if field not in limits:
                await state.clear()
                await message.answer(texts.ERR_GENERIC, reply_markup=kb.main_menu())
                return
            low, high = limits[field]
            try:
                value = int(raw)
            except ValueError:
                await message.answer(texts.ASK_NUMBER, reply_markup=kb.cancel_inline())
                return
            if not low <= value <= high:
                await message.answer(f"⚠️ Значення має бути в межах {low}–{high}.")
                return
            await self._db.update_settings(message.from_user.id, **{field: value})
            await state.clear()
            await message.answer(
                texts.NUMBER_SET_DONE.format(value=value),
                reply_markup=await self._autoreply_markup(message.from_user.id),
            )

        @r.callback_query(F.data.startswith("nav:"))
        @error_guard
        async def nav_to(cb: CallbackQuery, state: FSMContext) -> None:
            """Єдина навігація «назад»: nav:back/cancel → головне, nav:<target> → батько."""
            target = cb.data.split(":", 1)[1]
            builders: dict[str, Callable[[int], Awaitable[tuple[str, InlineKeyboardMarkup]]]] = {
                "keys": self._api_keys_payload,
                "connect": self._connect_payload,
                "prompt": self._prompt_payload,
                "kb": self._kb_payload,
                "kblist": self._kblist_payload,
                "style": self._style_payload,
                "perchat": self._perchat_payload,
                "autoreply": self._autoreply_payload,
                "profiles": self._profiles_payload,
            }
            await state.clear()
            if target in {"back", "cancel"}:
                payload = (texts.WELCOME, kb.main_menu())
            else:
                builder = builders.get(target)
                if builder is None:
                    await cb.answer()
                    return
                payload = await builder(cb.from_user.id)
            if cb.message is not None:
                await self._safe_edit(cb.message, payload[0], payload[1])
            await cb.answer()

        @r.callback_query(F.data == "noop")
        @error_guard
        async def noop(cb: CallbackQuery) -> None:
            await cb.answer()

    # ------------------------------------------------------------------ вхід через QR

    async def _start_qr_login(self, message: Message, state: FSMContext,
                              user_id: int, api_id: int, api_hash: str) -> None:
        """Створити клієнт, запросити QR-логін і запустити фонове очікування."""
        old_task = self._qr_tasks.pop(user_id, None)
        if old_task and not old_task.done():
            old_task.cancel()
        old_client = self._pending_clients.pop(user_id, None)
        if old_client is not None:
            try:
                await old_client.disconnect()
            except Exception:  # noqa: BLE001
                logger.debug("Не вдалося від'єднати старий клієнт", exc_info=True)

        self._config.sessions_dir.mkdir(parents=True, exist_ok=True)
        session_path = self._config.sessions_dir / f"{user_id}.session"
        client = TelegramClient(
            str(session_path), api_id, api_hash, device_model="TelegramHub",
        )
        try:
            await client.connect()
            qr = await client.qr_login()
        except telethon_errors.ApiIdInvalidError:
            await client.disconnect()
            await state.clear()
            await message.answer(
                "❌ Telegram відхилив пару api_id/api_hash — значення недійсні.\n\n"
                "Перевірте на my.telegram.org/apps і почніть заново.",
                reply_markup=kb.connect_menu(),
            )
            return
        except Exception:
            await client.disconnect()
            await state.clear()
            logger.exception("QR-логін не вдався")
            await message.answer(texts.ERR_GENERIC)
            return

        self._pending_clients[user_id] = client
        await state.update_data(api_id=api_id, api_hash=api_hash)

        qr_path = self._config.sessions_dir / f"qr_{user_id}.png"
        # segno малює PNG синхронно — не блокуємо цикл подій
        await asyncio.to_thread(
            lambda: segno.make(qr.url).save(str(qr_path), scale=8, border=2)
        )
        token_part = qr.url.split("token=", 1)[-1]
        rows = [[InlineKeyboardButton(
            text="🔓 Підтвердити вхід",
            url=f"https://t.me/login?token={token_part}",
        )]]
        rows.append([InlineKeyboardButton(text=texts.BTN_CANCEL, callback_data="qr:cancel")])
        try:
            await message.answer_photo(
                FSInputFile(qr_path),
                caption=texts.QR_READY,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
        finally:
            qr_path.unlink(missing_ok=True)

        task = asyncio.create_task(self._qr_watch(user_id, client, message, state, qr))
        self._qr_tasks[user_id] = task

    async def _qr_watch(self, user_id: int, client: TelegramClient, message: Message,
                        state: FSMContext, qr: Any) -> None:
        """Фоново чекає підтвердження QR-логіну (до ~4 хв, з оновленням токена)."""
        try:
            confirmed = False
            for _ in range(8):  # 8 × 30 с ≈ 4 хв
                try:
                    await qr.wait(timeout=30)
                except asyncio.TimeoutError:
                    try:
                        await qr.recreate()
                    except Exception:  # noqa: BLE001
                        logger.exception("Не вдалося оновити QR-токен")
                        break
                    continue
                confirmed = True
                break
            self._qr_tasks.pop(user_id, None)
            if not confirmed and not await client.is_user_authorized():
                await client.disconnect()
                self._pending_clients.pop(user_id, None)
                await state.clear()
                await message.answer(texts.QR_TIMEOUT, reply_markup=kb.connect_menu())
                return
            await self._finish_connect(message, state, client=client)
        except telethon_errors.SessionPasswordNeededError:
            self._qr_tasks.pop(user_id, None)
            self._pending_clients[user_id] = client
            await state.set_state(ConnectStates.password)
            await message.answer(texts.ASK_PASSWORD, reply_markup=kb.cancel_inline())
        except asyncio.CancelledError:
            self._qr_tasks.pop(user_id, None)
            raise
        except Exception:
            self._qr_tasks.pop(user_id, None)
            self._pending_clients.pop(user_id, None)
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            logger.exception("Помилка очікування QR-логіну")
            try:
                await message.answer(texts.ERR_GENERIC)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ допоміжне

    async def _api_keys_payload(self, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        """Текст і клавіатура розділу «API ключі ШІ» (для навігації «назад»)."""
        return texts.API_KEYS_MENU, kb.api_keys_menu()

    async def _connect_payload(self, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        """Текст і клавіатура розділу «Підключити акаунт» зі свіжим статусом."""
        account = await self._db.get_account(user_id)
        status_text = (
            texts.STATUS_CONNECTED.format(phone=account["phone"])
            if account and self._userbot.connected else texts.STATUS_NOT_CONNECTED
        )
        return texts.CONNECT_MENU.format(status=status_text), kb.connect_menu()

    async def _prompt_payload(self, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        """Текст і клавіатура розділу «Промпт»."""
        return texts.PROMPT_SPLIT_MENU, kb.prompt_menu()

    async def _kb_payload(self, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        """Текст і клавіатура меню «База знань»."""
        return await self._kb_menu_text(user_id), kb.kb_menu()

    async def _kblist_payload(self, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        """Текст і клавіатура списку записів бази знань."""
        entries = await self._db.list_kb_entries(user_id)
        return self._kb_list_view(entries)

    async def _style_payload(self, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        """Текст і клавіатура розділу «Аналіз стилю»."""
        return texts.STYLE_MENU, kb.style_menu()

    async def _perchat_payload(self, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        """Текст і клавіатура меню пер-чат профілів (список наявних)."""
        profiles = await self._db.list_chat_profiles(user_id)
        items = [(p["chat_ref"], f"{p['chat_ref']} · {p['updated_at'][:16]}")
                 for p in profiles]
        return texts.CHAT_PROFILE_MENU, kb.chat_profile_list(items)

    async def _autoreply_markup(self, user_id: int) -> InlineKeyboardMarkup:
        """Клавіатура «Налаштування авто-відповідей» зі свіжими значеннями."""
        s = await self._db.get_settings(user_id)
        np_active = bool((s.get("next_prompt") or "").strip())
        return kb.autoreply_menu(
            auto_reply=bool(s["auto_reply"]),
            whitelist_mode=bool(s["whitelist_mode"]),
            only_mention=bool(s["only_private_or_mention"]),
            reply_delay_min=float(s["reply_delay_min"]),
            reply_delay_max=float(s["reply_delay_max"]),
            max_chunks=int(s["max_chunks"]),
            context_messages=int(s["context_messages"]),
            cooldown_seconds=int(s["cooldown_seconds"]),
            rudeness_level=int(s.get("rudeness_level", 1) or 0),
            next_prompt_hint=" ✅" if np_active else "",
        )

    async def _rudeness_menu(self, cb: CallbackQuery) -> None:
        """Показати меню вибору рівня грубості."""
        s = await self._db.get_settings(cb.from_user.id)
        level = int(s.get("rudeness_level", 1) or 0)
        try:
            await cb.message.edit_text(  # type: ignore[union-attr]
                texts.RUDENESS_MENU, reply_markup=kb.rudeness_menu(level),
            )
        except TelegramAPIError:
            await cb.message.answer(  # type: ignore[union-attr]
                texts.RUDENESS_MENU, reply_markup=kb.rudeness_menu(level),
            )

    async def _autoreply_payload(self, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        """Текст і клавіатура розділу «Налаштування авто-відповідей»."""
        s = await self._db.get_settings(user_id)
        summary = "Оберіть параметр:"
        if s["profile_name"]:
            summary += texts.AUTOREPLY_PROFILE_LINE.format(name=html.escape(s["profile_name"]))
        return (
            texts.AUTOREPLY_MENU.format(summary=summary),
            await self._autoreply_markup(user_id),
        )

    async def _profiles_payload(self, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        """Текст і клавіатура розділу «Профілі»."""
        settings = await self._db.get_settings(user_id)
        active = settings["profile_name"] or texts.PROFILE_NONE_ACTIVE
        return (
            texts.PROFILES_MENU.format(active=html.escape(active)),
            kb.profiles_menu(),
        )

    async def _journal_markup(self, user_id: int) -> InlineKeyboardMarkup:
        """Клавіатура меню «Нагадування» зі свіжими нагадуваннями."""
        reminders = await self._db.list_reminders(user_id)
        kb_reminders = [
            {"id": r["id"], "due_at": format_due(r["due_at"]), "text": r["text"]}
            for r in reminders
        ]
        return kb.journal_menu(kb_reminders)

    async def _journal_view(self, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        """Текст і клавіатура меню «🔔 Нагадування» (журнал + список нагадувань)."""
        entries = await self._db.list_qa_entries(user_id, limit=10)
        if entries:
            body = "\n\n".join(
                texts.JOURNAL_ENTRY_FMT.format(
                    title=html.escape(e["chat_title"] or e["chat_ref"] or "—"),
                    created_at=(e["created_at"] or "").replace("T", " ")[:16],
                    question=html.escape((e["question"] or "")[:150]),
                    answer=html.escape((e["answer"] or "")[:150]),
                )
                for e in entries
            )
        else:
            body = texts.JOURNAL_EMPTY
        reminders = await self._db.list_reminders(user_id)
        if reminders:
            items = "\n".join(
                texts.REMINDER_FMT.format(
                    due_at=format_due(r["due_at"]),
                    text=html.escape((r["text"] or "")[:80]),
                )
                for r in reminders
            )
            reminders_block = texts.REMINDERS_BLOCK.format(items=items)
        else:
            reminders_block = texts.REMINDERS_EMPTY
        text = texts.JOURNAL_MENU.format(
            count=len(entries), entries=body, reminders_block=reminders_block
        )
        return text, await self._journal_markup(user_id)

    async def _send_journal_menu(self, message: Message) -> None:
        await self._db.ensure_user(message.from_user.id)
        text, markup = await self._journal_view(message.from_user.id)
        await message.answer(text, reply_markup=markup)

    async def _refresh_journal_menu(self, cb: CallbackQuery) -> None:
        text, markup = await self._journal_view(cb.from_user.id)
        try:
            await cb.message.edit_text(text, reply_markup=markup)  # type: ignore[union-attr]
        except TelegramAPIError:
            logger.debug("Не вдалося оновити меню нагадувань", exc_info=True)

    async def _kb_menu_text(self, user_id: int) -> str:
        entries = await self._db.list_kb_entries(user_id)
        total = await self._db.kb_total_chars(user_id)
        return texts.KB_MENU.format(
            count=len(entries), chars=total, limit=texts.KB_MAX_CHARS
        )

    @staticmethod
    def _kb_list_view(entries: list[dict[str, Any]]) -> tuple[str, InlineKeyboardMarkup]:
        if entries:
            title = f"📋 <b>Записи бази знань</b> ({len(entries)}):"
        else:
            title = texts.KB_LIST_EMPTY
        return title, kb.kb_entries_list(entries)

    async def _make_profile_snapshot(self, user_id: int) -> dict[str, Any]:
        """Знімок поточних налаштувань, короткого промпта й бази знань."""
        settings = await self._db.get_settings(user_id)
        settings_fields = {
            key: settings[key]
            for key in settings
            if key not in ("last_error", "profile_name")
        }
        prompts = await self._db.get_prompts(user_id)
        kb_entries = await self._db.list_kb_entries(user_id)
        return {
            "settings": settings_fields,
            "base_prompt": prompts["base_prompt"],
            "kb": [
                {"title": e["title"], "content": e["content"]} for e in kb_entries
            ],
        }

    async def _profiles_view(self, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        profiles = await self._db.list_profiles(user_id)
        settings = await self._db.get_settings(user_id)
        active = settings["profile_name"] or ""
        if profiles:
            items = "\n".join(
                texts.PROFILE_ITEM_FMT.format(
                    profile_id=p["id"],
                    name=html.escape(p["name"]),
                    created_at=(p["created_at"] or "").replace("T", " ")[:16],
                )
                + (texts.PROFILE_ACTIVE_MARK if p["name"] == active else "")
                for p in profiles
            )
            text = texts.PROFILES_LIST.format(items=items)
        else:
            text = texts.PROFILES_EMPTY
        return text, kb.profile_list(profiles, active)

    async def _refresh_profiles_view(self, cb: CallbackQuery) -> None:
        text, markup = await self._profiles_view(cb.from_user.id)
        try:
            await cb.message.edit_text(text, reply_markup=markup)  # type: ignore[union-attr]
        except TelegramAPIError:
            logger.debug("Не вдалося оновити список профілів", exc_info=True)

    async def _send_autoreply_menu(self, message: Message) -> None:
        await self._db.ensure_user(message.from_user.id)
        text, markup = await self._autoreply_payload(message.from_user.id)
        await message.answer(text, reply_markup=markup)

    async def _finish_connect(self, message: Message, state: FSMContext,
                              client: TelegramClient | None = None) -> None:
        """Зберегти акаунт, закрити тимчасовий клієнт і запустити userbot."""
        data = await state.get_data()
        user_id = message.from_user.id
        client = client or self._pending_clients.get(user_id)
        if client is None:
            await state.clear()
            await message.answer(texts.CONNECT_FAILED.format(reason="сесію втрачено"),
                                 reply_markup=kb.connect_menu())
            return
        me = await client.get_me()
        phone = getattr(me, "phone", None) or str(data.get("phone", ""))
        api_id = int(data["api_id"])
        api_hash = str(data["api_hash"])
        await self._db.save_account(user_id, api_id, api_hash, phone)
        await state.clear()
        self._pending_clients.pop(user_id, None)
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            logger.debug("Не вдалося від'єднати клієнт підключення", exc_info=True)
        ok = await self._userbot.start()
        if ok:
            _text, markup = await self._connect_payload(user_id)
            await message.answer(
                texts.CONNECT_SUCCESS.format(user_id=user_id), reply_markup=markup,
            )
        else:
            _text, markup = await self._connect_payload(user_id)
            await message.answer(
                texts.CONNECT_FAILED.format(reason="userbot не запустився"),
                reply_markup=markup,
            )

    async def _refresh_autoreply_menu(self, cb: CallbackQuery) -> None:
        await self._safe_edit_markup(
            cb.message, await self._autoreply_markup(cb.from_user.id),  # type: ignore[arg-type]
        )

    async def _refresh_lists(self, cb: CallbackQuery, field: str, prefix: str) -> None:
        s = await self._db.get_settings(cb.from_user.id)
        items = list(s[field])
        await self._safe_edit_markup(cb.message, kb.list_manage(items, prefix))  # type: ignore[arg-type]

    async def close_pending(self) -> None:
        """Закрити незавершені клієнти підключення (при зупинці застосунку)."""
        for task in list(self._qr_tasks.values()):
            if not task.done():
                task.cancel()
        self._qr_tasks.clear()
        for client in list(self._pending_clients.values()):
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                logger.debug("Не вдалося від'єднати клієнт підключення", exc_info=True)
        self._pending_clients.clear()


def build_router(config: Config, db: Database, userbot: Userbot) -> Router:
    """Створити контрольний бот і повернути його Router.

    Екземпляр ControlBot зберігається атрибутом на Router, щоб main.py
    міг викликати ``close_pending()`` під час зупинки застосунку.
    """
    bot = ControlBot(config, db, userbot)
    bot.router.control_bot = bot
    return bot.router
