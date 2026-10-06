import unittest
from copy import deepcopy

from rule_engine.combat_magic import (
    concentration_dc,
    consume_reaction_window,
    open_reaction_window,
    resolve_complex_saving_throw,
    resolve_concentration_check,
)


class CombatMagicInfrastructureTests(unittest.TestCase):
    def roll_one(self, _upper_bound):
        return 0

    def roll_twenty(self, upper_bound):
        return upper_bound - 1

    def test_concentration_dc_uses_half_damage_with_bounds(self):
        self.assertEqual(concentration_dc(1), 10)
        self.assertEqual(concentration_dc(21), 10)
        self.assertEqual(concentration_dc(22), 11)
        self.assertEqual(concentration_dc(100), 30)

    def test_failed_concentration_clears_spell_and_effect(self):
        character = {
            "spellcasting": {"concentration_spell_id": "web"},
            "active_effects": [{"id": "web", "source_id": "web", "duration": {"kind": "turns", "remaining": 2}}],
        }
        result = resolve_concentration_check(character, damage=22, constitution_modifier=0, randbelow=self.roll_one)
        self.assertFalse(result["success"])
        self.assertTrue(result["concentration_broken"])
        self.assertIsNone(character["spellcasting"]["concentration_spell_id"])
        self.assertEqual(character["active_effects"], [])

    def test_successful_concentration_keeps_spell(self):
        character = {"spellcasting": {"concentration_spell_id": "bless"}}
        result = resolve_concentration_check(character, damage=1, constitution_modifier=0, randbelow=self.roll_twenty)
        self.assertTrue(result["success"])
        self.assertEqual(character["spellcasting"]["concentration_spell_id"], "bless")

    def test_complex_save_halves_damage_and_adds_failure_condition_metadata(self):
        result = resolve_complex_saving_throw(
            modifier=0,
            dc=10,
            ability="dexterity",
            damage_dice="2d6",
            condition_on_failure="restrained",
            randbelow=self.roll_twenty,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["damage"], 6)
        self.assertIsNone(result["condition"])

    def test_reaction_window_is_pending_until_server_owned_consumption(self):
        combat = {"reaction_windows": []}
        window = open_reaction_window(
            combat,
            trigger="creature_hit_by_attack",
            triggering_actor_id="attacker",
            target_actor_id="defender",
            payload={"feature_id": "misty_escape"},
        )
        self.assertEqual(window["status"], "pending")
        consumed = consume_reaction_window(combat, window["id"], "defender")
        self.assertEqual(consumed["status"], "consumed")
        with self.assertRaises(ValueError):
            consume_reaction_window(combat, window["id"], "defender")


if __name__ == "__main__":
    unittest.main()
