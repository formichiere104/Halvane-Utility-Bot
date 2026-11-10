"""Link codes: generation and checking what the game filed. Pure: no network, no Discord."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

CLOCK_SKEW = 120  # seconds of tolerance between the game server's clock and ours


@dataclass
class PendingCode:
    code: str
    discord_id: int
    created_at: float  # unix seconds
    expires_at: float


def new_code(alphabet: str, length: int, taken: set[str] | None = None) -> str:
    """A random code from the shared alphabet, cryptographically random, not already pending."""
    taken = taken or set()
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(length))
        if code not in taken:
            return code


def filed_user(value: Any, pending: PendingCode) -> tuple[int | None, str]:
    """The Roblox user id the game filed for this pending code, or (None, reason) if the entry can't be trusted."""
    if not isinstance(value, dict):
        return None, "entry is not a table"
    user_id, at = value.get("userId"), value.get("at")
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        return None, "entry has no valid userId"
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return None, "entry has no timestamp"
    if at < pending.created_at - CLOCK_SKEW:
        return None, "entry is older than this code (stale)"
    if at > pending.expires_at + CLOCK_SKEW:
        return None, "entry was filed after the code expired"
    return user_id, "ok"
