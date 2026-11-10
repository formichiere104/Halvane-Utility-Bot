"""Post or edit the managed messages on Discord, and remember their ids."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import discord

from ..store import EmbedStore
from .render import Rendered

NO_PINGS = discord.AllowedMentions.none()


class PublishError(Exception):
    """Publishing stopped; nothing after the failing message was changed."""


@dataclass
class PublishReport:
    key: str
    channel_id: int
    edited: int = 0
    created: int = 0
    recreated: int = 0          # stored message was gone, a new one was posted (lands at the bottom)
    pruned: int = 0
    leftover: list[int] = field(default_factory=list)     # extra old messages kept (publish with prune to delete)
    left_in_old_channel: list[int] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.edited} edited", f"{self.created} posted"]
        if self.recreated:
            parts.append(f"{self.recreated} re-posted at the bottom (the old message was gone)")
        if self.pruned:
            parts.append(f"{self.pruned} extra deleted")
        if self.leftover:
            parts.append(f"{len(self.leftover)} extra old message(s) kept; publish with prune to delete")
        if self.left_in_old_channel:
            parts.append(f"{len(self.left_in_old_channel)} old message(s) left in the previous channel")
        return ", ".join(parts)


async def _channel(bot: discord.Client, channel_id: int) -> discord.abc.Messageable:
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden) as exc:
            raise PublishError(f"can't open channel {channel_id}: {exc.text}") from None
    if not isinstance(channel, discord.abc.Messageable):
        raise PublishError(f"channel {channel_id} can't hold messages")
    return channel


def _embeds(message: dict) -> list[discord.Embed]:
    return [discord.Embed.from_dict(e) for e in message["embeds"]]


STYLE = {"primary": discord.ButtonStyle.primary, "secondary": discord.ButtonStyle.secondary,
         "success": discord.ButtonStyle.success, "danger": discord.ButtonStyle.danger}

log = logging.getLogger(__name__)


class ActionButton(discord.ui.Button):
    """A button whose click runs the handler the bot registered for its custom_id (bot.action_handlers).

    discord.py routes a click to the view it stored for that message before any persistent view, and posting a
    message with a view stores that view. So every button the bot posts must carry the real behaviour itself.
    """

    async def callback(self, interaction: discord.Interaction) -> None:
        handlers = getattr(interaction.client, "action_handlers", {})
        handler = handlers.get(self.custom_id)
        if handler is None:
            await interaction.response.send_message("This button is not available right now.", ephemeral=True)
            return
        try:
            await handler(interaction)
        except Exception:
            log.exception("button %s failed", self.custom_id)
            if not interaction.response.is_done():
                await interaction.response.send_message("Something went wrong; the details are in the bot's log.",
                                                        ephemeral=True)


def message_view(message: dict) -> discord.ui.View | None:
    """Buttons for a rendered message, or None if it has none."""
    buttons = message.get("buttons") or []
    if not buttons:
        return None
    view = discord.ui.View(timeout=None)
    for b in buttons:
        view.add_item(ActionButton(label=b["label"], style=STYLE[b["style"]], custom_id=b["custom_id"]))
    return view


def persistent_view(custom_ids: list[str]) -> discord.ui.View:
    """Registered once at start-up so buttons posted before a restart keep working."""
    view = discord.ui.View(timeout=None)
    for cid in custom_ids:
        view.add_item(ActionButton(label="action", custom_id=cid))
    return view


async def publish(bot: discord.Client, store: EmbedStore, rendered: Rendered, prune: bool = False) -> PublishReport:
    if rendered.warnings:
        raise PublishError("fix these first: " + "; ".join(rendered.warnings))
    channel = await _channel(bot, rendered.channel_id)
    report = PublishReport(key=rendered.key, channel_id=rendered.channel_id)

    stored = await store.get(rendered.key)
    old_ids: list[int] = []
    if stored:
        if stored.channel_id == rendered.channel_id:
            old_ids = list(stored.message_ids)
        else:
            report.left_in_old_channel = list(stored.message_ids)

    new_ids: list[int] = []
    for i, msg in enumerate(rendered.messages):
        if i < len(old_ids):
            try:
                await channel.get_partial_message(old_ids[i]).edit(
                    content=msg["content"], embeds=_embeds(msg), view=message_view(msg), allowed_mentions=NO_PINGS)
                report.edited += 1
                new_ids.append(old_ids[i])
                continue
            except discord.NotFound:
                report.recreated += 1
            except discord.Forbidden as exc:
                await store.save(rendered.key, rendered.channel_id, new_ids + old_ids[i:], stored.content_hash)
                raise PublishError(
                    f"can't edit message {old_ids[i]}: {exc.text}. A message can only be edited by the bot "
                    "that posted it; delete it by hand and publish again.") from None
        view = message_view(msg)
        if view is None:
            sent = await channel.send(content=msg["content"], embeds=_embeds(msg), allowed_mentions=NO_PINGS)
        else:
            sent = await channel.send(content=msg["content"], embeds=_embeds(msg), view=view, allowed_mentions=NO_PINGS)
        report.created += 1 if i >= len(old_ids) else 0
        new_ids.append(sent.id)

    extra = old_ids[len(rendered.messages):]
    if extra and prune:
        for mid in extra:
            try:
                await channel.get_partial_message(mid).delete()
            except discord.NotFound:
                pass
            report.pruned += 1
        extra = []
    report.leftover = extra
    await store.save(rendered.key, rendered.channel_id, new_ids + extra, rendered.content_hash)
    return report


async def adopt(bot: discord.Client, store: EmbedStore, key: str, channel_id: int, message_ids: list[int]) -> None:
    """Take over messages this bot already posted (for example before the bot existed as a service)."""
    channel = await _channel(bot, channel_id)
    for mid in message_ids:
        try:
            message = await channel.fetch_message(mid)
        except discord.NotFound:
            raise PublishError(f"message {mid} is not in that channel") from None
        if bot.user is None or message.author.id != bot.user.id:
            raise PublishError(f"message {mid} was posted by {message.author}, not by this bot, so it can't be "
                               "edited. Delete it by hand and publish instead.")
    await store.save(key, channel_id, message_ids, content_hash="")
