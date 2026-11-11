"""The house style: embeds, notices and log entries."""
from __future__ import annotations

import datetime as dt
import types

import discord

from halvane_bot import ui
from halvane_bot.approvals.ledger import load_approvals
from halvane_bot.cogs.approvals import certification_notice, posting_notice, promotion_notice
from halvane_bot.config import load_settings
from halvane_bot.ids import load_ids
from halvane_bot.sync.service import SyncResult


class Person:
    id = 7
    display_name = "Dude"
    mention = "<@7>"
    display_avatar = types.SimpleNamespace(url="https://cdn.example/avatar.png")


def test_palette_stays_blue_to_purple():
    for color in (ui.SKY, ui.BLUE, ui.INDIGO, ui.VIOLET, ui.DUSK):
        r, g, b = color >> 16, (color >> 8) & 0xFF, color & 0xFF
        assert b >= r and b >= g  # blue is always the strongest channel: no reds, greens or greys


def test_card_footer_names_brand_and_who_ran_it():
    embed = ui.card("Roles updated", "text", actor=Person(), fields=[("Added", "", True)])
    assert embed.footer.text == "Halvane Personnel Office • Run by Dude"
    assert embed.footer.icon_url == "https://cdn.example/avatar.png"
    assert embed.timestamp is not None and embed.fields[0].value == "None"  # empty values never break Discord
    assert ui.card("x", "y" * 5000).description.endswith("…")


def test_notices_are_cheerful():
    assert promotion_notice("<@7>", "Team Lead", "Dispatch").line == "<@7> has been promoted to **Team Lead** of **Dispatch**!"
    assert certification_notice("<@7>", "Registry Certified").line == "<@7> is now **Registry Certified**!"
    assert certification_notice("<@7>", "Fire and Spill Response").line == \
        "<@7> is now certified in **Fire and Spill Response**!"
    assert posting_notice("<@7>", "Dispatch").line.startswith("<@7> has joined **Dispatch**!")


def test_training_is_called_instructors_in_notices():
    s = load_settings()
    cfg = load_approvals(s.root / "config" / "approvals.yaml", load_ids(s.ids_file))
    assert cfg.divisions["training"].name == "Instructors" and cfg.divisions["dispatch"].name == "Dispatch"


def test_log_fields_say_what_who_where_and_when():
    when = dt.datetime(2026, 11, 11, 21, 37, tzinfo=dt.timezone.utc)
    interaction = types.SimpleNamespace(
        command=types.SimpleNamespace(qualified_name="approve"),
        namespace=[("member", Person()), ("division", "dispatch"), ("rank", "team_lead")],
        channel=types.SimpleNamespace(mention="<#55>"), created_at=when,
        type=discord.InteractionType.application_command)
    fields = ui.log_fields(interaction, Person())
    assert fields[0] == ("Command", "/approve member: <@7> division: dispatch rank: team_lead", False)
    assert fields[1] == ("Run by", "<@7>", True) and fields[2] == ("Channel", "<#55>", True)
    stamp = int(when.timestamp())
    assert fields[3] == ("When", f"<t:{stamp}:f> (<t:{stamp}:R>)", True)


def test_sync_fields():
    role = types.SimpleNamespace(mention="<@&1>")
    assert ui.sync_fields(SyncResult(link=None)) == [("Roles", "Already up to date.", False)]
    fields = ui.sync_fields(SyncResult(link=None, added=[role], in_game=["+Reach Truck Certified"]))
    assert ("Roles added", "<@&1>", False) in fields and ("In game", "+Reach Truck Certified", False) in fields
