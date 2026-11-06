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
from tg_hub.persona.analyzer import (
    DEFAULT_SAMPLE_CHAR_LIMIT,
    build_dialogue_user_prompt,
    build_style_user_prompt,
    generate_chat_profile,
)
from tg_hub.persona.styles import (
    DEFAULT_BASE_PROMPT,
    DEFAULT_CHAT_PROFILE_META_PROMPT,
    DEFAULT_STYLE_META_PROMPT,
)
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
        (True, "Моє старе", None),
        (False, "Привіт", "10:05"),
        (True, "Привіт-привіт", "10:06"),
        (False, "", None),
        (True, "   ", None),
        (False, "Як справи?", "10:07"),
    ]
    text = format_context(pairs, max_messages=20)
    lines = text.splitlines()
    assert lines == [
        "Я: Моє старе",
        "[10:05] Співрозмовник: Привіт",
        "[10:06] Я: Привіт-привіт",
        "[10:07] Співрозмовник: Як справи?",
    ]

    short = format_context(pairs, max_messages=2)
    assert short.splitlines() == ["[10:06] Я: Привіт-привіт", "[10:07] Співрозмовник: Як справи?"]


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


def test_strip_ai_tells_and_em_dashes():
    from tg_hub.userbot import strip_ai_tells

    # довге тире — головна «палева» ШІ
    assert strip_ai_tells("Привіт — як справи?") == "Привіт ... як справи?"
    assert "—" not in strip_ai_tells("раз — два")
    # assistant-стартери зникають
    cleaned = strip_ai_tells("Сподіваюсь, це допомогло! Давай розберемо деталі.")
    assert "Сподіваюсь" not in cleaned
    assert "Давай розберемо" not in cleaned
    # звичайний текст не пошкоджується
    assert strip_ai_tells("ок, надішлю пізніше") == "ок, надішлю пізніше"


def test_burst_reply_threshold_constant():
    from tg_hub import userbot

    assert userbot.BURST_REPLY_THRESHOLD == 3
    assert userbot.DEBOUNCE_SECONDS == 1.0
    assert userbot.MAX_BURST_WAIT == 15.0


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
    assert "=== ВИБІРКА ПОВІДОМЛЕНЬ ===" in prompt  # глобальний мета-промпт, не діалоговий
    assert "ДІАЛОГ однієї людини" not in prompt


def test_build_style_user_prompt_skips_empty():
    prompt = build_style_user_prompt(["", "   ", "Реальний текст"])
    assert "Реальний текст" in prompt


# ------------------------------------------------------- пер-чат профілі (діалог)

def test_build_dialogue_user_prompt_labels_and_order():
    pairs = [
        (True, "Привіт, як справи?", "05.11"),
        (False, "Привіт! Все добре", "05.11"),
        (True, "Радий чути", None),
    ]
    prompt = build_dialogue_user_prompt(pairs, char_limit=DEFAULT_SAMPLE_CHAR_LIMIT)
    assert "[05.11] Я: Привіт, як справи?" in prompt
    assert "[05.11] Інший: Привіт! Все добре" in prompt
    assert "Я: Радий чути" in prompt  # без дати — без мітки
    # діалоговий мета-промпт, не глобальний
    assert "=== ДІАЛОГ ===" in prompt
    assert "ВЗАЄМОДІЮ" in prompt


def test_build_dialogue_user_prompt_truncates_at_char_limit():
    long_text = "дуже довге повідомлення " * 100  # ~2400 символів
    pairs = [(i % 2 == 0, long_text, "01.01") for i in range(10)]
    prompt = build_dialogue_user_prompt(pairs, char_limit=500)
    block = prompt.split("=== ДІАЛОГ ===\n", 1)[1].split("\n=== КІНЕЦЬ ДІАЛОГУ ===", 1)[0]
    # сума рядків вибірки обмежена char_limit (+ розділювачі між рядками)
    assert len(block) <= 500 + 10
    assert block  # щось потрапило у вибірку


class _FakeMsg:
    def __init__(self, text: str, out: bool, date) -> None:
        self.message = text
        self.out = out
        self.date = date


class _FakeTelethonClient:
    """iter_messages повертає від НОВИХ до старих — як справжній Telethon."""

    def __init__(self, messages: list[_FakeMsg]) -> None:
        self._messages = messages

    async def get_entity(self, entity):
        return entity

    def iter_messages(self, entity, limit, from_user=None):  # noqa: A002
        async def _gen():
            for msg in self._messages[:limit]:
                yield msg
        return _gen()


class _FakeLLM:
    def __init__(self) -> None:
        self.captured: dict = {}

    async def chat(self, messages, system=None):  # noqa: A002
        self.captured = {"messages": messages, "system": system}
        return "ПРОФІЛЬ: пишу з великої літери, жіночий рід"


@pytest.mark.asyncio
async def test_generate_chat_profile_reversed_chronology_and_labels():
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
    # Telethon: від нових до старих
    messages = [
        _FakeMsg("третє (найновіше)", False, now),
        _FakeMsg("друге", True, now - timedelta(hours=2)),
        _FakeMsg("перше (найстаріше)", True, now - timedelta(days=3)),
    ]
    llm = _FakeLLM()
    profile = await generate_chat_profile(
        _FakeTelethonClient(messages), llm, 12345, char_limit=DEFAULT_SAMPLE_CHAR_LIMIT,
    )
    assert profile == "ПРОФІЛЬ: пишу з великої літери, жіночий рід"
    prompt = llm.captured["messages"][0]["content"]
    assert "Я:" in prompt and "Інший:" in prompt
    # хронологія: найстаріше — першим, найновіше — останнім
    first, second, third = prompt.index("перше"), prompt.index("друге"), prompt.index("третє")
    assert first < second < third
    # мітки дат у форматі ДД.ММ
    assert "[02.11] Я: перше (найстаріше)" in prompt
    assert "[05.11] Інший: третє (найновіше)" in prompt
    assert "=== ДІАЛОГ ===" in prompt
    assert "стилістики" in (llm.captured["system"] or "")


