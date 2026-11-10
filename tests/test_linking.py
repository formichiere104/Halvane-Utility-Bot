from __future__ import annotations

from halvane_bot.roblox.opencloud import entry_value, parse_member_roles
from halvane_bot.sync.linking import CLOCK_SKEW, PendingCode, filed_user, new_code
from halvane_bot.sync.role_map import changes

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def test_codes_use_the_shared_alphabet_and_skip_taken_ones():
    codes = {new_code(ALPHABET, 6) for _ in range(500)}
    assert all(len(c) == 6 and set(c) <= set(ALPHABET) for c in codes)
    assert len(codes) > 490  # random, not a fixed sequence
    taken = {new_code(ALPHABET, 1) for _ in range(200)} - {"Z"}
    assert new_code(ALPHABET, 1, taken | set(ALPHABET) - {"Z"}) == "Z"


def pending(created=1000.0, expires=1600.0):
    return PendingCode(code="ABC234", discord_id=7, created_at=created, expires_at=expires)


def test_filed_entry_is_accepted():
    assert filed_user({"userId": 1069090294, "at": 1200}, pending()) == (1069090294, "ok")


def test_bad_or_stale_entries_are_refused():
    p = pending()
    for value in (None, "x", {"at": 1200}, {"userId": "1", "at": 1200}, {"userId": -5, "at": 1200},
                  {"userId": True, "at": 1200}, {"userId": 5}):
        assert filed_user(value, p)[0] is None, value
    assert filed_user({"userId": 5, "at": 1000 - CLOCK_SKEW - 1}, p) == (None, "entry is older than this code (stale)")
    assert filed_user({"userId": 5, "at": 1600 + CLOCK_SKEW + 1}, p)[0] is None
    assert filed_user({"userId": 5, "at": 1000 - CLOCK_SKEW + 1}, p)[0] == 5  # small clock skew is fine


def test_member_roles_from_a_membership_listing():
    body = {"groupMemberships": [
        {"path": "groups/9/memberships/abc", "user": "users/42",
         "role": "groups/9/roles/300", "roles": ["groups/9/roles/300", "groups/9/roles/100"]},
    ]}
    assert parse_member_roles(body, 9, 42) == {300, 100}
    assert parse_member_roles({"groupMemberships": []}, 9, 42) is None            # not in the group
    assert parse_member_roles(body, 8, 42) is None                                 # other group
    only_role = {"groupMemberships": [{"path": "groups/9/memberships/a", "user": "users/42",
                                       "role": "groups/9/roles/55"}]}
    assert parse_member_roles(only_role, 9, 42) == {55}                            # older response shape


def test_entry_value_object_or_json_string():
    assert entry_value({"value": {"userId": 5, "at": 9}}) == {"userId": 5, "at": 9}
    assert entry_value({"value": '{"userId": 5, "at": 9}'}) == {"userId": 5, "at": 9}
    assert entry_value({"value": "not json"}) == "not json"
    assert entry_value({}) is None


def test_changes_only_touch_managed_roles():
    managed = frozenset({1, 2, 3, 4})
    add, remove = changes(current=[2, 3, 99], desired=frozenset({1, 2}), managed=managed)
    assert add == {1} and remove == {3}   # 99 is not managed: left alone
