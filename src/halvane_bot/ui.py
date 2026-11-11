"""One look for everything the bot says: embeds in the Halvane palette, from light blue to blue-purple.

Every reply, notice and log line is built with `card`, so they share colours, the footer (brand, who ran it,
when) and the no-ping rule. Mentions work in descriptions and field values, never in titles, field names or
footers (Discord shows them raw there).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Sequence

import discord

if TYPE_CHECKING:
    from .sync.service import SyncResult

NO_PINGS = discord.AllowedMentions.none()

# The palette: light blue for information, deepening to blue-purple for staff work and purple for celebrations.
SKY = 0x8FCBF5      # information and status
BLUE = 0x4F8FD6     # done
INDIGO = 0x5D6FE0   # staff actions and the server log
VIOLET = 0x8B6CEF   # personnel notices: promotions, postings, certifications
DUSK = 0x6A5C9E     # refused or not done

FIELD_MAX = 1024
DESCRIPTION_MAX = 4096

Field = tuple[str, str, bool]  # name, value, inline


@dataclass(frozen=True)
class Style:
    brand: str = "Halvane Personnel Office"


STYLE = Style()


def configure(brand: str | None) -> None:
    """Set the footer brand from config/bot.yaml (called once at start-up)."""
    global STYLE
    if brand:
        STYLE = Style(brand=brand)


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def card(title: str | None = None, description: str | None = None, *, color: int = SKY,
         fields: Iterable[Field] = (), actor: discord.abc.User | None = None,
         thumbnail: str | None = None) -> discord.Embed:
    """An embed in the house style. `actor` puts "Run by <name>" and their avatar in the footer."""
    embed = discord.Embed(title=title, description=clip(description, DESCRIPTION_MAX) if description else None,
                          color=color, timestamp=discord.utils.utcnow())
    for name, value, inline in fields:
        embed.add_field(name=name, value=clip(value or "None", FIELD_MAX), inline=inline)
    footer = STYLE.brand + (f" • Run by {actor.display_name}" if actor else "")
    embed.set_footer(text=footer, icon_url=actor.display_avatar.url if actor else None)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    return embed


def problem(text: str, title: str = "Not done") -> discord.Embed:
    return card(title, text, color=DUSK)


async def respond(interaction: discord.Interaction, embed: discord.Embed, *, private: bool = False,
                  **extra: Any) -> None:
    """Reply to a command with one embed, whether or not it was deferred. Never pings.

    After a defer, Discord shows the reply the way the defer was made (public or private) whatever `private` says.
    """
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=private, allowed_mentions=NO_PINGS, **extra)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=private, allowed_mentions=NO_PINGS,
                                                **extra)


def _shown(value: Any) -> str:
    mention = getattr(value, "mention", None)
    return mention if isinstance(mention, str) else str(value)


def command_text(interaction: discord.Interaction) -> str:
    """'/approve member: @Dude division: dispatch rank: team_lead', or the button's label for a click."""
    command = interaction.command
    if command is None:
        return "Verify button" if interaction.type == discord.InteractionType.component else "?"
    options = " ".join(f"{name}: {_shown(value)}" for name, value in interaction.namespace)
    return f"/{command.qualified_name}" + (f" {options}" if options else "")


def where(channel: Any) -> str:
    mention = getattr(channel, "mention", None)
    return mention if isinstance(mention, str) else "Direct message"


def log_fields(interaction: discord.Interaction | None, actor: discord.abc.User | None) -> list[Field]:
    """Who ran it, in which channel, and when: the first fields of every log entry."""
    fields: list[Field] = []
    if interaction is not None:
        fields.append(("Command", command_text(interaction), False))
    if actor is not None:
        fields.append(("Run by", actor.mention, True))
    if interaction is not None:
        fields.append(("Channel", where(interaction.channel), True))
    when = int((interaction.created_at if interaction is not None else discord.utils.utcnow()).timestamp())
    fields.append(("When", f"<t:{when}:f> (<t:{when}:R>)", True))
    return fields


def mentions(roles: Sequence[Any]) -> str:
    return " ".join(r.mention for r in roles)


def sync_fields(result: SyncResult) -> list[Field]:
    """What a role sync changed, as embed fields."""
    fields: list[Field] = []
    if result.added:
        fields.append(("Roles added", mentions(result.added), False))
    if result.removed:
        fields.append(("Roles removed", mentions(result.removed), False))
    if result.in_game:
        fields.append(("In game", ", ".join(result.in_game), False))
    if not (result.added or result.removed or result.in_game) and not result.error:
        fields.append(("Roles", "Already up to date.", False))
    if result.error:
        fields.append(("Not synced", result.error, False))
    if result.in_game_error:
        fields.append(("Game not updated", result.in_game_error, False))
    return fields
