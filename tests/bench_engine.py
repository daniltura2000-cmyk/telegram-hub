"""Performance benchmarks for the burst-reply engine hot paths (read-only audit).

Measures, with plain pytest + time.perf_counter (no new deps):
  (a) a 10-message burst through _on_new_message -> filters -> debounce,
      with a FAKE message object (get_chat stubbed, counted);
  (b) Database.get_settings latency over 100 sequential calls (temp DB),
      plus the pure asyncio.to_thread/_run dispatch overhead;
  (c) Database.add_qa_entry with 2000-char question/answer, 50 writes.

Run with:  ./.venv/Scripts/python -m pytest tests/bench_engine.py -q -s
All numbers are printed so they are visible with ``pytest -s``.
"""
from __future__ import annotations

import asyncio
import statistics
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tg_hub import userbot as userbot_mod
from tg_hub.db import Database
from tg_hub.userbot import Userbot

_BENCH_SEQ = iter(range(1000))
OWNER_ID = 111
PEER_ID = 222
CHAT_ID = -100300
BURST_TEXT = "привіт, як справи?"


class FakeChat:
    """Stand-in for the Telethon chat entity returned by message.get_chat()."""

    def __init__(self, chat_id: int, username: str | None = None) -> None:
        self.id = chat_id
        self.username = username


class FakeMessage:
    """Minimal fake of events.NewMessage.Event.message for the filter path."""

    def __init__(self, chat: FakeChat, text: str, counter: dict[str, int]) -> None:
        self.out = False
        self.action = None
        self.sender_id = PEER_ID
        self.chat_id = chat.id
        self.message = text
        self.is_private = True
        self.mentioned = False
        self._chat = chat
        self._counter = counter

    async def get_chat(self) -> FakeChat:
        self._counter["get_chat"] += 1
        return self._chat  # stubbed: instant, no network


class FakeEvent:
    def __init__(self, message: FakeMessage) -> None:
        self.message = message


def make_userbot(db: Database) -> Userbot:
    config = SimpleNamespace(
        llm_timeout=30.0,
        sessions_dir=Path("/nonexistent-sessions"),
        telegram_api_id=0,
        telegram_api_hash="",
    )
    return Userbot(config, db, owner_user_id=OWNER_ID)


async def _seed(db: Database) -> None:
    await db.init()
    await db.ensure_user(OWNER_ID)
    await db.update_settings(
        OWNER_ID,
        auto_reply=1,
        whitelist_mode=0,
        ignore_list=[],
        only_private_or_mention=0,
        cooldown_seconds=0,
        reply_delay_min=0.0,
        reply_delay_max=0.0,
    )


async def _run_burst(db: Database, size: int, debounce: float = 0.01) -> dict[str, float | int]:
    """Dispatch ``size`` messages through the real filter+debounce path."""
    counter: dict[str, int] = {"get_chat": 0}
    ub = make_userbot(db)
    chat = FakeChat(CHAT_ID, username=None)

    # isolate the filter+debounce path: stub LLM, sending and Q&A journal
    replies: list[str] = []

    async def fake_generate(user_id, settings, chat_id, burst):  # noqa: ANN001
        replies.append("ok")
        return "ok"

    async def fake_send(chat_id, reply, settings):  # noqa: ANN001
        pass

    async def fake_record_qa(*a, **k):  # noqa: ANN001, ANN003
        return None

    ub._generate_reply = fake_generate  # type: ignore[method-assign]
    ub._humanized_send = fake_send  # type: ignore[method-assign]
    ub._record_qa_entry = fake_record_qa  # type: ignore[method-assign]

    old_debounce = userbot_mod.DEBOUNCE_SECONDS
    try:
        userbot_mod.DEBOUNCE_SECONDS = debounce  # shrink the silence window
        t0 = time.perf_counter()
        for i in range(size):
            await ub._on_new_message(FakeEvent(FakeMessage(chat, f"{BURST_TEXT} #{i}", counter)))
        # wait for the final debounce task (the one that fires the reply)
        task = ub._debounce_tasks.get(CHAT_ID)
        assert task is not None, "debounce task was not scheduled"
        await asyncio.shield(task)
        wall = time.perf_counter() - t0
    finally:
        userbot_mod.DEBOUNCE_SECONDS = old_debounce

    assert len(replies) == 1, f"expected exactly 1 reply per burst, got {len(replies)}"
    pending_left = len(ub._pending.get(CHAT_ID, []))
    assert pending_left == 0, f"burst messages left in _pending: {pending_left}"
    return {"wall_s": wall, "get_chat_calls": counter["get_chat"], "size": size}


# ---------------------------------------------------------------- (a) burst path

