import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException
from fastapi.testclient import TestClient
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
    def saving_throw(*, dc=13, modifier=3, ability="dexterity"):
        return api.ResolveRequest(
            action={
                "type": "saving_throw",
                "ability": ability,
                "dc": dc,
                "modifier": modifier,
            }
        )

    @staticmethod
    def attack(*, attack_bonus=5, target_ac=15, damage=None):
        action = {
            "type": "attack",
            "attack_bonus": attack_bonus,
            "target_ac": target_ac,
        }
        if damage is not None:
            action["damage"] = damage
        return api.ResolveRequest(
            action=action
        )

    @staticmethod
    def randbelow_for(d20_result):
        return lambda upper_bound: d20_result - 1

    @staticmethod
    def randbelow_sequence(*results):
        values = iter(results)
        return lambda upper_bound: next(values) - 1

    def test_explicit_ability_check_resolves_success_with_expected_contract(self):
        body = self.ability_check(dc=15, modifier=3)
        result = api.resolve_request(body, randbelow=self.randbelow_for(14))

        expected = {
            "schema_version": "rule-resolution-v1",
            "resolution_id": result["resolution_id"],
            "status": "resolved",
            "action": {"type": "ability_check", "ability": "strength"},
            "check": {"ability": "strength", "dc": 15, "modifier": 3},
            "rolls": [{"type": "d20", "result": 14}],
            "outcome": {"total": 17, "success": True},
            "rules_used": ["ability_check.mvp.v1"],
        }
        self.assertEqual(result, expected)
        self.assertEqual(str(UUID(result["resolution_id"])), result["resolution_id"])
        self.assertNotIn("facts_resolvidos", result)

    def test_resolve_endpoint_dispatches_structured_check_and_authorizes(self):
        body = self.ability_check(dc=15, modifier=3)
        with patch.object(api, "roll_dice", return_value={"rolls": [14]}) as roll:
            result = api.resolve(body, "test-secret")

        self.assertEqual(result["schema_version"], "rule-resolution-v1")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["outcome"], {"total": 17, "success": True})
        roll.assert_called_once_with("d20", randbelow=None)
        with self.assertRaises(HTTPException) as raised:
            api.resolve(body, "wrong-secret")
        self.assertEqual(raised.exception.status_code, 401)

    def test_ability_check_fails_below_dc_and_succeeds_at_exact_dc(self):
        below = api.resolve_explicit_action(
            self.ability_check(dc=15, modifier=3), randbelow=self.randbelow_for(5)
        )
        equal = api.resolve_request(
            self.ability_check(dc=15, modifier=3), randbelow=self.randbelow_for(12)
        )

        self.assertEqual(below["schema_version"], "rule-resolution-v1")
        self.assertEqual(below["outcome"], {"total": 8, "success": False})
        self.assertEqual(equal["outcome"], {"total": 15, "success": True})

    def test_ability_check_supports_negative_modifier(self):
        result = api.resolve_explicit_action(
            self.ability_check(dc=10, modifier=-2), randbelow=self.randbelow_for(11)
        )

        self.assertEqual(
            result["check"], {"ability": "strength", "dc": 10, "modifier": -2}
        )
        self.assertEqual(result["outcome"], {"total": 9, "success": False})

    def test_saving_throw_resolves_success_with_expected_contract(self):
        result = api.resolve_request(
            self.saving_throw(dc=13, modifier=3), randbelow=self.randbelow_for(10)
        )

        self.assertEqual(result["schema_version"], "rule-resolution-v1")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(
            result["action"], {"type": "saving_throw", "ability": "dexterity"}
        )
        self.assertEqual(
            result["check"], {"ability": "dexterity", "dc": 13, "modifier": 3}
        )
        self.assertEqual(result["rolls"], [{"type": "d20", "result": 10}])
        self.assertEqual(result["outcome"], {"total": 13, "success": True})
        self.assertEqual(result["rules_used"], ["saving_throw.mvp.v1"])

    def test_saving_throw_resolves_failure(self):
        result = api.resolve_request(
            self.saving_throw(dc=13, modifier=3), randbelow=self.randbelow_for(9)
        )

        self.assertEqual(result["outcome"], {"total": 12, "success": False})

    def test_saving_throw_natural_20_is_normal_result(self):
        result = api.resolve_saving_throw(
            self.saving_throw(dc=30, modifier=0), randbelow=self.randbelow_for(20)
        )

        self.assertEqual(result["rolls"], [{"type": "d20", "result": 20}])
        self.assertEqual(result["outcome"], {"total": 20, "success": False})

    def test_saving_throw_natural_1_is_normal_result(self):
        result = api.resolve_saving_throw(
            self.saving_throw(dc=1, modifier=0), randbelow=self.randbelow_for(1)
        )

        self.assertEqual(result["rolls"], [{"type": "d20", "result": 1}])
        self.assertEqual(result["outcome"], {"total": 1, "success": True})

    def test_attack_roll_hits(self):
        result = api.resolve_request(
            self.attack(), randbelow=self.randbelow_for(14)
        )

        self.assertEqual(
            result["outcome"],
            {
                "total": 19,
                "hit": True,
                "critical": False,
                "natural_1": False,
                "damage": None,
            },
        )

    def test_attack_roll_misses(self):
        result = api.resolve_request(
            self.attack(), randbelow=self.randbelow_for(7)
        )

        self.assertEqual(
            result["outcome"],
            {
                "total": 12,
                "hit": False,
                "critical": False,
                "natural_1": False,
                "damage": None,
            },
        )

    def test_attack_roll_hits_at_exact_target_ac(self):
        result = api.resolve_request(
            self.attack(), randbelow=self.randbelow_for(10)
        )

        self.assertEqual(
            result["outcome"],
            {
                "total": 15,
                "hit": True,
                "critical": False,
                "natural_1": False,
                "damage": None,
            },
        )

    def test_attack_roll_natural_1_is_automatic_miss_with_large_bonus(self):
        result = api.resolve_attack(
            self.attack(attack_bonus=20, target_ac=5),
            randbelow=self.randbelow_for(1),
        )

        self.assertEqual(result["rolls"], [{"type": "d20", "result": 1}])
        self.assertEqual(
            result["outcome"],
            {
                "total": 21,
                "hit": False,
                "critical": False,
                "natural_1": True,
                "damage": None,
            },
        )
        self.assertNotIn("damage", result)

    def test_attack_roll_natural_20_is_automatic_hit_with_impossible_ac(self):
        result = api.resolve_attack(
            self.attack(attack_bonus=-5, target_ac=30),
            randbelow=self.randbelow_for(20),
        )

        self.assertEqual(result["rolls"], [{"type": "d20", "result": 20}])
        self.assertEqual(
            result["outcome"],
            {
                "total": 15,
                "hit": True,
                "critical": True,
                "natural_1": False,
                "damage": None,
            },
        )
        self.assertNotIn("damage", result)

    def test_attack_hit_rolls_damage_and_uses_damage_rule(self):
        result = api.resolve_request(
            self.attack(damage={"dice": "1d8", "modifier": 3}),
            randbelow=self.randbelow_sequence(14, 6),
        )

        self.assertEqual(
            result["check"],
            {
                "attack_bonus": 5,
                "target_ac": 15,
                "damage": {"dice": "1d8", "modifier": 3},
            },
        )
        self.assertEqual(
            result["rolls"],
            [{"type": "d20", "result": 14}, {"type": "d8", "result": 6}],
        )
        self.assertEqual(
            result["outcome"],
            {
                "total": 19,
                "hit": True,
                "critical": False,
                "natural_1": False,
                "damage": 9,
            },
        )
        self.assertEqual(
            result["rules_used"], ["attack_roll.mvp.v1", "attack_damage.mvp.v1"]
        )

    def test_attack_miss_does_not_roll_damage(self):
        calls = []

        def randbelow(upper_bound):
            calls.append(upper_bound)
            return 7

        result = api.resolve_request(
            self.attack(damage={"dice": "1d8", "modifier": 3}),
            randbelow=randbelow,
        )

        self.assertEqual(calls, [20])
        self.assertEqual(result["rolls"], [{"type": "d20", "result": 8}])
        self.assertEqual(result["outcome"]["damage"], None)
        self.assertEqual(result["rules_used"], ["attack_roll.mvp.v1"])

    def test_attack_natural_1_does_not_roll_damage(self):
        calls = []

        def randbelow(upper_bound):
            calls.append(upper_bound)
            return 0

        result = api.resolve_request(
            self.attack(attack_bonus=20, target_ac=5, damage={"dice": "1d8", "modifier": 3}),
            randbelow=randbelow,
        )

        self.assertEqual(calls, [20])
        self.assertEqual(result["outcome"]["total"], 21)
        self.assertEqual(result["outcome"]["hit"], False)
        self.assertEqual(result["outcome"]["natural_1"], True)
        self.assertEqual(result["outcome"]["damage"], None)

    def test_attack_natural_20_rolls_single_damage_die_without_doubling(self):
        result = api.resolve_request(
            self.attack(attack_bonus=-5, target_ac=30, damage={"dice": "1d8", "modifier": 3}),
            randbelow=self.randbelow_sequence(20, 4),
        )

        self.assertEqual(result["outcome"]["total"], 15)
        self.assertEqual(result["outcome"]["hit"], True)
        self.assertEqual(result["outcome"]["critical"], True)
        self.assertEqual(result["outcome"]["natural_1"], False)
        self.assertEqual(result["outcome"]["damage"], 7)
        self.assertEqual(len(result["rolls"]), 2)

    def test_attack_damage_rejects_negative_modifier(self):
        with self.assertRaises(ValueError):
            self.attack(damage={"dice": "1d8", "modifier": -1})

    def test_attack_roll_natural_flags_are_mutually_exclusive(self):
        for d20_result in (1, 20):
            with self.subTest(d20_result=d20_result):
                result = api.resolve_attack(
                    self.attack(), randbelow=self.randbelow_for(d20_result)
                )
                self.assertNotEqual(
                    result["outcome"]["critical"], result["outcome"]["natural_1"]
                )

    def test_attack_roll_contract_and_rule_id(self):
        result = api.resolve_request(
            self.attack(), randbelow=self.randbelow_for(14)
        )

        self.assertEqual(result["schema_version"], "rule-resolution-v1")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["action"], {"type": "attack"})
        self.assertEqual(result["check"], {"attack_bonus": 5, "target_ac": 15})
        self.assertEqual(result["rules_used"], ["attack_roll.mvp.v1"])

    def test_ability_check_rng_is_injected_and_repeatable(self):
        body = self.ability_check()
        first = api.resolve_explicit_action(body, randbelow=self.randbelow_for(20))
        second = api.resolve_explicit_action(body, randbelow=self.randbelow_for(20))

        self.assertNotEqual(first["resolution_id"], second["resolution_id"])
        self.assertEqual(first["rolls"], [{"type": "d20", "result": 20}])
        self.assertEqual(first["rolls"], second["rolls"])
        self.assertEqual(first["outcome"], second["outcome"])
        self.assertEqual(len(first["rolls"]), 1)

    def test_invalid_or_incomplete_ability_checks_fail_request_validation(self):
        invalid_actions = [
            {"type": "ability_check", "ability": "strength", "modifier": 2},
            {"type": "ability_check", "ability": "strength", "dc": 12},
            {"type": "ability_check", "dc": 12, "modifier": 2},
            {"type": "saving_throw", "ability": "strength", "modifier": 2},
            {"type": "saving_throw", "ability": "dexterity", "dc": 12},
            {"type": "saving_throw", "ability": "athletics", "dc": 12, "modifier": 2},
            {"type": "attack", "target_ac": 15},
            {"type": "attack", "attack_bonus": 5},
            {"type": "attack", "attack_bonus": "5", "target_ac": 15},
            {"type": "attack", "attack_bonus": 5, "target_ac": 15, "critical": True},
            {"type": "ability_check", "ability": "athletics", "dc": 12, "modifier": 2},
            {"type": "ability_check", "ability": "strength", "dc": 0, "modifier": 2},
            {"type": "ability_check", "ability": "strength", "dc": "12", "modifier": 2},
            {
                "type": "ability_check",
                "ability": "strength",
                "dc": 12,
                "modifier": True,
            },
            {
                "type": "ability_check",
                "ability": "strength",
                "dc": 12,
                "modifier": 2,
                "advantage": True,
            },
        ]
        for action in invalid_actions:
            with self.subTest(action=action), self.assertRaises(ValidationError):
                api.ResolveRequest(action=action)

    def test_free_text_is_never_interpreted_as_an_ability_check(self):
        result = api.resolve_request(
            api.ResolveRequest(action="quero fazer um teste de Força CD 15"),
            randbelow=self.randbelow_for(20),
        )

        self.assertEqual(result["schema_version"], "rule-resolution-v1")
        self.assertEqual(result["status"], "needs_rule_validation")
        self.assertEqual(set(result), {"schema_version", "status", "reason"})

    def test_resolution_does_not_mutate_request_state(self):
        body = self.ability_check()
        body.state = {"hp": 12, "conditions": []}
        original_state = {"hp": 12, "conditions": []}

        api.resolve_request(body, randbelow=self.randbelow_for(14))

        self.assertEqual(body.state, original_state)

    def test_public_resolve_endpoint_returns_rule_resolution_contract(self):
        response = TestClient(api.app).post(
            "/v1/resolve",
            headers={"x-api-key": "test-secret"},
            json={
                "action": {
                    "type": "ability_check",
                    "ability": "strength",
                    "dc": 15,
                    "modifier": 3,
                },
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["schema_version"], "rule-resolution-v1")
        self.assertEqual(payload["status"], "resolved")
        self.assertEqual(payload["action"], {"type": "ability_check", "ability": "strength"})
        self.assertEqual(payload["check"], {"ability": "strength", "dc": 15, "modifier": 3})
        self.assertIn("outcome", payload)
        self.assertIn("rules_used", payload)

    def test_public_resolve_endpoint_rejects_invalid_request(self):
        response = TestClient(api.app).post(
            "/v1/resolve",
            headers={"x-api-key": "test-secret"},
            json={
                "action": {
                    "type": "ability_check",
                    "ability": "not-an-ability",
                    "dc": 15,
                    "modifier": 3,
                },
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_public_resolve_endpoint_returns_rule_error_without_success_payload(self):
        client = TestClient(api.app, raise_server_exceptions=False)
        response = client.post(
            "/v1/resolve",
            headers={"x-api-key": "test-secret"},
            json={
                "state": {},
                "action": {
                    "type": "ability_check",
                    "ability": "strength",
                    "dc": 15,
                    "character_id": "missing",
                },
            },
        )

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("status\": \"resolved\"", response.text)


if __name__ == "__main__":
    unittest.main()
