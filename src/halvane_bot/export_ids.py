"""`python -m halvane_bot export-ids`: read roles and channels from Discord and write config/ids.yaml.

Uses the REST API directly (no gateway connection), so it can run while the bot is running.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

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


def export_ids(settings: Settings, token: str | None = None) -> Path:
    token = token or discord_token()
    gid = settings.guild_id
    roles = sorted(_get(f"/guilds/{gid}/roles", token), key=lambda r: -r["position"])
    channels = sorted(_get(f"/guilds/{gid}/channels", token), key=lambda c: (c["type"] != 4, c["position"]))

    existing = load_ids(settings.ids_file) if settings.ids_file.exists() else None
    role_items = [(int(r["id"]), "everyone" if int(r["id"]) == gid else r["name"], "role")
                  for r in roles if not r.get("managed")]
    chan_items = [(int(c["id"]), c["name"], CHANNEL_KIND.get(c["type"], f"type{c['type']}")) for c in channels]

    role_keys = assign_keys(role_items, existing.roles if existing else {})
    chan_keys = assign_keys(chan_items, existing.channels if existing else {})

    text = render_ids_yaml(
        gid, role_keys, chan_keys,
        {int(r["id"]): r["name"] for r in roles},
        {int(c["id"]): ("#" if c["type"] in (0, 5, 15) else "") + c["name"] for c in channels},
    )
    settings.ids_file.parent.mkdir(parents=True, exist_ok=True)
    settings.ids_file.write_text(text, encoding="utf-8")
    return settings.ids_file
