"""Нагадування: парсер часу (без зовнішніх залежностей) і фоновий цикл.

Парсер приймає:
- абсолютний час: ``ДД.ММ.РРРР ГГ:ХХ`` (наївний локальний час);
- відносний час: ``через 30 хв``, ``через 2 год``, ``через 3 дні``.

Час зберігається як ISO-рядок наївного локального часу — порівняння рядків
ISO-форматів коректне, тому відбір прострочених нагадувань робиться в SQL.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from . import texts
from .db import Database

logger = logging.getLogger(__name__)

REMINDER_CHECK_INTERVAL = 30.0  # секунди між перевірками прострочених нагадувань

# «через 30 хв» / «через 2 год» / «через 3 дні» (та повні слова)
_RELATIVE_RE = re.compile(
    r"через\s+(\d{1,4})\s*(хв|хвилин|хвилини|год|годин|години|годину|дн|дні|днів|день)",
    re.IGNORECASE,
)
_ABSOLUTE_FORMAT = "%d.%m.%Y %H:%M"

_UNIT_SECONDS = {
    "хв": 60,
    "хвилин": 60,
    "хвилини": 60,
    "год": 3600,
    "годин": 3600,
    "години": 3600,
    "годину": 3600,
    "дн": 86400,
    "дні": 86400,
    "днів": 86400,
    "день": 86400,
}


def now_local_iso() -> str:
    """Наївний локальний час у ISO з точністю до секунди (для due_at/now)."""
    return datetime.now().isoformat(timespec="seconds")


def parse_reminder_time(raw: str, now: datetime | None = None) -> datetime | None:
    """Розібрати час нагадування. Повертає наївний локальний ``datetime`` або None.

    ``now`` — точка відліку для відносних форматів (за замовчуванням — зараз).
    """
    text = (raw or "").strip().lower()
    if not text:
        return None
    base = now or datetime.now()

    match = _RELATIVE_RE.fullmatch(text)
    if match:
        value = int(match.group(1))
        unit = match.group(2)
        return base + timedelta(seconds=value * _UNIT_SECONDS[unit])

    try:
        return datetime.strptime(text, _ABSOLUTE_FORMAT)
    except ValueError:
        return None


def format_due(due_at: str) -> str:
    """ISO-рядок → «ДД.ММ.РРРР ГГ:ХХ» для показу в меню."""
    try:
        parsed = datetime.fromisoformat(due_at)
    except (ValueError, TypeError):
        return due_at
    return parsed.strftime("%d.%m.%Y %H:%M")


async def reminder_loop(db: Database, send: Callable[[int, str], Awaitable[None]],
                        interval: float = REMINDER_CHECK_INTERVAL) -> None:
    """Фоновий цикл: кожні ``interval`` секунд надсилає спровочені нагадування.

    ``send(user_id, text)`` — async-колбек надсилання (контрольний бот).
    """
    while True:
        try:
            due = await db.list_due_reminders(now_local_iso())
            for item in due:
                try:
                    await send(int(item["user_id"]), texts.REMINDER_TEXT.format(text=item["text"]))
                except Exception:  # noqa: BLE001 — не роняємо цикл через Telegram
                    logger.exception("Не вдалося надіслати нагадування #%s", item["id"])
                try:
                    await db.mark_reminder_done(int(item["id"]))
                except Exception:  # noqa: BLE001
                    logger.exception("Не вдалося позначити нагадування #%s виконаним", item["id"])
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — цикл живе вічно
            logger.exception("Помилка у фоновому циклі нагадувань")
        await asyncio.sleep(interval)


def serialize_snapshot(data: dict[str, Any]) -> str:
    """JSON-серіалізація знімка профілю (з Unicode без \\u-екранування)."""
    return json.dumps(data, ensure_ascii=False)
