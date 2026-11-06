"""Тести сховища (SQLite через asyncio.to_thread) — без мережі."""
from __future__ import annotations

import pytest

from tg_hub.db import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "hub.db")
    await database.init()
    return database


@pytest.mark.asyncio
async def test_user_and_settings_defaults(db: Database):
    settings = await db.get_settings(1)
    assert settings["auto_reply"] == 0
    assert settings["whitelist_mode"] == 1
    assert settings["ignore_list"] == []
    assert settings["reply_delay_min"] == 1.5
    assert settings["context_messages"] == 20
    # повторне читання не змінює значень
    again = await db.get_settings(1)
    assert again == settings


@pytest.mark.asyncio
async def test_llm_keys_roundtrip_and_activation(db: Database):
    key1 = await db.add_llm_key(1, "openai", "https://api.openai.com/v1", "https://api.openai.com/v1", "sk-aaaabbbb", "gpt-4o-mini")
    key2 = await db.add_llm_key(1, "anthropic", "", None, "sk-ant-ccc", "claude-3-haiku")

    keys = await db.list_llm_keys(1)
    assert [k["id"] for k in keys] == [key1, key2]
    assert all(k["is_active"] == 0 for k in keys)

    assert await db.set_active_key(1, key1) is True
    active = await db.get_active_key(1)
    assert active["id"] == key1
    assert active["api_key"] == "sk-aaaabbbb"

    # перемикання активності: лише один активний
    assert await db.set_active_key(1, key2) is True
    keys = await db.list_llm_keys(1)
    assert {k["is_active"] for k in keys} == {0, 1}
    assert (await db.get_active_key(1))["id"] == key2

    # видалення активного ключа прибирає його зі списку
    assert await db.delete_llm_key(1, key2) is True
    assert await db.get_active_key(1) is None
    # чужий ключ недоступний
    assert await db.set_active_key(2, key1) is False


@pytest.mark.asyncio
async def test_prompts_and_style_profile(db: Database):
    await db.set_base_prompt(7, "Ти — це я.")
    prompts = await db.get_prompts(7)
    assert prompts["base_prompt"] == "Ти — це я."
    assert prompts["style_profile"] == ""

    await db.set_style_profile(7, "Тон: жартівливий.")
    prompts = await db.get_prompts(7)
    assert prompts["base_prompt"] == "Ти — це я."
    assert prompts["style_profile"] == "Тон: жартівливий."


@pytest.mark.asyncio
async def test_lists_add_remove_clear(db: Database):
    assert await db.add_to_list(1, "ignore_list", "@spam") is True
    assert await db.add_to_list(1, "ignore_list", "@spam") is False
    settings = await db.get_settings(1)
    assert settings["ignore_list"] == ["@spam"]

    assert await db.remove_from_list(1, "ignore_list", "@spam") is True
    assert await db.remove_from_list(1, "ignore_list", "@spam") is False

    await db.add_to_list(1, "whitelist_chats", "-100123")
    await db.clear_list(1, "whitelist_chats")
    assert (await db.get_settings(1))["whitelist_chats"] == []

    with pytest.raises(ValueError):
        await db.add_to_list(1, "no_such_field", "x")


@pytest.mark.asyncio
async def test_settings_update_and_last_error(db: Database):
    await db.update_settings(1, auto_reply=1, reply_delay_min=1.5, reply_delay_max=4.0, max_chunks=2)
    settings = await db.get_settings(1)
    assert settings["auto_reply"] == 1
    assert settings["reply_delay_min"] == 1.5
    assert settings["max_chunks"] == 2

    await db.update_settings(1, last_error="LLM: HTTP 500")
    assert (await db.get_settings(1))["last_error"] == "LLM: HTTP 500"

    # невідомі поля ігноруються
    await db.update_settings(1, bogus_field="x")
    assert "bogus_field" not in await db.get_settings(1)


@pytest.mark.asyncio
async def test_analyzed_chats_and_accounts(db: Database):
    await db.mark_analyzed(1, "12345")
    await db.mark_analyzed(1, "12345")  # дублікат оновлює дату
    analyzed = await db.list_analyzed(1)
    assert [a["chat_ref"] for a in analyzed] == ["12345"]
    assert analyzed[0]["analyzed_at"]

    await db.save_account(1, 12345, "0123456789abcdef0123456789abcdef", "+380****2233")
    account = await db.get_account(1)
    assert account["api_id"] == 12345
    assert account["api_hash"] == "0123456789abcdef0123456789abcdef"
    assert (await db.get_any_account())["user_id"] == 1
    assert await db.get_account(2) is None


