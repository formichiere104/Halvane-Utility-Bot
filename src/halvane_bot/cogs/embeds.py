"""/embeds list | preview | publish | adopt.

Files are read from disk on every command, so `git pull` on the host is enough to pick up new text.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..embeds.loader import EmbedSet, EmbedSpecError, load_all
from ..embeds.publisher import NO_PINGS, PublishError, adopt, publish
from ..embeds.render import Rendered, RenderError, render_set
from ..ids import IdRegistry, load_ids

if TYPE_CHECKING:
    from ..bot import HalvaneBot

log = logging.getLogger(__name__)


class UserFacingError(app_commands.AppCommandError):
    """Shown to the person who ran the command, as is."""


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class EmbedsCog(commands.GroupCog, group_name="embeds",
                group_description="Manage the server's information messages."):
    def __init__(self, bot: HalvaneBot) -> None:
        self.bot = bot
        super().__init__()

    # ---- helpers ---------------------------------------------------------------------------------

    def _load(self) -> tuple[IdRegistry, dict[str, EmbedSet]]:
        s = self.bot.settings
        try:
            return load_ids(s.ids_file), load_all(s.embeds_dir)
        except (EmbedSpecError, OSError) as exc:
            raise UserFacingError(f"Couldn't read the embed files: {exc}") from None

    def _render(self, key: str) -> Rendered:
        ids, sets = self._load()
        if key not in sets:
            raise UserFacingError(f"No embed file called `{key}`. Use `/embeds list` to see them.")
        try:
            return render_set(sets[key], ids, self.bot.settings.links)
        except RenderError as exc:
            raise UserFacingError(f"`{key}` can't be rendered: {exc}") from None

    async def _key_choices(self, current: str) -> list[app_commands.Choice[str]]:
        try:
            keys = sorted(load_all(self.bot.settings.embeds_dir))
        except (EmbedSpecError, OSError):
            return []
        return [app_commands.Choice(name=k, value=k) for k in keys if current.lower() in k][:25]

    async def _log(self, text: str) -> None:
        key = self.bot.settings.log_channel
        if not key:
            return
        try:
            channel = self.bot.get_channel(load_ids(self.bot.settings.ids_file).channel(key))
            if isinstance(channel, discord.abc.Messageable):
                await channel.send(text, allowed_mentions=NO_PINGS)
        except Exception:  # logging must never break the command
            log.exception("could not write to the log channel")

    # ---- access ------------------------------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        guild = interaction.guild
        if guild is None or not isinstance(member, discord.Member):
            raise UserFacingError("Use this in the server.")
        if member.id == guild.owner_id:
            return True
        ids = load_ids(self.bot.settings.ids_file)
        allowed = {ids.roles[k] for k in self.bot.settings.embed_managers if k in ids.roles}
        if allowed & {r.id for r in member.roles}:
            return True
        raise UserFacingError("Only the Directorate can manage these messages.")

    async def cog_app_command_error(self, interaction: discord.Interaction,
                                    error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandInvokeError):
            error = error.original  # errors raised inside a command callback arrive wrapped
        if isinstance(error, UserFacingError):
            text = str(error)
        else:
            log.exception("embeds command failed", exc_info=error)
            text = "Something went wrong; the details are in the bot's log."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    # ---- commands ----------------------------------------------------------------------------------

    @app_commands.command(name="list", description="Show every managed message and whether it is up to date.")
    async def list_(self, interaction: discord.Interaction) -> None:
        ids, sets = self._load()
        stored = await self.bot.store.all()
        lines = []
        for key in sorted(set(sets) | set(stored)):
            es, st = sets.get(key), stored.get(key)
            if es is None:
                status = "file removed; the posted message stays until deleted by hand"
                where = f"<#{st.channel_id}>"
            else:
                where = f"<#{ids.channels.get(es.channel, 0)}>" if es.channel in ids.channels else f"`{es.channel}`?"
                try:
                    rendered = render_set(es, ids, self.bot.settings.links)
                    if st is None:
                        status = "not published"
                    elif st.content_hash == "":
                        status = "adopted, not published yet"
                    elif st.content_hash == rendered.content_hash:
                        status = "up to date"
                    else:
                        status = "changed since last publish"
                    if rendered.warnings:
                        status += f" · {len(rendered.warnings)} warning(s)"
                except RenderError as exc:
                    status = f"error: {exc}"
            note = f" · {es.note}" if es and es.note else ""
            lines.append(f"**{key}** → {where} · {status}{note}")
        await interaction.response.send_message("\n".join(lines) or "No embed files.", ephemeral=True,
                                                allowed_mentions=NO_PINGS)

    @app_commands.command(name="preview", description="Show a message privately, exactly as it would be posted.")
    @app_commands.describe(key="Embed file name")
    async def preview(self, interaction: discord.Interaction, key: str) -> None:
        rendered = self._render(key)
        total = len(rendered.messages)
        for i, msg in enumerate(rendered.messages):
            header = f"**Preview · {key} · message {i + 1}/{total}** (not posted)"
            if i == 0 and rendered.warnings:
                header += "\n" + "\n".join(f"⚠ {w}" for w in rendered.warnings)
            content = header + ("\n\n" + msg["content"] if msg["content"] else "")
            embeds = [discord.Embed.from_dict(e) for e in msg["embeds"]]
            send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
            await send(content, embeds=embeds, ephemeral=True, allowed_mentions=NO_PINGS)

    @app_commands.command(name="publish", description="Post the message, or edit the one already posted.")
    @app_commands.describe(key="Embed file name",
                           prune="Delete extra old messages if the file now has fewer messages")
    async def publish_(self, interaction: discord.Interaction, key: str, prune: bool = False) -> None:
        rendered = self._render(key)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            report = await publish(self.bot, self.bot.store, rendered, prune=prune)
        except PublishError as exc:
            raise UserFacingError(f"`{key}` was not published: {exc}") from None
        await interaction.followup.send(f"Published **{key}** in <#{report.channel_id}>: {report.summary()}.",
                                        ephemeral=True)
        await self._log(f"`/embeds publish {key}` by {interaction.user.mention}: {report.summary()}.")

    @app_commands.command(name="adopt", description="Take over messages this bot already posted.")
    @app_commands.describe(key="Embed file name", message_ids="Message ids in display order, separated by commas")
    async def adopt_(self, interaction: discord.Interaction, key: str, message_ids: str) -> None:
        rendered = self._render(key)
        try:
            ids = [int(x) for x in message_ids.replace(" ", "").split(",") if x]
        except ValueError:
            raise UserFacingError("Message ids must be numbers separated by commas.") from None
        if not ids:
            raise UserFacingError("Give at least one message id.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await adopt(self.bot, self.bot.store, key, rendered.channel_id, ids)
        except PublishError as exc:
            raise UserFacingError(f"Couldn't adopt: {exc}") from None
        await interaction.followup.send(
            f"Adopted {len(ids)} message(s) for **{key}**. `/embeds publish {key}` now edits them.", ephemeral=True)
        await self._log(f"`/embeds adopt {key}` by {interaction.user.mention}: {len(ids)} message(s).")

    # ---- autocomplete for every command's `key` -------------------------------------------------------

    @preview.autocomplete("key")
    async def _ac_preview(self, interaction: discord.Interaction, current: str):
        return await self._key_choices(current)

    @publish_.autocomplete("key")
    async def _ac_publish(self, interaction: discord.Interaction, current: str):
        return await self._key_choices(current)

    @adopt_.autocomplete("key")
    async def _ac_adopt(self, interaction: discord.Interaction, current: str):
        return await self._key_choices(current)
