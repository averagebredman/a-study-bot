"""Async SQLite storage with short-lived connections per operation."""

from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS topic_weakness (
    user_id INTEGER NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    topic_name TEXT NOT NULL,
    mistake_count INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, subject, topic_name),
    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS quiz_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    topic_name TEXT NOT NULL,
    question_type TEXT NOT NULL CHECK (question_type IN ('mcq', 'short')),
    is_correct INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
);
"""


class Database:
    def __init__(self, path: str | pathlib.Path) -> None:
        self.path = str(path)

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        conn = await aiosqlite.connect(self.path)
        try:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA busy_timeout = 5000")
            await conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        finally:
            await conn.close()

    async def init(self) -> None:
        pathlib.Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as conn:
            cursor = await conn.execute("PRAGMA journal_mode = WAL")
            await cursor.fetchone()
            await conn.executescript(_SCHEMA)
            await self._migrate(conn)

    @staticmethod
    async def _migrate(conn: aiosqlite.Connection) -> None:
        """Add per-subject storage to databases created before subject support."""
        rows = await conn.execute_fetchall("PRAGMA table_info(topic_weakness)")
        if any(row["name"] == "subject" for row in rows):
            return
        await conn.executescript(
            "ALTER TABLE topic_weakness RENAME TO topic_weakness_old; "
            "ALTER TABLE quiz_history RENAME TO quiz_history_old;"
        )
        await conn.executescript(_SCHEMA)
        await conn.executescript(
            "INSERT INTO topic_weakness "
            "(user_id, subject, topic_name, mistake_count, last_seen_at) "
            "SELECT user_id, '', topic_name, mistake_count, last_seen_at "
            "FROM topic_weakness_old; "
            "INSERT INTO quiz_history "
            "(id, user_id, subject, topic_name, question_type, is_correct, created_at) "
            "SELECT id, user_id, '', topic_name, question_type, is_correct, created_at "
            "FROM quiz_history_old; "
            "DROP TABLE topic_weakness_old; "
            "DROP TABLE quiz_history_old;"
        )

    async def ensure_user(self, user_id: int, username: str) -> None:
        async with self._connect() as conn:
            await conn.execute(
                "INSERT INTO users (user_id, username) VALUES (?, ?) "
                "ON CONFLICT (user_id) DO UPDATE SET username = excluded.username",
                (user_id, username),
            )
            await conn.commit()

    async def record_analyzed_topics(
        self, user_id: int, subject: str, topics: list[str]
    ) -> None:
        async with self._connect() as conn:
            for topic_name in topics:
                name = topic_name.strip()
                if not name:
                    continue
                await conn.execute(
                    "INSERT INTO topic_weakness (user_id, subject, topic_name) "
                    "VALUES (?, ?, ?) "
                    "ON CONFLICT (user_id, subject, topic_name) DO UPDATE SET "
                    "mistake_count = mistake_count + 1, "
                    "last_seen_at = datetime('now')",
                    (user_id, subject, name),
                )
            await conn.commit()

    async def get_weak_topics(
        self, user_id: int, subject: str, limit: int
    ) -> list[str]:
        async with self._connect() as conn:
            rows = await conn.execute_fetchall(
                "SELECT topic_name FROM topic_weakness "
                "WHERE user_id = ? AND subject = ? "
                "ORDER BY mistake_count DESC, last_seen_at DESC "
                "LIMIT ?",
                (user_id, subject, limit),
            )
        return [row["topic_name"] for row in rows]

    async def get_latest_subject(self, user_id: int) -> str | None:
        async with self._connect() as conn:
            rows = await conn.execute_fetchall(
                "SELECT subject FROM topic_weakness "
                "WHERE user_id = ? "
                "ORDER BY last_seen_at DESC, rowid DESC LIMIT 1",
                (user_id,),
            )
        return rows[0]["subject"] if rows else None

    async def record_quiz_answer(
        self,
        user_id: int,
        subject: str,
        topic_name: str,
        question_type: str,
        is_correct: bool,
    ) -> None:
        async with self._connect() as conn:
            await conn.execute(
                "INSERT INTO quiz_history "
                "(user_id, subject, topic_name, question_type, is_correct) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, subject, topic_name, question_type, int(is_correct)),
            )
            if is_correct:
                await conn.execute(
                    "INSERT OR IGNORE INTO topic_weakness "
                    "(user_id, subject, topic_name, mistake_count) "
                    "VALUES (?, ?, ?, 0)",
                    (user_id, subject, topic_name),
                )
            else:
                await conn.execute(
                    "INSERT INTO topic_weakness (user_id, subject, topic_name) "
                    "VALUES (?, ?, ?) "
                    "ON CONFLICT (user_id, subject, topic_name) DO UPDATE SET "
                    "mistake_count = mistake_count + 1, "
                    "last_seen_at = datetime('now')",
                    (user_id, subject, topic_name),
                )
            await conn.commit()

    async def get_topic_stats(
        self, user_id: int, subject: str
    ) -> list[dict[str, Any]]:
        async with self._connect() as conn:
            rows = await conn.execute_fetchall(
                "SELECT tw.topic_name AS topic_name, "
                "tw.mistake_count AS mistake_count, "
                "COUNT(qh.id) AS attempts, "
                "COALESCE(SUM(qh.is_correct), 0) AS correct "
                "FROM topic_weakness tw "
                "LEFT JOIN quiz_history qh "
                "ON qh.user_id = tw.user_id AND qh.subject = tw.subject "
                "AND qh.topic_name = tw.topic_name "
                "WHERE tw.user_id = ? AND tw.subject = ? "
                "GROUP BY tw.topic_name, tw.mistake_count "
                "ORDER BY tw.mistake_count DESC, tw.last_seen_at DESC",
                (user_id, subject),
            )
        return [dict(row) for row in rows]

    async def get_subject_summary(self, user_id: int) -> list[dict[str, Any]]:
        async with self._connect() as conn:
            rows = await conn.execute_fetchall(
                "SELECT tw.subject AS subject, "
                "COUNT(DISTINCT tw.topic_name) AS topic_count, "
                "COALESCE(SUM(tw.mistake_count), 0) AS mistake_total, "
                "COUNT(qh.id) AS attempts, "
                "COALESCE(SUM(qh.is_correct), 0) AS correct "
                "FROM topic_weakness tw "
                "LEFT JOIN quiz_history qh "
                "ON qh.user_id = tw.user_id AND qh.subject = tw.subject "
                "AND qh.topic_name = tw.topic_name "
                "WHERE tw.user_id = ? "
                "GROUP BY tw.subject "
                "ORDER BY tw.subject",
                (user_id,),
            )
        return [dict(row) for row in rows]
