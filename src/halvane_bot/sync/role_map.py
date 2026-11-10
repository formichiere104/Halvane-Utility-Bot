"""Load config/role_map.yaml and work out which Discord roles a member should hold. Pure: no network."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from ..ids import IdRegistry, UnknownIdError


class RoleMapError(Exception):
    """role_map.yaml is malformed, names an unknown key, or maps a protected role."""


@dataclass(frozen=True)
class RoleMap:
    group_id: int
    linked: frozenset[int]              # Discord role ids
    not_linked: frozenset[int]
    in_group: frozenset[int]
    group_roles: dict[int, frozenset[int]]   # Roblox group role id -> Discord role ids
    protected: frozenset[int]

    @property
    def managed(self) -> frozenset[int]:
        """Every Discord role the bot may add or remove. Nothing else is ever touched."""
        out = set(self.linked) | self.not_linked | self.in_group
        for ids in self.group_roles.values():
            out |= ids
        return frozenset(out)

    def desired(self, linked: bool, group_role_ids: Iterable[int] | None) -> frozenset[int]:
        """Managed roles a member should hold.

        linked: the member has a linked Roblox account.
        group_role_ids: the Roblox group roles they hold, or None if they aren't in the group.
        """
        if not linked:
            return self.not_linked
        out = set(self.linked)
        if group_role_ids is not None:
            out |= self.in_group
            for rid in group_role_ids:
                out |= self.group_roles.get(int(rid), frozenset())
        return frozenset(out)


def load_role_map(path: Path, ids: IdRegistry) -> RoleMap:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise RoleMapError(f"{path.name}: invalid YAML: {exc}") from exc

    def keys_to_ids(where: str, keys) -> frozenset[int]:
        if not isinstance(keys, list):
            raise RoleMapError(f"{path.name}: {where} must be a list of role keys")
        try:
            return frozenset(ids.role(k) for k in keys)
        except UnknownIdError as exc:
            raise RoleMapError(f"{path.name}: {where}: {exc}") from None

    try:
        group_id = int(raw["group_id"])
    except (KeyError, TypeError, ValueError):
        raise RoleMapError(f"{path.name}: group_id is required") from None

    protected_keys = raw.get("protected") or []
    protected = keys_to_ids("protected", protected_keys)

    group_roles: dict[int, frozenset[int]] = {}
    for gid, entry in (raw.get("group_roles") or {}).items():
        if not isinstance(entry, dict) or "discord" not in entry:
            raise RoleMapError(f"{path.name}: group role {gid} needs a 'discord' list")
        group_roles[int(gid)] = keys_to_ids(f"group role {gid} ({entry.get('name', '?')})", entry["discord"])

    rm = RoleMap(
        group_id=group_id,
        linked=keys_to_ids("linked", raw.get("linked") or []),
        not_linked=keys_to_ids("not_linked", raw.get("not_linked") or []),
        in_group=keys_to_ids("in_group", raw.get("in_group") or []),
        group_roles=group_roles,
        protected=protected,
    )
    clash = rm.managed & rm.protected
    if clash:
        names = sorted(k for k, v in ids.roles.items() if v in clash)
        raise RoleMapError(f"{path.name}: protected role(s) {names} must not be synced")
    return rm
