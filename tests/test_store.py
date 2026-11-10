from __future__ import annotations

import asyncio

from halvane_bot.store import EmbedStore


def test_save_get_and_update(tmp_path):
    async def scenario():
        store = EmbedStore(tmp_path / "data" / "test.db")
        await store.open()
        try:
            assert await store.get("rules") is None
            await store.save("rules", 200, [1, 2], "abc")
            first = await store.get("rules")
            await store.save("rules", 200, [3], "def")
            second = await store.get("rules")
            everything = await store.all()
        finally:
            await store.close()
        return first, second, everything

    first, second, everything = asyncio.run(scenario())
    assert first.message_ids == (1, 2) and first.content_hash == "abc"
    assert second.message_ids == (3,) and second.content_hash == "def"
    assert set(everything) == {"rules"}
