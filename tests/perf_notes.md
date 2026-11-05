# Performance audit — burst reply engine & hot paths (`tg_hub/userbot.py`, `tg_hub/db.py`)

Read-only audit, 2026-11-05. All numbers below come from `tests/bench_engine.py`
(run: `./.venv/Scripts/python -m pytest tests/bench_engine.py -q -s` on this machine;
Timings are wall-clock via `time.perf_counter`, warm single run — treat as order-of-magnitude,
not SLA). Network calls (get_chat / get_messages / LLM / send_message) are **stubbed** in the
bench, so measured ms reflect CPU/DB only; production network cost is called out separately.

## Per-message path map

```
_on_new_message (per incoming message)
  ├─ db.get_settings(user_id)          DB: 2 sqlite3.connect + 2 INSERT OR IGNORE + SELECT + JSON parse
  ├─ _passes_filters
  │    └─ message.get_chat()           NETWORK roundtrip (unless Telethon entity cache hits)
  ├─ _pending[chat].append(text)
  └─ debounce: cancel old task, create_task(_debounced_reply)

_debounced_reply (once per burst, after 2 s silence)
  ├─ _cooldown_ok                      in-memory
  ├─ _generate_reply
  │    ├─ db.get_active_key            DB: 1 connect (_run → asyncio.to_thread)
  │    ├─ db.get_prompts               DB: 1 connect
  │    ├─ _build_context → get_messages(limit=context_messages)   NETWORK (2nd roundtrip)
  │    ├─ db.get_full_kb               DB: 1 connect (full KB scan → prompt)
  │    └─ create_client + llm.chat     LLM call (dominant latency)
  ├─ _humanized_send                   NETWORK: send_chat_action + send_message per chunk
  └─ _record_qa_entry
       ├─ client.get_entity(chat_id)   NETWORK (3rd roundtrip, usually Telethon-cached)
       └─ db.add_qa_entry              DB: 1 connect
```

## Measured evidence

| # | Measurement (bench_engine.py) | Result |
|---|-------------------------------|--------|
| M1 | 10-message burst through filter+debounce (get_chat stubbed) | **26.6 ms** wall, **2.66 ms/msg**, `get_chat` called **10×** |
| M2 | Burst scaling 1 / 5 / 10 | wall 20.8 / 23.2 / 24.1 ms; **get_chat calls = 1 / 5 / 10** (linear) |
| M3 | `get_settings` × 100 sequential (temp file DB) | total 118.9 ms, **mean 1.189 ms**, p95 1.433 ms |
| M4 | `_get_settings_sync` alone × 100 vs async `get_settings` × 100 | 94.1 ms vs 126.7 ms → `asyncio.to_thread`+lock adds **~0.33 ms/call**; the sync body itself is 0.94 ms/call (2 connections per call) |
| M5 | `Database._run(noop)` × 200 | **104.7 µs/call** pure dispatch (lock + thread hop) |
| M6 | `add_qa_entry`, 2000-char question + 2000-char answer, × 50 | total 274.5 ms, **mean 5.489 ms/write** |
| M7 | `Database(":memory:")` in bench | fails — each `sqlite3.connect(":memory:")` is a **separate empty DB** because every call opens its own connection (confirms connection-per-call pattern) |

## Findings (priority order)

