"""Сховище: SQLite (stdlib sqlite3), кожен виклик — окреме з'єднання в asyncio.to_thread.

Жодних блокуючих викликів у циклі подій: всі операції з БД виконуються
у робочому потоці через :func:`asyncio.to_thread`.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_user_id INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS llm_keys (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    provider   TEXT    NOT NULL,
    label      TEXT    NOT NULL DEFAULT '',
    base_url   TEXT,
    api_key    TEXT    NOT NULL,
    model      TEXT    NOT NULL,
    is_active  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS prompts (
    user_id       INTEGER PRIMARY KEY,
    base_prompt   TEXT,
    style_profile TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    user_id                INTEGER PRIMARY KEY,
    auto_reply             INTEGER NOT NULL DEFAULT 0,
    whitelist_mode         INTEGER NOT NULL DEFAULT 1,
    whitelist_chats        TEXT    NOT NULL DEFAULT '[]',
    ignore_list            TEXT    NOT NULL DEFAULT '[]',
    reply_delay_min        REAL    NOT NULL DEFAULT 3.0,
    reply_delay_max        REAL    NOT NULL DEFAULT 10.0,
    only_private_or_mention INTEGER NOT NULL DEFAULT 0,
    context_messages       INTEGER NOT NULL DEFAULT 20,
    cooldown_seconds       INTEGER NOT NULL DEFAULT 60,
    max_chunks             INTEGER NOT NULL DEFAULT 3,
    last_error             TEXT
);

CREATE TABLE IF NOT EXISTS analyzed_chats (
    user_id     INTEGER NOT NULL,
    chat_ref    TEXT    NOT NULL,
    analyzed_at TEXT    NOT NULL,
    PRIMARY KEY (user_id, chat_ref)
);

CREATE TABLE IF NOT EXISTS accounts (
    user_id     INTEGER PRIMARY KEY,
    api_id      INTEGER NOT NULL,
    api_hash    TEXT    NOT NULL,
    phone       TEXT    NOT NULL DEFAULT '',
    connected_at TEXT   NOT NULL DEFAULT ''
);
"""

DEFAULT_SETTINGS: dict[str, Any] = {
    "auto_reply": 0,
    "whitelist_mode": 1,
    "whitelist_chats": [],
    "ignore_list": [],
    "reply_delay_min": 3.0,
    "reply_delay_max": 10.0,
    "only_private_or_mention": 0,
    "context_messages": 20,
    "cooldown_seconds": 60,
    "max_chunks": 3,
    "last_error": None,
}

