"""Read and schema-check embed YAML files. One file = one managed set of messages in one channel.

File format (embeds/<key>.yaml, the key is the file name):

    channel: rules            # channel key from config/ids.yaml
    note: Server rules.       # optional, shown in /embeds list
    messages:
      - content: optional plain text above the embeds
        embeds:
          - title: Staff Conduct
            color: "#4F8FD6"
            description: |
              Text with {role:station_chief}, {channel:events} and {link:roblox_group} tokens.
            fields: [{name: ..., value: ..., inline: false}]
            footer: Discretion. Delivery. Duty.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SET_KEYS = {"channel", "note", "messages"}
MESSAGE_KEYS = {"content", "embeds"}
EMBED_KEYS = {"title", "description", "color", "url", "footer", "author", "fields", "image", "thumbnail"}
FIELD_KEYS = {"name", "value", "inline"}


class EmbedSpecError(Exception):
    """A YAML file that does not match the format."""


@dataclass(frozen=True)
class MessageSpec:
    content: str | None
    embeds: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class EmbedSet:
    key: str
    channel: str
    note: str
    messages: tuple[MessageSpec, ...]
    source: Path


def _fail(path: Path, where: str, problem: str) -> EmbedSpecError:
    return EmbedSpecError(f"{path.name}: {where}: {problem}")


def _check_keys(path: Path, where: str, data: Any, allowed: set[str]) -> dict:
    if not isinstance(data, dict):
        raise _fail(path, where, "must be a mapping")
    unknown = set(data) - allowed
    if unknown:
        raise _fail(path, where, f"unknown key(s) {sorted(unknown)}; allowed: {sorted(allowed)}")
    return data


def _check_embed(path: Path, where: str, embed: Any) -> dict:
    embed = _check_keys(path, where, embed, EMBED_KEYS)
    if not any(embed.get(k) for k in ("title", "description", "fields", "image")):
        raise _fail(path, where, "needs at least a title, description, fields or image")
    for i, field in enumerate(embed.get("fields") or []):
        f = _check_keys(path, f"{where}.fields[{i}]", field, FIELD_KEYS)
        if not f.get("name") or not f.get("value"):
            raise _fail(path, f"{where}.fields[{i}]", "name and value are required")
    for key in ("footer", "author"):
        if key in embed and not isinstance(embed[key], (str, dict)):
            raise _fail(path, f"{where}.{key}", "must be text or a mapping")
    return embed


def load_embed_set(path: Path) -> EmbedSet:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise EmbedSpecError(f"{path.name}: invalid YAML: {exc}") from exc
    raw = _check_keys(path, "top level", raw, SET_KEYS)
    channel = raw.get("channel")
    if not isinstance(channel, str) or not channel:
        raise _fail(path, "channel", "a channel key is required")
    messages_raw = raw.get("messages")
    if not isinstance(messages_raw, list) or not messages_raw:
        raise _fail(path, "messages", "a non-empty list is required")

    messages = []
    for i, msg in enumerate(messages_raw):
        msg = _check_keys(path, f"messages[{i}]", msg, MESSAGE_KEYS)
        embeds = msg.get("embeds") or []
        if not isinstance(embeds, list):
            raise _fail(path, f"messages[{i}].embeds", "must be a list")
        content = msg.get("content")
        if content is not None and not isinstance(content, str):
            raise _fail(path, f"messages[{i}].content", "must be text")
        if not content and not embeds:
            raise _fail(path, f"messages[{i}]", "needs content or embeds")
        checked = tuple(_check_embed(path, f"messages[{i}].embeds[{j}]", e) for j, e in enumerate(embeds))
        messages.append(MessageSpec(content=content or None, embeds=checked))

    return EmbedSet(key=path.stem, channel=channel, note=str(raw.get("note") or ""),
                    messages=tuple(messages), source=path)


def load_all(directory: Path) -> dict[str, EmbedSet]:
    """Every embeds/*.yaml except files starting with '_' (drafts)."""
    sets: dict[str, EmbedSet] = {}
    for path in sorted(directory.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        es = load_embed_set(path)
        sets[es.key] = es
    return sets
