"""Тести LLM-рівня: збірка payload, парсинг відповідей, форматування контексту.

Жодних мережевих викликів.
"""
from __future__ import annotations

import pytest

import httpx

from tg_hub.llm import (
    AnthropicClient,
    GoogleClient,
    LLMError,
    OpenAICompatClient,
    create_client,
)
from tg_hub.llm.anthropic_provider import build_anthropic_payload, parse_anthropic_response
from tg_hub.llm.google_provider import build_google_payload, build_google_url, parse_google_response
from tg_hub.llm.openai_compat import build_openai_payload, parse_openai_response
from tg_hub.persona.analyzer import build_style_user_prompt
from tg_hub.persona.styles import DEFAULT_BASE_PROMPT, DEFAULT_STYLE_META_PROMPT
from tg_hub.userbot import format_context, split_reply, strip_markdown_artifacts

MESSAGES = [
    {"role": "user", "content": "Привіт!"},
    {"role": "assistant", "content": "Привіт-привіт."},
]


# ---------------------------------------------------------------- OpenAI-сумісний

def test_openai_payload_includes_system_first():
    payload = build_openai_payload("gpt-4o-mini", MESSAGES, "Ти — бот.")
    assert payload["model"] == "gpt-4o-mini"
    assert payload["messages"][0] == {"role": "system", "content": "Ти — бот."}
    assert payload["messages"][1:] == MESSAGES


def test_openai_payload_without_system():
    payload = build_openai_payload("m", MESSAGES, "")
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant"]


def test_openai_parse_response():
    data = {"choices": [{"message": {"role": "assistant", "content": "Відповідь."}}]}
    assert parse_openai_response(data) == "Відповідь."


def test_openai_parse_response_errors():
    with pytest.raises(LLMError):
        parse_openai_response({})
    with pytest.raises(LLMError):
        parse_openai_response({"choices": [{"message": {"content": None}}]})


# ---------------------------------------------------------------- Anthropic

def test_anthropic_payload_system_separate():
    payload = build_anthropic_payload("claude-3-haiku", MESSAGES, "Системний промпт")
    assert payload["system"] == "Системний промпт"
    assert payload["model"] == "claude-3-haiku"
    assert payload["max_tokens"] > 0
    assert "system" not in payload["messages"][0]


def test_anthropic_parse_response_joins_text_blocks():
    data = {"content": [{"type": "text", "text": "Частина 1 "}, {"type": "tool_use"}, {"type": "text", "text": "Частина 2"}]}
    assert parse_anthropic_response(data) == "Частина 1 Частина 2"


def test_anthropic_parse_response_errors():
    with pytest.raises(LLMError):
        parse_anthropic_response({"content": []})
    with pytest.raises(LLMError):
        parse_anthropic_response({})


# ---------------------------------------------------------------- Google

def test_google_url_and_payload():
    url = build_google_url("gemini-1.5-flash", "KEY123")
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=KEY123"

    payload = build_google_payload(MESSAGES, "Системний промпт")
    assert payload["system_instruction"] == {"parts": [{"text": "Системний промпт"}]}
    assert payload["contents"][1]["role"] == "model"  # assistant -> model
    assert payload["contents"][0]["parts"] == [{"text": "Привіт!"}]


def test_google_parse_response():
    data = {"candidates": [{"content": {"parts": [{"text": "Привіт"}, {"text": "!"}]}}]}
    assert parse_google_response(data) == "Привіт!"


def test_google_parse_response_errors():
    with pytest.raises(LLMError):
        parse_google_response({"candidates": []})
    with pytest.raises(LLMError):
        parse_google_response({})


# ---------------------------------------------------------------- фабрика

def test_create_client_factory():
    client = create_client("openai", "https://api.example.com/v1/", "key", "m1", timeout=30)
    assert isinstance(client, OpenAICompatClient)
    assert client.base_url == "https://api.example.com/v1"
    assert client.timeout == 30

    assert isinstance(create_client("anthropic", None, "key", "m2"), AnthropicClient)
    assert isinstance(create_client("google", None, "key", "m3"), GoogleClient)
    assert isinstance(create_client("openai-compatible", None, "key", "m4"), OpenAICompatClient)

    with pytest.raises(ValueError):
        create_client("unknown", None, "key", "m")


def test_client_validates_args():
    with pytest.raises(ValueError):
        OpenAICompatClient("https://api.example.com", "", "m")
    with pytest.raises(ValueError):
        AnthropicClient("key", "")


# ---------------------------------------------------------------- форматування контексту / людяність

