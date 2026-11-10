"""`python -m halvane_bot export-ids`: read roles and channels from Discord and write config/ids.yaml.

Uses the REST API directly (no gateway connection), so it can run while the bot is running. Prints what changed
since the last export: new keys, renamed roles or channels (their keys are kept), and removed ones.
"""
from __future__ import annotations

import fnmatch
import json
import re
import urllib.request
from pathlib import Path
from typing import Mapping

from .config import Settings, discord_token
from .ids import CHANNEL_KIND, assign_keys, load_ids, render_ids_yaml

API = "https://discord.com/api/v10"


def _get(path: str, token: str):
    req = urllib.request.Request(API + path, headers={
        "Authorization": f"Bot {token}",
        "User-Agent": "DiscordBot (halvane-utility-bot, 0.1)",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


_LINE = re.compile(r'^  (\w+): "(\d+)"  # (.*)$')


def ignored(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def old_names(text: str) -> dict[int, str]:
    """Names as they were at the last export, from the comments in ids.yaml."""
    out: dict[int, str] = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            out[int(m.group(2))] = m.group(3)
    return out


def describe_changes(kind: str, old: Mapping[str, int], new: Mapping[str, int],
                     before: Mapping[int, str], after: Mapping[int, str]) -> list[str]:
    """Lines for people: what a refresh added, renamed or removed for one kind (role / channel)."""
    lines = [f"new      {kind:<8} {k}  ({after.get(v, '')})" for k, v in new.items() if k not in old]
    lines += [f"renamed  {kind:<8} {k}  ({before[v]} -> {after.get(v, '')}; key kept)"
              for k, v in new.items() if k in old and v in before and before[v] != after.get(v, before[v])]
    lines += [f"REMOVED  {kind:<8} {k}  (was {before.get(v, '?')}): files that still use it fail the check below"
              for k, v in old.items() if k not in new]
    return lines


def export_ids(settings: Settings, token: str | None = None) -> tuple[Path, list[str]]:
    token = token or discord_token()
    gid = settings.guild_id
    roles = sorted(_get(f"/guilds/{gid}/roles", token), key=lambda r: -r["position"])
    channels = sorted(_get(f"/guilds/{gid}/channels", token), key=lambda c: (c["type"] != 4, c["position"]))

    existing = load_ids(settings.ids_file) if settings.ids_file.exists() else None
    role_items = [(int(r["id"]), "everyone" if int(r["id"]) == gid else r["name"], "role")
                  for r in roles if not r.get("managed")]
    channels = [c for c in channels if not ignored(c["name"], settings.ids_ignore_channels)]
    chan_items = [(int(c["id"]), c["name"], CHANNEL_KIND.get(c["type"], f"type{c['type']}")) for c in channels]

    role_keys = assign_keys(role_items, existing.roles if existing else {})
    chan_keys = assign_keys(chan_items, existing.channels if existing else {})
    role_names = {int(r["id"]): r["name"] for r in roles}
    chan_names = {int(c["id"]): ("#" if c["type"] in (0, 5, 15) else "") + c["name"] for c in channels}

    before = old_names(settings.ids_file.read_text(encoding="utf-8")) if existing else {}
    report = (describe_changes("role", existing.roles if existing else {}, role_keys, before, role_names)
              + describe_changes("channel", existing.channels if existing else {}, chan_keys, before, chan_names))

    text = render_ids_yaml(gid, role_keys, chan_keys, role_names, chan_names)
    settings.ids_file.parent.mkdir(parents=True, exist_ok=True)
    settings.ids_file.write_text(text, encoding="utf-8", newline="\n")  # same bytes on Windows and the VPS
    return settings.ids_file, report