# -------------------------------------------------------------- журнал Q&A

@pytest.mark.asyncio
async def test_qa_journal_roundtrip_and_limit(db: Database):
    ids = []
    for i in range(12):
        entry_id = await db.add_qa_entry(
            1, f"@chat{i}", f"Чат {i}", f"питання {i}", f"відповідь {i}",
        )
        ids.append(entry_id)
    # чужий користувач не змішується
    await db.add_qa_entry(2, "@other", "Інший", "q", "a")

    entries = await db.list_qa_entries(1, limit=10)
    assert len(entries) == 10
    # останні 10 з 12, від нових до старих
    assert [e["question"] for e in entries] == [f"питання {i}" for i in range(11, 1, -1)]
    first = entries[0]
    assert first["chat_ref"] == "@chat11"
    assert first["chat_title"] == "Чат 11"
    assert first["answer"] == "відповідь 11"
    assert first["created_at"]

    # кожен новий burst — окремий запис (без злиття)
    await db.add_qa_entry(1, "@chat11", "Чат 11", "новий burst", "нова відповідь")
    again = await db.list_qa_entries(1, limit=10)
    assert again[0]["question"] == "новий burst"
    assert again[1]["question"] == "питання 11"

    await db.clear_qa_journal(1)
    assert await db.list_qa_entries(1) == []
    assert await db.list_qa_entries(2)  # чужий журнал не чіпаємо


# -------------------------------------------------------------- база знань

@pytest.mark.asyncio
async def test_kb_entries_roundtrip_and_full_text(db: Database):
    await db.add_kb_entry(1, "Про мене", "Я живу в Києві.")
    await db.add_kb_entry(1, "Правила", "Не гадай — питай.")
    await db.add_kb_entry(2, "Чужий", "чужий текст")

    entries = await db.list_kb_entries(1)
    assert [e["title"] for e in entries] == ["Про мене", "Правила"]

    full = await db.get_full_kb(1)
    assert "Про мене" in full and "Правила" in full
    assert full.index("Про мене") < full.index("Правила")
    assert "чужий текст" not in full

    assert await db.kb_total_chars(1) == len("Я живу в Києві.") + len("Не гадай — питай.")

    entry = await db.get_kb_entry(1, entries[0]["id"])
    assert entry["content"] == "Я живу в Києві."
    assert await db.get_kb_entry(2, entries[0]["id"]) is None  # чужий запис недоступний

    assert await db.delete_kb_entry(1, entries[0]["id"]) is True
    assert await db.delete_kb_entry(1, entries[0]["id"]) is False
    assert len(await db.list_kb_entries(1)) == 1

    await db.replace_kb_entries(1, [{"title": "Новий", "content": "Вміст"}])
    assert [e["title"] for e in await db.list_kb_entries(1)] == ["Новий"]


# -------------------------------------------------------------- профілі

@pytest.mark.asyncio
async def test_settings_profile_name_default_and_update(db: Database):
    settings = await db.get_settings(1)
    assert settings["profile_name"] == ""

    await db.update_settings(1, profile_name="Робота")
    assert (await db.get_settings(1))["profile_name"] == "Робота"


