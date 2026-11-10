"""Async Open Cloud client: only the calls the bot needs. Every Roblox call lives here, so a change in the
(Beta) group endpoints is a one-file fix.

Key permissions used:
- Staff Register group: read (phase 2), write (phase 4: assign and unassign roles)
- data stores on the game's universe: read, list, delete (LinkCodes); create + update (ApprovalLedger)
- messaging: publish (tell live servers a ledger changed)
The key is IP-restricted, so this only works from the VPS.

Transport: urllib in a worker thread, not aiohttp. On the VPS every aiohttp request to apis.roblox.com hung
until its timeout (even IPv4-only), while urllib on the same host answered at once (10 Nov 2026). The bot makes
a handful of calls a minute, so a thread per call is plenty.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

API = "https://apis.roblox.com"
RETRY_STATUS = {429, 500, 502, 503, 504}
TIMEOUT = 15  # seconds per socket operation


class OpenCloudError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Open Cloud HTTP {status}: {detail}")
        self.status = status


@dataclass(frozen=True)
class Membership:
    path: str            # groups/{group_id}/memberships/{membership_id}
    roles: frozenset[int]


@dataclass(frozen=True)
class Entry:
    value: Any
    etag: str | None


def without_nulls(value: Any) -> Any:
    """A copy of value with every None removed: dict keys dropped, list items skipped."""
    if isinstance(value, dict):
        return {k: without_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [without_nulls(v) for v in value if v is not None]
    return value


def parse_membership(body: dict[str, Any], group_id: int, user_id: int) -> Membership | None:
    """The user's membership in this group from a ListGroupMemberships response. None = not a member."""
    prefix = f"groups/{group_id}/"
    for m in body.get("groupMemberships") or []:
        if not str(m.get("path", "")).startswith(prefix) or m.get("user") != f"users/{user_id}":
            continue
        paths = m.get("roles") or ([m["role"]] if m.get("role") else [])
        roles = {int(p.rsplit("/", 1)[-1]) for p in paths if str(p).startswith(f"groups/{group_id}/roles/")}
        return Membership(path=str(m["path"]), roles=frozenset(roles))
    return None


def parse_member_roles(body: dict[str, Any], group_id: int, user_id: int) -> set[int] | None:
    """Role ids the user holds in this group, from a ListGroupMemberships response. None = not a member."""
    m = parse_membership(body, group_id, user_id)
    return None if m is None else set(m.roles)


def entry_value(body: dict[str, Any]) -> Any:
    """The stored value of a GetDataStoreEntry response. Tolerates the value arriving as a JSON string."""
    value = body.get("value")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


