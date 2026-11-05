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
