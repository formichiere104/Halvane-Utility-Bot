from __future__ import annotations

import pytest

from halvane_bot.embeds.loader import EmbedSpecError, load_all, load_embed_set


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_key_is_the_file_name(tmp_path):
    es = load_embed_set(write(tmp_path, "rules.yaml", "channel: rules\nmessages:\n  - embeds:\n      - title: Hi\n"))
    assert es.key == "rules" and es.channel == "rules" and es.messages[0].embeds[0]["title"] == "Hi"


@pytest.mark.parametrize("text, problem", [
    ("messages:\n  - content: hi\n", "channel"),
    ("channel: rules\n", "messages"),
    ("channel: rules\nmessages:\n  - {}\n", "content or embeds"),
    ("channel: rules\nmessages:\n  - embeds:\n      - colour: red\n        title: x\n", "unknown key"),
    ("channel: rules\nmessages:\n  - embeds:\n      - color: '#000000'\n", "at least"),
    ("channel: rules\nmessages:\n  - embeds:\n      - fields: [{name: a}]\n", "name and value"),
    ("channel: rules\nextra: 1\nmessages:\n  - content: hi\n", "unknown key"),
])
def test_bad_files_are_rejected(tmp_path, text, problem):
    with pytest.raises(EmbedSpecError, match=problem):
        load_embed_set(write(tmp_path, "bad.yaml", text))


def test_buttons(tmp_path):
    ok = load_embed_set(write(tmp_path, "v.yaml",
                              "channel: verify\nmessages:\n  - content: hi\n    buttons:\n"
                              "      - {action: verify, label: Verify}\n"))
    assert ok.messages[0].buttons == ({"action": "verify", "label": "Verify", "style": "primary"},)
    with pytest.raises(EmbedSpecError, match="unknown action"):
        load_embed_set(write(tmp_path, "b.yaml",
                             "channel: verify\nmessages:\n  - content: hi\n    buttons: [{action: nuke, label: X}]\n"))
    six = ", ".join(["{action: verify, label: V}"] * 6)
    with pytest.raises(EmbedSpecError, match="at most"):
        load_embed_set(write(tmp_path, "c.yaml", f"channel: verify\nmessages:\n  - content: hi\n    buttons: [{six}]\n"))


def test_drafts_are_skipped(tmp_path):
    write(tmp_path, "_draft.yaml", "not: valid")
    write(tmp_path, "rules.yaml", "channel: rules\nmessages:\n  - content: hi\n")
    assert list(load_all(tmp_path)) == ["rules"]
