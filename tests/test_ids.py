from __future__ import annotations

from halvane_bot.ids import assign_keys, load_ids, render_ids_yaml, slug


def test_slug_handles_separators_labels_and_dots():
    assert slug("Packing · NCO") == "packing_nco"
    assert slug("[──── GRADE ────]") == "sep_grade"
    assert slug("— High Ranks") == "label_high_ranks"
    assert slug("Dept. 21") == "dept_21"
    assert slug("Ch. 9 · HR") == "ch_9_hr"
    assert slug("ranks-explained") == "ranks_explained"


def test_text_channel_gets_plain_key_voice_gets_suffix():
    items = [(2, "Directorate", "voice"), (1, "directorate", "text"), (3, "DIRECTORATE", "category")]
    keys = assign_keys(items, existing={})
    assert keys["directorate"] == 1
    assert keys["directorate_voice"] == 2
    assert keys["cat_directorate"] == 3


def test_existing_keys_survive_a_rename():
    keys = assign_keys([(10, "Station Chief (renamed)", "role")], existing={"station_chief": 10})
    assert keys == {"station_chief": 10}


def test_ids_yaml_round_trip(tmp_path):
    path = tmp_path / "ids.yaml"
    path.write_text(render_ids_yaml(5, {"a": 1}, {"rules": 2}, {1: "A"}, {2: "#rules"}), encoding="utf-8")
    reg = load_ids(path)
    assert reg.guild_id == 5 and reg.role("a") == 1 and reg.channel("rules") == 2
