"""Load settings from config/bot.yaml and secrets from the environment (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

DEFAULT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(Exception):
    """Raised when a setting is missing or malformed."""


@dataclass(frozen=True)
class Settings:
    root: Path
    guild_id: int
    database: Path
    embeds_dir: Path
    ids_file: Path
    embed_managers: tuple[str, ...]
    log_channel: str | None
    links: dict[str, str] = field(default_factory=dict)
    sync_commands: bool = True
    group_id: int = 0
    universe_id: int = 0
    role_map_file: Path | None = None


def project_root() -> Path:
    return Path(os.environ.get("HALVANE_ROOT", DEFAULT_ROOT)).resolve()


def load_settings(root: Path | None = None) -> Settings:
    root = root or project_root()
    load_dotenv(root / ".env")
    path = root / "config" / "bot.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"missing {path}") from exc

    def need(key: str):
        if key not in raw or raw[key] in (None, ""):
            raise ConfigError(f"config/bot.yaml: '{key}' is required")
        return raw[key]

    try:
        guild_id = int(need("guild_id"))
    except ValueError as exc:
        raise ConfigError("config/bot.yaml: 'guild_id' must be a number") from exc

    links = raw.get("links") or {}
    if not isinstance(links, dict):
        raise ConfigError("config/bot.yaml: 'links' must be a mapping")
    roblox = raw.get("roblox") or {}
    if not isinstance(roblox, dict):
        raise ConfigError("config/bot.yaml: 'roblox' must be a mapping")
    try:
        group_id = int(roblox.get("group_id") or 0)
        universe_id = int(roblox.get("universe_id") or 0)
    except ValueError as exc:
        raise ConfigError("config/bot.yaml: roblox ids must be numbers") from exc

    return Settings(
        root=root,
        guild_id=guild_id,
        database=root / need("database"),
        embeds_dir=root / need("embeds_dir"),
        ids_file=root / need("ids_file"),
        embed_managers=tuple(raw.get("embed_managers") or ()),
        log_channel=(raw.get("log_channel") or None),
        links={str(k): str(v or "") for k, v in links.items()},
        sync_commands=os.environ.get("SYNC_COMMANDS", "1") != "0",
        group_id=group_id,
        universe_id=universe_id,
        role_map_file=(root / raw["role_map_file"]) if raw.get("role_map_file") else None,
    )


def roblox_api_key() -> str:
    """Open Cloud API key (phase 2+). Restricted by IP on Roblox's side, so it only works from allowed hosts."""
    key = os.environ.get("ROBLOX_API_KEY", "").strip()
    if not key:
        raise ConfigError("ROBLOX_API_KEY is not set (put it in .env on the host)")
    return key


def discord_token() -> str:
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise ConfigError("DISCORD_TOKEN is not set (put it in .env on the host)")
    return token
