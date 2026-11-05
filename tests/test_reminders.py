"""Тести нагадувань: парсер часу та відбір прострочених у БД — без мережі."""
from __future__ import annotations

from datetime import datetime

import pytest

from tg_hub.db import Database
from tg_hub.reminders import format_due, now_local_iso, parse_reminder_time


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "hub.db")
    await database.init()
    return database


NOW = datetime(2026, 11, 5, 12, 0, 0)


# ---------------------------------------------------------------- парсер часу

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("через 30 хв", datetime(2026, 11, 5, 12, 30)),
        ("Через 30 хв", datetime(2026, 11, 5, 12, 30)),
        ("через 2 год", datetime(2026, 11, 5, 14, 0)),
        ("через 1 год", datetime(2026, 11, 5, 13, 0)),
        ("через 3 дні", datetime(2026, 11, 8, 12, 0)),
        ("через 1 день", datetime(2026, 11, 6, 12, 0)),
        ("через 45 хвилин", datetime(2026, 11, 5, 12, 45)),
        ("через 5 годин", datetime(2026, 11, 5, 17, 0)),
    ],
)
def test_parse_relative(raw, expected):
    assert parse_reminder_time(raw, now=NOW) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("25.11.2026 14:30", datetime(2026, 11, 25, 14, 30)),
        ("01.01.2027 00:00", datetime(2027, 1, 1, 0, 0)),
        (" 25.11.2026 14:30 ", datetime(2026, 11, 25, 14, 30)),
    ],
)
def test_parse_absolute(raw, expected):
    assert parse_reminder_time(raw, now=NOW) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "завтра",
        "25-11-2026 14:30",
        "25.11.2026",
        "14:30",
        "через 30 хвилин тому",
        "через",
        "через хв",
        "через  хв",
        "через півгодини",
    ],
)
def test_parse_invalid(raw):
    assert parse_reminder_time(raw, now=NOW) is None


def test_parse_none_and_whitespace():
    assert parse_reminder_time("   ", now=NOW) is None
    assert parse_reminder_time(None, now=NOW) is None


def test_format_due_roundtrip():
    due = datetime(2026, 11, 25, 14, 30, 5)
    iso = due.isoformat(timespec="seconds")
    assert format_due(iso) == "25.11.2026 14:30"


def test_format_due_garbage():
    assert format_due("не-iso") == "не-iso"


# ---------------------------------------------------------------- відбір у БД

@pytest.mark.asyncio
async def test_due_reminder_filtering(db: Database):
    past = "2026-01-01T10:00:00"
    future = "2099-01-01T10:00:00"
    await db.add_reminder(1, past, "прострочене")
    await db.add_reminder(1, future, "майбутнє")

    due = await db.list_due_reminders("2026-11-05T12:00:00")
    assert [r["text"] for r in due] == ["прострочене"]

    # позначили виконаним → більше не в виборці
    assert await db.mark_reminder_done(due[0]["id"]) is True
    assert await db.list_due_reminders("2026-11-05T12:00:00") == []
    # повторна позначка — ні
    assert await db.mark_reminder_done(due[0]["id"]) is False


@pytest.mark.asyncio
async def test_reminders_pending_list_and_delete(db: Database):
    await db.add_reminder(1, "2026-11-06T09:00:00", "перше")
    await db.add_reminder(1, "2026-11-05T09:00:00", "друге")
    pending = await db.list_reminders(1)
    assert [r["text"] for r in pending] == ["друге", "перше"]  # за часом

    assert await db.delete_reminder(1, pending[0]["id"]) is True
    assert await db.delete_reminder(2, pending[0]["id"]) is False  # чужий
    assert len(await db.list_reminders(1)) == 1


@pytest.mark.asyncio
async def test_now_local_iso_sortable_with_parse_output():
    """now_local_iso і вихід парсера — однаковий порівнюваний формат."""
    due = parse_reminder_time("через 30 хв")
    assert due is not None
    assert due.isoformat(timespec="seconds") > now_local_iso()


# --------------------------------------------------- регресія: «через 0 …»

@pytest.mark.parametrize("raw", ["через 0 хв", "через 0 год", "через 0 днів", "через 00 хвилин"])
def test_parse_relative_zero_rejected(raw):
    """«через 0 хв» — це зараз, а не майбутнє: парсер відхиляє."""
    assert parse_reminder_time(raw, now=NOW) is None


# --------------------------------------------------- регресія: цикл надсилання

class _FakeReminderDb:
    """Мінімальний двічі-сторонній підставник Database для reminder_loop."""

    def __init__(self, items):
        self._items = list(items)
        self.done_ids: list[int] = []

    async def list_due_reminders(self, now_iso: str):
        return [i for i in self._items if i["id"] not in self.done_ids]

    async def mark_reminder_done(self, reminder_id: int) -> bool:
        self.done_ids.append(reminder_id)
        return True


@pytest.mark.asyncio
async def test_reminder_loop_sends_overdue_immediately_and_marks_done():
    import asyncio

    from tg_hub.reminders import reminder_loop
    from tg_hub import texts

    db = _FakeReminderDb([{"id": 1, "user_id": 42, "text": "полити квіти"}])
    sent: list[tuple[int, str]] = []

    async def send(user_id: int, text: str) -> None:
        sent.append((user_id, text))

    task = asyncio.create_task(reminder_loop(db, send, interval=0.05))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if db.done_ids:
            break
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert sent == [(42, texts.REMINDER_TEXT.format(text="полити квіти"))]
    assert db.done_ids == [1]


@pytest.mark.asyncio
async def test_reminder_loop_failed_send_is_not_marked_done():
    import asyncio

    from tg_hub.reminders import reminder_loop

    db = _FakeReminderDb([{"id": 7, "user_id": 42, "text": "текст"}])
    calls = {"n": 0}

    async def failing_send(user_id: int, text: str) -> None:
        calls["n"] += 1
        raise RuntimeError("telegram down")

    task = asyncio.create_task(reminder_loop(db, failing_send, interval=0.05))
    await asyncio.sleep(0.17)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # надсилання повторювалося, але нагадування НЕ позначено виконаним
    assert calls["n"] >= 2
    assert db.done_ids == []
