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

from .. import ui
from ..embeds.actions import VERIFY_CUSTOM_ID
from ..roblox.opencloud import OpenCloudError
from ..sync.linking import PendingCode, filed_user, new_code
from .common import UserFacingError, has_any_role_key, report_error

if TYPE_CHECKING:
    from ..bot import HalvaneBot

log = logging.getLogger(__name__)

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
        self.checks: dict[str, int] = {}  # code -> how often it has been looked up (for log pacing)

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
        return ui.card("Staff Register", text, color=ui.SKY)

    async def start_verification(self, interaction: discord.Interaction) -> None:
        if self.bot.cloud is None:
            await ui.respond(interaction, ui.problem("Verification is not available right now."), private=True)
            return
        uid = interaction.user.id
        now = time.time()
        recent = [t for t in self.attempts.get(uid, []) if now - t < CODE_WINDOW]
        if len(recent) >= CODES_PER_WINDOW:
            await ui.respond(interaction, ui.problem(
                "You have asked for three codes in ten minutes. Please wait before asking again."), private=True)
            return
        self.attempts[uid] = recent + [now]
        s = self.bot.settings
        code = new_code(s.link_alphabet, s.link_length, {p.code for p, _ in self.pending.values()})
        pending = PendingCode(code, uid, now, now + s.link_expires_minutes * 60)
        self.pending[uid] = (pending, interaction)  # a newer code replaces an older one
        log.info("issued link code %s to %s (%d), expires in %d min", code, interaction.user, uid,
                 s.link_expires_minutes)
        link = await self.bot.links.by_discord(uid)
        # Always private: anyone who saw the code could file it with their own Roblox account.
        await ui.respond(interaction, self._code_embed(pending, link.roblox_name if link else None), private=True)

    async def _edit(self, interaction: discord.Interaction, title: str, text: str, color: int,
                    fields: list[ui.Field] | None = None) -> None:
        try:
            await interaction.edit_original_response(embed=ui.card(title, text, color=color, fields=fields or []))
        except discord.HTTPException:
            pass  # the private message is gone or its token expired; nothing else to do

    @tasks.loop(seconds=5)
    async def poll(self) -> None:
        if self.bot.cloud is None or not self.pending:
            return
        for uid, (p, interaction) in list(self.pending.items()):
            # Anything unexpected is logged here: some network errors would otherwise make discord.py's
            # task loop retry silently, which is exactly how a stuck link looks from outside.
            try:
                await self._check(uid, p, interaction)
            except Exception:
                log.exception("link-code check for %s failed", p.code)

    async def _check(self, uid: int, p: PendingCode, interaction: discord.Interaction) -> None:
        cloud = self.bot.cloud
        assert cloud is not None
        now = time.time()
        if now > p.expires_at + 5:
            if self.pending.get(uid, (None,))[0] is p:
                del self.pending[uid]
            log.info("link code %s expired without being filed", p.code)
            await self._edit(interaction, "Code expired", "That code expired. Run `/verify` for a new one.", ui.DUSK)
            return
        checks = self.checks[p.code] = self.checks.get(p.code, 0) + 1
        try:
            value = await cloud.get_link_entry(p.code)
        except OpenCloudError as exc:
            if exc.status in (401, 403):
                # The key can't read entries: waiting won't help, so say so instead of going quiet.
                log.error("LinkCodes read refused: %s", exc)
                if self.pending.get(uid, (None,))[0] is p:
                    del self.pending[uid]
                await self._edit(interaction, "Not linked",
                                 "The Recruitment Office can't read the register right now. Staff have been "
                                 "told; please try again later.", ui.DUSK)
                await self.bot.log_event("Linking failed", f"Roblox refused to read LinkCodes ({exc}). "
                                         "Run `roblox-check` on the host.", interaction=interaction, color=ui.DUSK)
            else:
                log.warning("reading link code %s failed (will retry): %s", p.code, exc)
            return
        if value is None:
            if checks == 1 or checks % 12 == 0:  # first check, then once a minute
                log.info("link code %s: nothing filed yet (check %d)", p.code, checks)
            return
        log.info("link code %s: found %r", p.code, value)
        roblox_id, reason = filed_user(value, p)
        if roblox_id is None:
            log.warning("ignoring LinkCodes entry %s: %s (value %r, code issued at %d)",
                        p.code, reason, value, int(p.created_at))
            return
        if self.pending.get(uid, (None,))[0] is not p:
            return  # replaced by a newer code meanwhile
        del self.pending[uid]
        self.checks.pop(p.code, None)
        await self._complete(p, roblox_id, interaction)

    @poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()
        log.info("link-code poll running every 5 s")

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
        result = await sync.sync_member(member, set_nickname=True, force_ledger=True) if member else None
        if moved_from and guild and (old := guild.get_member(moved_from)):
            await sync.sync_member(old)

        name = f"**{username}**" + (f" ({display})" if display != username else "")
        if result:
            await self._edit(interaction, "Linked", f"Linked to {name}.", ui.BLUE, ui.sync_fields(result))
        else:
            await self._edit(interaction, "Linked", f"Linked to {name}. Your roles update when you rejoin the server.",
                             ui.BLUE)
        who = member.mention if member else f"<@{p.discord_id}>"
        await self.bot.log_event(
            "Account linked", f"{who} linked Roblox {name} (`{roblox_id}`).", interaction=interaction,
            fields=[("Moved from", f"<@{moved_from}>", True)] if moved_from else [])

    # ---- member commands --------------------------------------------------------------------------

    @app_commands.command(name="verify", description="Link your Roblox account to get your roles.")
    @app_commands.guild_only()
    async def verify(self, interaction: discord.Interaction) -> None:
        await self.start_verification(interaction)

    @app_commands.command(name="update", description="Update your roles (Discord and in game) from the Roblox group now.")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 30)
    async def update(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member) or self.bot.role_sync is None:
            raise UserFacingError("Use this in the server.")
        await interaction.response.defer(thinking=True)
        result = await self.bot.role_sync.sync_member(member, set_nickname=True, force_ledger=True)
        if result.link is None:
            await ui.respond(interaction, ui.card(
                "Not linked yet", f"{member.mention}, use `/verify` or the Verify button in #verify to link your "
                "Roblox account.", actor=member))
            return
        head = f"{member.mention} is linked to **{result.link.roblox_name}**."
        if result.in_group is False:
            head += " They are not in the Staff Register (our Roblox group) yet."
        await ui.respond(interaction, ui.card("Roles updated", head, color=ui.BLUE, actor=member,
                                              fields=ui.sync_fields(result)))

    @app_commands.command(name="unlink", description="Remove the link to your Roblox account and its roles.")
    @app_commands.guild_only()
    async def unlink(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member) or self.bot.role_sync is None:
            raise UserFacingError("Use this in the server.")
        if not await self.bot.links.unlink(member.id):
            raise UserFacingError("You are not linked to a Roblox account.")
        await interaction.response.defer(thinking=True)
        result = await self.bot.role_sync.sync_member(member)
        await ui.respond(interaction, ui.card("Account unlinked", f"{member.mention} unlinked their Roblox account.",
                                              actor=member, fields=ui.sync_fields(result)))
        await self.bot.log_event("Account unlinked", f"{member.mention} unlinked their Roblox account.",
                                 interaction=interaction)

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
        await interaction.response.defer(thinking=True)
        assert self.bot.role_sync is not None
        result = await self.bot.role_sync.sync_member(member, set_nickname=True, force_ledger=True)
        linked = f"linked to **{result.link.roblox_name}** (`{result.link.roblox_id}`)" if result.link else "not linked"
        text = f"{member.mention} is {linked}."
        await ui.respond(interaction, ui.card("Member synced", text, color=ui.INDIGO, actor=interaction.user,
                                              fields=ui.sync_fields(result)))
        await self.bot.log_event("Member synced", text, interaction=interaction, fields=ui.sync_fields(result))

    @sync_group.command(name="all", description="Sync every linked member now (also reloads the role map).")
    async def sync_all(self, interaction: discord.Interaction) -> None:
        self._require_manager(interaction)
        await interaction.response.defer(thinking=True)
        assert self.bot.role_sync is not None
        checked, changed, errors = await self.bot.role_sync.sweep()
        counts: list[ui.Field] = [("Checked", str(checked), True), ("Changed", str(changed), True),
                                  ("Could not sync", str(errors), True)]
        await ui.respond(interaction, ui.card("Everyone synced", "Every linked member was synced from the Roblox "
                                              "group.", color=ui.INDIGO, actor=interaction.user, fields=counts))
        await self.bot.log_event("Everyone synced", None, interaction=interaction, fields=counts)

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
        try:
            checked, changed, errors = await self.bot.role_sync.sweep()
            if changed or errors:
                log.info("sweep: %d checked, %d changed, %d errors", checked, changed, errors)
            await self.cleanup_link_codes()
        except Exception:
            log.exception("sweep failed")

    async def cleanup_link_codes(self) -> int:
        """Delete LinkCodes entries nobody is waiting for: older than a code's lifetime plus five minutes and not
        pending here. Keeps the data store holding only codes in use. Returns how many were removed."""
        cloud = self.bot.cloud
        if cloud is None:
            return 0
        active = {p.code for p, _ in self.pending.values()}
        max_age = self.bot.settings.link_expires_minutes * 60 + 300
        removed = 0
        for code in await cloud.list_entry_ids(cloud.link_store):
            if code in active:
                continue
            value = await cloud.get_link_entry(code)
            at = value.get("at") if isinstance(value, dict) else None
            if isinstance(at, (int, float)) and not isinstance(at, bool) and time.time() - at < max_age:
                continue  # filed moments ago; its code may still be claimed
            await cloud.delete_link_entry(code)
            removed += 1
        if removed:
            log.info("removed %d unused link code(s)", removed)
        return removed

    @sweep_loop.before_loop
    async def _before_sweep(self) -> None:
        await self.bot.wait_until_ready()
