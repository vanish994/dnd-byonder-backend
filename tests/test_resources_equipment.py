import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from fastapi.testclient import TestClient

import rule_engine.app as api
from rule_engine.character import derive_character


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
    "current_hp": 5,
}


class ResourcesEquipmentTests(unittest.TestCase):
    def setUp(self):
        self.state = {"character": deepcopy(CHARACTER)}

    def apply(self, action):
        body = api.ResolveRequest(action=action, state=self.state)
        result = api.resolve_request(body, randbelow=self.sequence(20, 4, 6, 3))
        self.state.clear()
        self.state.update(body.state)
        return result

    @staticmethod
    def sequence(*values):
        values = iter(values)
        return lambda upper_bound: next(values) - 1

    def test_resource_is_generic_deterministic_and_bounded(self):
        self.apply({"type": "define_resource", "resource_id": "test_power", "maximum": 2, "recovery": "short_rest", "recovery_amount": 1})
        self.apply({"type": "consume_resource", "resource_id": "test_power", "amount": 2})
        self.assertEqual(self.state["character"]["resources"]["test_power"]["current"], 0)
        before = deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "insufficient"):
            self.apply({"type": "consume_resource", "resource_id": "test_power"})
        self.assertEqual(self.state, before)
        self.apply({"type": "rest", "rest_type": "short_rest"})
        self.assertEqual(self.state["character"]["resources"]["test_power"]["current"], 1)

    def test_long_rest_recovers_short_rest_resources_and_turn_resources_reset_at_turn_start(self):
        self.apply({"type": "define_resource", "resource_id": "short_power", "maximum": 1, "current": 0, "recovery": "short_rest"})
        self.apply({"type": "define_resource", "resource_id": "turn_power", "maximum": 1, "current": 0, "recovery": "turn"})
        self.apply({"type": "rest", "rest_type": "long_rest"})
        self.assertEqual(self.state["character"]["resources"]["short_power"]["current"], 1)
        self.assertEqual(self.state["character"]["resources"]["turn_power"]["current"], 0)

        state = {"character": deepcopy(self.state["character"])}
        body = api.ResolveRequest(action={"type": "start_combat", "combatants": [
            {"id": "player", "character": state["character"], "side": "player"},
            {"id": "enemy", "hp": 10, "max_hp": 10, "ac": 10, "initiative_modifier": -20, "side": "enemy"},
        ]}, state=state)
        api.resolve_request(body, randbelow=self.sequence(20, 1))
        state.clear()
        state.update(body.state)
        self.assertEqual(state["combat"]["combatants"]["player"]["character"]["resources"]["turn_power"]["current"], 1)

    def test_equipment_change_is_rejected_during_active_combat(self):
        state = {"character": deepcopy(CHARACTER), "combat": {"active": True}}
        before = deepcopy(state)
        body = api.ResolveRequest(action={"type": "add_item", "item_id": "longsword"}, state=state)
        with self.assertRaisesRegex(ValueError, "active combat"):
            api.resolve_request(body)
        self.assertEqual(state, before)

    def test_short_rest_recovers_short_resources_and_until_rest(self):
        self.apply({"type": "define_resource", "resource_id": "short_power", "maximum": 2, "current": 0, "recovery": "short_rest"})
        self.apply({"type": "define_resource", "resource_id": "long_power", "maximum": 2, "current": 0, "recovery": "long_rest"})
        self.state["character"]["conditions"] = [
            {"id": "poisoned", "duration": {"kind": "until_rest"}, "effects": []},
        ]
        self.apply({"type": "rest", "rest_type": "short_rest"})
        resources = self.state["character"]["resources"]
        self.assertEqual(resources["short_power"]["current"], 2)
        self.assertEqual(resources["long_power"]["current"], 0)
        self.assertEqual(self.state["character"]["conditions"], [])
        self.assertEqual(self.state["character"]["current_hp"], 5)

    def test_long_rest_recovers_hp_all_resources_and_temporary_conditions(self):
        self.apply({"type": "define_resource", "resource_id": "long_power", "maximum": 2, "current": 0, "recovery": "long_rest"})
        self.state["character"]["conditions"] = [
            {"id": "poisoned", "duration": {"kind": "until_rest"}, "effects": []},
            {"id": "prone", "duration": {"kind": "turns", "remaining": 1}, "effects": []},
        ]
        result = self.apply({"type": "rest", "rest_type": "long_rest"})
        self.assertEqual(self.state["character"]["current_hp"], 12)
        self.assertEqual(self.state["character"]["resources"]["long_power"]["current"], 2)
        self.assertEqual(self.state["character"]["conditions"], [])
        self.assertIn("poisoned", result["outcome"]["conditions_removed"])

    def test_rest_is_rejected_during_active_combat_without_mutation(self):
        state = deepcopy(self.state)
        state["combat"] = {"active": True}
        before = deepcopy(state)
        body = api.ResolveRequest(action={"type": "rest", "rest_type": "long_rest"}, state=state)
        with self.assertRaisesRegex(ValueError, "active combat"):
            api.resolve_request(body)
        self.assertEqual(state, before)

    def test_inventory_quantity_equip_and_atomic_removal(self):
        self.apply({"type": "add_item", "item_id": "leather", "quantity": 2})
        self.apply({"type": "equip_item", "item_id": "leather"})
        self.assertEqual(self.state["character"]["equipped"]["armor"], "leather")
        self.assertEqual(derive_character(self.state["character"])[1]["ac"], {"value": 13, "source": "leather"})
        before = deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "equipped"):
            self.apply({"type": "remove_item", "item_id": "leather", "quantity": 2})
        self.assertEqual(self.state, before)
        self.apply({"type": "unequip_item", "slot": "armor"})
        self.apply({"type": "remove_item", "item_id": "leather", "quantity": 2})
        self.assertNotIn("leather", self.state["character"]["inventory"])

    def test_inventory_rejects_missing_item_and_negative_or_zero_quantity(self):
        before = deepcopy(self.state)
        with self.assertRaises(ValueError):
            self.apply({"type": "remove_item", "item_id": "longsword"})
        self.assertEqual(self.state, before)
        with self.assertRaises(ValueError):
            api.ResolveRequest(action={"type": "add_item", "item_id": "longsword", "quantity": 0}, state=self.state)

    def test_equipped_weapon_drives_combat_attack_and_round_trip(self):
        self.apply({"type": "add_item", "item_id": "longsword"})
        self.apply({"type": "equip_item", "item_id": "longsword"})
        character = json.loads(json.dumps(self.state["character"]))
        self.state = {"character": character}
        self.apply({"type": "start_combat", "combatants": [
            {"id": "player", "character": character, "side": "player"},
            {"id": "goblin", "hp": 20, "max_hp": 20, "ac": 12, "initiative_modifier": -20, "side": "enemy"},
        ]})
        result = self.apply({"type": "attack", "actor_id": "player", "target_id": "goblin"})
        self.assertEqual(result["check"]["weapon_id"], "longsword")
        self.assertEqual(result["check"]["damage"], {"dice": "1d8", "modifier": 3})
        json.loads(json.dumps(self.state))

    def test_failed_operation_after_partial_mutation_is_rolled_back(self):
        before = deepcopy(self.state)

        def mutate_then_fail(character, item_id, quantity=1, **kwargs):
            character.setdefault("inventory", {})["broken"] = {"quantity": 1, "item": {"id": "broken", "kind": "item"}}
            raise ValueError("forced inventory failure")

        with patch.object(api, "add_item", side_effect=mutate_then_fail):
            body = api.ResolveRequest(action={"type": "add_item", "item_id": "broken"}, state=self.state)
            with self.assertRaisesRegex(ValueError, "forced"):
                api.resolve_request(body)
        self.assertEqual(self.state, before)

    def test_public_resolve_endpoint_accepts_r4_action_and_preserves_contract(self):
        previous_key = api.API_KEY
        api.API_KEY = "r4-secret"
        self.addCleanup(setattr, api, "API_KEY", previous_key)
        client = TestClient(api.app)
        response = client.post(
            "/v1/resolve",
            headers={"x-api-key": "r4-secret"},
            json={"action": {"type": "add_item", "item_id": "leather"}, "state": {"character": CHARACTER}},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["schema_version"], "rule-resolution-v1")
        self.assertEqual(response.json()["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
