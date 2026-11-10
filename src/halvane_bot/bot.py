"""The bot process: one Discord client, commands registered to the Halvane guild only."""
from __future__ import annotations

import logging
import os
from typing import Awaitable, Callable

import discord
from discord.ext import commands

from .config import Settings
from .embeds.actions import ACTIONS
from .embeds.publisher import NO_PINGS, persistent_view
from .ids import load_ids
from .roblox.opencloud import OpenCloud
from .store import Database, EmbedStore, LinkStore
from .sync.service import SyncService

log = logging.getLogger(__name__)


class HalvaneBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.none()
        intents.guilds = True   # channel and role cache
        intents.members = True  # privileged: member joins and the member cache for role sync
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
            help_command=None,
        )
        self.settings = settings
        self.db = Database(settings.database)
        self.store = EmbedStore(self.db)
        self.links = LinkStore(self.db)
        self.cloud: OpenCloud | None = None
        self.role_sync: SyncService | None = None
        # custom_id -> coroutine run when a posted button is clicked (cogs register theirs in cog_load)
        self.action_handlers: dict[str, Callable[[discord.Interaction], Awaitable[None]]] = {}

    async def setup_hook(self) -> None:
        from .cogs.embeds import EmbedsCog
        from .cogs.verify import VerifyCog

        await self.db.open()
        s = self.settings
        key = os.environ.get("ROBLOX_API_KEY", "").strip()
        if key and s.group_id and s.universe_id:
            self.cloud = OpenCloud(key, s.universe_id, s.group_id, s.link_store)
            await self.cloud.start()
        else:
            log.warning("ROBLOX_API_KEY or roblox ids missing: /verify is off, linked members are not synced")
        self.role_sync = SyncService(self, self.links, self.cloud)

        guild = discord.Object(id=s.guild_id)
        await self.add_cog(EmbedsCog(self), guild=guild)
        await self.add_cog(VerifyCog(self), guild=guild)
        # Buttons posted before a restart keep working: one persistent view covers every registered action.
        self.add_view(persistent_view(list(ACTIONS.values())))
        if s.sync_commands:
            synced = await self.tree.sync(guild=guild)
            log.info("registered %d command(s) in guild %s", len(synced), s.guild_id)

    async def on_ready(self) -> None:
        log.info("logged in as %s (%s)", self.user, getattr(self.user, "id", "?"))

    async def log_line(self, text: str) -> None:
        """Write one line to the configured log channel. Never raises."""
        key = self.settings.log_channel
        if not key:
            return
        try:
            channel = self.get_channel(load_ids(self.settings.ids_file).channel(key))
            if isinstance(channel, discord.abc.Messageable):
                await channel.send(text, allowed_mentions=NO_PINGS)
        except Exception:
            log.exception("could not write to the log channel")

    async def close(self) -> None:
        if self.cloud is not None:
            await self.cloud.close()
        await self.db.close()
        await super().close()
