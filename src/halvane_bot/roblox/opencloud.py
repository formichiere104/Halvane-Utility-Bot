"""Async Open Cloud client: only the calls the bot needs. Every Roblox call lives here, so a change in the
(Beta) group endpoints is a one-file fix.

Key permissions used: group read on the Staff Register; data store read + delete on the game's universe.
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
from typing import Any

log = logging.getLogger(__name__)

API = "https://apis.roblox.com"
RETRY_STATUS = {429, 500, 502, 503, 504}
TIMEOUT = 15  # seconds per socket operation


class OpenCloudError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Open Cloud HTTP {status}: {detail}")
        self.status = status


def parse_member_roles(body: dict[str, Any], group_id: int, user_id: int) -> set[int] | None:
    """Role ids the user holds in this group, from a ListGroupMemberships response. None = not a member."""
    prefix = f"groups/{group_id}/"
    for m in body.get("groupMemberships") or []:
        if not str(m.get("path", "")).startswith(prefix) or m.get("user") != f"users/{user_id}":
            continue
        paths = m.get("roles") or ([m["role"]] if m.get("role") else [])
        return {int(p.rsplit("/", 1)[-1]) for p in paths if str(p).startswith(f"groups/{group_id}/roles/")}
    return None


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

    def _send(self, method: str, url: str) -> tuple[int, str]:
        """One blocking HTTP call. Runs in a worker thread. Returns (status, body); HTTP errors are not raised."""
        req = urllib.request.Request(url, method=method,
                                     headers={"x-api-key": self._key, "User-Agent": "halvane-utility-bot"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    async def _request(self, method: str, path: str, *, allow_404: bool = False) -> dict[str, Any] | None:
        for attempt in range(4):
            try:
                status, text = await asyncio.to_thread(self._send, method, API + path)
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

    # ---- account linking (data store written by the game's Staff Register terminal) ----------------

    def _entry_path(self, code: str) -> str:
        store = urllib.parse.quote(self.link_store, safe="")
        return f"/cloud/v2/universes/{self.universe_id}/data-stores/{store}/entries/{urllib.parse.quote(code, safe='')}"

    async def get_link_entry(self, code: str) -> Any | None:
        """The value the game stored for this code ({userId, at}), or None if nothing is filed yet."""
        body = await self._request("GET", self._entry_path(code), allow_404=True)
        return None if body is None else entry_value(body)

    async def delete_link_entry(self, code: str) -> None:
        await self._request("DELETE", self._entry_path(code), allow_404=True)

    # ---- group ----------------------------------------------------------------------------------------

    async def member_roles(self, user_id: int) -> set[int] | None:
        """Every role the user holds in the Staff Register (base role excluded by Roblox), or None if not a member."""
        flt = urllib.parse.quote(f"user == 'users/{int(user_id)}'", safe="")
        body = await self._request("GET", f"/cloud/v2/groups/{self.group_id}/memberships?maxPageSize=10&filter={flt}")
        return parse_member_roles(body or {}, self.group_id, int(user_id))

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