@pytest.mark.asyncio
async def test_generate_chat_profile_raises_on_empty_dialogue():
    with pytest.raises(ValueError):
        await generate_chat_profile(_FakeTelethonClient([]), _FakeLLM(), 12345)


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


# ------------------------------------------------------------- Retry-After

@pytest.mark.asyncio
async def test_post_json_honors_retry_after_header(monkeypatch):
    import tg_hub.llm.base as base

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(base.asyncio, "sleep", fake_sleep)
    resp429 = _FakeResponse(429, {})
    resp429.headers = {"Retry-After": "7"}
    client = OpenAICompatClient("https://api.example.com", "key", "m", backoff_base=0)
    fake = _FakeClient([resp429, _FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})])
    await client._post_json(fake, "http://x", {}, {})
    assert sleeps == [7.0]  # Retry-After поважається замість backoff_base=0


@pytest.mark.asyncio
async def test_post_json_without_retry_after_uses_backoff(monkeypatch):
    import tg_hub.llm.base as base

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(base.asyncio, "sleep", fake_sleep)
    client = OpenAICompatClient("https://api.example.com", "key", "m", backoff_base=0)
    fake = _FakeClient([_FakeResponse(429, {}), _FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})])
    await client._post_json(fake, "http://x", {}, {})
    assert sleeps == [0.0]  # немає заголовка → звичайний backoff


# ------------------------------------- verify_model: 429 не блокує збереження

class _FakeVerifyClient:
    """Підставник httpx.AsyncClient для verify_model (повертає заготовлений статус)."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    async def __aenter__(self) -> "_FakeVerifyClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, url, headers=None, json=None, params=None, timeout=None):  # noqa: A002
        return _FakeResponse(self.status_code, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "openai", "anthropic"])
async def test_verify_model_429_warns_and_allows_saving(monkeypatch, provider):
    import tg_hub.llm.probe as probe

    monkeypatch.setattr(
        probe.httpx, "AsyncClient", lambda: _FakeVerifyClient(429)
    )
    warning = await probe.verify_model(provider, "https://api.example.com", "key", "some-model")
    assert warning is not None
    assert "429" in warning


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404])
async def test_verify_model_403_404_reject(monkeypatch, status):
    import tg_hub.llm.probe as probe
    from tg_hub.llm.probe import ProbeError

    monkeypatch.setattr(probe.httpx, "AsyncClient", lambda: _FakeVerifyClient(status))
    with pytest.raises(ProbeError):
        await probe.verify_model("google", None, "key", "gemini-x")


@pytest.mark.asyncio
async def test_verify_model_ok_returns_none(monkeypatch):
    import tg_hub.llm.probe as probe

    monkeypatch.setattr(probe.httpx, "AsyncClient", lambda: _FakeVerifyClient(200))
    assert await probe.verify_model("google", None, "key", "gemini-x") is None


# ------------------------------------ NotMenuText: FSM не з'їдає кнопки меню

@pytest.mark.asyncio
async def test_not_menu_text_filter():
    from tg_hub.control_bot import NotMenuText, _MENU_TEXTS

    class _Msg:
        def __init__(self, text: object) -> None:
            self.text = text

    f = NotMenuText()
    for button in _MENU_TEXTS:  # кнопки меню мають «проскочувати» повз FSM-обробники
        assert await f(_Msg(button)) is False
    assert await f(_Msg("звичайне повідомлення")) is True
    assert await f(_Msg(None)) is True  # медіа без тексту не блокуємо


def test_openai_payload_has_max_tokens():
    from tg_hub.llm.openai_compat import build_openai_payload
    payload = build_openai_payload("m", [{"role": "user", "content": "hi"}], "sys", max_tokens=200)
    assert payload["max_tokens"] == 200


def test_google_payload_max_tokens_and_thinking():
    from tg_hub.llm.google_provider import (
        _flash_model_can_disable_thinking,
        build_google_payload,
        parse_google_response,
    )
    payload = build_google_payload(
        [{"role": "user", "content": "hi"}], "sys", max_tokens=150, disable_thinking=True,
    )
    assert payload["generationConfig"]["maxOutputTokens"] == 150
    assert payload["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    plain = build_google_payload([{"role": "user", "content": "hi"}], "", max_tokens=150)
    assert "thinkingConfig" not in plain["generationConfig"]
    assert _flash_model_can_disable_thinking("gemini-3.6-flash")
    assert _flash_model_can_disable_thinking("gemini-2.5-flash")
    assert not _flash_model_can_disable_thinking("gemini-2.5-pro")
    assert not _flash_model_can_disable_thinking("gemma-4-31b-it")
    # думання не просочується у відповідь
    data = {"candidates": [{"content": {"parts": [
        {"text": "приховані думки", "thought": True}, {"text": "ок"},
    ]}}]}
    assert parse_google_response(data) == "ок"