1. **`get_chat()` network roundtrip per message — HIGH impact.**
   Evidence: M1/M2 — exactly N calls per burst of N (10-burst → 10 calls), and the count is
   linear in burst size. In production each call that misses Telethon's entity cache is a
   full roundtrip (~50–300 ms); a 10-message burst burns ~0.5–3 s of redundant network time
   and 9 identical lookups, all before debounce even fires.
   **Recommendation:** cache the chat entity per `chat_id` with a ~5-min TTL (dict
   `chat_id -> (entity, expires_at)`), or resolve the username once per burst inside
   `_debounced_reply` instead of per message in `_passes_filters`.
   Saves **N−1 network calls per burst** (9 of 10 in M1's burst).

2. **`get_settings()` per incoming message — HIGH impact (DB), MED latency.**
   Evidence: M3 — 1.189 ms/call mean; called once per message, so a 10-burst does 10 reads
   (M1: the 10 get_settings calls are ~24 of the 26.6 ms wall). Each call opens
   **2 sqlite connections** (see finding 4) and re-runs 2 `INSERT OR IGNORE` (ensure_user)
   on every read — pure write churn on the hot path.
   **Recommendation:** cache settings per user with a short TTL (5–30 s) invalidated by
   `update_settings`, or read once per burst (inside `_debounced_reply`) instead of once per
   message. Saves **N−1 DB reads (~12 ms of I/O churn) per burst** plus 2 connections each.

3. **`get_messages(context_messages)` per reply — MED impact.**
   Evidence: by code path, one `client.get_messages(chat_id, limit=context_messages)`
   (default 20, admin-settable to 50+) per reply — a second network roundtrip, unavoidable
   if real history is wanted. At 50 messages the fetch and prompt grow linearly.
   **Recommendation:** acceptable as-is; if needed, keep a small in-memory recent-history
   ring buffer per chat (messages already arrive via events) and skip `get_messages` for
   chats with a warm buffer. Saves 1 network roundtrip per reply.

4. **Connection-per-call SQLite pattern — MED impact.**
   Evidence: M3/M4 — every `_get_settings_sync` opens **2** connections (its own + the nested
   `_ensure_user_sync`); each `Database` method (`get_active_key`, `get_prompts`,
   `get_full_kb`, `add_qa_entry`, …) opens another. One burst of 10 → 1 reply ≈
   20 (settings) + 4 (reply path) ≈ **24 sqlite file-opens + commits**. M7 shows the pattern
   even breaks `:memory:` usage. M4 shows the sync body (0.94 ms) dwarfs the to_thread hop.
   **Recommendation:** keep one long-lived connection (or thread-local) + WAL mode; drop the
   nested `_ensure_user_sync` connection inside `_get_settings_sync` (reuse the caller's
   connection). Saves ~1–2 ms per DB call and halves connection churn.

5. **Unbounded growth of per-chat dicts — MED impact (memory over weeks).**
   Evidence: `_cooldowns` (userbot.py:122) is written on every reply and **never pruned**;
   `_typing_until` (:129) is written on typing updates and never pruned; `_pending`/
   `_debounce_tasks` self-clean on a completed reply, but a burst whose debounce task is
   cancelled (the normal per-message mechanism) leaves the pending list until the next
   message in that chat, and a chat that stops mid-burst keeps entries indefinitely.
   All grow with **distinct chats ever seen**, not burst size: one dict entry each (int→float /
   int→list). After weeks of group activity this is thousands of stale entries plus the
   retention of pending texts.
   **Recommendation:** periodic sweep (e.g. in `_on_user_update` or a 10-min task) dropping
   `_cooldowns`/`_typing_until` entries older than `cooldown_seconds + 60`, and capping
   `_pending` lists (e.g. last 20 messages per chat).

6. **`_run` dispatch overhead per DB call — LOW impact.**
   Evidence: M5 — 104.7 µs per `asyncio.to_thread` + lock hop; M4 — +0.33 ms/call end-to-end
   including future scheduling. With ~6 DB calls per burst the overhead is <2 ms — noise next
   to the network and LLM. All DB work is also serialized behind a single `asyncio.Lock`.
   **Recommendation:** not worth changing on its own; fixing finding 4 (persistent
   connection) removes most of the per-call cost anyway.

7. **`create_client` per reply + `get_entity` in `_record_qa_entry` — LOW impact.**
   Evidence: by code path — a fresh LLM client (new HTTP pool) is built per reply
   (userbot.py:330), and `get_entity` is a 3rd network touch per reply (Telethon caches
   entities after first lookup, so usually local — but it is an extra await per reply).
   **Recommendation:** cache the client per (provider, base_url, model, key) key;
   reuse the entity already resolved in `_passes_filters` for the Q&A chat title.

## Call-count table per burst (one chat, burst of N messages → 1 reply)

| Operation | Type | burst=1 | burst=5 | burst=10 | Scalable? |
|---|---|---|---|---|---|
| `get_settings` | DB (2 conns) | 1 | 5 | 10 | **per message** |
| `get_chat` | network | 1 | 5 | 10 | **per message** |
| debounce task create/cancel | local | 1 | 5 | 10 | per message |
| `get_active_key` | DB | 1 | 1 | 1 | per reply |
| `get_prompts` | DB | 1 | 1 | 1 | per reply |
| `get_messages` | network | 1 | 1 | 1 | per reply |
| `get_full_kb` | DB | 1 | 1 | 1 | per reply |
| LLM `chat` | network | 1 | 1 | 1 | per reply |
| `send_chat_action` + `send_message` | network | 1–4 | 1–4 | 1–4 | per chunk |
| `get_entity` | network (cached) | 1 | 1 | 1 | per reply |
| `add_qa_entry` | DB | 1 | 1 | 1 | per reply |
| sqlite3.connect total | local I/O | 6 | 14 | 24 | per message+reply |

Burst overhead that scales with N (before any reply): **2N network/DB roundtrips**
(N get_chat + N get_settings) — both cacheable. Per-reply cost is constant.

## Suggested fix order

1. Cache `get_chat` per chat_id (TTL ≈ 5 min) — biggest win, smallest diff. (HIGH)
2. Cache settings per user (TTL 5–30 s or burst-scoped read). (HIGH)
3. Persistent sqlite connection + WAL; reuse connection in `_get_settings_sync`. (MED)
4. Sweep `_cooldowns`/`_typing_until`/stale `_pending`. (MED)
5. Per-key LLM client cache; reuse filter-resolved entity for Q&A title. (LOW)
