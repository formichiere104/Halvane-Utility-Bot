"""Carry one staff decision everywhere it belongs: the game's ledger, the Roblox group, the Discord tier role,
live game servers, and the member's synced roles."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

import discord

from ..ids import load_ids
from ..roblox.opencloud import OpenCloudError
from .ledger import (ApprovalConfig, ApprovalError, Ledger, group_changes, load_approvals, mirror_group,
                     mirror_labels, tier_changes)

if TYPE_CHECKING:
    from ..bot import HalvaneBot
    from ..sync.service import SyncResult

log = logging.getLogger(__name__)

WRITE_ATTEMPTS = 3
HINT_403 = ("Roblox refused: the Open Cloud key is missing a permission for this (group write, data store "
            "create/update or messaging publish), or its owner's group rank is below the role being set.")


@dataclass
class ApplyResult:
    ledger: Ledger
    group_assigned: list[int] = field(default_factory=list)
    group_unassigned: list[int] = field(default_factory=list)
    tier_added: list[discord.Role] = field(default_factory=list)
    tier_removed: list[discord.Role] = field(default_factory=list)
    sync: SyncResult | None = None
    warnings: list[str] = field(default_factory=list)


class ApprovalService:
    def __init__(self, bot: HalvaneBot) -> None:
        self.bot = bot
        self.cfg: ApprovalConfig = self.load()
        # Roblox user id -> the certification and posting group roles the ledger last matched. Lets the 15-minute
        # sweep skip the ledger read for members whose group roles haven't changed.
        self._mirrored: dict[int, frozenset[int]] = {}

    def load(self) -> ApprovalConfig:
        s = self.bot.settings
        return load_approvals(s.root / "config" / "approvals.yaml", load_ids(s.ids_file))

    def _mirrored_roles(self, have: set[int] | frozenset[int]) -> frozenset[int]:
        cfg = self.cfg
        tracked = ({c.group_role for c in cfg.certifications.values()}
                   | {d.posting_group_role for d in cfg.divisions.values() if d.posting_group_role})
        return frozenset(set(have) & tracked)

    async def mirror(self, roblox_id: int, have: set[int] | frozenset[int], *, force: bool = False) -> list[str]:
        """Bring the ledger's certifications and postings in line with the member's group roles and tell live
        servers. Returns what changed ("+Reach Truck Certified"); [] when nothing did. Raises OpenCloudError."""
        cloud = self.bot.cloud
        if cloud is None:
            return []
        seen = self._mirrored_roles(have)
        if not force and self._mirrored.get(roblox_id) == seen:
            return []
        key, labels = str(roblox_id), []
        for attempt in range(WRITE_ATTEMPTS):
            entry = await cloud.get_entry(self.cfg.ledger_store, key)
            old = Ledger.from_value(entry.value if entry else None)
            new = mirror_group(old, have, self.cfg, time.time())
            if new is None:
                break
            try:
                await cloud.put_entry(self.cfg.ledger_store, key, new.to_value(self.cfg, time.time()),
                                      user_ids=[roblox_id], etag=entry.etag if entry else None)
                labels = mirror_labels(old, new, self.cfg)
                break
            except OpenCloudError as exc:
                if exc.status in (409, 412) and attempt < WRITE_ATTEMPTS - 1:
                    continue
                raise
        self._mirrored[roblox_id] = seen
        if labels:
            try:
                await cloud.publish(self.cfg.message_topic, {"userId": roblox_id, "kind": "ledger"})
            except OpenCloudError as exc:
                log.warning("live servers not told about %s's group change: %s", roblox_id, exc)
        return labels

    async def apply(self, member: discord.Member, mutate: Callable[[Ledger], Ledger],
                    division: str | None) -> ApplyResult:
        """Read the ledger, apply `mutate` (may raise ApprovalError before anything is written), write it back,
        then update the group, the division's tier role, live servers and the member's synced roles."""
        cloud, cfg = self.bot.cloud, self.cfg
        if cloud is None:
            raise ApprovalError("Roblox access is not configured on the bot.")
        link = await self.bot.links.by_discord(member.id)
        if link is None:
            raise ApprovalError(f"{member.display_name} has not linked a Roblox account yet (/verify).")
        try:
            membership = await cloud.membership(link.roblox_id)
            if membership is None:
                raise ApprovalError(f"**{link.roblox_name}** is not in the Staff Register (the Roblox group).")

            # 1. The ledger, with the etag so two staff approving at once can't overwrite each other.
            key = str(link.roblox_id)
            for attempt in range(WRITE_ATTEMPTS):
                entry = await cloud.get_entry(cfg.ledger_store, key)
                current = Ledger.from_value(entry.value if entry else None)
                # Certifications and postings given or taken in the group by hand count first.
                current = mirror_group(current, membership.roles, cfg, time.time()) or current
                new = mutate(current)
                try:
                    await cloud.put_entry(cfg.ledger_store, key, new.to_value(cfg, time.time()),
                                          user_ids=[link.roblox_id], etag=entry.etag if entry else None)
                    break
                except OpenCloudError as exc:
                    if exc.status in (409, 412) and attempt < WRITE_ATTEMPTS - 1:
                        continue  # changed meanwhile: read again and reapply
                    raise
            result = ApplyResult(ledger=new)

            # 2. The Roblox group: one grade role, plus any approved postings and certifications.
            assign, unassign = group_changes(membership.roles, new, cfg)
            for role_id in sorted(assign):
                await cloud.assign_role(membership, role_id)
                result.group_assigned.append(role_id)
            for role_id in sorted(unassign):
                await cloud.unassign_role(membership, role_id)
                result.group_unassigned.append(role_id)
            self._mirrored[link.roblox_id] = self._mirrored_roles((set(membership.roles) | assign) - unassign)

            # 3. Live servers re-read this player's ledger.
            try:
                await cloud.publish(cfg.message_topic, {"userId": link.roblox_id, "kind": "ledger"})
            except OpenCloudError as exc:
                result.warnings.append(f"live servers were not told ({exc.status}); the player gets it on rejoin")
        except OpenCloudError as exc:
            if exc.status in (401, 403):
                raise ApprovalError(HINT_403) from None
            raise ApprovalError(f"Roblox error: {exc}") from None

        # 4. The Discord tier role for the division (Discord-only, never touched by the role sync).
        if division is not None:
            add_ids, remove_ids = tier_changes({r.id for r in member.roles}, new, cfg, division)
            add = [r for i in add_ids if (r := member.guild.get_role(i))]
            remove = [r for i in remove_ids if (r := member.guild.get_role(i))]
            try:
                if add:
                    await member.add_roles(*add, reason="Approval")
                if remove:
                    await member.remove_roles(*remove, reason="Approval")
                result.tier_added, result.tier_removed = add, remove
            except discord.HTTPException as exc:
                result.warnings.append(f"Discord tier role not changed ({exc.status})")

        # 5. Grade, posting and certification roles in Discord follow the group.
        if self.bot.role_sync is not None:
            result.sync = await self.bot.role_sync.sync_member(member, ledger=False)  # the ledger is fresh
        return result
