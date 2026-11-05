"""Побудувачі клавіатур (reply та inline) для контрольного бота."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.types import ReplyKeyboardMarkup
from aiogram.utils.keyboard import KeyboardButton

from . import texts


def main_menu() -> ReplyKeyboardMarkup:
    """Головне меню керування (reply-клавіатура)."""
    rows = [
        [KeyboardButton(text=texts.BTN_API_KEYS), KeyboardButton(text=texts.BTN_CONNECT)],
        [KeyboardButton(text=texts.BTN_PROMPT), KeyboardButton(text=texts.BTN_STYLE)],
        [KeyboardButton(text=texts.BTN_AUTOREPLY), KeyboardButton(text=texts.BTN_STATUS)],
        [KeyboardButton(text=texts.BTN_REMINDERS), KeyboardButton(text=texts.BTN_PROFILES)],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def back_button_row() -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(text=texts.BTN_BACK, callback_data="nav:back")]]


def api_keys_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=texts.BTN_KEY_ADD, callback_data="key:add")],
        [InlineKeyboardButton(text=texts.BTN_KEY_LIST, callback_data="key:list")],
        *back_button_row(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def provider_choice() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=texts.PROVIDER_GOOGLE, callback_data="keyprov:google")],
        [InlineKeyboardButton(text="🟢 OpenAI", callback_data="keyprov:openai:https://api.openai.com/v1")],
        [InlineKeyboardButton(text="🟣 OpenRouter", callback_data="keyprov:openai:https://openrouter.ai/api/v1")],
        [InlineKeyboardButton(text="🟡 DeepSeek", callback_data="keyprov:openai:https://api.deepseek.com/v1")],
        [InlineKeyboardButton(text="🟠 Anthropic", callback_data="keyprov:anthropic")],
        [InlineKeyboardButton(text="⚙️ Інший OpenAI-сумісний (свій URL)", callback_data="keyprov:openai")],
        *back_button_row(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def model_choice(models: list[str]) -> InlineKeyboardMarkup:
    """Вибір моделі зі списку, отриманого від провайдера."""
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for name in models[:30]:
        current.append(InlineKeyboardButton(
            text=name, callback_data=f"keymodel:{name}",
        ))
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    rows.append([InlineKeyboardButton(
        text="⌨️ Інша модель (ввести вручну)", callback_data="keymodel:__manual__",
    )])
    rows.extend(back_button_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def key_actions(key_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"✅ Активувати #{key_id}", callback_data=f"key:activate:{key_id}")],
        [InlineKeyboardButton(text=f"🗑 Видалити #{key_id}", callback_data=f"key:delete:{key_id}")],
        *back_button_row(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def key_list(keys: list[dict]) -> InlineKeyboardMarkup:
    """Список ключів: кожен ключ — кнопка активації, видалення — окремим рядком."""
    rows: list[list[InlineKeyboardButton]] = []
    for key in keys:
        mark = " ⭐" if key["is_active"] else ""
        label = f"#{key['id']} · {key['provider']} · {key['model']}{mark}"
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"key:activate:{key['id']}"),
            InlineKeyboardButton(text="🗑", callback_data=f"key:delete:{key['id']}"),
        ])
    if not rows:
        rows.append([InlineKeyboardButton(text="—", callback_data="noop")])
    rows.extend(back_button_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def connect_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=texts.BTN_CONNECT_START, callback_data="connect:start")],
        [InlineKeyboardButton(text=texts.BTN_QR_LOGIN, callback_data="connect:qr")],
        *back_button_row(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def prompt_menu() -> InlineKeyboardMarkup:
    """Меню «📝 Промпт»: дві частини — короткий промпт і база знань."""
    rows = [
        [InlineKeyboardButton(text=texts.BTN_PROMPT_SHORT, callback_data="prompt:short")],
        [InlineKeyboardButton(text=texts.BTN_KB, callback_data="kb:open")],
        *back_button_row(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def prompt_edit_menu() -> InlineKeyboardMarkup:
    """Керування коротким промптом: змінити / скинути."""
    rows = [
        [InlineKeyboardButton(text=texts.BTN_PROMPT_EDIT, callback_data="prompt:edit")],
        [InlineKeyboardButton(text=texts.BTN_PROMPT_RESET, callback_data="prompt:reset")],
        *back_button_row(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=texts.BTN_KB_ADD, callback_data="kb:add")],
        [InlineKeyboardButton(text=texts.BTN_KB_LIST, callback_data="kb:list")],
        [InlineKeyboardButton(text=texts.BTN_KB_EXPORT, callback_data="kb:export")],
        *back_button_row(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_entries_list(entries: list[dict]) -> InlineKeyboardMarkup:
    """Список записів бази знань: перегляд (кнопка з назвою) + видалення."""
    rows: list[list[InlineKeyboardButton]] = []
    for entry in entries:
        title = (entry["title"] or "Без назви")[:40]
        rows.append([
            InlineKeyboardButton(text=f"📄 {title}", callback_data=f"kb:view:{entry['id']}"),
            InlineKeyboardButton(text=texts.BTN_REM_DELETE, callback_data=f"kb:del:{entry['id']}"),
        ])
    if not rows:
        rows.append([InlineKeyboardButton(text="—", callback_data="noop")])
    rows.append([InlineKeyboardButton(text=texts.BTN_KB_ADD, callback_data="kb:add")])
    rows.extend(back_button_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def journal_menu(reminders: list[dict]) -> InlineKeyboardMarkup:
    """Меню «🔔 Нагадування»: додати нагадування, список активних, очистити журнал."""
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=texts.BTN_REMINDER_ADD, callback_data="jr:add")],
    ]
    for item in reminders:
        label = f"🗑 {item['due_at']} · {(item['text'] or '')[:40]}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"rem:del:{item['id']}")])
    rows.append([InlineKeyboardButton(text=texts.BTN_JOURNAL_CLEAR, callback_data="jr:clear")])
    rows.extend(back_button_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profiles_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=texts.BTN_PROFILE_SAVE, callback_data="prof:save")],
        [InlineKeyboardButton(text=texts.BTN_PROFILE_LIST, callback_data="prof:list")],
        *back_button_row(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile_list(profiles: list[dict], active_name: str) -> InlineKeyboardMarkup:
    """Список профілів: «✅ Увімкнути #N» і «🗑 Видалити #N» для кожного."""
    rows: list[list[InlineKeyboardButton]] = []
    for profile in profiles:
        is_active = profile["name"] == active_name
        rows.append([
            InlineKeyboardButton(
                text=f"✅ Увімкнути #{profile['id']}{texts.PROFILE_ACTIVE_MARK if is_active else ''}",
                callback_data=f"prof:apply:{profile['id']}",
            ),
            InlineKeyboardButton(
                text=f"🗑 #{profile['id']}",
                callback_data=f"prof:del:{profile['id']}",
            ),
        ])
    if not rows:
        rows.append([InlineKeyboardButton(text="—", callback_data="noop")])
    rows.extend(back_button_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def style_menu() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=texts.BTN_STYLE_ONE, callback_data="style:one"),
            InlineKeyboardButton(text=texts.BTN_STYLE_MULTI, callback_data="style:multi"),
        ],
        [InlineKeyboardButton(text=texts.BTN_STYLE_ALL, callback_data="style:all")],
        [
            InlineKeyboardButton(text=texts.BTN_STYLE_SHOW, callback_data="style:show"),
            InlineKeyboardButton(text=texts.BTN_STYLE_REGEN, callback_data="style:regen"),
        ],
        *back_button_row(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dialogs_one(dialogs: list[tuple[int, str, str]]) -> InlineKeyboardMarkup:
    """Список діалогів для вибору ОДНОГО чату: (chat_id, title, callback_ref)."""
    rows = [
        [InlineKeyboardButton(text=title, callback_data=f"style:pickone:{ref}")]
        for _chat_id, title, ref in dialogs
    ]
    if not rows:
        rows = [[InlineKeyboardButton(text="—", callback_data="noop")]]
    rows.extend(back_button_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dialogs_multi(dialogs: list[tuple[int, str, str]], selected: set[str]) -> InlineKeyboardMarkup:
    """Список діалогів для мультивибору з підтвердженням."""
    rows: list[list[InlineKeyboardButton]] = []
    for _chat_id, title, ref in dialogs:
        mark = "☑️ " if ref in selected else "▫️ "
        rows.append([InlineKeyboardButton(text=mark + title, callback_data=f"style:toggle:{ref}")])
    if not rows:
        rows = [[InlineKeyboardButton(text="—", callback_data="noop")]]
    rows.append([
        InlineKeyboardButton(text=texts.BTN_STYLE_DONE_MULTI, callback_data="style:donemulti"),
        InlineKeyboardButton(text=texts.BTN_MULTI_CLEAR, callback_data="style:clearmulti"),
    ])
    rows.extend(back_button_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def autoreply_menu(
    *,
    auto_reply: bool,
    whitelist_mode: bool,
    only_mention: bool,
    reply_delay_min: float,
    reply_delay_max: float,
    max_chunks: int,
    context_messages: int,
    cooldown_seconds: int,
) -> InlineKeyboardMarkup:
    on_off = texts.STATE_ON if auto_reply else texts.STATE_OFF
    mode = texts.MODE_WHITELIST if whitelist_mode else texts.MODE_ALL_EXCEPT
    mention = texts.STATE_ON if only_mention else texts.STATE_OFF
    rows = [
        [InlineKeyboardButton(text=texts.BTN_TOGGLE_AUTOREPLY.format(state=on_off), callback_data="ar:toggle")],
        [InlineKeyboardButton(text=texts.BTN_TOGGLE_MODE.format(mode=mode), callback_data="ar:mode")],
        [InlineKeyboardButton(text=texts.BTN_TOGGLE_MENTION.format(state=mention), callback_data="ar:mention")],
        [
            InlineKeyboardButton(text=texts.BTN_IGNORE_LIST, callback_data="ar:ignore"),
            InlineKeyboardButton(text=texts.BTN_WHITELIST, callback_data="ar:whitelist"),
        ],
        [
            InlineKeyboardButton(
                text=texts.BTN_REPLY_DELAY.format(min=reply_delay_min, max=reply_delay_max),
                callback_data="ar:delay",
            )
        ],
        [
            InlineKeyboardButton(text=texts.BTN_MAX_CHUNKS.format(value=max_chunks), callback_data="ar:chunks"),
            InlineKeyboardButton(text=texts.BTN_CONTEXT_COUNT.format(value=context_messages), callback_data="ar:context"),
        ],
        [InlineKeyboardButton(text=texts.BTN_COOLDOWN.format(value=cooldown_seconds), callback_data="ar:cooldown")],
        *back_button_row(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def list_manage(items: list[str], prefix: str) -> InlineKeyboardMarkup:
    """Керування списком (ігнор/білий): кнопки видалення елементів + дії."""
    rows = [
        [InlineKeyboardButton(text=f"➖ {item}", callback_data=f"{prefix}:del:{item}")]
        for item in items
    ]
    rows.append([
        InlineKeyboardButton(text=texts.BTN_LIST_ADD, callback_data=f"{prefix}:add"),
        InlineKeyboardButton(text=texts.BTN_LIST_CLEAR, callback_data=f"{prefix}:clear"),
    ])
    rows.extend(back_button_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_CANCEL, callback_data="nav:cancel")]])
