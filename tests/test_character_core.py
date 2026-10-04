import unittest

from pydantic import ValidationError

import rule_engine.app as api
from rule_engine.character import Character, ability_modifier, proficiency_bonus


CHARACTER = {
    "id": "player",
    "level": 1,
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
}


class CharacterCoreTests(unittest.TestCase):
    def seq(self, *values):
        values = iter(values)
        return lambda upper_bound: next(values) - 1

    def apply(self, state, action, *, randbelow=None):
        body = api.ResolveRequest(action=action, state=state)
        result = api.resolve_request(body, randbelow=randbelow)
        state.clear()
        state.update(body.state)
        return result

    def test_ability_modifier_uses_generic_floor_formula(self):
        expected = {8: -1, 9: -1, 10: 0, 11: 0, 12: 1, 13: 1, 14: 2, 15: 2, 16: 3, 18: 4, 20: 5}
        for score, modifier in expected.items():
            with self.subTest(score=score):
                self.assertEqual(ability_modifier(score), modifier)

    def test_proficiency_bonus_boundaries(self):
        expected = {1: 2, 4: 2, 5: 3, 8: 3, 9: 4, 12: 4, 13: 5, 16: 5, 17: 6, 20: 6}
        for level, bonus in expected.items():
            with self.subTest(level=level):
                self.assertEqual(proficiency_bonus(level), bonus)

    def test_character_derives_expected_foundation(self):
        character = Character.model_validate(CHARACTER)
        derived = character.derived()
        self.assertEqual(derived["ability_modifiers"], {
            "strength": 3, "dexterity": 2, "constitution": 2,
            "intelligence": 0, "wisdom": 1, "charisma": -1,
        })
        self.assertEqual(derived["proficiency_bonus"], 2)
        self.assertEqual(derived["skill_modifiers"]["athletics"], 5)
        self.assertEqual(derived["skill_modifiers"]["stealth"], 2)
        self.assertEqual(derived["saving_throw_modifiers"]["strength"], 5)
        self.assertEqual(derived["saving_throw_modifiers"]["dexterity"], 2)
        self.assertEqual(derived["hp"], {"current": 12, "max": 12})
        self.assertEqual(derived["ac"], {"value": 12, "source": "unarmored"})
        self.assertEqual(derived["initiative_modifier"], 2)

    def test_character_validation_rejects_invalid_inputs(self):
        cases = [
            ({**CHARACTER, "level": 0}, "level"),
            ({**CHARACTER, "class": {"id": "wizard", "level": 1}}, "class"),
            ({**CHARACTER, "abilities": {**CHARACTER["abilities"], "strength": 31}}, "ability"),
            ({**CHARACTER, "proficiencies": {"skills": ["unknown"]}}, "skill"),
            ({**CHARACTER, "proficiencies": {"saving_throws": ["athletics"]}}, "saving"),
        ]
        for value, label in cases:
            with self.subTest(label=label), self.assertRaises(ValidationError):
                Character.model_validate(value)

    def test_create_character_persists_source_and_returns_derived_stats(self):
        state = {}
        result = self.apply(state, {"type": "create_character", "character": CHARACTER})
        self.assertEqual(result["schema_version"], "rule-resolution-v1")
        self.assertEqual(result["outcome"]["derived"]["hp"]["max"], 12)
        self.assertEqual(result["outcome"]["derived"]["ac"]["value"], 12)
        self.assertEqual(state["character"]["id"], "player")
        self.assertNotIn("ability_modifiers", state["character"])

    def test_skill_check_derives_athletics_modifier_and_rejects_conflicting_modifier(self):
        state = {"character": CHARACTER}
        result = self.apply(
            state,
            {"type": "skill_check", "skill": "athletics", "dc": 12, "character_id": "player"},
            randbelow=self.seq(7),
        )
        self.assertEqual(result["check"], {"skill": "athletics", "ability": "strength", "dc": 12, "modifier": 5})
        self.assertEqual(result["outcome"], {"total": 12, "success": True})
        self.assertIn("skill_check.v1", result["rules_used"])
        with self.assertRaises(ValidationError):
            api.ResolveRequest(action={"type": "skill_check", "skill": "athletics", "dc": 12, "character_id": "player", "modifier": 999}, state=state)

    def test_normal_skill_check_uses_one_d20(self):
        state = {
            "character": CHARACTER,
            "combat": {
                "combatants": {
                    "player": {"conditions": []},
                },
            },
        }

        result = self.apply(
            state,
            {"type": "skill_check", "skill": "athletics", "dc": 12, "character_id": "player"},
            randbelow=self.seq(17),
        )

        self.assertEqual(result["rolls"], [{"type": "d20", "result": 17}])
        self.assertEqual(result["outcome"]["total"], 22)

    def test_poisoned_skill_check_uses_disadvantage_and_lower_roll(self):
        state = {
            "character": CHARACTER,
            "combat": {
                "combatants": {
                    "player": {
                        "conditions": [{"id": "poisoned"}],
                    },
                },
            },
        }

        result = self.apply(
            state,
            {"type": "skill_check", "skill": "athletics", "dc": 12, "character_id": "player"},
            randbelow=self.seq(17, 4),
        )

        self.assertEqual(result["rolls"][0]["mode"], "disadvantage")
        self.assertEqual(result["rolls"][0]["rolls"], [17, 4])
        self.assertEqual(result["rolls"][0]["result"], 4)
        self.assertEqual(result["outcome"]["total"], 9)

    def test_removing_poisoned_restores_normal_skill_check(self):
        state = {
            "character": CHARACTER,
            "combat": {
                "combatants": {
                    "player": {
                        "conditions": [{"id": "poisoned"}],
                    },
                },
            },
        }
        action = {
            "type": "skill_check",
            "skill": "athletics",
            "dc": 12,
            "character_id": "player",
        }

        poisoned_result = self.apply(state, action, randbelow=self.seq(17, 4))
        self.assertEqual(poisoned_result["rolls"][0]["mode"], "disadvantage")
        state["combat"]["combatants"]["player"]["conditions"] = []

        normal_result = self.apply(state, action, randbelow=self.seq(17))

        self.assertEqual(normal_result["rolls"], [{"type": "d20", "result": 17}])

    def test_poisoned_skill_and_ability_checks_share_disadvantage_rule(self):
        state = {
            "character": CHARACTER,
            "combat": {
                "combatants": {
                    "player": {
                        "conditions": [{"id": "poisoned"}],
                    },
                },
            },
        }

        skill_result = self.apply(
            state,
            {"type": "skill_check", "skill": "athletics", "dc": 12, "character_id": "player"},
            randbelow=self.seq(17, 4),
        )
        ability_result = self.apply(
            state,
            {"type": "ability_check", "ability": "strength", "dc": 12, "character_id": "player"},
            randbelow=self.seq(17, 4),
        )

        self.assertEqual(skill_result["rolls"][0]["mode"], "disadvantage")
        self.assertEqual(ability_result["rolls"][0]["mode"], "disadvantage")

    def test_saving_throw_derives_proficiency(self):
        state = {"character": CHARACTER}
        result = self.apply(
            state,
            {"type": "saving_throw", "ability": "strength", "dc": 15, "character_id": "player"},
            randbelow=self.seq(10),
        )
        self.assertEqual(result["check"]["modifier"], 5)
        self.assertEqual(result["outcome"], {"total": 15, "success": True})
        self.assertIn("saving_throw.v1", result["rules_used"])

    def test_start_combat_derives_character_hp_ac_and_initiative(self):
        state = {}
        result = self.apply(
            state,
            {
                "type": "start_combat",
                "combatants": [
                    {"id": "player", "character": CHARACTER, "side": "player"},
                    {"id": "goblin", "hp": 8, "max_hp": 8, "ac": 12, "initiative_modifier": 0, "side": "enemy"},
                ],
            },
            randbelow=self.seq(10, 8),
        )
        player = state["combat"]["combatants"]["player"]
        self.assertEqual(player["hp"], 12)
        self.assertEqual(player["max_hp"], 12)
        self.assertEqual(player["ac"], 12)
        self.assertEqual(player["initiative_modifier"], 2)
        self.assertEqual(result["rolls"][0]["result"], 10)

    def test_weapon_attack_derives_bonus_and_damage_modifier(self):
        state = {}
        self.apply(
            state,
            {
                "type": "start_combat",
                "combatants": [
                    {"id": "player", "character": CHARACTER, "side": "player"},
                    {"id": "goblin", "hp": 20, "max_hp": 20, "ac": 12, "initiative_modifier": -20, "side": "enemy"},
                ],
            },
            randbelow=self.seq(20, 1),
        )
        self.assertEqual(state["combat"]["current_actor_id"], "player")
        result = self.apply(
            state,
            {"type": "attack", "actor_id": "player", "target_id": "goblin", "weapon_id": "longsword"},
            randbelow=self.seq(14, 4),
        )
        self.assertEqual(result["check"]["attack_bonus"], 5)
        self.assertEqual(result["check"]["damage"], {"dice": "1d8", "modifier": 3})
        self.assertEqual(result["outcome"]["total"], 19)
        self.assertEqual(result["outcome"]["damage"], 7)
        self.assertIn("weapon_attack.v1", result["rules_used"])
        self.assertIn("weapon_damage.v1", result["rules_used"])
        self.assertEqual(state["combat"]["combatants"]["goblin"]["hp"], 13)

    def test_legacy_attack_path_remains_supported(self):
        body = api.ResolveRequest(action={"type": "attack", "attack_bonus": 5, "target_ac": 15})
        result = api.resolve_request(body, randbelow=self.seq(14))
        self.assertEqual(result["outcome"]["total"], 19)
        self.assertEqual(result["rules_used"], ["attack_roll.mvp.v1"])


if __name__ == "__main__":
    unittest.main()
