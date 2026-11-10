"""/approve, /demote, /certify: the Directorate records a decision; ApprovalService carries it everywhere.

Quota is checked by staff in the ticket for now: the game has no Compliance or Personnel File yet. When it
does, the bot will refuse an approval whose quota isn't met.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable

import discord
from discord import app_commands
from discord.ext import commands

from ..approvals.ledger import ApprovalError, Ledger, approve, certify, demote
from ..approvals.service import ApprovalService
from ..embeds.publisher import NO_PINGS
from ..ids import load_ids
from .common import UserFacingError, has_any_role_key, report_error

if TYPE_CHECKING:
    from ..bot import HalvaneBot

log = logging.getLogger(__name__)

STAFF = discord.Permissions(manage_guild=True)


class ApprovalsCog(commands.Cog):
    def __init__(self, bot: HalvaneBot) -> None:
        self.bot = bot
        self.service = bot.approvals or ApprovalService(bot)

    async def cog_app_command_error(self, interaction: discord.Interaction,
                                    error: app_commands.AppCommandError) -> None:
        await report_error(interaction, error)

    # ---- helpers ----------------------------------------------------------------------------------

    def _require_manager(self, interaction: discord.Interaction) -> discord.Member:
        member = interaction.user
        if not isinstance(member, discord.Member) or not has_any_role_key(
                member, self.bot.settings.sync_managers, self.bot.settings.ids_file):
            raise UserFacingError("Only the Directorate can record approvals.")
        return member

    def _choices(self, items: list[tuple[str, str]], current: str) -> list[app_commands.Choice[str]]:
        cur = current.lower()
        return [app_commands.Choice(name=label, value=key) for key, label in items
                if cur in label.lower() or cur in key][:25]

    async def _notice(self, text: str) -> None:
        try:
            channel = self.bot.get_channel(load_ids(self.bot.settings.ids_file).channel(self.service.cfg.notices_channel))
            if isinstance(channel, discord.abc.Messageable):
                await channel.send(text, allowed_mentions=NO_PINGS)
        except Exception:
            log.exception("could not post the personnel notice")

    async def _run(self, interaction: discord.Interaction, member: discord.Member,
                   mutate: Callable[[Ledger], Ledger], division: str | None,
                   done: str, notice: str | None) -> None:
        staff = self._require_manager(interaction)
        if member.bot:
            raise UserFacingError("Bots have no Personnel File.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.service.apply(member, mutate, division)
        except ApprovalError as exc:
            raise UserFacingError(str(exc)) from None
        lines = [done]
        if result.tier_added or result.tier_removed:
            lines.append("Tier: " + " ".join([f"+{r.mention}" for r in result.tier_added]
                                             + [f"-{r.mention}" for r in result.tier_removed]))
        if result.sync is not None:
            lines.append(f"Synced roles: {result.sync.summary()}")
        lines += [f"Note: {w}" for w in result.warnings]
        await interaction.followup.send("\n".join(lines), ephemeral=True, allowed_mentions=NO_PINGS)
        if notice:
            await self._notice(notice)
        await self.bot.log_line(f"{staff.mention}: {done}")

    # ---- commands ---------------------------------------------------------------------------------

    @app_commands.command(name="approve", description="Directorate: record an approved rank or posting.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(member="Who is approved", division="Which division",
                           rank="The rank approved, or Posting to unlock the division")
    async def approve_(self, interaction: discord.Interaction, member: discord.Member,
                       division: str, rank: str) -> None:
        cfg = self.service.cfg
        if division not in cfg.divisions or (rank != "posting" and rank not in cfg.steps):
            raise UserFacingError("Pick the division and rank from the lists.")
        d = cfg.divisions[division]
        by = f"discord:{interaction.user.id}"
        if rank == "posting":
            done = f"{member.mention} joins **{d.label}**."
        else:
            done = f"{member.mention} is approved as **{cfg.steps[rank].label}** in **{d.label}**."
        await self._run(interaction, member, lambda led: approve(led, cfg, division, rank, by, time.time()),
                        None if rank == "posting" else division, done, notice=done)

    @app_commands.command(name="demote", description="Directorate: lower or remove a leadership rank.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(member="Who", division="Which division", to="The new rank, or None to remove it")
    async def demote_(self, interaction: discord.Interaction, member: discord.Member,
                      division: str, to: str) -> None:
        cfg = self.service.cfg
        target = None if to == "none" else to
        if division not in cfg.divisions or (target is not None and target not in cfg.steps):
            raise UserFacingError("Pick the division and rank from the lists.")
        d = cfg.divisions[division]
        by = f"discord:{interaction.user.id}"
        label = cfg.steps[target].label if target else "no leadership rank"
        done = f"{member.mention} is now **{label}** in **{d.label}**."
        await self._run(interaction, member, lambda led: demote(led, cfg, division, target, by, time.time()),
                        division, done, notice=None)  # demotions are not announced

    @app_commands.command(name="certify", description="Directorate: record a certification.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(member="Who passed", certification="Which certification")
    async def certify_(self, interaction: discord.Interaction, member: discord.Member, certification: str) -> None:
        cfg = self.service.cfg
        if certification not in cfg.certifications:
            raise UserFacingError("Pick the certification from the list.")
        by = f"discord:{interaction.user.id}"
        done = f"{member.mention} is certified: **{cfg.certifications[certification].label}**."
        await self._run(interaction, member, lambda led: certify(led, cfg, certification, by, time.time()),
                        None, done, notice=done)

    # ---- autocomplete ----------------------------------------------------------------------------------

    def _divisions(self) -> list[tuple[str, str]]:
        return [(k, d.label) for k, d in self.service.cfg.divisions.items()]

    def _ranks(self) -> list[tuple[str, str]]:
        return [(s.key, s.label) for s in self.service.cfg.step_list()]

    @approve_.autocomplete("division")
    async def _ac_approve_division(self, interaction: discord.Interaction, current: str):
        return self._choices(self._divisions(), current)

    @approve_.autocomplete("rank")
    async def _ac_approve_rank(self, interaction: discord.Interaction, current: str):
        return self._choices([("posting", "Posting (unlock the division)")] + self._ranks(), current)

    @demote_.autocomplete("division")
    async def _ac_demote_division(self, interaction: discord.Interaction, current: str):
        return self._choices(self._divisions(), current)

    @demote_.autocomplete("to")
    async def _ac_demote_to(self, interaction: discord.Interaction, current: str):
        return self._choices([("none", "None (remove the rank)")] + self._ranks(), current)

    @certify_.autocomplete("certification")
    async def _ac_certify(self, interaction: discord.Interaction, current: str):
        return self._choices([(k, c.label) for k, c in self.service.cfg.certifications.items()], current)
