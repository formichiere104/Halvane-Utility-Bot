"""`python -m halvane_bot roblox-check`: prove the Open Cloud key works from this host, read-only.

1. Lists the Staff Register's roles and checks every group role id in config/role_map.yaml exists.
2. Lists one entry of the LinkCodes data store (works on an empty store too).
Run it on the VPS: the key is IP-restricted, so from any other machine it answers 401.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .config import Settings, roblox_api_key
from .ids import load_ids
from .sync.role_map import load_role_map

API = "https://apis.roblox.com"

HINTS = {
    401: "key rejected: wrong key, expired, or this host's IP is not on the key's allowed list",
    403: "key accepted but missing a permission for this resource (check the key's scopes and targets)",
    404: "not found: check the group / universe id in config/bot.yaml",
}


PROBE_KEY = "halvane-permission-probe-never-written"


def _get(path: str, key: str) -> tuple[int, dict]:
    return _call("GET", path, key)


def _call(method: str, path: str, key: str) -> tuple[int, dict]:
    req = urllib.request.Request(API + path, method=method,
                                 headers={"x-api-key": key, "User-Agent": "halvane-utility-bot"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b"{}")
        except ValueError:
            body = {}
        return exc.code, body


def _get_all(path: str, key: str, field: str, page_size: int) -> tuple[int, list[dict]]:
    """Follow nextPageToken until the list ends. Open Cloud caps pages (group roles: 20, memberships: 100)."""
    items: list[dict] = []
    token = ""
    for _ in range(100):  # hard stop against a token loop
        sep = "&" if "?" in path else "?"
        url = f"{path}{sep}maxPageSize={page_size}" + (f"&pageToken={urllib.parse.quote(token)}" if token else "")
        status, body = _get(url, key)
        if status != 200:
            return status, body if isinstance(body, list) else [body]
        items += body.get(field, [])
        token = body.get("nextPageToken") or ""
        if not token:
            break
    return 200, items


def run_roblox_check(settings: Settings) -> int:
    key = roblox_api_key()
    failures = 0

    status, roles = _get_all(f"/cloud/v2/groups/{settings.group_id}/roles", key, "groupRoles", page_size=20)
    body = roles[0] if status != 200 and roles else {}
    if status == 200:
        found = {int(r["id"]): r.get("displayName", "?") for r in roles}
        print(f"ok    group {settings.group_id}: {len(found)} roles readable")
        if settings.role_map_file:
            rm = load_role_map(settings.role_map_file, load_ids(settings.ids_file))
            missing = [gid for gid in rm.group_roles if gid not in found]
            if missing:
                failures += 1
                print(f"FAIL  role_map.yaml names group roles that don't exist: {missing}")
            else:
                print(f"ok    all {len(rm.group_roles)} mapped group roles exist")
    else:
        failures += 1
        print(f"FAIL  group roles: HTTP {status}: {HINTS.get(status, body)}")

    store = urllib.parse.quote(settings.link_store, safe="")
    base = f"/cloud/v2/universes/{settings.universe_id}/data-stores/{store}/entries"
    status, body = _get(f"{base}?maxPageSize=1", key)
    if status == 200:
        print(f"ok    data store '{settings.link_store}': entries can be listed "
              f"({len(body.get('dataStoreEntries', []))} shown)")
    else:
        failures += 1
        print(f"FAIL  listing '{settings.link_store}': HTTP {status}: {HINTS.get(status, body)}")

    # Read and delete are separate permissions from list. Probe both on a key that never exists:
    # 404 means the key may do it; 401/403 means it may not. Nothing is read or deleted.
    for method, what in (("GET", "read an entry"), ("DELETE", "delete an entry")):
        status, body = _call(method, f"{base}/{PROBE_KEY}", key)
        if status in (200, 404):
            print(f"ok    the key may {what} in '{settings.link_store}'")
        else:
            failures += 1
            print(f"FAIL  {what} in '{settings.link_store}': HTTP {status}: {HINTS.get(status, body)}"
                  + ("  -> add this operation to the key's data store permissions" if status == 403 else ""))

    print("\n" + ("Open Cloud access works." if not failures else f"{failures} check(s) failed."))
    return 1 if failures else 0


def run_link_check(settings: Settings, code: str) -> int:
    """Read one LinkCodes entry exactly as the bot's poll does and print what Roblox answers. Read-only."""
    import time

    from .roblox.opencloud import entry_value

    key = roblox_api_key()
    store = urllib.parse.quote(settings.link_store, safe="")
    code = code.strip().upper()
    path = f"/cloud/v2/universes/{settings.universe_id}/data-stores/{store}/entries/{urllib.parse.quote(code)}"
    status, body = _get(path, key)
    print(f"GET {path}\nHTTP {status}")
    if status == 404:
        print("Nothing is filed under this code (or it was already used and deleted).")
        return 1
    if status != 200:
        print(f"{HINTS.get(status, '')}\n{json.dumps(body)[:500]}")
        return 1
    value = entry_value(body)
    print(f"value: {value!r}")
    if isinstance(value, dict) and isinstance(value.get("at"), (int, float)):
        print(f"filed {int(time.time() - value['at'])} s ago by this host's clock "
              "(a large negative or very large number means the host clock is off)")
    return 0
