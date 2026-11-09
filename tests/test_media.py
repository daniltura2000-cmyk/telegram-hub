"""Тести медіа-манери: арсенал у БД, збір медіа з чату, парсинг маркера."""
from __future__ import annotations

import pytest

from tg_hub.db import Database
from tg_hub.persona.analyzer import collect_media_usage, classify_outgoing_media
from tg_hub.userbot import extract_media_marker, send_media


class _FakeDoc:
    def __init__(self, doc_id: int, access_hash: int, mime_type: str = "") -> None:
        self.id = doc_id
        self.access_hash = access_hash
        self.mime_type = mime_type
        self.attributes: list[object] = []


class _FakeMsg:
    def __init__(self, *, out: bool, sticker=None, gif=None, document=None,
                 message: str = "") -> None:
        self.out = out
        self.sticker = sticker
        self.gif = gif
        self.document = document
        self.message = message
        self.attributes: list[object] = []


class _FakeClient:
    """Мінімальний клієнт: get_entity + iter_messages з готовим списком."""

    def __init__(self, messages: list[object]) -> None:
        self._messages = messages

    async def get_entity(self, entity):
        return entity

    def iter_messages(self, entity, limit, from_user=None):
        async def _gen():
            for message in self._messages[:limit]:
                yield message
        return _gen()


# ---------------------------------------------------------------- БД roundtrip

@pytest.mark.asyncio
async def test_media_arsenal_roundtrip(tmp_path):
    db = Database(tmp_path / "hub.db")
    await db.init()

    assert await db.list_media_arsenal(1, "100") == []

    await db.save_media_usage(1, "100", "sticker", "123456_789")
    await db.save_media_usage(1, "100", "gif", "222_333")
    # повторний UPSERT того самого file_ref → usage_count зростає
    await db.save_media_usage(1, "100", "sticker", "123456_789")
    # той самий стікер в ІНШОМУ чаті — окремий запис
    await db.save_media_usage(1, "200", "sticker", "123456_789")

    rows = await db.list_media_arsenal(1, "100")
    by_ref = {r["file_ref"]: r for r in rows}
    assert set(by_ref) == {"123456_789", "222_333"}
    assert by_ref["123456_789"]["kind"] == "sticker"
    assert by_ref["123456_789"]["usage_count"] == 2
    assert by_ref["123456_789"]["last_used_at"]
    assert by_ref["222_333"]["usage_count"] == 1

    all_rows = await db.list_media_arsenal(1)
    assert len(all_rows) == 3

    # очищення одного чату не чіпає інші
    await db.clear_media_arsenal(1, "100")
    assert await db.list_media_arsenal(1, "100") == []
    assert len(await db.list_media_arsenal(1)) == 1

    # повне очищення
    await db.clear_media_arsenal(1)
    assert await db.list_media_arsenal(1) == []


# ------------------------------------------------------- збір медіа з чату

@pytest.mark.asyncio
async def test_collect_media_usage_out_only_and_order():
    own_sticker = _FakeMsg(out=True, sticker=_FakeDoc(10, 11))
    foreign_sticker = _FakeMsg(out=False, sticker=_FakeDoc(99, 99))
    own_gif = _FakeMsg(out=True, gif=_FakeDoc(20, 21))
    own_text = _FakeMsg(out=True, message="привіт")
    own_gif_doc = _FakeMsg(out=True, document=_FakeDoc(30, 31, mime_type="image/gif"))
    own_doc_other = _FakeMsg(out=True, document=_FakeDoc(40, 41, mime_type="video/mp4"))
    client = _FakeClient([
        own_sticker, foreign_sticker, own_gif, own_text, own_gif_doc, own_doc_other,
    ])
    usage = await collect_media_usage(client, "chat", limit=50)
    # порядок як у iter_messages; тільки out=True; текст/чужі/не-гіф документи skip
    assert usage == [
        ("sticker", "10_11"),
        ("gif", "20_21"),
        ("gif", "30_31"),
    ]


@pytest.mark.asyncio
async def test_collect_media_mime_via_attributes():
    # mime_type на самому документі відсутній, але є в attributes (DocumentAttribute*)
    class _MimeAttr:
        mime_type = "image/gif"

    doc = _FakeDoc(50, 51)
    doc.attributes = [_MimeAttr()]
    message = _FakeMsg(out=True, document=doc)
    assert classify_outgoing_media(message) == ("gif", "50_51")


@pytest.mark.asyncio
async def test_collect_media_missing_ids_skipped():
    message = _FakeMsg(out=True, sticker=_FakeDoc(None, 1))
    assert classify_outgoing_media(message) is None


# ------------------------------------------------------------- маркер відповіді

def test_extract_media_marker_sticker():
    text, media = extract_media_marker("ну ок ))\n[sticker 123_456]")
    assert text == "ну ок ))"
    assert media == ("sticker", "123_456")


def test_extract_media_marker_gif_and_none():
    text, media = extract_media_marker("тримай\n[gif 77_88]")
    assert text == "тримай"
    assert media == ("gif", "77_88")

    # без маркера — текст повертається без змін
    text, media = extract_media_marker("просто текст")
    assert text == "просто текст"
    assert media is None

    # маркер НЕ в кінці не з'їдається (не кінцевий — лишається в тексті)
    text, media = extract_media_marker("[sticker 1_2] і далі текст")
    assert media is None
    assert "[sticker 1_2]" in text


# ---------------------------------------------------------------- send_media

class _SendFileClient:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[object, object]] = []
        self._fail = fail

    async def send_file(self, chat_id, document, force_document=False):
        if self._fail:
            raise RuntimeError("boom")
        self.calls.append((chat_id, document))


@pytest.mark.asyncio
async def test_send_media_parses_ref_and_sends():
    from telethon.tl.types import InputDocument

    client = _SendFileClient()
    ok = await send_media(client, 42, "sticker", "123456_789")
    assert ok is True
    chat_id, document = client.calls[0]
    assert chat_id == 42
    assert isinstance(document, InputDocument)
    assert document.id == 123456
    assert document.access_hash == 789


@pytest.mark.asyncio
async def test_send_media_bad_ref_and_failure():
    client = _SendFileClient()
    assert await send_media(client, 1, "sticker", "не-число") is False
    assert await send_media(client, 1, "gif", "") is False

    failing = _SendFileClient(fail=True)
    assert await send_media(failing, 1, "gif", "5_6") is False
