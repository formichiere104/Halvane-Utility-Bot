"""Shared helpers for command cogs."""
from __future__ import annotations

import logging

import discord
from discord import app_commands

from ..ids import load_ids

log = logging.getLogger(__name__)


class UserFacingError(app_commands.AppCommandError):
    """Shown to the person who ran the command, as is."""


def has_any_role_key(member: discord.Member, keys: tuple[str, ...], ids_file) -> bool:
    """Server owner, or holds one of the roles named by key in ids.yaml."""
    if member.guild is not None and member.id == member.guild.owner_id:
        return True
    ids = load_ids(ids_file)
    allowed = {ids.roles[k] for k in keys if k in ids.roles}
    return bool(allowed & {r.id for r in member.roles})


async def report_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    """Reply privately with a clear message; log anything unexpected."""
    if isinstance(error, app_commands.CommandInvokeError):
        error = error.original  # errors raised inside a command callback arrive wrapped
    if isinstance(error, UserFacingError):
        text = str(error)
    elif isinstance(error, app_commands.CommandOnCooldown):
        text = f"Please wait {error.retry_after:.0f} seconds before using this again."
    else:
        log.exception("command failed", exc_info=error)
        text = "Something went wrong; the details are in the bot's log."
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)
