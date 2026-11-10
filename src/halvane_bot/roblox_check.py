"""`python -m halvane_bot roblox-check`: prove the Open Cloud key works from this host.

1. Lists the Staff Register's roles and checks every group role id in role_map.yaml and approvals.yaml exists.
2. LinkCodes: list, read and delete (read and delete are probed on a key that never exists).
3. Approvals (Phase 4): writes one probe entry to the ApprovalLedger and deletes it again, publishes one
   message to a topic no game server listens to, and probes group role assignment on a membership that
   doesn't exist. Nothing reaches a player, a member or a live server.
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
WRITE_PROBE_KEY = "halvane-permission-probe"  # never a user id, so the game never reads it
PROBE_TOPIC = "halvane-probe"                # no game server subscribes to it


def _get(path: str, key: str) -> tuple[int, dict]:
    return _call("GET", path, key)


def _call(method: str, path: str, key: str, body: dict | None = None) -> tuple[int, dict]:
    headers = {"x-api-key": key, "User-Agent": "halvane-utility-bot"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
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
        approval_roles = _approval_group_roles(settings)
        missing = sorted(gid for gid in approval_roles if gid not in found)
        if missing:
            failures += 1
            print(f"FAIL  approvals.yaml names group roles that don't exist: {missing}")
        else:
            print(f"ok    all {len(approval_roles)} group roles in approvals.yaml exist")
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

    failures += _check_approvals(settings, key)

    print("\n" + ("Open Cloud access works." if not failures else f"{failures} check(s) failed."))
    return 1 if failures else 0


def _approvals(settings: Settings):
    from .approvals.ledger import load_approvals
    return load_approvals(settings.root / "config" / "approvals.yaml", load_ids(settings.ids_file))


def _approval_group_roles(settings: Settings) -> set[int]:
    cfg = _approvals(settings)
    roles = {s.grade_group_role for s in cfg.steps.values()}
    roles |= {c.group_role for c in cfg.certifications.values()}
    roles |= {d.posting_group_role for d in cfg.divisions.values() if d.posting_group_role}
    return roles


def _fail(what: str, status: int, body: dict, fix: str) -> int:
    print(f"FAIL  {what}: HTTP {status}: {HINTS.get(status, body)}" + (f"  -> {fix}" if status == 403 else ""))
    return 1


def _check_approvals(settings: Settings, key: str) -> int:
    """Phase 4: the ledger write, the live-server message and group role assignment."""
    cfg = _approvals(settings)
    failures = 0

    store = urllib.parse.quote(cfg.ledger_store, safe="")
    entry = f"/cloud/v2/universes/{settings.universe_id}/data-stores/{store}/entries/{WRITE_PROBE_KEY}"
    status, body = _call("PATCH", entry + "?allowMissing=true", key, {"value": {"probe": True}})
    if status == 200:
        print(f"ok    the key may write entries in '{cfg.ledger_store}'")
        status, body = _call("GET", entry, key)
        if status == 200:
            print(f"ok    the key may read entries in '{cfg.ledger_store}'")
        else:
            failures += _fail(f"reading '{cfg.ledger_store}'", status, body,
                              "add Read entry to the key's data store permissions")
        status, body = _call("DELETE", entry, key)
        if status in (200, 404):
            print("ok    probe entry removed")
        else:
            print(f"note  could not remove the probe entry (HTTP {status}); it is harmless: "
                  f"key '{WRITE_PROBE_KEY}' is never a user id")
    else:
        failures += _fail(f"writing '{cfg.ledger_store}'", status, body,
                          "add Create entry and Update entry to the key's data store permissions")

    status, body = _call("POST", f"/cloud/v2/universes/{settings.universe_id}:publishMessage", key,
                         {"topic": PROBE_TOPIC, "message": "probe"})
    if status == 200:
        print("ok    the key may publish to live servers (Messaging Service)")
    else:
        failures += _fail("publishing a message", status, body,
                          "add Messaging Service > Publish for this experience to the key")

    # A membership id that can't exist: a key with group write gets "not found" or "invalid";
    # one without it gets 403. Nobody's roles change either way.
    lowest = cfg.step_list()[0].grade_group_role
    status, body = _call("POST", f"/cloud/v2/groups/{settings.group_id}/memberships/{WRITE_PROBE_KEY}:assignRole",
                         key, {"role": f"groups/{settings.group_id}/roles/{lowest}"})
    if status in (400, 404):
        print(f"ok    the key may assign group roles (Roblox refused the dummy membership: HTTP {status})")
    elif status in (401, 403):
        failures += _fail("assigning group roles", status, body,
                          "add Groups > Write for the Staff Register to the key")
    else:
        print(f"note  group role assignment probe answered HTTP {status}; /approve will show the real answer")
    return failures


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

    # Do the same read through the bot's own async client (roblox/opencloud.py) and compare.
    import asyncio

    from .roblox.opencloud import OpenCloud, OpenCloudError

    async def bot_read():
        cloud = OpenCloud(key, settings.universe_id, settings.group_id, settings.link_store)
        try:
            return await cloud.get_link_entry(code)
        finally:
            await cloud.close()

    try:
        bot_value = asyncio.run(bot_read())
        print(f"bot's client read: {bot_value!r}" + ("  (matches)" if bot_value == value else "  (DIFFERENT)"))
    except OpenCloudError as exc:
        print(f"bot's client read failed: {exc}")
    return 0
