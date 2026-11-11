"""/approve, /demote, /certify: the Directorate records a decision; ApprovalService carries it everywhere.

Replies are public in the channel the command was run in. Approvals and certifications are also announced in
#personnel-notices; demotions are not. Every decision is logged in #server-logs.

Quota is checked by staff in the ticket for now: the game has no Compliance or Personnel File yet. When it
does, the bot will refuse an approval whose quota isn't met.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

import discord
from discord import app_commands
from discord.ext import commands

from .. import ui
from ..approvals.ledger import ApprovalError, Ledger, approve, certify, demote
from ..approvals.service import ApplyResult, ApprovalService
from ..ids import load_ids
from .common import UserFacingError, has_any_role_key, report_error

if TYPE_CHECKING:
    from ..bot import HalvaneBot

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Notice:
    """The cheerful line for #personnel-notices."""
    title: str
    line: str


def promotion_notice(member: str, step: str, division: str) -> Notice:
    return Notice("🎉 Promotion", f"{member} has been promoted to **{step}** of **{division}**!")


def posting_notice(member: str, division: str) -> Notice:
    return Notice("📋 New Posting", f"{member} has joined **{division}**! Welcome aboard.")


def certification_notice(member: str, certification: str) -> Notice:
    if certification.endswith(("Certified", "Qualified")):
        return Notice("🎓 Certification", f"{member} is now **{certification}**!")
    return Notice("🎓 Certification", f"{member} is now certified in **{certification}**!")


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

    async def _announce(self, notice: Notice, member: discord.Member, staff: discord.Member) -> None:
        try:
            ids = load_ids(self.bot.settings.ids_file)
            channel = self.bot.get_channel(ids.channel(self.service.cfg.notices_channel))
            if isinstance(channel, discord.abc.Messageable):
                embed = ui.card(notice.title, notice.line, color=ui.VIOLET,
                                fields=[("Approved by", staff.mention, True)],
                                thumbnail=member.display_avatar.url)
                await channel.send(embed=embed, allowed_mentions=ui.NO_PINGS)
        except Exception:
            log.exception("could not post the personnel notice")

    def _reply_fields(self, result: ApplyResult) -> list[ui.Field]:
        fields: list[ui.Field] = []
        tier = " ".join([f"+{r.mention}" for r in result.tier_added] + [f"-{r.mention}" for r in result.tier_removed])
        if tier:
            fields.append(("Division tier", tier, False))
        if result.sync is not None:
            fields += ui.sync_fields(result.sync)
        fields += [("Note", w, False) for w in result.warnings]
        return fields

    async def _run(self, interaction: discord.Interaction, member: discord.Member,
                   mutate: Callable[[Ledger], Ledger], division: str | None,
                   title: str, done: str, notice: Notice | None) -> None:
        staff = self._require_manager(interaction)
        if member.bot:
            raise UserFacingError("Bots have no Personnel File.")
        await interaction.response.defer(thinking=True)  # public: the reply shows in this channel
        try:
            result = await self.service.apply(member, mutate, division)
        except ApprovalError as exc:
            raise UserFacingError(str(exc)) from None
        fields = self._reply_fields(result)
        await ui.respond(interaction, ui.card(title, done, color=ui.INDIGO, actor=staff, fields=fields,
                                              thumbnail=member.display_avatar.url))
        if notice:
            await self._announce(notice, member, staff)
        await self.bot.log_event(title, done, interaction=interaction,
                                 fields=[("Member", member.mention, True), *fields])

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
            title, done = "Posting approved", f"{member.mention} joins **{d.label}**."
            notice = posting_notice(member.mention, d.name)
        else:
            step = cfg.steps[rank].label
            title, done = "Promotion approved", f"{member.mention} is approved as **{step}** in **{d.label}**."
            notice = promotion_notice(member.mention, step, d.name)
        await self._run(interaction, member, lambda led: approve(led, cfg, division, rank, by, time.time()),
                        None if rank == "posting" else division, title, done, notice)

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
        if target:
            done = f"{member.mention} is now **{cfg.steps[target].label}** in **{d.label}**."
        else:
            done = f"{member.mention} no longer holds a leadership rank in **{d.label}**."
        await self._run(interaction, member, lambda led: demote(led, cfg, division, target, by, time.time()),
                        division, "Rank changed", done, notice=None)  # demotions are not announced

    @app_commands.command(name="certify", description="Directorate: record a certification.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(member="Who passed", certification="Which certification")
    async def certify_(self, interaction: discord.Interaction, member: discord.Member, certification: str) -> None:
        cfg = self.service.cfg
        if certification not in cfg.certifications:
            raise UserFacingError("Pick the certification from the list.")
        by = f"discord:{interaction.user.id}"
        label = cfg.certifications[certification].label
        done = f"{member.mention} is certified: **{label}**."
        await self._run(interaction, member, lambda led: certify(led, cfg, certification, by, time.time()),
                        None, "Certification recorded", done, certification_notice(member.mention, label))

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
