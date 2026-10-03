import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

import rule_engine.app as api


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

    @staticmethod
    def ability_check(*, dc=15, modifier=3, ability="strength"):
        return api.ResolveRequest(
            action={
                "type": "ability_check",
                "ability": ability,
                "dc": dc,
                "modifier": modifier,
            }
        )

    @staticmethod
    def randbelow_for(d20_result):
        return lambda upper_bound: d20_result - 1

    def test_explicit_ability_check_resolves_success_with_expected_contract(self):
        body = self.ability_check(dc=15, modifier=3)
        result = api.resolve_request(body, randbelow=self.randbelow_for(14))

        expected = {
            "status": "resolved",
            "action": {"type": "ability_check", "ability": "strength"},
            "check": {"dc": 15, "modifier": 3},
            "rolls": [{"type": "d20", "result": 14}],
            "outcome": {"total": 17, "success": True},
            "rule_id": api.ABILITY_CHECK_RULE_ID,
        }
        self.assertEqual({key: result[key] for key in expected}, expected)
        self.assertEqual(result["facts_resolvidos"], expected)
        self.assertNotIn("damage", result)
        self.assertNotIn("conditions_applied", result)

    def test_resolve_endpoint_dispatches_structured_check_and_authorizes(self):
        body = self.ability_check(dc=15, modifier=3)
        with patch.object(api, "roll_dice", return_value={"rolls": [14]}) as roll:
            result = api.resolve(body, "test-secret")

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["outcome"], {"total": 17, "success": True})
        roll.assert_called_once_with("d20", randbelow=None)
        with self.assertRaises(HTTPException) as raised:
            api.resolve(body, "wrong-secret")
        self.assertEqual(raised.exception.status_code, 401)

    def test_ability_check_fails_below_dc_and_succeeds_at_exact_dc(self):
        below = api.resolve_explicit_action(self.ability_check(dc=15, modifier=3), randbelow=self.randbelow_for(11))
        equal = api.resolve_request(self.ability_check(dc=15, modifier=3), randbelow=self.randbelow_for(12))

        self.assertEqual(below["outcome"], {"total": 14, "success": False})
        self.assertEqual(equal["outcome"], {"total": 15, "success": True})

    def test_ability_check_supports_negative_modifier(self):
        result = api.resolve_explicit_action(self.ability_check(dc=10, modifier=-2), randbelow=self.randbelow_for(11))

        self.assertEqual(result["check"]["modifier"], -2)
        self.assertEqual(result["outcome"], {"total": 9, "success": False})

    def test_ability_check_rng_is_injected_and_repeatable(self):
        body = self.ability_check()
        first = api.resolve_explicit_action(body, randbelow=self.randbelow_for(20))
        second = api.resolve_explicit_action(body, randbelow=self.randbelow_for(20))

        self.assertEqual(first, second)
        self.assertEqual(first["rolls"], [{"type": "d20", "result": 20}])

    def test_invalid_or_incomplete_ability_checks_fail_request_validation(self):
        invalid_actions = [
            {"type": "ability_check", "ability": "strength", "modifier": 2},
            {"type": "ability_check", "ability": "strength", "dc": 12},
            {"type": "ability_check", "dc": 12, "modifier": 2},
            {"type": "saving_throw", "ability": "strength", "dc": 12, "modifier": 2},
            {"type": "ability_check", "ability": "athletics", "dc": 12, "modifier": 2},
            {"type": "ability_check", "ability": "strength", "dc": 0, "modifier": 2},
            {"type": "ability_check", "ability": "strength", "dc": "12", "modifier": 2},
            {"type": "ability_check", "ability": "strength", "dc": 12, "modifier": True},
            {"type": "ability_check", "ability": "strength", "dc": 12, "modifier": 2, "advantage": True},
        ]
        for action in invalid_actions:
            with self.subTest(action=action), self.assertRaises(ValidationError):
                api.ResolveRequest(action=action)

    def test_free_text_is_never_interpreted_as_an_ability_check(self):
        result = api.resolve_request(
            api.ResolveRequest(action="quero fazer um teste de Força CD 15"),
            randbelow=self.randbelow_for(20),
        )

        self.assertEqual(result["status"], "needs_rule_validation")
        self.assertEqual(result["facts_resolvidos"], {})

    def test_resolution_does_not_mutate_request_state(self):
        body = self.ability_check()
        body.state = {"hp": 12, "conditions": []}
        original_state = {"hp": 12, "conditions": []}

        api.resolve_request(body, randbelow=self.randbelow_for(14))

        self.assertEqual(body.state, original_state)


if __name__ == "__main__":
    unittest.main()
