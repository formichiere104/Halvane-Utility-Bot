from __future__ import annotations

import pytest

from halvane_bot.approvals.ledger import (ApprovalError, Ledger, approve, certify, demote, grade, group_changes,
                                          load_approvals, mirror_group, mirror_labels, tier, tier_changes)
from halvane_bot.config import load_settings
from halvane_bot.ids import load_ids


@pytest.fixture(scope="module")
def cfg():
    s = load_settings()
    return load_approvals(s.root / "config" / "approvals.yaml", load_ids(s.ids_file))


def test_repo_config_loads(cfg):
    assert [s.key for s in cfg.step_list()] == ["team_lead", "section_chief", "branch_officer",
                                                 "senior_branch_officer", "controller", "station_chief"]
    assert len(cfg.divisions) == 6 and len(cfg.certifications) == 8
    assert cfg.divisions["packing"].posting_group_role is None


def test_approve_raises_rank_and_refuses_to_go_sideways(cfg):
    led = approve(Ledger(postings=["forklift_operations"]), cfg, "forklift_operations", "team_lead", "discord:1", 100)
    assert led.leadership == {"forklift_operations": "team_lead"} and grade(led, cfg).key == "team_lead"
    led = approve(led, cfg, "forklift_operations", "section_chief", "discord:1", 101)
    assert tier(led, cfg, "forklift_operations") == "nco"
    with pytest.raises(ApprovalError, match="Already Section Chief"):
        approve(led, cfg, "forklift_operations", "team_lead", "discord:1", 102)
    assert [e["step"] for e in led.log] == ["team_lead", "section_chief"]


def test_grade_is_the_highest_step_in_any_division(cfg):
    led = approve(Ledger(postings=["dispatch"]), cfg, "packing", "controller", "d", 1)
    led = approve(led, cfg, "dispatch", "team_lead", "d", 2)
    assert grade(led, cfg).key == "controller"
    assert tier(led, cfg, "packing") == "command" and tier(led, cfg, "dispatch") == "nco"


def test_postings(cfg):
    led = approve(Ledger(), cfg, "dispatch", "posting", "d", 1)
    assert led.postings == ["dispatch"]
    with pytest.raises(ApprovalError, match="open to everyone"):
        approve(led, cfg, "packing", "posting", "d", 2)
    with pytest.raises(ApprovalError, match="already approved"):
        approve(led, cfg, "dispatch", "posting", "d", 3)


def test_demote_lowers_or_clears(cfg):
    led = approve(Ledger(postings=["provost"]), cfg, "provost", "branch_officer", "d", 1)
    led = demote(led, cfg, "provost", "team_lead", "d", 2)
    assert led.leadership == {"provost": "team_lead"}
    with pytest.raises(ApprovalError, match="not below"):
        demote(led, cfg, "provost", "controller", "d", 3)
    led = demote(led, cfg, "provost", None, "d", 4)
    assert led.leadership == {} and grade(led, cfg) is None
    with pytest.raises(ApprovalError, match="No leadership"):
        demote(led, cfg, "provost", None, "d", 5)


def test_certify(cfg):
    led = certify(Ledger(), cfg, "reach_truck_certified", "d", 1)
    assert led.certifications == ["reach_truck_certified"]
    with pytest.raises(ApprovalError, match="already recorded"):
        certify(led, cfg, "reach_truck_certified", "d", 2)


def test_group_changes_keep_one_grade_and_never_drop_postings(cfg):
    tl, sc, ctl = (cfg.steps[k].grade_group_role for k in ("team_lead", "section_chief", "controller"))
    led = approve(Ledger(), cfg, "dispatch", "posting", "d", 1)
    led = approve(led, cfg, "dispatch", "section_chief", "d", 2)
    led = certify(led, cfg, "reach_truck_certified", "d", 3)
    unrelated = 12345
    assign, unassign = group_changes({tl, ctl, unrelated}, led, cfg)
    assert assign == {sc, cfg.divisions["dispatch"].posting_group_role, cfg.certifications["reach_truck_certified"].group_role}
    assert unassign == {tl, ctl}          # other grade roles go; the unrelated role stays