@pytest.mark.asyncio
async def test_profile_apply_copies_settings_prompts_and_kb(db: Database):
    import json

    # поточний стан користувача
    await db.update_settings(
        1,
        auto_reply=1,
        reply_delay_min=7.0,
        reply_delay_max=15.0,
        cooldown_seconds=120,
        max_chunks=2,
        context_messages=30,
        whitelist_mode=0,
        whitelist_chats=["@one"],
        ignore_list=["@spam"],
        only_private_or_mention=1,
    )
    await db.set_base_prompt(1, "Промпт профілю «Робота».")
    await db.add_kb_entry(1, "База", "Вміст бази профілю.")

    snapshot = {
        "settings": {
            key: value
            for key, value in (await db.get_settings(1)).items()
            if key not in ("last_error", "profile_name")
        },
        "base_prompt": (await db.get_prompts(1))["base_prompt"],
        "kb": [
            {"title": e["title"], "content": e["content"]}
            for e in await db.list_kb_entries(1)
        ],
    }
    profile_id = await db.save_profile(1, "Робота", json.dumps(snapshot, ensure_ascii=False))

    # «зіпсуємо» активні налаштування
    await db.update_settings(
        1,
        auto_reply=0,
        reply_delay_min=60.0,
        reply_delay_max=90.0,
        cooldown_seconds=600,
        max_chunks=5,
        context_messages=5,
        whitelist_mode=1,
        whitelist_chats=[],
        ignore_list=[],
        only_private_or_mention=0,
        profile_name="",
    )
    await db.set_base_prompt(1, "Інший промпт.")
    await db.replace_kb_entries(1, [{"title": "Зайвий", "content": "зайвий"}])

    assert await db.apply_profile(1, profile_id) is True
    settings = await db.get_settings(1)
    assert settings["auto_reply"] == 1
    assert settings["reply_delay_min"] == 7.0
    assert settings["reply_delay_max"] == 15.0
    assert settings["cooldown_seconds"] == 120
    assert settings["max_chunks"] == 2
    assert settings["context_messages"] == 30
    assert settings["whitelist_mode"] == 0
    assert settings["whitelist_chats"] == ["@one"]
    assert settings["ignore_list"] == ["@spam"]
    assert settings["only_private_or_mention"] == 1
    assert settings["profile_name"] == "Робота"

    assert (await db.get_prompts(1))["base_prompt"] == "Промпт профілю «Робота»."
    kb_entries = await db.list_kb_entries(1)
    assert [(e["title"], e["content"]) for e in kb_entries] == [
        ("База", "Вміст бази профілю.")
    ]

    # профіль іншого користувача не застосовується
    assert await db.apply_profile(2, profile_id) is False
    assert await db.apply_profile(1, 999) is False


@pytest.mark.asyncio
async def test_profiles_list_get_delete(db: Database):
    first = await db.save_profile(1, "Перший", "{}")
    second = await db.save_profile(1, "Другий", "{}")
    await db.save_profile(2, "Чужий", "{}")

    profiles = await db.list_profiles(1)
    assert [p["name"] for p in profiles] == ["Перший", "Другий"]

    got = await db.get_profile(1, second)
    assert got["name"] == "Другий"
    assert got["data"] == "{}"
    assert await db.get_profile(2, second) is None

    assert await db.delete_profile(1, first) is True
    assert await db.delete_profile(1, first) is False
    assert [p["id"] for p in await db.list_profiles(1)] == [second]


@pytest.mark.asyncio
async def test_qa_entry_texts_capped(db: Database):
    """Гігантські тексти не потрапляють у журнал повністю (ліміт 2000 символів)."""
    from tg_hub.db import QA_TEXT_MAX_CHARS

    big_q = "п" * 10_000
    big_a = "в" * 10_000
    await db.add_qa_entry(1, "@chat", "Чат", big_q, big_a)
    entry = (await db.list_qa_entries(1))[0]
    assert len(entry["question"]) == QA_TEXT_MAX_CHARS
    assert len(entry["answer"]) == QA_TEXT_MAX_CHARS

    # короткі тексти не обрізаються
    await db.add_qa_entry(1, "@chat", "Чат", "коротке", "коротка")
    short = (await db.list_qa_entries(1))[0]
    assert short["question"] == "коротке" and short["answer"] == "коротка"


@pytest.mark.asyncio
async def test_chat_profile_roundtrip(db):
    # негативний chat_ref (супергрупи Bot API: -100...) проходить повний цикл
    await db.save_chat_profile(1, "-100300", "Профіль: пишу з великої, жартую")
    got = await db.get_chat_profile(1, "-100300")
    assert "жартую" in got
    # upsert: повторне збереження оновлює, а не дублює
    await db.save_chat_profile(1, "-100300", "Оновлений")
    assert (await db.get_chat_profile(1, "-100300")) == "Оновлений"
    assert (await db.get_chat_profile(1, "999")) == ""
    assert (await db.get_chat_profile(2, "-100300")) == ""  # чужий user_id не бачить
    rows = await db.list_chat_profiles(1)
    assert len(rows) == 1 and rows[0]["chat_ref"] == "-100300"
    # приватний чат: позитивний id теж працює
    await db.save_chat_profile(1, "123456789", "Інший профіль")
    assert (await db.get_chat_profile(1, "123456789")) == "Інший профіль"
    assert len(await db.list_chat_profiles(1)) == 2
    await db.delete_chat_profile(1, "-100300")
    assert (await db.get_chat_profile(1, "-100300")) == ""
    assert len(await db.list_chat_profiles(1)) == 1
    # видалення неіснуючого — тихо, без помилок
    await db.delete_chat_profile(1, "-100300")
