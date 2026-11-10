"""ApprovalService end to end with a fake Roblox and a fake Discord member."""
from __future__ import annotations

import asyncio
import json
import tempfile
import types
from dataclasses import replace
from pathlib import Path

import pytest

from halvane_bot.approvals.ledger import ApprovalError, approve, demote
from halvane_bot.approvals.service import ApprovalService
from halvane_bot.bot import HalvaneBot
from halvane_bot.config import load_settings
from halvane_bot.roblox.opencloud import Entry, Membership, OpenCloudError


class FakeCloud:
    def __init__(self, member_roles, conflict_once=False):
        self.membership_value = Membership("groups/467115756/memberships/abc", frozenset(member_roles))
        self.store: dict[str, dict] = {}
        self.etag = 0
        self.conflict_once = conflict_once
        self.assigned: list[int] = []
        self.unassigned: list[int] = []
        self.published: list[tuple] = []
        self.writes = 0

    async def membership(self, user_id):
        return self.membership_value

    async def get_entry(self, store, key):
        v = self.store.get(key)
        return None if v is None else Entry(v, str(self.etag))

    async def put_entry(self, store, key, value, *, user_ids=None, etag=None):
        self.writes += 1
        if "null" in json.dumps(value):  # like Roblox: 400 "Value cannot be null"
            raise OpenCloudError(400, "Value cannot be null. (Parameter 'value')")
        if self.conflict_once:
            self.conflict_once = False
            raise OpenCloudError(409, "etag mismatch")
        self.store[key] = value
        self.etag += 1

    async def assign_role(self, m, role_id):
        self.assigned.append(role_id)

    async def unassign_role(self, m, role_id):
        self.unassigned.append(role_id)

    async def publish(self, topic, message):
        self.published.append((topic, message))


class FakeRole:
    def __init__(self, rid):
        self.id = rid
        self.mention = f"<@&{rid}>"


def fake_member(role_ids):
    guild = types.SimpleNamespace(get_role=lambda i: FakeRole(i))
    member = types.SimpleNamespace(id=7, display_name="whov2", guild=guild, bot=False,
                                   roles=[FakeRole(i) for i in role_ids], added=[], removed=[])

    async def add_roles(*roles, reason=None):
        member.added += [r.id for r in roles]

    async def remove_roles(*roles, reason=None):
        member.removed += [r.id for r in roles]
    member.add_roles, member.remove_roles = add_roles, remove_roles
    return member


def run(cloud, member, mutate, division, link=True):
    async def scenario():
        bot = HalvaneBot(replace(load_settings(), database=Path(tempfile.mkdtemp()) / "t.db"))
        await bot.db.open()
        try:
            if link:
                await bot.links.link(7, 1069090294, "RedSuix")
            bot.cloud = cloud
            bot.role_sync = None  # the member sync itself is covered elsewhere
            service = ApprovalService(bot)
            return service, await service.apply(member, lambda led: mutate(led, service.cfg), division)
        finally:
            await bot.db.close()
    return asyncio.run(scenario())


def test_approve_writes_ledger_sets_group_tier_and_tells_servers():
    team_lead_grade, controller_grade = 915936116, 917892132
    cloud = FakeCloud(member_roles={controller_grade, 999}, conflict_once=True)
    member = fake_member([])
    service, result = run(cloud, member, lambda led, cfg: approve(led, cfg, "dispatch", "team_lead", "discord:1", 100),
                          "dispatch")
    value = cloud.store["1069090294"]
    assert value["leadership"] == {"dispatch": "team_lead"} and value["grade"] == "team_lead"
    assert cloud.writes == 2                       # first write hit an etag conflict and was redone
    assert cloud.assigned == [team_lead_grade]
    assert cloud.unassigned == [controller_grade]  # the old grade role goes; role 999 is untouched
    assert cloud.published == [("personnel", {"userId": 1069090294, "kind": "ledger"})]
    nco = service.cfg.divisions["dispatch"].tier_roles["nco"]
    assert member.added == [nco] and member.removed == []


def test_refused_before_anything_is_written():
    cloud = FakeCloud(member_roles=set())
    member = fake_member([])

    def already(led, cfg):
        raise ApprovalError("Already Team Lead")
    with pytest.raises(ApprovalError, match="Already"):
        run(cloud, member, already, "dispatch")
    assert cloud.writes == 0 and cloud.assigned == [] and member.added == []


def test_unlinked_member_is_refused():
    with pytest.raises(ApprovalError, match="has not linked"):
        run(FakeCloud(set()), fake_member([]), lambda led, cfg: led, None, link=False)


def test_missing_permission_is_explained():
    cloud = FakeCloud(set())

    async def refused(*a, **k):
        raise OpenCloudError(403, "forbidden")
    cloud.put_entry = refused
    with pytest.raises(ApprovalError, match="missing a permission"):
        run(cloud, fake_member([]), lambda led, cfg: approve(led, cfg, "dispatch", "posting", "d", 1), None)


def test_demote_to_none_writes_and_drops_the_grade_role():
    team_lead_grade = 915936116
    cloud = FakeCloud(member_roles={team_lead_grade})
    cloud.store["1069090294"] = {"v": 1, "leadership": {"dispatch": "team_lead"}, "grade": "team_lead",
                                 "postings": ["dispatch"], "certifications": [], "updatedAt": 1, "log": []}
    nco_member = fake_member([])
    service, _ = run(cloud, nco_member, lambda led, cfg: led, None)  # load cfg for the tier role id
    nco = service.cfg.divisions["dispatch"].tier_roles["nco"]
    member = fake_member([nco])
    _, result = run(cloud, member, lambda led, cfg: demote(led, cfg, "dispatch", None, "discord:1", 2), "dispatch")
    value = cloud.store["1069090294"]
    assert value["leadership"] == {} and "grade" not in value and value["postings"] == ["dispatch"]
    assert cloud.unassigned == [team_lead_grade]
    assert member.removed == [nco] and member.added == []
