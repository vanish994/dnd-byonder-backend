import sqlite3
import tempfile
import unittest
from pathlib import Path

import rule_engine.app as api
from fastapi import HTTPException


class RuleEngineApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.previous_db_path = api.DB_PATH
        self.previous_api_key = api.API_KEY
        api.DB_PATH = self.db_path
        api.API_KEY = "test-secret"
        self.addCleanup(self.restore_globals)
        self.addCleanup(self.temp_dir.cleanup)
        self.create_test_database()

    def restore_globals(self):
        api.DB_PATH = self.previous_db_path
        api.API_KEY = self.previous_api_key

    def create_test_database(self):
        with sqlite3.connect(self.db_path) as db:
            db.executescript("""
                CREATE TABLE documents (
                    doc_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    edition TEXT NOT NULL,
                    canonical_candidate INTEGER NOT NULL
                );
                CREATE TABLE chunks (
                    rowid INTEGER PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    section TEXT,
                    page INTEGER,
                    line_start INTEGER,
                    line_end INTEGER,
                    text TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE chunks_fts USING fts5(text);
            """)
            documents = [
                ("dmg-2024", "Dungeon Master's Guide 2024", "2024", 1),
                ("dmg-alt", "Dungeon Master's Guide 2024", "2024", 0),
                ("phb-2024", "Player's Handbook 2024", "2024", 1),
                ("mm-2025", "Monster Manual 2025", "2025", 0),
                ("ravenloft", "Van Richten's Guide to Ravenloft", "not explicit", 0),
                ("phb-2014", "Player's Handbook 2014", "2014", 1),
            ]
            for index, doc in enumerate(documents, start=1):
                doc_id, title, _edition, _canonical = doc
                db.execute("INSERT INTO documents VALUES (?, ?, ?, ?)", doc)
                text = "concentration test"
                db.execute(
                    "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (index, doc_id, f"chunk-{index}", title, "Test", None, 1, 1, text),
                )
                db.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)", (index, text))

    def test_dice_endpoint_returns_raw_roll_and_requires_configured_key(self):
        body = api.DiceRollRequest(expression="d20+3")
        result = api.dice_roll(body, "test-secret")
        self.assertEqual(result["expression"], "1d20+3")
        self.assertEqual(result["total"], result["rolls"][0] + 3)
        self.assertNotIn("success", result)
        self.assertNotIn("damage", result)
        with self.assertRaises(HTTPException) as raised:
            api.dice_roll(body, "wrong-secret")
        self.assertEqual(raised.exception.status_code, 401)

    def test_search_returns_only_strict_editions_and_canonical_duplicate(self):
        result = api.search(api.SearchRequest(query="concentration", limit=30), "test-secret")
        source_ids = {item["source_id"] for item in result["results"]}
        editions = {item["edition"] for item in result["results"]}
        self.assertEqual(source_ids, {"dmg-2024", "phb-2024", "mm-2025"})
        self.assertEqual(editions, {"2024", "2025"})
        self.assertIn("explicit 2024/2025 only", result["source_policy"])

    def test_search_cannot_be_expanded_to_2014(self):
        result = api.search(
            api.SearchRequest(query="concentration", edition="2014", limit=30),
            "test-secret",
        )
        self.assertEqual(result["count"], 0)

    def test_health_reports_live_search_scope(self):
        result = api.health()
        self.assertEqual(result["edition_scope"], ["2024", "2025"])
        self.assertEqual(result["documents"], 6)


if __name__ == "__main__":
    unittest.main()
