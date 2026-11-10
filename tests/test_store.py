from __future__ import annotations

import asyncio

from halvane_bot.store import Database, EmbedStore, LinkStore


def run(tmp_path, scenario):
    async def main():
        db = Database(tmp_path / "data" / "test.db")
        await db.open()
        try:
            return await scenario(EmbedStore(db), LinkStore(db))
        finally:
            await db.close()
    return asyncio.run(main())


def test_embeds_save_get_and_update(tmp_path):
    async def scenario(embeds, _links):
        assert await embeds.get("rules") is None
        await embeds.save("rules", 200, [1, 2], "abc")
        first = await embeds.get("rules")
        await embeds.save("rules", 200, [3], "def")
        return first, await embeds.get("rules"), await embeds.all()

    first, second, everything = run(tmp_path, scenario)
    assert first.message_ids == (1, 2) and first.content_hash == "abc"
    assert second.message_ids == (3,) and second.content_hash == "def"
    assert set(everything) == {"rules"}


def test_links_one_member_per_roblox_account(tmp_path):
    async def scenario(_embeds, links):
        assert await links.link(1, 500, "alpha") is None          # new link
        assert await links.link(1, 600, "beta") is None           # same member switches account
        assert await links.by_roblox(500) is None
        moved_from = await links.link(2, 600, "beta")             # account moves to another member
        return moved_from, await links.by_discord(1), await links.by_discord(2), await links.unlink(2), await links.all()

    moved_from, old, new, removed, remaining = run(tmp_path, scenario)
    assert moved_from == 1 and old is None
    assert new.roblox_id == 600 and new.roblox_name == "beta"
    assert removed is True and remaining == []
