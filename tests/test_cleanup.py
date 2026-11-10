"""Unused link codes are deleted; codes being waited for, or filed moments ago, are kept."""
from __future__ import annotations

import asyncio
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from halvane_bot.bot import HalvaneBot
from halvane_bot.cogs.verify import VerifyCog
from halvane_bot.config import load_settings
from halvane_bot.sync.linking import PendingCode


class FakeCloud:
    link_store = "LinkCodes"

    def __init__(self, entries):
        self.entries = entries
        self.deleted: list[str] = []

    async def list_entry_ids(self, store):
        return list(self.entries)

    async def get_link_entry(self, code):
        return self.entries.get(code)

    async def delete_link_entry(self, code):
        self.deleted.append(code)
        self.entries.pop(code, None)


def test_cleanup_keeps_live_codes_and_removes_the_rest():
    async def scenario():
        bot = HalvaneBot(replace(load_settings(), database=Path(tempfile.mkdtemp()) / "t.db"))
        now = time.time()
        bot.cloud = FakeCloud({
            "OLDONE": {"userId": 1, "at": now - 3600},       # stale: delete
            "BROKEN": "not a table",                         # junk: delete
            "FRESH2": {"userId": 2, "at": now - 30},          # just filed: keep
            "WAITED": {"userId": 3, "at": now - 3600},        # someone is waiting on it here: keep
        })
        cog = VerifyCog(bot)
        cog.pending[7] = (PendingCode("WAITED", 7, now, now + 600), object())
        removed = await cog.cleanup_link_codes()
        return removed, sorted(bot.cloud.deleted), sorted(bot.cloud.entries)

    removed, deleted, left = asyncio.run(scenario())
    assert removed == 2 and deleted == ["BROKEN", "OLDONE"] and left == ["FRESH2", "WAITED"]
