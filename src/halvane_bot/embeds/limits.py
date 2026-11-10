"""Discord's message and embed size limits."""
from __future__ import annotations

from typing import Any

MAX_CONTENT = 2000
MAX_EMBEDS = 10
MAX_TOTAL_CHARS = 6000     # all embeds in one message together
MAX_TITLE = 256
MAX_DESCRIPTION = 4096
MAX_FIELDS = 25
MAX_FIELD_NAME = 256
MAX_FIELD_VALUE = 1024
MAX_FOOTER = 2048
MAX_AUTHOR = 256


def embed_chars(embed: dict[str, Any]) -> int:
    total = len(embed.get("title") or "") + len(embed.get("description") or "")
    total += len((embed.get("footer") or {}).get("text") or "")
    total += len((embed.get("author") or {}).get("name") or "")
    for f in embed.get("fields") or []:
        total += len(f.get("name") or "") + len(f.get("value") or "")
    return total


def message_problems(message: dict[str, Any]) -> list[str]:
    """Problems with one rendered message (API dict form). Empty list = fits."""
    problems: list[str] = []
    content = message.get("content") or ""
    embeds = message.get("embeds") or []
    if len(content) > MAX_CONTENT:
        problems.append(f"content is {len(content)} characters (max {MAX_CONTENT})")
    if len(embeds) > MAX_EMBEDS:
        problems.append(f"{len(embeds)} embeds (max {MAX_EMBEDS} per message)")
    total = sum(embed_chars(e) for e in embeds)
    if total > MAX_TOTAL_CHARS:
        problems.append(f"embeds total {total} characters (max {MAX_TOTAL_CHARS} per message)")
    for i, e in enumerate(embeds):
        label = f"embed {i + 1}"
        checks = [
            (len(e.get("title") or ""), MAX_TITLE, "title"),
            (len(e.get("description") or ""), MAX_DESCRIPTION, "description"),
            (len(e.get("fields") or []), MAX_FIELDS, "fields"),
            (len((e.get("footer") or {}).get("text") or ""), MAX_FOOTER, "footer"),
            (len((e.get("author") or {}).get("name") or ""), MAX_AUTHOR, "author"),
        ]
        problems += [f"{label} {name} is {n} (max {cap})" for n, cap, name in checks if n > cap]
        for j, f in enumerate(e.get("fields") or []):
            if len(f.get("name") or "") > MAX_FIELD_NAME:
                problems.append(f"{label} field {j + 1} name is too long (max {MAX_FIELD_NAME})")
            if len(f.get("value") or "") > MAX_FIELD_VALUE:
                problems.append(f"{label} field {j + 1} value is too long (max {MAX_FIELD_VALUE})")
    return problems
