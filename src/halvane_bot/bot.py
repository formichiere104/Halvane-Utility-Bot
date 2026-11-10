"""The bot process: one Discord client, commands registered to the Halvane guild only."""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from .config import Settings
from .store import EmbedStore

log = logging.getLogger(__name__)


class HalvaneBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.none()
        intents.guilds = True  # channel and role cache; no privileged intents needed for phase 1
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
            help_command=None,
        )
        self.settings = settings
        self.store = EmbedStore(settings.database)

    async def setup_hook(self) -> None:
        from .cogs.embeds import EmbedsCog

        await self.store.open()
        guild = discord.Object(id=self.settings.guild_id)
        await self.add_cog(EmbedsCog(self), guild=guild)
        if self.settings.sync_commands:
            synced = await self.tree.sync(guild=guild)
            log.info("registered %d command group(s) in guild %s", len(synced), self.settings.guild_id)

    async def on_ready(self) -> None:
        log.info("logged in as %s (%s)", self.user, getattr(self.user, "id", "?"))

    async def close(self) -> None:
        await self.store.close()
        await super().close()
