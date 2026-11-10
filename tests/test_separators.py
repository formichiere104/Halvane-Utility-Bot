"""Followed separators: [DIVISION LEADERSHIP] is held while a member holds any role under it."""
from __future__ import annotations

import asyncio
import tempfile
import types
from dataclasses import replace
from pathlib import Path

from halvane_bot.bot import HalvaneBot
from halvane_bot.config import load_settings
from halvane_bot.export_ids import describe_changes, old_names
from halvane_bot.ids import load_ids
from halvane_bot.sync.role_map import is_separator, separator_changes

# position, id, name: highest first, as Discord lists them
SERVER = [(9, 1, "[── GRADE ──]"), (8, 2, "Team Lead"), (7, 3, "[── DIVISION LEADERSHIP ──]"),
          (6, 4, "Dept. 21 · Lead"), (5, 5, "Dispatch · NCO"), (4, 6, "[── QUALIFICATIONS ──]"), (3, 7, "Registry")]
ROLES = [(rid, pos, name) for pos, rid, name in SERVER]
SEP = frozenset({3})


def test_is_separator():
    assert is_separator("[──── GRADE ────]") and not is_separator("— High Ranks") and not is_separator("Team Lead")


def test_separator_follows_the_roles_under_it():
    assert separator_changes({5}, ROLES, SEP) == ({3}, set())          # a tier role: gets the separator
    assert separator_changes({4, 3}, ROLES, SEP) == (set(), set())     # a hand-given lead: keeps it
    assert separator_changes({3, 2, 7}, ROLES, SEP) == (set(), {3})    # nothing under it: loses it
    assert separator_changes({2, 7}, ROLES, SEP) == (set(), set())     # roles under other separators don't count
    assert separator_changes({5}, ROLES, frozenset({99})) == (set(), set())  # deleted separator: ignored


class Role:
    def __init__(self, rid, position, name):
        self.id, self.position, self.name, self.managed = rid, position, name, False
        self.mention = f"<@&{rid}>"

    def is_default(self):
        return self.position == 0


def test_sync_gives_the_separator_with_a_tier_role():
    """The real role list: Dispatch NCO alone brings [DIVISION LEADERSHIP]."""
    settings = load_settings()
    ids = load_ids(settings.ids_file)
    keys = list(ids.roles)
    server = {rid: Role(rid, len(keys) - i, key) for i, (key, rid) in enumerate(ids.roles.items())}
    for key in ("sep_grade", "sep_postings", "sep_division_leadership", "sep_qualifications", "sep_server_team"):
        server[ids.role(key)].name = f"[── {key} ──]"
    held = [server[ids.role("dispatch_nco")], server[ids.role("verified")]]
    guild = types.SimpleNamespace(roles=list(server.values()), get_role=server.get, owner_id=0)
    member = types.SimpleNamespace(id=7, roles=held, guild=guild, bot=False, added=[], removed=[])

    async def add_roles(*roles, reason=None):
        member.added += [r.id for r in roles]

    async def remove_roles(*roles, reason=None):
        member.removed += [r.id for r in roles]
    member.add_roles, member.remove_roles = add_roles, remove_roles

    async def scenario():
        bot = HalvaneBot(replace(settings, database=Path(tempfile.mkdtemp()) / "t.db"))
        await bot.db.open()
        try:
            from halvane_bot.sync.service import SyncService
            return await SyncService(bot, bot.links, None).sync_member(member)  # not linked
        finally:
            await bot.db.close()
    asyncio.run(scenario())
    assert ids.role("sep_division_leadership") in member.added


def test_export_report_names_new_renamed_and_removed():
    old = {"team_lead": 1, "dept_23": 2}
    new = {"team_lead": 1, "new_role": 3}
    before = old_names('  team_lead: "1"  # Team Lead' + chr(10) + '  dept_23: "2"  # Dept. 23' + chr(10))
    lines = describe_changes("role", old, new, before, {1: "Team Leader", 3: "New Role"})
    assert lines[0].startswith("new") and "new_role" in lines[0]
    assert "renamed" in lines[1] and "Team Lead -> Team Leader" in lines[1]
    assert lines[2].startswith("REMOVED") and "dept_23" in lines[2] and "Dept. 23" in lines[2]


def test_ticket_channels_are_left_out_of_ids():
    from halvane_bot.export_ids import ignored
    patterns = load_settings().ids_ignore_channels
    assert ignored("closed-0002", patterns) and ignored("ticket-0141", patterns)
    assert not ignored("open-a-ticket", patterns) and not ignored("ticket-transcripts", patterns)