def test_burst_filter_debounce_path(tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """10-message burst through _on_new_message -> _passes_filters -> debounce."""
    monkeypatch.setattr(userbot_mod, "DEBOUNCE_SECONDS", 0.01)

    async def scenario() -> dict[str, float | int]:
        db = Database(tmp_path / f"bench_{next(_BENCH_SEQ)}.db")
        await _seed(db)
        return await _run_burst(db, size=10)

    result = asyncio.run(scenario())
    wall_ms = result["wall_s"] * 1000.0
    per_msg = wall_ms / result["size"]
    print(f"\n[burst x10] wall={wall_ms:.1f} ms  per-message={per_msg:.2f} ms  "
          f"get_chat calls={result['get_chat_calls']}")

    # get_chat має кеш: одна мережева подорож на burst (було б N без кешу)
    assert result["get_chat_calls"] == 1
    assert result["wall_s"] < 30.0  # sanity guard, not a tight SLA


def test_burst_scaling_1_5_10(tmp_path, capsys: pytest.CaptureFixture) -> None:
    """get_chat call count and wall time for burst sizes 1 / 5 / 10."""

    async def scenario() -> list[dict[str, float | int]]:
        results = []
        for size in (1, 5, 10):
            db = Database(tmp_path / f"bench_{next(_BENCH_SEQ)}.db")  # fresh DB + fresh Userbot per size
            await _seed(db)
            results.append(await _run_burst(db, size=size))
        return results

    results = asyncio.run(scenario())
    print("\n[burst scaling] size | wall ms | per-message ms | get_chat calls")
    for r in results:
        print(f"  {r['size']:>3} | {r['wall_s'] * 1000:8.1f} | "
              f"{r['wall_s'] * 1000 / r['size']:8.2f} | {r['get_chat_calls']:>4}")
    # з кешем чатів — один get_chat на burst незалежно від розміру серії
    assert [r["get_chat_calls"] for r in results] == [1, 1, 1]


# ---------------------------------------------------------------- (b) get_settings

def test_get_settings_latency_100(tmp_path, capsys: pytest.CaptureFixture) -> None:
    """Database.get_settings: 100 sequential calls on a temp DB file."""

    async def scenario() -> tuple[float, float, list[float]]:
        db = Database(tmp_path / f"bench_{next(_BENCH_SEQ)}.db")
        await _seed(db)
        samples: list[float] = []
        for _ in range(100):
            t0 = time.perf_counter()
            settings = await db.get_settings(OWNER_ID)
            samples.append(time.perf_counter() - t0)
            assert settings["auto_reply"] == 1
        return sum(samples), statistics.mean(samples), samples

    total_s, mean_s, samples = asyncio.run(scenario())
    samples_sorted = sorted(samples)
    p95 = samples_sorted[int(0.95 * len(samples_sorted)) - 1]
    print(f"\n[get_settings x100] total={total_s * 1000:.1f} ms  mean={mean_s * 1000:.3f} ms  "
          f"p95={p95 * 1000:.3f} ms")
    assert total_s < 30.0


def test_get_settings_sync_vs_async(tmp_path, capsys: pytest.CaptureFixture) -> None:
    """Isolate the _run overhead: sync body vs full async get_settings call."""

    async def scenario() -> tuple[float, float]:
        db = Database(tmp_path / f"bench_{next(_BENCH_SEQ)}.db")
        await _seed(db)
        # warm-up
        await db.get_settings(OWNER_ID)
        t0 = time.perf_counter()
        for _ in range(100):
            db._get_settings_sync(OWNER_ID)  # pure sqlite work, no to_thread
        sync_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        for _ in range(100):
            await db.get_settings(OWNER_ID)  # + asyncio.Lock + to_thread + future hops
        async_s = time.perf_counter() - t0
        return sync_s, async_s

    sync_s, async_s = asyncio.run(scenario())
    print(f"\n[_run overhead] sync _get_settings_sync x100={sync_s * 1000:.1f} ms  "
          f"async get_settings x100={async_s * 1000:.1f} ms  "
          f"to_thread+lock delta={(async_s - sync_s) * 1000:.1f} ms "
          f"({(async_s - sync_s) * 10000:.0f} us/call)")
    assert async_s >= sync_s * 0.5  # informational; sync path is never dramatically slower


def test_run_noop_dispatch_overhead(tmp_path, capsys: pytest.CaptureFixture) -> None:
    """Pure cost of Database._run: asyncio.Lock + asyncio.to_thread with no SQL."""

    async def scenario() -> float:
        db = Database(tmp_path / f"bench_{next(_BENCH_SEQ)}.db")
        await db.init()
        noop = lambda: None  # noqa: E731
        await db._run(noop)  # warm-up
        t0 = time.perf_counter()
        for _ in range(200):
            await db._run(noop)
        return time.perf_counter() - t0

    total_s = asyncio.run(scenario())
    print(f"\n[_run noop x200] total={total_s * 1000:.1f} ms  per-call={total_s * 5000:.1f} us")
    assert total_s < 30.0


# ---------------------------------------------------------------- (c) add_qa_entry

def test_add_qa_entry_2000chars_x50(tmp_path, capsys: pytest.CaptureFixture) -> None:
    """add_qa_entry with 2000-char question/answer, 50 sequential writes."""

    async def scenario() -> float:
        db = Database(tmp_path / f"bench_{next(_BENCH_SEQ)}.db")
        await _seed(db)
        question = "Q" * 2000
        answer = "A" * 2000
        t0 = time.perf_counter()
        for i in range(50):
            await db.add_qa_entry(OWNER_ID, "@peer", "Peer", question, answer)
        return time.perf_counter() - t0

    total_s = asyncio.run(scenario())
    print(f"\n[add_qa_entry x50 (2000+2000 chars)] total={total_s * 1000:.1f} ms  "
          f"mean={total_s * 20:.3f} ms")
    assert total_s < 30.0
