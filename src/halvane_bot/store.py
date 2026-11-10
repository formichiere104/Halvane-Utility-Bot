"""SQLite storage for the messages the bot manages."""
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
"""


@dataclass(frozen=True)
class StoredEmbed:
    key: str
    channel_id: int
    message_ids: tuple[int, ...]
    content_hash: str
    updated_at: str


class EmbedStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("EmbedStore is not open")
        return self._db

    async def get(self, key: str) -> StoredEmbed | None:
        async with self.db.execute(
            "SELECT key, channel_id, message_ids, content_hash, updated_at FROM embed_messages WHERE key = ?",
            (key,),
        ) as cur:
            row = await cur.fetchone()
        return _row(row) if row else None

    async def all(self) -> dict[str, StoredEmbed]:
        async with self.db.execute(
            "SELECT key, channel_id, message_ids, content_hash, updated_at FROM embed_messages"
        ) as cur:
            rows = await cur.fetchall()
        return {r[0]: _row(r) for r in rows}

    async def save(self, key: str, channel_id: int, message_ids: list[int], content_hash: str) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        await self.db.execute(
            "INSERT INTO embed_messages (key, channel_id, message_ids, content_hash, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
            "channel_id = excluded.channel_id, message_ids = excluded.message_ids, "
            "content_hash = excluded.content_hash, updated_at = excluded.updated_at",
            (key, channel_id, json.dumps([int(m) for m in message_ids]), content_hash, now),
        )
        await self.db.commit()


def _row(row) -> StoredEmbed:
    return StoredEmbed(row[0], int(row[1]), tuple(int(m) for m in json.loads(row[2])), row[3], row[4])
