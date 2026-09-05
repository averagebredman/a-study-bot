"""Offline checks: DB logic and AI JSON parsing. No network or Discord needed."""

from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from services.ai_client import AIError, extract_json
from services.database import Database
from utils.subjects import normalize_subject


class JsonExtractionTest(unittest.TestCase):
    def test_fenced_array(self) -> None:
        raw = 'Sure! ```json\n[{"type": "mcq"}]\n```'
        self.assertEqual(extract_json(raw), [{"type": "mcq"}])

    def test_object_after_prose(self) -> None:
        raw = (
            'Here you go: {"subject": "ICT", "weak_topics": []}. '
            "Hope that helps!"
        )
        self.assertEqual(extract_json(raw)["subject"], "ICT")

    def test_braces_inside_strings(self) -> None:
        raw = '[{"question": "Is {x} > 1?", "options": ["yes", "no"]}]'
        self.assertEqual(extract_json(raw)[0]["options"][0], "yes")

    def test_missing_json_raises(self) -> None:
        with self.assertRaises(AIError):
            extract_json("I am sorry, I cannot do that.")


class SubjectNormalizationTest(unittest.TestCase):
    def test_canonical_subjects(self) -> None:
        self.assertEqual(
            normalize_subject("HKDSE Information and Communication Technology"),
            "ICT",
        )
        self.assertEqual(normalize_subject("DSE Physics Paper 1"), "Physics")
        self.assertEqual(
            normalize_subject("Mathematics Extended Part Module 2"),
            "M2",
        )
        self.assertEqual(normalize_subject("HKDSE Mathematics Compulsory"), "Math")


class DatabaseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmpdir.name) / "bot.db")
        await self.db.init()

    async def asyncTearDown(self) -> None:
        self._tmpdir.cleanup()

    async def test_weak_topics_and_history(self) -> None:
        await self.db.ensure_user(42, "alice")
        await self.db.record_analyzed_topics(42, "ICT", ["Logic gates", "SQL"])
        await self.db.record_analyzed_topics(42, "ICT", ["Logic gates"])
        topics = await self.db.get_weak_topics(42, "ICT", 10)
        self.assertEqual(topics, ["Logic gates", "SQL"])

        await self.db.record_quiz_answer(42, "ICT", "Logic gates", "mcq", True)
        await self.db.record_quiz_answer(42, "ICT", "Logic gates", "short", False)
        stats = {
            row["topic_name"]: row
            for row in await self.db.get_topic_stats(42, "ICT")
        }
        self.assertEqual(stats["Logic gates"]["mistake_count"], 3)
        self.assertEqual(stats["Logic gates"]["attempts"], 2)
        self.assertEqual(stats["Logic gates"]["correct"], 1)

    async def test_weak_topic_limit(self) -> None:
        await self.db.ensure_user(7, "bob")
        await self.db.record_analyzed_topics(7, "Physics", ["B"])
        await self.db.record_analyzed_topics(7, "Physics", ["A"])
        await self.db.record_analyzed_topics(7, "Physics", ["A"])
        self.assertEqual(await self.db.get_weak_topics(7, "Physics", 1), ["A"])

    async def test_subjects_stay_separate(self) -> None:
        await self.db.ensure_user(3, "charlie")
        await self.db.record_analyzed_topics(3, "ICT", ["Databases"])
        await self.db.record_analyzed_topics(3, "Math", ["Databases"])
        self.assertEqual(
            await self.db.get_weak_topics(3, "ICT", 10), ["Databases"]
        )
        self.assertEqual(
            await self.db.get_weak_topics(3, "Math", 10), ["Databases"]
        )
        summary = {
            row["subject"]: row for row in await self.db.get_subject_summary(3)
        }
        self.assertEqual(set(summary), {"ICT", "Math"})
        self.assertEqual(summary["ICT"]["mistake_total"], 1)
        self.assertEqual(
            await self.db.get_latest_subject(3), "Math"
        )

    async def test_migrates_legacy_schema(self) -> None:
        path = Path(self._tmpdir.name) / "legacy.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                first_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE topic_weakness (
                user_id INTEGER NOT NULL,
                topic_name TEXT NOT NULL,
                mistake_count INTEGER NOT NULL DEFAULT 1,
                last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, topic_name)
            );
            CREATE TABLE quiz_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                topic_name TEXT NOT NULL,
                question_type TEXT NOT NULL,
                is_correct INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO users (user_id, username) VALUES (1, 'legacy');
            INSERT INTO topic_weakness (user_id, topic_name) VALUES (1, 'SQL');
            INSERT INTO quiz_history (user_id, topic_name, question_type, is_correct)
                VALUES (1, 'SQL', 'short', 0);
            """
        )
        conn.commit()
        conn.close()

        db = Database(path)
        await db.init()
        summary = await db.get_subject_summary(1)
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["subject"], "")
        self.assertEqual(summary[0]["attempts"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