def test_tier_changes_touch_one_division_only(cfg):
    led = approve(Ledger(postings=["dispatch"]), cfg, "dispatch", "branch_officer", "d", 1)
    d = cfg.divisions["dispatch"]
    other = cfg.divisions["packing"].tier_roles["nco"]
    add, remove = tier_changes({d.tier_roles["nco"], other}, led, cfg, "dispatch")
    assert add == {d.tier_roles["officer"]} and remove == {d.tier_roles["nco"]}


def test_ledger_round_trip(cfg):
    led = certify(approve(Ledger(postings=["dispatch"]), cfg, "dispatch", "team_lead", "d", 1),
                  cfg, "registry_certified", "d", 2)
    value = led.to_value(cfg, 99)
    assert value["grade"] == "team_lead" and value["v"] == 1 and value["updatedAt"] == 99
    again = Ledger.from_value(value)
    assert again.leadership == led.leadership and again.certifications == led.certifications
    assert Ledger.from_value(None).leadership == {} and Ledger.from_value("junk").postings == []


def has_null(value) -> bool:
    if value is None:
        return True
    if isinstance(value, dict):
        return any(has_null(v) for v in value.values())
    if isinstance(value, list):
        return any(has_null(v) for v in value)
    return False


def test_cleared_ledger_has_no_nulls(cfg):
    """Roblox answers 400 "Value cannot be null" to a null anywhere in a data store value (/demote to None did)."""
    led = approve(Ledger(postings=["dispatch"]), cfg, "dispatch", "team_lead", "d", 1)
    led = demote(led, cfg, "dispatch", None, "d", 2)
    value = led.to_value(cfg, 3)
    assert "grade" not in value and not has_null(value)
    assert value["log"][-1]["step"] == "none"
    assert Ledger.from_value(value).leadership == {}


def test_division_ranks_need_the_posting_first(cfg):
    with pytest.raises(ApprovalError, match="needs its posting first"):
        approve(Ledger(), cfg, "dispatch", "team_lead", "d", 1)
    led = approve(approve(Ledger(), cfg, "dispatch", "posting", "d", 1), cfg, "dispatch", "team_lead", "d", 2)
    assert led.leadership == {"dispatch": "team_lead"}
    # Packing and Facilities are open to everyone: no posting to wait for.
    assert approve(Ledger(), cfg, "facilities", "team_lead", "d", 3).leadership == {"facilities": "team_lead"}


def test_mirror_follows_the_group_for_certifications_and_postings(cfg):
    reach = cfg.certifications["reach_truck_certified"].group_role
    hazmat = cfg.certifications["hazardous_goods_certified"].group_role
    dispatch = cfg.divisions["dispatch"].posting_group_role
    grade_role = cfg.steps["controller"].grade_group_role
    old = Ledger(leadership={"packing": "team_lead"}, postings=["provost"],
                 certifications=["registry_certified", "retired_cert"])
    new = mirror_group(old, {reach, hazmat, dispatch, grade_role, 999}, cfg, 50)
    assert new is not None
    # Given in the group: added. Not in the group any more: removed. Unknown to the config: kept.
    assert new.certifications == ["hazardous_goods_certified", "reach_truck_certified", "retired_cert"]
    assert new.postings == ["dispatch"]
    assert new.leadership == {"packing": "team_lead"}  # a grade role in the group never sets leadership
    assert new.log[-1] == {"at": 50, "by": "roblox-group", "action": "group",
                           "added": ["certification:hazardous_goods_certified", "certification:reach_truck_certified",
                                     "posting:dispatch"],
                           "removed": ["certification:registry_certified", "posting:provost"]}
    assert mirror_labels(old, new, cfg) == ["+Hazardous Goods Certified", "+Reach Truck Certified",
                                            "-Registry Certified", "+Dispatch posting", "-Provost posting"]
    assert mirror_group(new, {reach, hazmat, dispatch}, cfg, 51) is None  # already matches: nothing to write
    assert mirror_group(Ledger(), {999}, cfg, 52) is None               # nobody gets an empty entry
