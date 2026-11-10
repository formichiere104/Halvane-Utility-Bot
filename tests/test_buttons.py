"""Clicks on buttons the bot posts must reach the registered handler.

discord.py routes a click to the view stored for that message before the persistent view, and posting a
message with a view stores it. A plain Button there swallowed every Verify click (10 Nov 2026).
"""
from __future__ import annotations

import asyncio
import types

import discord
from discord.ui.view import ViewStore

from halvane_bot.embeds.publisher import message_view, persistent_view

CID = "halvane:verify"
MSG = {"buttons": [{"custom_id": CID, "label": "Verify", "style": "primary"}]}


def click(store_views) -> int:
    """Register views the way the bot does, simulate one click on message 123, return how often the handler ran."""
    async def scenario() -> int:
        calls: list[object] = []

        async def handler(interaction) -> None:
            calls.append(interaction)

        store = ViewStore(None)  # the router discord.py uses; no connection needed for routing
        for view, message_id in store_views():
            store.add_view(view, message_id)
        interaction = types.SimpleNamespace(
            message=types.SimpleNamespace(id=123),
            data={"custom_id": CID, "component_type": 2},
            client=types.SimpleNamespace(action_handlers={CID: handler}),
        )
        store.dispatch_view(2, CID, interaction)
        await asyncio.sleep(0.05)
        return len(calls)

    return asyncio.run(scenario())


def test_published_button_reaches_the_handler():
    # start-up: persistent view; publish: the message's own view is stored under the message id
    assert click(lambda: [(persistent_view([CID]), None), (message_view(MSG), 123)]) == 1


def test_after_a_restart_the_persistent_view_handles_it():
    assert click(lambda: [(persistent_view([CID]), None)]) == 1


def test_a_plain_button_would_swallow_the_click():
    """The bug: a plain Button stored for the message wins over the persistent view and does nothing."""
    def views():
        plain = discord.ui.View(timeout=None)
        plain.add_item(discord.ui.Button(label="Verify", custom_id=CID))
        return [(persistent_view([CID]), None), (plain, 123)]
    assert click(views) == 0