_JSON_SETTINGS_FIELDS = frozenset({"whitelist_chats", "ignore_list"})
_SETTINGS_FIELDS = frozenset(DEFAULT_SETTINGS)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Асинхронна обгортка над SQLite-файлом."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()

    # --- внутрішнє ---

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def _run(self, fn: Callable[..., Any], *args: Any) -> Any:
        async with self._lock:
            return await asyncio.to_thread(fn, *args)

    def _row_to_key(self, row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    # --- ініціалізація ---

    def _init_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    async def init(self) -> None:
        await self._run(self._init_sync)

    @property
    def path(self) -> Path:
        return self._path

    # --- користувачі ---

    def _ensure_user_sync(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO users(telegram_user_id) VALUES (?)", (user_id,))
            conn.execute("INSERT OR IGNORE INTO settings(user_id) VALUES (?)", (user_id,))

    async def ensure_user(self, user_id: int) -> None:
        await self._run(self._ensure_user_sync, user_id)

    # --- LLM-ключі ---

    def _add_key_sync(self, user_id: int, provider: str, label: str,
                      base_url: str | None, api_key: str, model: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO llm_keys(user_id, provider, label, base_url, api_key, model, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",
                (user_id, provider, label, base_url, api_key, model),
            )
            return int(cur.lastrowid or 0)

    async def add_llm_key(self, user_id: int, provider: str, label: str,
                          base_url: str | None, api_key: str, model: str) -> int:
        return await self._run(self._add_key_sync, user_id, provider, label, base_url, api_key, model)

    def _list_keys_sync(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM llm_keys WHERE user_id = ? ORDER BY id", (user_id,)
            ).fetchall()
        return [self._row_to_key(r) for r in rows]

    async def list_llm_keys(self, user_id: int) -> list[dict[str, Any]]:
        return await self._run(self._list_keys_sync, user_id)

    def _delete_key_sync(self, user_id: int, key_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM llm_keys WHERE id = ? AND user_id = ?", (key_id, user_id)
            )
            return cur.rowcount > 0

    async def delete_llm_key(self, user_id: int, key_id: int) -> bool:
        return await self._run(self._delete_key_sync, user_id, key_id)

    def _set_active_key_sync(self, user_id: int, key_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE llm_keys SET is_active = 1 WHERE id = ? AND user_id = ?",
                (key_id, user_id),
            )
            if cur.rowcount == 0:
                return False
            conn.execute(
                "UPDATE llm_keys SET is_active = 0 WHERE user_id = ? AND id != ?",
                (user_id, key_id),
            )
            return True

    async def set_active_key(self, user_id: int, key_id: int) -> bool:
        return await self._run(self._set_active_key_sync, user_id, key_id)

    def _active_key_sync(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM llm_keys WHERE user_id = ? AND is_active = 1 LIMIT 1", (user_id,)
            ).fetchone()
        return self._row_to_key(row) if row else None

    async def get_active_key(self, user_id: int) -> dict[str, Any] | None:
        return await self._run(self._active_key_sync, user_id)

    # --- промпти та профіль стилю ---

    def _get_prompt_sync(self, user_id: int) -> dict[str, str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT base_prompt, style_profile FROM prompts WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row is None:
            return {"base_prompt": "", "style_profile": ""}
        return {"base_prompt": row["base_prompt"] or "", "style_profile": row["style_profile"] or ""}

    async def get_prompts(self, user_id: int) -> dict[str, str]:
        return await self._run(self._get_prompt_sync, user_id)

    def _set_prompt_sync(self, user_id: int, base_prompt: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO prompts(user_id, base_prompt, style_profile) VALUES (?, ?, '') "
                "ON CONFLICT(user_id) DO UPDATE SET base_prompt = excluded.base_prompt",
                (user_id, base_prompt),
            )

    async def set_base_prompt(self, user_id: int, base_prompt: str) -> None:
        await self._run(self._set_prompt_sync, user_id, base_prompt)

    def _set_style_sync(self, user_id: int, style_profile: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO prompts(user_id, base_prompt, style_profile) VALUES (?, '', ?) "
                "ON CONFLICT(user_id) DO UPDATE SET style_profile = excluded.style_profile",
                (user_id, style_profile),
            )

    async def set_style_profile(self, user_id: int, style_profile: str) -> None:
        await self._run(self._set_style_sync, user_id, style_profile)

    # --- налаштування ---

    def _get_settings_sync(self, user_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            self._ensure_user_sync(user_id)
            row = conn.execute(
                "SELECT * FROM settings WHERE user_id = ?", (user_id,)
            ).fetchone()
        result = dict(DEFAULT_SETTINGS)
        if row is not None:
            for field in DEFAULT_SETTINGS:
                if field in row.keys():
                    result[field] = row[field]
        for field in _JSON_SETTINGS_FIELDS:
            try:
                result[field] = json.loads(result[field] or "[]")
            except (json.JSONDecodeError, TypeError):
                result[field] = []
        return result

    async def get_settings(self, user_id: int) -> dict[str, Any]:
        return await self._run(self._get_settings_sync, user_id)

    def _update_settings_sync(self, user_id: int, fields: dict[str, Any]) -> None:
        clean = {k: v for k, v in fields.items() if k in _SETTINGS_FIELDS}
        if not clean:
            return
        for field in _JSON_SETTINGS_FIELDS:
            if field in clean:
                clean[field] = json.dumps(clean[field], ensure_ascii=False)
        columns = ", ".join(clean)
        placeholders = ", ".join("?" for _ in clean)
        assignments = ", ".join(f"{field} = ?" for field in clean)
        values = [user_id, *clean.values(), *clean.values()]
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO settings(user_id, {columns}) VALUES (?, {placeholders}) "
                f"ON CONFLICT(user_id) DO UPDATE SET {assignments}",
                values,
            )

    async def update_settings(self, user_id: int, **fields: Any) -> None:
        await self._run(self._update_settings_sync, user_id, fields)

    # --- списки чатів (ігнор / білий список) ---

    async def add_to_list(self, user_id: int, field: str, value: str) -> bool:
        """Додати елемент до списку; повертає False, якщо він уже там."""
        if field not in _JSON_SETTINGS_FIELDS:
            raise ValueError(f"Невідоме поле списку: {field}")
        settings = await self.get_settings(user_id)
        items: list[str] = list(settings[field])
        if value in items:
            return False
        items.append(value)
        await self.update_settings(user_id, **{field: items})
        return True

    async def remove_from_list(self, user_id: int, field: str, value: str) -> bool:
        if field not in _JSON_SETTINGS_FIELDS:
            raise ValueError(f"Невідоме поле списку: {field}")
        settings = await self.get_settings(user_id)
        items: list[str] = list(settings[field])
        if value not in items:
            return False
        items.remove(value)
        await self.update_settings(user_id, **{field: items})
        return True

    async def clear_list(self, user_id: int, field: str) -> None:
        if field not in _JSON_SETTINGS_FIELDS:
            raise ValueError(f"Невідоме поле списку: {field}")
        await self.update_settings(user_id, **{field: []})

    # --- проаналізовані чати ---

    def _mark_analyzed_sync(self, user_id: int, chat_ref: str) -> None:
        with self._connect() as conn:
            self._ensure_user_sync(user_id)
            conn.execute(
                "INSERT INTO analyzed_chats(user_id, chat_ref, analyzed_at) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, chat_ref) DO UPDATE SET analyzed_at = excluded.analyzed_at",
                (user_id, chat_ref, _now_iso()),
            )

    async def mark_analyzed(self, user_id: int, chat_ref: str) -> None:
        await self._run(self._mark_analyzed_sync, user_id, chat_ref)

    def _list_analyzed_sync(self, user_id: int) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT chat_ref, analyzed_at FROM analyzed_chats WHERE user_id = ? ORDER BY analyzed_at DESC",
                (user_id,),
            ).fetchall()
        return [{"chat_ref": r["chat_ref"], "analyzed_at": r["analyzed_at"]} for r in rows]

    async def list_analyzed(self, user_id: int) -> list[dict[str, str]]:
        return await self._run(self._list_analyzed_sync, user_id)

    # --- підключений акаунт (Telethon) ---

    def _save_account_sync(self, user_id: int, api_id: int, api_hash: str, phone: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO accounts(user_id, api_id, api_hash, phone, connected_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET api_id = excluded.api_id, "
                "api_hash = excluded.api_hash, phone = excluded.phone, "
                "connected_at = excluded.connected_at",
                (user_id, api_id, api_hash, phone, _now_iso()),
            )

    async def save_account(self, user_id: int, api_id: int, api_hash: str, phone: str) -> None:
        await self._run(self._save_account_sync, user_id, api_id, api_hash, phone)

    def _get_account_sync(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE user_id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    async def get_account(self, user_id: int) -> dict[str, Any] | None:
        return await self._run(self._get_account_sync, user_id)

    def _any_account_sync(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM accounts LIMIT 1").fetchone()
        return dict(row) if row else None

    async def get_any_account(self) -> dict[str, Any] | None:
        return await self._run(self._any_account_sync)