class OpenCloud:
    def __init__(self, api_key: str, universe_id: int, group_id: int, link_store: str) -> None:
        self._key = api_key
        self.universe_id = universe_id
        self.group_id = group_id
        self.link_store = link_store

    async def start(self) -> None:
        """Nothing to open (kept so callers don't change)."""

    async def close(self) -> None:
        """Nothing to close."""

    def _send(self, method: str, url: str, body: Any = None) -> tuple[int, str]:
        """One blocking HTTP call. Runs in a worker thread. Returns (status, body); HTTP errors are not raised."""
        headers = {"x-api-key": self._key, "User-Agent": "halvane-utility-bot"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    async def _request(self, method: str, path: str, *, body: Any = None,
                       allow_404: bool = False) -> dict[str, Any] | None:
        for attempt in range(4):
            try:
                status, text = await asyncio.to_thread(self._send, method, API + path, body)
            except (urllib.error.URLError, OSError, ValueError) as exc:  # DNS, connection, TLS, timeout
                if attempt == 3:
                    # A timeout's message can be empty, so always name the exception type.
                    raise OpenCloudError(0, f"network error: {type(exc).__name__} {exc}".rstrip()) from None
                await asyncio.sleep(min(2 ** attempt, 8))
                continue
            if status in RETRY_STATUS and attempt < 3:
                await asyncio.sleep(min(2 ** attempt, 8))
                continue
            if status == 404 and allow_404:
                return None
            if status >= 400:
                raise OpenCloudError(status, text[:200])
            return json.loads(text) if text.strip() else {}
        raise OpenCloudError(0, "gave up after retries")

    # ---- data stores ----------------------------------------------------------------------------------

    def _store_path(self, store: str) -> str:
        return f"/cloud/v2/universes/{self.universe_id}/data-stores/{urllib.parse.quote(store, safe='')}/entries"

    async def get_entry(self, store: str, key: str) -> Entry | None:
        body = await self._request("GET", f"{self._store_path(store)}/{urllib.parse.quote(key, safe='')}",
                                   allow_404=True)
        return None if body is None else Entry(entry_value(body), body.get("etag"))

    async def put_entry(self, store: str, key: str, value: Any, *, user_ids: list[int] | None = None,
                        etag: str | None = None) -> None:
        """Replace the entry's value (created if missing). With an etag, Roblox refuses if it changed meanwhile.
        JSON nulls are dropped first: Roblox answers 400 "Value cannot be null" to a null anywhere inside a value
        (and Luau has no nil-in-table to read one back as anyway)."""
        body: dict[str, Any] = {"value": without_nulls(value), "users": [f"users/{int(u)}" for u in user_ids or []]}
        if etag:
            body["etag"] = etag
        await self._request("PATCH", f"{self._store_path(store)}/{urllib.parse.quote(key, safe='')}"
                                     "?allowMissing=true", body=body)

    async def delete_entry(self, store: str, key: str) -> None:
        await self._request("DELETE", f"{self._store_path(store)}/{urllib.parse.quote(key, safe='')}",
                            allow_404=True)

    async def list_entry_ids(self, store: str, limit: int = 1000) -> list[str]:
        ids: list[str] = []
        token = ""
        while len(ids) < limit:
            query = "?maxPageSize=100" + (f"&pageToken={urllib.parse.quote(token, safe='')}" if token else "")
            body = await self._request("GET", self._store_path(store) + query) or {}
            ids += [str(e.get("id") or str(e.get("path", "")).rsplit("/", 1)[-1])
                    for e in body.get("dataStoreEntries") or []]
            token = body.get("nextPageToken") or ""
            if not token:
                break
        return ids

    # ---- account linking (data store written by the game's Staff Register terminal) ----------------

    async def get_link_entry(self, code: str) -> Any | None:
        """The value the game stored for this code ({userId, at}), or None if nothing is filed yet."""
        entry = await self.get_entry(self.link_store, code)
        return None if entry is None else entry.value

    async def delete_link_entry(self, code: str) -> None:
        await self.delete_entry(self.link_store, code)

    # ---- group ----------------------------------------------------------------------------------------

    async def membership(self, user_id: int) -> Membership | None:
        flt = urllib.parse.quote(f"user == 'users/{int(user_id)}'", safe="")
        body = await self._request("GET", f"/cloud/v2/groups/{self.group_id}/memberships?maxPageSize=10&filter={flt}")
        return parse_membership(body or {}, self.group_id, int(user_id))

    async def member_roles(self, user_id: int) -> set[int] | None:
        """Every role the user holds in the Staff Register (base role excluded by Roblox), or None if not a member."""
        m = await self.membership(user_id)
        return None if m is None else set(m.roles)

    async def assign_role(self, membership: Membership, role_id: int) -> None:
        """No-op on Roblox's side if the member already holds it."""
        await self._request("POST", f"/cloud/v2/{membership.path}:assignRole",
                            body={"role": f"groups/{self.group_id}/roles/{int(role_id)}"})

    async def unassign_role(self, membership: Membership, role_id: int) -> None:
        await self._request("POST", f"/cloud/v2/{membership.path}:unassignRole",
                            body={"role": f"groups/{self.group_id}/roles/{int(role_id)}"})

    # ---- messaging ------------------------------------------------------------------------------------

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        """Tell live game servers something changed. Max 1 KB."""
        await self._request("POST", f"/cloud/v2/universes/{self.universe_id}:publishMessage",
                            body={"topic": topic, "message": json.dumps(message, separators=(",", ":"))})

    # ---- users ----------------------------------------------------------------------------------------

    async def user_names(self, user_id: int) -> tuple[str, str]:
        """(username, display name). Falls back to the id if the lookup fails."""
        try:
            body = await self._request("GET", f"/cloud/v2/users/{int(user_id)}")
        except OpenCloudError as exc:
            log.warning("user lookup for %s failed: %s", user_id, exc)
            return str(user_id), str(user_id)
        body = body or {}
        name = str(body.get("name") or user_id)
        return name, str(body.get("displayName") or name)
