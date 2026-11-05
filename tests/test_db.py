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
    assert settings["reply_delay_min"] == 3.0
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

    await db.save_account(1, 12345, "0123456789abcdef0123456789abcdef", "+380991112233")
    account = await db.get_account(1)
    assert account["api_id"] == 12345
    assert account["api_hash"] == "0123456789abcdef0123456789abcdef"
    assert (await db.get_any_account())["user_id"] == 1
    assert await db.get_account(2) is None
