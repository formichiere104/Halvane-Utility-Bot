"""SQLite storage: one database file, one small class per table group."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS embed_messages (
    key          TEXT PRIMARY KEY,
    channel_id   INTEGER NOT NULL,
    message_ids  TEXT    NOT NULL,   -- JSON list, in display order
    content_hash TEXT    NOT NULL,   -- hash of what was last published ('' = adopted, never published)
    updated_at   TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
    discord_id   INTEGER PRIMARY KEY,
    roblox_id    INTEGER NOT NULL UNIQUE,  -- one Discord member per Roblox account
    roblox_name  TEXT    NOT NULL,
    linked_at    TEXT    NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not open")
        return self._conn


# ---- managed messages ------------------------------------------------------------------------------

@dataclass(frozen=True)
class StoredEmbed:
    key: str
    channel_id: int
    message_ids: tuple[int, ...]
    content_hash: str
    updated_at: str


class EmbedStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str) -> StoredEmbed | None:
        async with self._db.conn.execute(
            "SELECT key, channel_id, message_ids, content_hash, updated_at FROM embed_messages WHERE key = ?",
            (key,),
        ) as cur:
            row = await cur.fetchone()
        return _embed_row(row) if row else None

    async def all(self) -> dict[str, StoredEmbed]:
        async with self._db.conn.execute(
            "SELECT key, channel_id, message_ids, content_hash, updated_at FROM embed_messages"
        ) as cur:
            rows = await cur.fetchall()
        return {r[0]: _embed_row(r) for r in rows}

    async def save(self, key: str, channel_id: int, message_ids: list[int], content_hash: str) -> None:
        await self._db.conn.execute(
            "INSERT INTO embed_messages (key, channel_id, message_ids, content_hash, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
            "channel_id = excluded.channel_id, message_ids = excluded.message_ids, "
            "content_hash = excluded.content_hash, updated_at = excluded.updated_at",
            (key, channel_id, json.dumps([int(m) for m in message_ids]), content_hash, _now()),
        )
        await self._db.conn.commit()


def _embed_row(row) -> StoredEmbed:
    return StoredEmbed(row[0], int(row[1]), tuple(int(m) for m in json.loads(row[2])), row[3], row[4])


# ---- account links -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Link:
    discord_id: int
    roblox_id: int
    roblox_name: str
    linked_at: str


class LinkStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def by_discord(self, discord_id: int) -> Link | None:
        return await self._one("SELECT discord_id, roblox_id, roblox_name, linked_at FROM links WHERE discord_id = ?",
                               discord_id)

    async def by_roblox(self, roblox_id: int) -> Link | None:
        return await self._one("SELECT discord_id, roblox_id, roblox_name, linked_at FROM links WHERE roblox_id = ?",
                               roblox_id)

    async def all(self) -> list[Link]:
        async with self._db.conn.execute("SELECT discord_id, roblox_id, roblox_name, linked_at FROM links") as cur:
            return [Link(int(r[0]), int(r[1]), r[2], r[3]) for r in await cur.fetchall()]

    async def link(self, discord_id: int, roblox_id: int, roblox_name: str) -> int | None:
        """Link the two accounts. Returns the Discord id that held this Roblox account before, if it moved."""
        previous = await self.by_roblox(roblox_id)
        await self._db.conn.execute("DELETE FROM links WHERE roblox_id = ? OR discord_id = ?", (roblox_id, discord_id))
        await self._db.conn.execute(
            "INSERT INTO links (discord_id, roblox_id, roblox_name, linked_at) VALUES (?, ?, ?, ?)",
            (discord_id, roblox_id, roblox_name, _now()),
        )
        await self._db.conn.commit()
        return previous.discord_id if previous and previous.discord_id != discord_id else None

    async def unlink(self, discord_id: int) -> bool:
        cur = await self._db.conn.execute("DELETE FROM links WHERE discord_id = ?", (discord_id,))
        await self._db.conn.commit()
        return (cur.rowcount or 0) > 0

    async def _one(self, sql: str, value: int) -> Link | None:
        async with self._db.conn.execute(sql, (value,)) as cur:
            r = await cur.fetchone()
        return Link(int(r[0]), int(r[1]), r[2], r[3]) if r else None
