from __future__ import annotations

import pytest

from halvane_bot.config import load_settings
from halvane_bot.ids import IdRegistry, load_ids
from halvane_bot.sync.role_map import RoleMapError, load_role_map

IDS = IdRegistry(guild_id=1, channels={}, roles={
    "verified": 1, "unverified": 2, "staff_register": 3, "team_lead": 4, "dispatch": 5, "director": 6,
})


def write(tmp_path, text):
    path = tmp_path / "role_map.yaml"
    path.write_text(text, encoding="utf-8")
    return path


BASE = """
group_id: 9
linked: [verified]
not_linked: [unverified]
in_group: [staff_register]
group_roles:
  "100": {name: Team Lead, discord: [team_lead]}
  "200": {name: Dispatch, discord: [dispatch]}
protected: [director]
"""


def test_desired_roles(tmp_path):
    rm = load_role_map(write(tmp_path, BASE), IDS)
    assert rm.desired(linked=False, group_role_ids=None) == {2}
    assert rm.desired(linked=True, group_role_ids=None) == {1}               # linked, not in the group
    assert rm.desired(linked=True, group_role_ids=[]) == {1, 3}              # in the group, base role only
    assert rm.desired(linked=True, group_role_ids=[100, 200, 999]) == {1, 3, 4, 5}   # unknown 999 ignored
    assert rm.managed == {1, 2, 3, 4, 5} and 6 not in rm.managed


def test_protected_roles_can_never_be_mapped(tmp_path):
    with pytest.raises(RoleMapError, match="protected"):
        load_role_map(write(tmp_path, BASE.replace("[dispatch]", "[director]")), IDS)


def test_unknown_key_fails(tmp_path):
    with pytest.raises(RoleMapError, match="unknown role key"):
        load_role_map(write(tmp_path, BASE.replace("[dispatch]", "[nope]")), IDS)


def test_repo_role_map_is_valid():
    s = load_settings()
    rm = load_role_map(s.root / "config" / "role_map.yaml", load_ids(s.ids_file))
    assert rm.group_id == 467115756
    assert len(rm.group_roles) == 20
