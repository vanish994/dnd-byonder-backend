import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

import rule_engine.app as api
from rule_engine.character import Character, derive_character
from rule_engine.progression import (
    XP_BY_LEVEL,
    experience_for_level,
    level_for_experience,
    proficiency_bonus_for_level,
)


CHARACTER = {
    "id": "player",
    "level": 1,
    "experience_points": 0,
    "class": {"id": "fighter", "level": 1},
    "abilities": {
        "strength": 16,
        "dexterity": 14,
        "constitution": 14,
        "intelligence": 10,
        "wisdom": 12,
        "charisma": 8,
    },
    "proficiencies": {
        "skills": ["athletics"],
        "saving_throws": ["strength", "constitution"],
    },
    "weapons": {
        "longsword": {
            "id": "longsword",
            "kind": "weapon",
            "slot": "weapon",
            "ability": "strength",
            "damage_dice": "1d8",
            "damage_type": "slashing",
            "proficient": True,
        },
    },
    "current_hp": 12,
}


class ProgressionTests(unittest.TestCase):
    def setUp(self):
        self.state = {"character": deepcopy(CHARACTER)}

    @staticmethod
    def sequence(*values):
        values = iter(values)
        return lambda upper_bound: next(values) - 1

    def apply(self, action, state=None, *, randbelow=None):
        target = self.state if state is None else state
        body = api.ResolveRequest(action=action, state=target)
        result = api.resolve_request(body, randbelow=randbelow or self.sequence(20, 4, 6, 3))
        target.clear()
        target.update(body.state)
        return result

    def test_official_xp_table_and_proficiency_are_total_level_derived(self):
        self.assertEqual(XP_BY_LEVEL[1], 0)
        self.assertEqual(XP_BY_LEVEL[2], 300)
        self.assertEqual(XP_BY_LEVEL[20], 355000)
        self.assertEqual(level_for_experience(299), 1)
        self.assertEqual(level_for_experience(300), 2)
        self.assertEqual(level_for_experience(355000), 20)
        self.assertEqual(experience_for_level(5), 6500)
        self.assertEqual({level: proficiency_bonus_for_level(level) for level in (1, 5, 9, 13, 17)},
                         {1: 2, 5: 3, 9: 4, 13: 5, 17: 6})

    def test_invalid_xp_and_level_are_rejected(self):
        with self.assertRaises(ValueError):
            level_for_experience(-1)
        with self.assertRaises(ValueError):
            proficiency_bonus_for_level(21)
        with self.assertRaises(ValidationError):
            Character.model_validate({**CHARACTER, "experience_points": -1})
        with self.assertRaises(ValidationError):
            api.ResolveRequest(action={"type": "add_experience", "amount": 0}, state=self.state)

    def test_guided_creation_builds_a_supported_higher_level_fighter(self):
        selection = api.GuidedCharacterRequest(
            name="Veteran",
            class_id="fighter",
            level=5,
            abilities={
                "strength": 15, "dexterity": 14, "constitution": 13,
                "intelligence": 12, "wisdom": 10, "charisma": 8,
            },
            skills=["athletics", "perception"],
            weapon_id="longsword",
        )
        character = api.build_guided_character(selection)
        derived = character.derived()
        self.assertEqual(character.experience_points, 6500)
        self.assertEqual(derived["hp"], {"current": 39, "max": 39})
        self.assertEqual(derived["proficiency_bonus"], 3)
        self.assertIn("extra_attack", character.class_features)
        self.assertEqual(character.resources["second_wind"]["maximum"], 3)

    def test_add_experience_is_atomic_and_reports_available_level_up(self):
        result = self.apply({"type": "add_experience", "amount": 300})
        self.assertEqual(result["outcome"]["experience_points"], 300)
        self.assertEqual(result["outcome"]["level"], 1)
        self.assertTrue(result["outcome"]["level_up_available"])
        self.assertEqual(self.state["character"]["level"], 1)

        before = deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "not found"):
            self.apply({"type": "add_experience", "amount": 10, "character_id": "missing"})
        self.assertEqual(self.state, before)

    def test_level_up_updates_level_hp_features_proficiency_and_class_resource(self):
        self.state["character"]["experience_points"] = 300
        result = self.apply({"type": "level_up"})
        character = self.state["character"]
        self.assertEqual(character["level"], 2)
        self.assertEqual(character["class"]["level"], 2)
        self.assertEqual(character["current_hp"], 20)
        self.assertEqual(result["outcome"]["hp_gain"], 8)
        self.assertIn("action_surge", character["class_features"])
        self.assertEqual(character["resources"]["second_wind"], {
            "id": "second_wind", "current": 2, "maximum": 2, "recovery": "short_rest",
        })
        self.assertEqual(derive_character(character)[1]["proficiency_bonus"], 2)

        self.state["character"]["experience_points"] = 2700
        self.apply({"type": "level_up"})
        self.assertEqual(self.state["character"]["level"], 3)

    def test_level_up_increases_second_wind_at_official_fighter_threshold(self):
        state = {"character": deepcopy(CHARACTER)}
        state["character"].update({
            "level": 3,
            "experience_points": 2700,
            "class": {"id": "fighter", "level": 3},
            "class_features": ["fighting_style", "second_wind", "weapon_mastery", "action_surge", "tactical_mind", "fighter_subclass"],
            "resources": {"second_wind": {"id": "second_wind", "current": 2, "maximum": 2, "recovery": "short_rest"}},
            "current_hp": 28,
        })
        self.apply({"type": "level_up"}, state=state)
        self.assertEqual(state["character"]["level"], 4)
        self.assertEqual(state["character"]["resources"]["second_wind"]["maximum"], 3)
        self.assertEqual(state["character"]["resources"]["second_wind"]["current"], 3)

    def test_level_up_keeps_injured_current_hp_but_updates_maximum(self):
        self.state["character"].update({"experience_points": 300, "current_hp": 4})
        self.apply({"type": "level_up"})
        self.assertEqual(self.state["character"]["current_hp"], 4)
        self.assertEqual(derive_character(self.state["character"])[1]["hp"]["max"], 20)

    def test_failed_level_up_restores_exact_original_state(self):
        self.state["character"]["experience_points"] = 300
        before = deepcopy(self.state)
        with patch.object(api, "class_resource_maximum", side_effect=ValueError("forced class resource failure")):
            with self.assertRaisesRegex(ValueError, "forced"):
                self.apply({"type": "level_up"})
        self.assertEqual(self.state, before)

    def test_level_up_round_trips_through_json_and_combat_uses_new_proficiency(self):
        self.state["character"].update({"level": 4, "experience_points": 6500, "class": {"id": "fighter", "level": 4}, "current_hp": 36})
        self.state = json.loads(json.dumps(self.state))
        self.apply({"type": "level_up"})
        character = self.state["character"]
        self.assertEqual(character["level"], 5)
        self.assertEqual(derive_character(character)[1]["proficiency_bonus"], 3)

        self.apply({"type": "start_combat", "combatants": [
            {"id": "player", "character": character, "side": "player"},
            {"id": "enemy", "hp": 20, "max_hp": 20, "ac": 10, "initiative_modifier": -20, "side": "enemy"},
        ], "encounter_id": None})
        result = self.apply({"type": "attack", "actor_id": "player", "target_id": "enemy", "weapon_id": "longsword"}, randbelow=self.sequence(20, 4, 6))
        self.assertEqual(result["check"]["attack_bonus"], 6)
        self.assertEqual(result["check"]["damage"]["modifier"], 3)
        json.dumps(self.state)

    def test_second_wind_uses_class_resource_and_heals_in_combat(self):
        character = api.build_guided_character(api.GuidedCharacterRequest(
            name="Wounded", class_id="fighter", level=1,
            abilities={
                "strength": 15, "dexterity": 14, "constitution": 13,
                "intelligence": 12, "wisdom": 10, "charisma": 8,
            },
            skills=["athletics", "perception"], weapon_id="longsword",
        ))
        character_state = api.character_to_state(character)
        character_state["current_hp"] = 3
        state = {"character": character_state}
        self.apply({"type": "start_combat", "combatants": [
            {"id": "player", "character": character_state, "side": "player"},
            {"id": "enemy", "hp": 10, "max_hp": 10, "ac": 10, "initiative_modifier": -20, "side": "enemy"},
        ]}, state=state, randbelow=self.sequence(20, 1))
        result = self.apply({"type": "second_wind", "actor_id": "player"}, state=state, randbelow=self.sequence(9))
        self.assertEqual(result["outcome"]["healing"], 8)
        self.assertEqual(state["combat"]["combatants"]["player"]["hp"], 11)
        self.assertEqual(state["combat"]["combatants"]["player"]["character"]["resources"]["second_wind"]["current"], 1)

    def test_public_http_accepts_experience_and_level_up_without_changing_resolution_schema(self):
        previous_key = api.API_KEY
        api.API_KEY = "r5-secret"
        self.addCleanup(setattr, api, "API_KEY", previous_key)
        client = TestClient(api.app)
        response = client.post("/v1/resolve", headers={"x-api-key": "r5-secret"}, json={
            "action": {"type": "add_experience", "amount": 300},
            "state": {"character": CHARACTER},
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["schema_version"], "rule-resolution-v1")
        self.assertTrue(response.json()["outcome"]["level_up_available"])


if __name__ == "__main__":
    unittest.main()
