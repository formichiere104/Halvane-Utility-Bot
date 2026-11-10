"""/verify, /update, /unlink, /sync, the Verify button, member joins and the periodic sweep.

Linking: /verify (or the Verify button in #verify) gives a code; the player files it at the Staff Register
terminal in the game; the game writes it to the LinkCodes data store; the poll below finds it, links the
accounts, deletes the entry and syncs roles. Pending codes live in memory: a restart only means /verify again.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..embeds.actions import VERIFY_CUSTOM_ID
from ..embeds.publisher import NO_PINGS
from ..roblox.opencloud import OpenCloudError
from ..sync.linking import PendingCode, filed_user, new_code
from .common import UserFacingError, has_any_role_key, report_error

if TYPE_CHECKING:
    from ..bot import HalvaneBot

log = logging.getLogger(__name__)

BLUE = 0x4F8FD6
GREEN = 0x3E9E6E
GREY = 0x7A8288
CODES_PER_WINDOW = 3
CODE_WINDOW = 600  # seconds


class VerifyCog(commands.Cog):
    sync_group = app_commands.Group(
        name="sync", description="Staff: sync roles from the Roblox group.",
        guild_only=True, default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: HalvaneBot) -> None:
        self.bot = bot
        self.pending: dict[int, tuple[PendingCode, discord.Interaction]] = {}
        self.attempts: dict[int, list[float]] = {}

    async def cog_load(self) -> None:
        # The Verify button in #verify (embeds/verify.yaml) runs this.
        self.bot.action_handlers[VERIFY_CUSTOM_ID] = self.start_verification
        self.sweep_loop.change_interval(minutes=self.bot.settings.sync_interval_minutes)
        self.poll.start()
        self.sweep_loop.start()

    async def cog_unload(self) -> None:
        self.bot.action_handlers.pop(VERIFY_CUSTOM_ID, None)
        self.poll.cancel()
        self.sweep_loop.cancel()

    async def cog_app_command_error(self, interaction: discord.Interaction,
                                    error: app_commands.AppCommandError) -> None:
        await report_error(interaction, error)

    # ---- linking ----------------------------------------------------------------------------------

    def _code_embed(self, p: PendingCode, current: str | None) -> discord.Embed:
        game = self.bot.settings.links.get("game") or ""
        text = (f"Your code: **`{p.code}`**\n\n"
                f"**1.** Join the game{': ' + game if game else '.'}\n"
                "**2.** In the Lobby, use the **Staff Register** terminal and file the code.\n"
                "**3.** This message updates as soon as it is filed.\n\n"
                f"The code expires <t:{int(p.expires_at)}:R>.")
        if current:
            text += f"\n\nYou are linked to **{current}** now; filing this code switches the link."
        return discord.Embed(title="Staff Register", description=text, color=BLUE)

    async def start_verification(self, interaction: discord.Interaction) -> None:
        if self.bot.cloud is None:
            await interaction.response.send_message("Verification is not available right now.", ephemeral=True)
            return
        uid = interaction.user.id
        now = time.time()
        recent = [t for t in self.attempts.get(uid, []) if now - t < CODE_WINDOW]
        if len(recent) >= CODES_PER_WINDOW:
            await interaction.response.send_message(
                "You have asked for three codes in ten minutes. Please wait before asking again.", ephemeral=True)
            return
        self.attempts[uid] = recent + [now]
        s = self.bot.settings
        code = new_code(s.link_alphabet, s.link_length, {p.code for p, _ in self.pending.values()})
        pending = PendingCode(code, uid, now, now + s.link_expires_minutes * 60)
        self.pending[uid] = (pending, interaction)  # a newer code replaces an older one
        link = await self.bot.links.by_discord(uid)
        await interaction.response.send_message(
            embed=self._code_embed(pending, link.roblox_name if link else None), ephemeral=True)

    async def _edit(self, interaction: discord.Interaction, title: str, text: str, color: int) -> None:
        try:
            await interaction.edit_original_response(embed=discord.Embed(title=title, description=text, color=color))
        except discord.HTTPException:
            pass  # the private message is gone or its token expired; nothing else to do

    @tasks.loop(seconds=5)
    async def poll(self) -> None:
        cloud = self.bot.cloud
        if cloud is None or not self.pending:
            return
        now = time.time()
        for uid, (p, interaction) in list(self.pending.items()):
            if now > p.expires_at + 5:
                if self.pending.get(uid, (None,))[0] is p:
                    del self.pending[uid]
                await self._edit(interaction, "Code expired", "That code expired. Run `/verify` for a new one.", GREY)
                continue
            try:
                value = await cloud.get_link_entry(p.code)
            except OpenCloudError as exc:
                log.warning("reading link code failed: %s", exc)
                continue
            if value is None:
                continue
            roblox_id, reason = filed_user(value, p)
            if roblox_id is None:
                log.info("ignoring a LinkCodes entry: %s", reason)
                continue
            if self.pending.get(uid, (None,))[0] is not p:
                continue  # replaced by a newer code meanwhile
            del self.pending[uid]
            await self._complete(p, roblox_id, interaction)

    @poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()

    async def _complete(self, p: PendingCode, roblox_id: int, interaction: discord.Interaction) -> None:
        cloud, sync = self.bot.cloud, self.bot.role_sync
        assert cloud is not None and sync is not None
        try:
            await cloud.delete_link_entry(p.code)
        except OpenCloudError as exc:
            log.warning("couldn't delete a used link code: %s", exc)
        username, display = await cloud.user_names(roblox_id)
        moved_from = await self.bot.links.link(p.discord_id, roblox_id, username)

        guild = self.bot.get_guild(self.bot.settings.guild_id)
        member = guild.get_member(p.discord_id) if guild else None
        result = await sync.sync_member(member, set_nickname=True) if member else None
        if moved_from and guild and (old := guild.get_member(moved_from)):
            await sync.sync_member(old)

        name = f"**{username}**" + (f" ({display})" if display != username else "")
        text = f"Linked to {name}. Your roles: {result.summary() if result else 'they update when you rejoin the server.'}"
        await self._edit(interaction, "Linked", text, GREEN)
        who = member.mention if member else f"<@{p.discord_id}>"
        await self.bot.log_line(f"{who} linked Roblox **{username}** (`{roblox_id}`)"
                                + (f", moved from <@{moved_from}>" if moved_from else "") + ".")

    # ---- member commands --------------------------------------------------------------------------

    @app_commands.command(name="verify", description="Link your Roblox account to get your roles.")
    @app_commands.guild_only()
    async def verify(self, interaction: discord.Interaction) -> None:
        await self.start_verification(interaction)

    @app_commands.command(name="update", description="Update your roles from the Roblox group now.")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 30)
    async def update(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member) or self.bot.role_sync is None:
            raise UserFacingError("Use this in the server.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.bot.role_sync.sync_member(member, set_nickname=True)
        head = (f"Linked to **{result.link.roblox_name}**." if result.link
                else "You are not linked yet: use `/verify` or the Verify button in #verify.")
        if result.link and result.in_group is False:
            head += " You are not in the Staff Register (our Roblox group) yet."
        await interaction.followup.send(f"{head}\nRoles: {result.summary()}", ephemeral=True,
                                        allowed_mentions=NO_PINGS)

    @app_commands.command(name="unlink", description="Remove the link to your Roblox account and its roles.")
    @app_commands.guild_only()
    async def unlink(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member) or self.bot.role_sync is None:
            raise UserFacingError("Use this in the server.")
        if not await self.bot.links.unlink(member.id):
            raise UserFacingError("You are not linked to a Roblox account.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.bot.role_sync.sync_member(member)
        await interaction.followup.send(f"Unlinked. Roles: {result.summary()}", ephemeral=True,
                                        allowed_mentions=NO_PINGS)
        await self.bot.log_line(f"{member.mention} unlinked their Roblox account.")

    # ---- staff ----------------------------------------------------------------------------------------

    def _require_manager(self, interaction: discord.Interaction) -> discord.Member:
        member = interaction.user
        if not isinstance(member, discord.Member) or not has_any_role_key(
                member, self.bot.settings.sync_managers, self.bot.settings.ids_file):
            raise UserFacingError("Only the Directorate can use this.")
        if self.bot.role_sync is None:
            raise UserFacingError("Role sync is not running.")
        return member

    @sync_group.command(name="member", description="Sync one member's roles now.")
    @app_commands.describe(member="Who to sync")
    async def sync_member(self, interaction: discord.Interaction, member: discord.Member) -> None:
        self._require_manager(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        assert self.bot.role_sync is not None
        result = await self.bot.role_sync.sync_member(member, set_nickname=True)
        linked = f"linked to **{result.link.roblox_name}** (`{result.link.roblox_id}`)" if result.link else "not linked"
        await interaction.followup.send(f"{member.mention} is {linked}. Roles: {result.summary()}",
                                        ephemeral=True, allowed_mentions=NO_PINGS)

    @sync_group.command(name="all", description="Sync every linked member now (also reloads the role map).")
    async def sync_all(self, interaction: discord.Interaction) -> None:
        self._require_manager(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        assert self.bot.role_sync is not None
        checked, changed, errors = await self.bot.role_sync.sweep()
        await interaction.followup.send(
            f"Synced {checked} linked member(s): {changed} changed, {errors} could not be synced.", ephemeral=True)

    # ---- events and the sweep ------------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot or member.guild.id != self.bot.settings.guild_id or self.bot.role_sync is None:
            return
        await self.bot.role_sync.sync_member(member, set_nickname=True)

    @tasks.loop(minutes=15)
    async def sweep_loop(self) -> None:
        if self.bot.role_sync is None:
            return
        checked, changed, errors = await self.bot.role_sync.sweep()
        if changed or errors:
            log.info("sweep: %d checked, %d changed, %d errors", checked, changed, errors)

    @sweep_loop.before_loop
    async def _before_sweep(self) -> None:
        await self.bot.wait_until_ready()
