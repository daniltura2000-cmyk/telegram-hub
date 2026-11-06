"""Тести на оновлення движка відповідей: швидкість, контекст, анти-галюцинації.

Жодних мережевих викликів: LLM і Telethon-клієнт замінені фейками.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import tg_hub.userbot as userbot_mod
from tg_hub.persona.styles import DEFAULT_BASE_PROMPT
from tg_hub.userbot import CONTEXT_MAX_CHARS, KB_MAX_CHARS, Userbot


# ------------------------------------------------------------------ константи

def test_speed_constants_tightened():
    assert userbot_mod.DEBOUNCE_SECONDS == 1.0
    assert userbot_mod.TYPING_GRACE == 1.5
    assert userbot_mod.MAX_BURST_WAIT == 15.0


# ------------------------------------------------------- анти-галюцинації

def test_base_prompt_has_no_hallucination_rule():
    assert "Не вигадуй імена, зустрічі, плани, цифри і факти" in DEFAULT_BASE_PROMPT
    assert "Краще ухильно чи коротко" in DEFAULT_BASE_PROMPT
    # нумерація правил послідовна 1..9
    for n in range(1, 10):
        assert f"{n}." in DEFAULT_BASE_PROMPT


def test_strip_ai_tells_does_not_eat_rest_of_line():
    """Регресія: tell-фраза без крапки перед ем-дефісом не повинна знищити відповідь."""
    cleaned = userbot_mod.strip_ai_tells("Сподіваюсь, все ок — напиши завтра")
    assert "Сподіваюсь" not in cleaned
    assert "—" not in cleaned
    assert "напиши завтра" in cleaned
def test_user_content_has_evasive_answer_instruction():
    """Дослівна інструкція «не вигадуй» є в user_content (джерело _generate_reply)."""
    import inspect

    source = inspect.getsource(Userbot._generate_reply)
    # фраза розбита на два рядкові літерали — перевіряємо обидві частини
    assert "Якщо останнє повідомлення - питання, на яке в історії або базі знань" in source
    assert "немає відповіді, НЕ вигадуй факти" in source
    assert "реальний власник" in source


# ----------------------------------------------------- фільтри на send-шлях

class FakeTelegramClient:
    """Ловить send_message/send_chat_action — заміна Telethon-клієнта."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.actions: list[str] = []

    async def send_chat_action(self, chat_id, action):  # noqa: ANN001
        self.actions.append(action)

    async def send_message(self, chat_id, text):  # noqa: ANN001
        self.sent.append(text)


def _make_userbot() -> Userbot:
    config = SimpleNamespace(llm_timeout=30.0, llm_max_tokens=200)
    return Userbot(config, db=None, owner_user_id=1)


@pytest.mark.asyncio
async def test_humanized_send_strips_ai_tells_and_markdown(monkeypatch: pytest.MonkeyPatch):
    """Повний send-шлях: ем-дефіси, «Сподіваюсь», markdown — зникли до send_message."""
    monkeypatch.setattr(userbot_mod, "TYPING_MIN_SECONDS", 0.0)
    ub = _make_userbot()
    fake = FakeTelegramClient()
    ub.client = fake  # type: ignore[assignment]
    settings = {"max_chunks": 3, "reply_delay_min": 0.0, "reply_delay_max": 0.0}

    await ub._humanized_send(
        123,
        "Сподіваюсь, це допомогло — дивись **тут** і `код`",
        settings,
    )

    assert fake.sent, "повідомлення мало бути надіслане"
    sent = "\n".join(fake.sent)
    assert "Сподіваюсь" not in sent
    assert "—" not in sent and "–" not in sent
    assert "**" not in sent and "`" not in sent
    assert "дивись" in sent and "тут" in sent


# ------------------------------------------------------- контекст / промпт

class _FakeMsg:
    def __init__(self, text: str, out: bool = False) -> None:
        self.message = text
        self.out = out
        self.date = datetime.now(timezone.utc)


