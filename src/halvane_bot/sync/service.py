"""Apply the role map to Discord members. Only roles in the map's managed set are ever added or removed."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

from ..ids import load_ids
from ..roblox.opencloud import OpenCloud, OpenCloudError
from ..store import Link, LinkStore
from .role_map import RoleMap, RoleMapError, changes, load_role_map

if TYPE_CHECKING:
    from ..bot import HalvaneBot

log = logging.getLogger(__name__)


@dataclass
class SyncResult:
    link: Link | None
    in_group: bool | None = None
    added: list[discord.Role] = field(default_factory=list)
    removed: list[discord.Role] = field(default_factory=list)
    error: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append("added " + " ".join(r.mention for r in self.added))
        if self.removed:
            parts.append("removed " + " ".join(r.mention for r in self.removed))
        if not parts and not self.error:
            parts.append("no changes")
        if self.error:
            parts.append(f"not synced: {self.error}")
        return "; ".join(parts)


class SyncService:
    def __init__(self, bot: HalvaneBot, links: LinkStore, cloud: OpenCloud | None) -> None:
        self.bot = bot
        self.links = links
        self.cloud = cloud
        self.role_map: RoleMap = self._load()

    def _load(self) -> RoleMap:
        s = self.bot.settings
        if s.role_map_file is None:
            raise RoleMapError("role_map_file is not set in config/bot.yaml")
        return load_role_map(s.role_map_file, load_ids(s.ids_file))

    def reload(self) -> None:
        """Pick up role map edits after a git pull. Keeps the old map if the new one is broken."""
        try:
            self.role_map = self._load()
        except (RoleMapError, OSError) as exc:
            log.error("role map not reloaded, keeping the previous one: %s", exc)

    async def sync_member(self, member: discord.Member, *, set_nickname: bool = False) -> SyncResult:
        link = await self.links.by_discord(member.id)
        result = SyncResult(link=link)
        group_roles: set[int] | None = None
        if link:
            if self.cloud is None:
                result.error = "Roblox access is not configured"
                return result
            try:
                group_roles = await self.cloud.member_roles(link.roblox_id)
            except OpenCloudError as exc:
                # Never strip roles because Roblox didn't answer.
                result.error = f"couldn't read the Roblox group (HTTP {exc.status})"
                log.warning("sync %s: %s", member, exc)
                return result
            result.in_group = group_roles is not None

        desired = self.role_map.desired(link is not None, group_roles)
        add_ids, remove_ids = changes((r.id for r in member.roles), desired, self.role_map.managed)
        guild = member.guild
        add = [r for i in sorted(add_ids) if (r := guild.get_role(i)) is not None]
        remove = [r for i in sorted(remove_ids) if (r := guild.get_role(i)) is not None]
        try:
            if add:
                await member.add_roles(*add, reason="Roblox group sync")
            if remove:
                await member.remove_roles(*remove, reason="Roblox group sync")
            result.added, result.removed = add, remove
        except discord.Forbidden:
            result.error = "the bot's role is below a role it has to change"
        except discord.HTTPException as exc:
            result.error = f"Discord refused the change ({exc.status})"

        if set_nickname and link:
            await self._nickname(member, link)
        return result

    async def _nickname(self, member: discord.Member, link: Link) -> None:
        fmt = self.bot.settings.nickname_format
        if not fmt or member.id == member.guild.owner_id:  # nobody can rename the server owner
            return
        nick = fmt.format(username=link.roblox_name)[:32]
        if member.nick != nick:
            try:
                await member.edit(nick=nick, reason="Roblox username")
            except discord.HTTPException as exc:
                log.info("couldn't set nickname for %s: %s", member, exc)

    async def sweep(self) -> tuple[int, int, int]:
        """Sync every linked member still in the server. Returns (checked, changed, errors)."""
        guild = self.bot.get_guild(self.bot.settings.guild_id)
        if guild is None:
            return 0, 0, 0
        self.reload()
        checked = changed = errors = 0
        for link in await self.links.all():
            member = guild.get_member(link.discord_id)
            if member is None or member.bot:
                continue
            result = await self.sync_member(member)
            checked += 1
            changed += result.changed
            errors += result.error is not None
            await asyncio.sleep(0.25)  # well inside Open Cloud's 300 requests a minute
        return checked, changed, errors
