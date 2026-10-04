from copy import deepcopy
from unittest.mock import Mock, patch
import unittest
from uuid import UUID

from fastapi.testclient import TestClient

from game.contracts import GameTurnRequest
from game.orchestrator import GameOrchestrator
import rule_engine.app as api
from services.narrator import NarratorError


VALID_SELECTION = {
    "name": "  Aria  ",
    "class_id": "fighter",
    "level": 1,
    "abilities": {
        "strength": 15,
        "dexterity": 14,
        "constitution": 13,
        "intelligence": 12,
        "wisdom": 10,
        "charisma": 8,
    },
    "skills": ["athletics", "persuasion"],
    "weapon_id": "longsword",
}


class CharacterCreationTests(unittest.TestCase):
    def setUp(self):
        self.api_key = patch.object(api, "API_KEY", "test-backend-secret")
        self.api_key.start()
        self.addCleanup(self.api_key.stop)
        self.client = TestClient(api.app)
        self.headers = {"x-api-key": "test-backend-secret"}

    def post(self, endpoint, body=None):
        return self.client.post(
            f"/v1/character/{endpoint}",
            json=deepcopy(body if body is not None else VALID_SELECTION),
            headers=self.headers,
        )

    def test_options_are_versioned_and_define_every_selection_server_side(self):
        response = self.client.get("/v1/character/options", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        options = response.json()
        self.assertEqual(options["schema_version"], "character-options-v1")
        self.assertEqual(options["levels"], list(range(1, 21)))
        self.assertEqual(options["standard_array"], [15, 14, 13, 12, 10, 8])
        self.assertEqual([item["id"] for item in options["abilities"]], list(VALID_SELECTION["abilities"]))
        self.assertTrue(all(item["label"] and item["description"] for item in options["abilities"]))
        self.assertEqual([item["id"] for item in options["classes"]], ["fighter"])
        fighter = options["classes"][0]
        self.assertEqual(fighter["levels"], list(range(1, 21)))
        self.assertEqual(fighter["skill_choices"]["count"], 2)
        self.assertEqual(set(fighter["skill_choices"]["options"]), {
            "acrobatics", "animal_handling", "athletics", "history", "insight",
            "intimidation", "persuasion", "perception", "survival",
        })
        self.assertEqual(set(fighter["skill_choices"]["options"]), {skill["id"] for skill in options["skills"]})
        self.assertTrue(all(skill["ability"] in VALID_SELECTION["abilities"] for skill in options["skills"]))
        self.assertEqual(fighter["saving_throw_proficiencies"], ["strength", "constitution"])
        self.assertEqual(fighter["weapon_options"], ["longsword"])
        self.assertEqual([weapon["id"] for weapon in options["weapons"]], ["longsword"])
        self.assertEqual(options["weapons"][0]["damage_dice"], "1d8")
        self.assertEqual(options["selection_rules"]["ability_assignment"]["mode"], "standard_array")
        self.assertTrue(options["selection_rules"]["ability_assignment"]["use_all_values"])
        self.assertTrue(options["selection_rules"]["skills"]["unique"])
        self.assertEqual(options["selection_rules"]["weapons"]["count"], 1)

    def test_valid_selection_previews_real_derived_sheet_and_creates_session(self):
        with patch.object(api, "build_game_orchestrator") as orchestrator:
            preview_response = self.post("validate")
            creation_response = self.post("create")
        orchestrator.assert_not_called()
        self.assertEqual(preview_response.status_code, 200, preview_response.text)
        self.assertEqual(creation_response.status_code, 200, creation_response.text)
        preview = preview_response.json()
        created = creation_response.json()
        self.assertEqual(preview["schema_version"], "character-creation-v1")
        self.assertTrue(preview["valid"])
        self.assertEqual(preview["character"]["name"], "Aria")
        self.assertEqual(preview["character"]["class"], {"id": "fighter", "level": 1})
        self.assertEqual(preview["character"]["abilities"], VALID_SELECTION["abilities"])
        self.assertEqual(preview["character"]["current_hp"], 11)
        self.assertEqual(preview["character"]["proficiencies"]["skills"], {"athletics": True, "persuasion": True})
        self.assertEqual(preview["character"]["proficiencies"]["saving_throws"], {"strength": True, "constitution": True})
        self.assertEqual(preview["derived"]["ability_modifiers"], {
            "strength": 2, "dexterity": 2, "constitution": 1,
            "intelligence": 1, "wisdom": 0, "charisma": -1,
        })
        self.assertEqual(preview["derived"]["proficiency_bonus"], 2)
        self.assertEqual(preview["derived"]["skill_modifiers"]["athletics"], 4)
        self.assertEqual(preview["derived"]["skill_modifiers"]["persuasion"], 1)
        self.assertEqual(preview["derived"]["saving_throw_modifiers"]["strength"], 4)
        self.assertEqual(preview["derived"]["saving_throw_modifiers"]["constitution"], 3)
        self.assertEqual(preview["derived"]["hp"], {"current": 11, "max": 11})
        self.assertEqual(preview["derived"]["ac"], {"value": 12, "source": "unarmored"})
        self.assertEqual(preview["derived"]["initiative_modifier"], 2)
        self.assertEqual(preview["derived"]["weapons"]["longsword"], {
            "id": "longsword", "ability": "strength", "damage_dice": "1d8",
            "proficient": True, "attack_bonus": 4, "damage_modifier": 2,
        })
        self.assertEqual(preview["rule_resolution"]["schema_version"], "rule-resolution-v1")
        self.assertEqual(preview["rule_resolution"]["status"], "resolved")
        self.assertEqual(preview["rule_resolution"]["outcome"]["derived"], preview["derived"])
        self.assertEqual(created["derived"], preview["derived"])
        self.assertEqual(created["rule_resolution"]["action"]["character_id"], created["character"]["id"])
        self.assertEqual(created["state"]["character"], created["character"])
        self.assertEqual(created["state"]["scene"]["id"], "intro")
        self.assertEqual(created["state"]["encounter"]["id"], "intro-ambush")
        self.assertEqual([action["type"] for action in created["available_actions"]], ["ability_check", "start_combat"])
        self.assertEqual(str(UUID(created["campaign_id"])), created["campaign_id"])
        self.assertNotEqual(created["character"]["id"], preview["character"]["id"])

        narrator = Mock()
        narrator.narrate.return_value = "A aventura começa."
        session = GameOrchestrator(narrator, resolve_action=api.resolve_game_action)
        first_turn = session.turn(GameTurnRequest(
            campaign_id=created["campaign_id"], state=created["state"],
            available_actions=created["available_actions"], player_input="Olho ao redor.",
        ))
        self.assertEqual(first_turn.campaign_id, created["campaign_id"])
        self.assertEqual(first_turn.state, created["state"])
        self.assertEqual(first_turn.available_actions, created["available_actions"])
        self.assertEqual(first_turn.rule_resolution["schema_version"], "rule-resolution-v1")
        self.assertEqual(first_turn.rule_resolution["status"], "needs_rule_validation")
        self.assertEqual(first_turn.narration, "A aventura começa.")
        check_turn = session.turn(GameTurnRequest(
            campaign_id=created["campaign_id"], state=first_turn.state,
            player_input="Examino as pegadas.",
            action={"type": "skill_check", "skill": "perception", "dc": 10, "character_id": created["character"]["id"]},
        ))
        self.assertEqual(check_turn.rule_resolution["check"]["modifier"], 0)
        self.assertEqual(check_turn.rule_resolution["status"], "resolved")

    def test_created_fighter_keeps_mechanics_when_narrator_fails_in_combat(self):
        created = self.post("create").json()
        character_id = created["character"]["id"]
        narrator = Mock()
        narrator.narrate.side_effect = NarratorError("unavailable")
        session = GameOrchestrator(narrator, resolve_action=api.resolve_game_action)
        with patch.object(api, "roll_dice", side_effect=[{"rolls": [20]}, {"rolls": [1]}]):
            first_turn = session.turn(GameTurnRequest(
                campaign_id=created["campaign_id"], state=created["state"],
                available_actions=created["available_actions"], player_input="Começo a lutar.",
                action={"type": "start_combat", "combatants": [
                    {"id": character_id, "character": created["character"], "side": "player"},
                    {"id": "goblin", "hp": 12, "max_hp": 12, "ac": 10,
                     "initiative_modifier": 0, "side": "enemy"},
                ]},
            ))
        self.assertEqual(first_turn.narration_status, "unavailable")
        self.assertEqual(first_turn.rule_resolution["status"], "resolved")
        self.assertEqual(first_turn.state["character"], created["character"])
        self.assertEqual(first_turn.state["combat"]["current_actor_id"], character_id)
        self.assertEqual(first_turn.state["combat"]["combatants"][character_id]["hp"], 11)
        self.assertEqual(first_turn.available_actions, [{"type": "move"}, {"type": "attack"}, {"type": "second_wind"}, {"type": "end_turn"}])
        with patch.object(api, "roll_dice", side_effect=[{"rolls": [15]}, {"rolls": [5]}]):
            attack_turn = session.turn(GameTurnRequest(
                campaign_id=created["campaign_id"], state=first_turn.state,
                available_actions=first_turn.available_actions, player_input="Ataco com a espada longa.",
                action={"type": "attack", "actor_id": character_id,
                        "target_id": "goblin", "weapon_id": "longsword"},
            ))
        self.assertEqual(attack_turn.narration_status, "unavailable")
        self.assertEqual(attack_turn.rule_resolution["schema_version"], "rule-resolution-v1")
        self.assertEqual(attack_turn.rule_resolution["check"]["attack_bonus"], 4)
        self.assertEqual(attack_turn.rule_resolution["check"]["damage"], {"dice": "1d8", "modifier": 2})
        self.assertEqual(attack_turn.rule_resolution["outcome"]["damage"], 7)
        self.assertEqual(attack_turn.state["combat"]["combatants"]["goblin"]["hp"], 5)
        self.assertEqual(attack_turn.available_actions, [{"type": "move"}, {"type": "second_wind"}, {"type": "end_turn"}])

    def test_both_routes_reject_invalid_catalog_selections_and_forged_fields(self):
        cases = {
            "class": {"class_id": "wizard"},
            "level": {"level": 21},
            "wrong_ability": {"abilities": {**VALID_SELECTION["abilities"], "arcana": 15}},
            "missing_ability": {"abilities": {key: score for key, score in VALID_SELECTION["abilities"].items() if key != "wisdom"}},
            "invalid_array_duplicate": {"abilities": {**VALID_SELECTION["abilities"], "wisdom": 15}},
            "invalid_array_value": {"abilities": {**VALID_SELECTION["abilities"], "wisdom": 11}},
            "known_but_not_fighter_skill": {"skills": ["athletics", "arcana"]},
            "unknown_skill": {"skills": ["athletics", "not_a_skill"]},
            "wrong_skill_count": {"skills": ["athletics"]},
            "duplicate_skills": {"skills": ["athletics", "athletics"]},
            "invalid_weapon": {"weapon_id": "greatsword"},
            "blank_name": {"name": "  "},
            "forged_proficiency": {"proficiencies": {"saving_throws": ["dexterity"]}},
            "forged_hp": {"current_hp": 999},
            "wrong_level_type": {"level": True},
            "wrong_score_type": {"abilities": {**VALID_SELECTION["abilities"], "strength": "15"}},
        }
        for path in ("validate", "create"):
            for label, changes in cases.items():
                with self.subTest(path=path, case=label):
                    response = self.post(path, {**VALID_SELECTION, **changes})
                    self.assertEqual(response.status_code, 422, response.text)

    def test_all_character_routes_require_backend_api_key(self):
        self.assertEqual(self.client.get("/v1/character/options").status_code, 401)
        for path in ("validate", "create"):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(f"/v1/character/{path}", json=VALID_SELECTION).status_code, 401)
                self.assertEqual(self.client.post(f"/v1/character/{path}", json=VALID_SELECTION, headers={"x-api-key": "wrong"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
