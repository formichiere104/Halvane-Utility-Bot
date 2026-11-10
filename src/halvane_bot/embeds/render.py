"""Turn an EmbedSet into Discord API message dicts. Pure: no network, no discord.py."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..ids import IdRegistry, UnknownIdError
from .actions import ACTIONS
from .limits import message_problems
from .loader import EmbedSet

TOKEN_RE = re.compile(r"\{(role|channel|link):([A-Za-z0-9_\-]+)\}")


class RenderError(Exception):
    """The set can't be rendered (unknown key, bad colour, over Discord's limits)."""


@dataclass
class Rendered:
    key: str
    channel_id: int
    messages: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        blob = json.dumps({"channel": self.channel_id, "messages": self.messages}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()


def parse_color(value: Any) -> int:
    if isinstance(value, int) and 0 <= value <= 0xFFFFFF:
        return value
    if isinstance(value, str) and re.fullmatch(r"#?[0-9A-Fa-f]{6}", value.strip()):
        return int(value.strip().lstrip("#"), 16)
    raise RenderError(f"colour {value!r} must look like \"#4F8FD6\"")


def fill(text: str, ids: IdRegistry, links: Mapping[str, str], warnings: list[str]) -> str:
    """Replace {role:key}, {channel:key} and {link:key} tokens."""
    def sub(match: re.Match) -> str:
        kind, key = match.group(1), match.group(2)
        if kind == "role":
            return f"<@&{ids.role(key)}>"
        if kind == "channel":
            return f"<#{ids.channel(key)}>"
        if key not in links:
            raise RenderError(f"unknown link key '{key}' (add it under links: in config/bot.yaml)")
        if not links[key]:
            warnings.append(f"link '{key}' is empty in config/bot.yaml")
            return f"({key} link not set)"
        return links[key]

    try:
        return TOKEN_RE.sub(sub, text)
    except UnknownIdError as exc:
        raise RenderError(str(exc)) from None


MENTION_RE = re.compile(r"\{(role|channel):")


def render_embed(spec: Mapping[str, Any], ids: IdRegistry, links: Mapping[str, str],
                 warnings: list[str]) -> dict[str, Any]:
    def f(text: Any) -> str:
        return fill(str(text), ids, links, warnings)

    # Discord shows mentions as raw <@&id> text in titles, field names, footers and author names.
    plain_parts = [("title", spec.get("title")), ("footer", spec.get("footer")), ("author", spec.get("author"))]
    plain_parts += [(f"field name '{x.get('name')}'", x.get("name")) for x in spec.get("fields") or []]
    for where, value in plain_parts:
        if value is not None and MENTION_RE.search(str(value)):
            raise RenderError(f"{where}: role and channel mentions only render in descriptions and field "
                              "values, not in titles, field names, footers or author names")

    out: dict[str, Any] = {}
    if spec.get("title"):
        out["title"] = f(spec["title"])
    if spec.get("description"):
        out["description"] = f(spec["description"]).rstrip()
    if spec.get("url"):
        out["url"] = f(spec["url"])
    if spec.get("color") is not None:
        out["color"] = parse_color(spec["color"])
    if spec.get("fields"):
        out["fields"] = [{"name": f(x["name"]), "value": f(x["value"]).rstrip(),
                          "inline": bool(x.get("inline", False))} for x in spec["fields"]]
    footer = spec.get("footer")
    if footer:
        footer = {"text": footer} if isinstance(footer, str) else dict(footer)
        out["footer"] = {k: f(v) for k, v in footer.items()}
    author = spec.get("author")
    if author:
        author = {"name": author} if isinstance(author, str) else dict(author)
        out["author"] = {k: f(v) for k, v in author.items()}
    for key in ("image", "thumbnail"):
        if spec.get(key):
            out[key] = {"url": f(spec[key])}
    return out


def render_set(es: EmbedSet, ids: IdRegistry, links: Mapping[str, str]) -> Rendered:
    warnings: list[str] = []
    try:
        channel_id = ids.channel(es.channel)
    except UnknownIdError as exc:
        raise RenderError(f"{es.key}: {exc}") from None

    messages: list[dict[str, Any]] = []
    problems: list[str] = []
    for i, msg in enumerate(es.messages):
        try:
            rendered = {
                "content": fill(msg.content, ids, links, warnings) if msg.content else None,
                "embeds": [render_embed(e, ids, links, warnings) for e in msg.embeds],
            }
            if msg.buttons:  # only present when used, so older messages keep their content hash
                rendered["buttons"] = [{"custom_id": ACTIONS[b["action"]], "label": b["label"], "style": b["style"]}
                                       for b in msg.buttons]
        except RenderError as exc:
            raise RenderError(f"{es.key}: message {i + 1}: {exc}") from None
        problems += [f"message {i + 1}: {p}" for p in message_problems(rendered)]
        messages.append(rendered)
    if problems:
        raise RenderError(f"{es.key}: " + "; ".join(problems))
    return Rendered(key=es.key, channel_id=channel_id, messages=messages, warnings=warnings)
