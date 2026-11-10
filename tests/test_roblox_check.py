"""roblox-check must follow page tokens: Open Cloud returns at most 20 group roles per page."""
from __future__ import annotations

from halvane_bot import roblox_check


def test_get_all_follows_next_page_token(monkeypatch):
    pages = {
        "": {"groupRoles": [{"id": str(i)} for i in range(20)], "nextPageToken": "p2"},
        "p2": {"groupRoles": [{"id": str(i)} for i in range(20, 31)], "nextPageToken": ""},
    }
    seen: list[str] = []

    def fake_get(url: str, key: str):
        token = url.split("pageToken=")[1] if "pageToken=" in url else ""
        seen.append(url)
        return 200, pages[token]

    monkeypatch.setattr(roblox_check, "_get", fake_get)
    status, items = roblox_check._get_all("/cloud/v2/groups/1/roles", "k", "groupRoles", page_size=20)
    assert status == 200 and len(items) == 31
    assert all("maxPageSize=20" in u for u in seen) and len(seen) == 2


def test_get_all_stops_on_error(monkeypatch):
    monkeypatch.setattr(roblox_check, "_get", lambda url, key: (401, {"errors": ["no"]}))
    status, items = roblox_check._get_all("/x", "k", "groupRoles", page_size=20)
    assert status == 401 and items == [{"errors": ["no"]}]


def _approval_calls(monkeypatch, answer):
    calls: list[tuple[str, str, dict | None]] = []

    def fake_call(method, path, key, body=None):
        calls.append((method, path, body))
        return answer(method, path), {}
    monkeypatch.setattr(roblox_check, "_call", fake_call)
    from halvane_bot.config import load_settings
    return roblox_check._check_approvals(load_settings(), "k"), calls


def test_approval_probes_write_then_delete_and_touch_no_real_key(monkeypatch):
    def answer(method, path):
        return 404 if path.endswith(":assignRole") else 200
    failures, calls = _approval_calls(monkeypatch, answer)
    assert failures == 0
    methods = [(m, p.rsplit("/", 1)[-1]) for m, p, _ in calls]
    assert methods[:3] == [("PATCH", "halvane-permission-probe?allowMissing=true"),
                           ("GET", "halvane-permission-probe"), ("DELETE", "halvane-permission-probe")]
    publish = calls[3]
    assert publish[1].endswith(":publishMessage") and publish[2]["topic"] == "halvane-probe"
    assert "/memberships/halvane-permission-probe:assignRole" in calls[4][1]


def test_missing_permissions_fail_with_hints(monkeypatch, capsys):
    failures, calls = _approval_calls(monkeypatch, lambda method, path: 403)
    out = capsys.readouterr().out
    assert failures == 3  # ledger write, publish, group write; no read/delete tried after a refused write
    assert [m for m, _, _ in calls] == ["PATCH", "POST", "POST"]
    assert "Create entry and Update entry" in out and "Messaging Service" in out and "Groups > Write" in out