def test_format_context_labels_and_limits():
    pairs = [
        (True, "Моє старе"),
        (False, "Привіт"),
        (True, "Привіт-привіт"),
        (False, ""),
        (True, "   "),
        (False, "Як справи?"),
    ]
    text = format_context(pairs, max_messages=20)
    lines = text.splitlines()
    assert lines == [
        "Я: Моє старе",
        "Співрозмовник: Привіт",
        "Я: Привіт-привіт",
        "Співрозмовник: Як справи?",
    ]

    short = format_context(pairs, max_messages=2)
    assert short.splitlines() == ["Я: Привіт-привіт", "Співрозмовник: Як справи?"]


def test_strip_markdown_and_disclaimers():
    raw = "```python\nprint(1)\n```\n**Жирний** і __підкреслений__ текст. ### Заголовок"
    cleaned = strip_markdown_artifacts(raw)
    assert "```" not in cleaned
    assert "**" not in cleaned
    assert "__" not in cleaned
    assert "###" not in cleaned
    assert "Жирний" in cleaned

    with_disclaimer = "Я — штучний інтелект, тому...\nОсь моя відповідь."
    assert strip_markdown_artifacts(with_disclaimer) == "Ось моя відповідь."
    assert strip_markdown_artifacts("As an AI language model I cannot.") == ""


def test_split_reply_chunks():
    assert split_reply("") == []
    assert split_reply("Коротка відповідь.", max_chunks=3) == ["Коротка відповідь."]

    long_text = ". ".join(f"Речення номер {i} з довгим поясненням" for i in range(40)) + "."
    chunks = split_reply(long_text, max_chunks=3)
    assert 1 < len(chunks) <= 3
    assert "".join(chunks).replace(" ", "").startswith("Реченняномер0")
    assert all(c.strip() for c in chunks)

    assert split_reply("Без жодних речень і довгий-предовгий текст " * 20, max_chunks=3)  # один шматок


def test_default_prompts_ukrainian_and_instructions():
    assert "ШІ" in DEFAULT_BASE_PROMPT or "інтелект" in DEFAULT_BASE_PROMPT
    assert "{style_profile}" in DEFAULT_BASE_PROMPT
    assert "Співрозмовник" in DEFAULT_BASE_PROMPT
    assert "СТИЛЬ" in DEFAULT_STYLE_META_PROMPT or "ПРОФІЛЬ" in DEFAULT_STYLE_META_PROMPT
    assert "{samples}" in DEFAULT_STYLE_META_PROMPT


def test_build_style_user_prompt_truncates():
    samples = [" повідомлення " * 1000 for _ in range(30)]  # ~37k символів на вибірку
    prompt = build_style_user_prompt(samples, char_limit=1500)
    assert len(prompt) < len(DEFAULT_STYLE_META_PROMPT) + 1600 + 200
    assert "=== ВИБІРКА ПОВІДОМЛЕНЬ ===" in prompt


def test_build_style_user_prompt_skips_empty():
    prompt = build_style_user_prompt(["", "   ", "Реальний текст"])
    assert "Реальний текст" in prompt


# ---------------------------------------------------------------- повтори (backoff)

class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = f"HTTP {status_code}"

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Підставник httpx.AsyncClient: віддає заготовлені відповіді/винятки."""

    def __init__(self, results: list) -> None:
        self._results = list(results)
        self.calls = 0

    async def post(self, url, headers=None, json=None, timeout=None):  # noqa: A002
        self.calls += 1
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _client() -> OpenAICompatClient:
    return OpenAICompatClient("https://api.example.com", "key", "m", backoff_base=0)


@pytest.mark.asyncio
async def test_post_json_retries_on_429_then_succeeds():
    client = _client()
    fake = _FakeClient([
        _FakeResponse(429, {}),
        httpx.TimeoutException("timeout"),
        _FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]}),
    ])
    data = await client._post_json(fake, "http://x", {}, {})
    assert fake.calls == 3
    assert parse_openai_response(data) == "ok"


@pytest.mark.asyncio
async def test_post_json_no_retry_on_client_error():
    client = _client()
    fake = _FakeClient([_FakeResponse(401, {})])
    with pytest.raises(LLMError, match="401"):
        await client._post_json(fake, "http://x", {}, {})
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_post_json_gives_up_after_max_retries():
    client = _client()
    fake = _FakeClient([_FakeResponse(503, {}) for _ in range(5)])
    with pytest.raises(LLMError, match="3 спроб"):
        await client._post_json(fake, "http://x", {}, {})
    assert fake.calls == 3  # DEFAULT_MAX_RETRIES
