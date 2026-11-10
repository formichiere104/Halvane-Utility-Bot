from __future__ import annotations

from pathlib import Path

import pytest

from halvane_bot.embeds.loader import EmbedSet, MessageSpec
from halvane_bot.embeds.render import RenderError, fill, parse_color, render_set


def make_set(*embeds, content=None, channel="rules") -> EmbedSet:
    return EmbedSet(key="t", channel=channel, note="", source=Path("t.yaml"),
                    messages=(MessageSpec(content=content, embeds=tuple(embeds)),))


def test_fill_replaces_role_channel_and_link(ids, links):
    warnings: list[str] = []
    out = fill("{role:station_chief} in {channel:events}: {link:roblox_group}", ids, links, warnings)
    assert out == "<@&100> in <#201>: https://example.com/group"
    assert warnings == []


def test_empty_link_is_a_warning_not_an_error(ids, links):
    warnings: list[str] = []
    assert "not set" in fill("{link:game}", ids, links, warnings)
    assert warnings and "game" in warnings[0]


def test_unknown_keys_fail(ids, links):
    with pytest.raises(RenderError):
        render_set(make_set({"description": "{role:nope}"}), ids, links)
    with pytest.raises(RenderError):
        render_set(make_set({"description": "x"}, channel="nope"), ids, links)
    with pytest.raises(RenderError):
        fill("{link:nope}", ids, links, [])


def test_colour_parsing():
    assert parse_color("#4F8FD6") == 0x4F8FD6
    assert parse_color("4f8fd6") == 0x4F8FD6
    with pytest.raises(RenderError):
        parse_color("blue")


def test_mentions_in_titles_or_field_names_are_rejected(ids, links):
    with pytest.raises(RenderError, match="only render"):
        render_set(make_set({"title": "{role:team_lead}", "description": "x"}), ids, links)
    with pytest.raises(RenderError, match="only render"):
        render_set(make_set({"fields": [{"name": "{role:team_lead}", "value": "x"}]}), ids, links)
    ok = render_set(make_set({"fields": [{"name": "Team Lead", "value": "{role:team_lead}"}]}), ids, links)
    assert ok.messages[0]["embeds"][0]["fields"][0]["value"] == "<@&101>"


def test_over_the_limit_is_rejected(ids, links):
    with pytest.raises(RenderError, match="description"):
        render_set(make_set({"description": "x" * 4097}), ids, links)
    with pytest.raises(RenderError, match="6000"):
        render_set(make_set(*[{"description": "x" * 1500} for _ in range(5)]), ids, links)


def test_hash_changes_only_when_output_changes(ids, links):
    a = render_set(make_set({"description": "same"}), ids, links)
    b = render_set(make_set({"description": "same"}), ids, links)
    c = render_set(make_set({"description": "different"}), ids, links)
    assert a.content_hash == b.content_hash != c.content_hash


def test_buttons_render_and_leave_old_hashes_alone(ids, links):
    plain = render_set(make_set({"description": "x"}), ids, links)
    assert "buttons" not in plain.messages[0]  # messages published before buttons existed keep their hash
    es = EmbedSet(key="t", channel="rules", note="", source=Path("t.yaml"), messages=(
        MessageSpec(content=None, embeds=({"description": "x"},),
                    buttons=({"action": "verify", "label": "Verify", "style": "primary"},)),))
    with_button = render_set(es, ids, links)
    assert with_button.messages[0]["buttons"] == [{"custom_id": "halvane:verify", "label": "Verify", "style": "primary"}]
    assert with_button.content_hash != plain.content_hash


def test_footer_and_trailing_newlines(ids, links):
    r = render_set(make_set({"description": "text\n\n", "footer": "Motto", "color": "#000001"}), ids, links)
    embed = r.messages[0]["embeds"][0]
    assert embed == {"description": "text", "footer": {"text": "Motto"}, "color": 1}