class _FakeGetMessagesClient:
    def __init__(self, messages: list[_FakeMsg]) -> None:
        self._messages = messages
        self.calls = 0

    async def get_messages(self, chat_ref, limit: int = 1):  # noqa: ANN001
        self.calls += 1
        # Telethon повертає від НОВИХ до СТАРИХ — як реальний клієнт
        return list(reversed(self._messages[:limit]))


@pytest.mark.asyncio
async def test_build_context_trims_oldest_keeps_recent():
    """Велика історія обрізається до ~3000 символів, свіжі повідомлення лишаються."""
    ub = _make_userbot()
    big = "дуже довге старе повідомлення про минулі справи " * 40  # ~2200 симв
    messages = [_FakeMsg(f"{big} #{i}") for i in range(10)]
    messages.append(_FakeMsg("СВІЖЕ ОСТАННЄ ПОВІДОМЛЕННЯ", out=False))
    ub.client = _FakeGetMessagesClient(messages)  # type: ignore[assignment]

    context = await ub._build_context(123, limit=20)

    assert len(context) <= CONTEXT_MAX_CHARS + 100  # + рядок-маркер обрізання
    assert "СВІЖЕ ОСТАННЄ ПОВІДОМЛЕННЯ" in context
    assert context.startswith("…(старіші повідомлення обрізано)")
    # найстаріше повідомлення (#0) не вціліло — обрізано згори
    assert f"{big} #0" not in context


class _FakeLLM:
    def __init__(self) -> None:
        self.captured_system = ""
        self.captured_messages: list[dict] = []

    async def chat(self, messages, system=None):  # noqa: ANN001
        self.captured_system = system or ""
        self.captured_messages = messages
        return "ок"


class _FakeDB:
    async def get_active_key(self, user_id):  # noqa: ANN001
        return {"provider": "openai", "base_url": "https://x", "api_key": "k", "model": "m"}

    async def get_prompts(self, user_id):  # noqa: ANN001
        return {"base_prompt": DEFAULT_BASE_PROMPT, "style_profile": "профіль-тест"}

    async def get_chat_profile(self, user_id, chat_id):  # noqa: ANN001
        return None

    async def get_full_kb(self, user_id):  # noqa: ANN001
        return "ФАКТ-ЗАПИС " * 500  # 5500 символів — має обрізатися до KB_MAX_CHARS


@pytest.mark.asyncio
async def test_generate_reply_structure_kb_cap_and_timing_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """user_content: ОСТАННІ ПОВІДОМЛЕННЯ перед ІСТОРІЄЮ; KB обрізана; timing-лог."""
    ub = _make_userbot()
    ub._db = _FakeDB()  # type: ignore[assignment]
    fake_llm = _FakeLLM()
    monkeypatch.setattr(userbot_mod, "create_client", lambda **kwargs: fake_llm)
    ub.client = _FakeGetMessagesClient([_FakeMsg("привіт")])  # type: ignore[assignment]

    settings = {"context_messages": 20}
    with caplog.at_level("INFO", logger="tg_hub.userbot"):
        reply = await ub._generate_reply(1, settings, 123, ["привіт", "як справи?"])

    assert reply == "ок"
    user_content = fake_llm.captured_messages[0]["content"]
    pos_latest = user_content.index("ОСТАННІ ПОВІДОМЛЕННЯ")
    pos_history = user_content.index("ІСТОРІЯ ДІАЛОГУ")
    assert pos_latest < pos_history, "burst має стояти ПЕРЕД історією"
    assert "• привіт" in user_content and "• як справи?" in user_content
    assert "НЕ вигадуй факти" in user_content
    assert "не тягни стару" in user_content  # інструкція про нову тему лишилась

    # KB обрізана до ліміту (голова)
    assert fake_llm.captured_system.count("ФАКТ-ЗАПИС") <= KB_MAX_CHARS // len("ФАКТ-ЗАПИС ") + 1
    assert "…" in fake_llm.captured_system.split("ФАКТ-ЗАПИС ")[-1]

    # timing-лог у .run_log
    timing_lines = [r for r in caplog.records if "згенеровано за" in r.getMessage()]
    assert timing_lines, "має бути logger.info з часом генерації"
    assert "с" in timing_lines[0].getMessage()
