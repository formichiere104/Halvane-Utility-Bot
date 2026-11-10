"""The real config and embed files in this repo must always render (warnings such as empty links are allowed)."""
from __future__ import annotations

from halvane_bot.check import run_check
from halvane_bot.config import load_settings
from halvane_bot.embeds.loader import load_all
from halvane_bot.embeds.render import render_set
from halvane_bot.ids import load_ids


def test_every_embed_file_renders():
    settings = load_settings()
    ids = load_ids(settings.ids_file)
    sets = load_all(settings.embeds_dir)
    assert sets, "no embed files found"
    for es in sets.values():
        render_set(es, ids, settings.links)


def test_check_command_passes(capsys):
    assert run_check(load_settings()) == 0


def test_special_divisions_are_never_explained():
    """#ranks-explained may list Dept. 21/22 only as [REDACTED] (decision with the user, 10 Nov 2026)."""
    settings = load_settings()
    es = load_all(settings.embeds_dir)["ranks-explained"]
    text = "\n".join(e.get("description", "") for m in es.messages for e in m.embeds)
    for key in ("dept_21", "dept_22"):
        line = next(ln for ln in text.splitlines() if f"{{role:{key}}} –" in ln)
        assert line.rstrip().endswith("[REDACTED]"), line
