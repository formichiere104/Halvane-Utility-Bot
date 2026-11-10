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
