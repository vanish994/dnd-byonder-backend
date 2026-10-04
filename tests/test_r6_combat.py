import json
import unittest
from copy import deepcopy
from unittest.mock import patch

import rule_engine.app as api


class R6CombatTests(unittest.TestCase):
    def sequence(self, *values):
        iterator = iter(values)
        return lambda upper_bound: next(iterator) - 1

    def character(self, level):
        return api.build_guided_character(api.GuidedCharacterRequest(
            name=f"Fighter {level}",
            class_id="fighter",
            level=level,
            abilities={
                "strength": 15,
                "dexterity": 14,
                "constitution": 13,
                "intelligence": 12,
                "wisdom": 10,
                "charisma": 8,
            },
            skills=["athletics", "perception"],
            weapon_id="longsword",
        ))

    def state_for(self, level, enemy_hp=100):
        character = self.character(level)
        state = {}
        body = api.ResolveRequest(action={
            "type": "start_combat",
            "combatants": [
                {"id": "player", "character": character, "side": "player"},
                {"id": "enemy", "hp": enemy_hp, "max_hp": enemy_hp, "ac": 10, "initiative_modifier": -20, "side": "enemy"},
            ],
        }, state=state)
        api.resolve_request(body, randbelow=self.sequence(10, 8))
        state.clear()
        state.update(body.state)
        return state

    def attack(self, state, rolls):
        body = api.ResolveRequest(action={
            "type": "attack",
            "actor_id": "player",
            "target_id": "enemy",
            "weapon_id": "longsword",
        }, state=state)
        result = api.resolve_request(body, randbelow=self.sequence(*rolls))
        state.clear()
        state.update(body.state)
        return result

    def test_extra_attack_count_by_authoritative_level(self):
        for level, expected in ((1, 1), (4, 1), (5, 2), (11, 3), (20, 4)):
            with self.subTest(level=level):
                state = self.state_for(level)
                rolls = []
                for _ in range(expected):
                    rolls.extend((10, 1))
                result = self.attack(state, rolls)
                if expected == 1:
                    self.assertEqual(result["outcome"]["damage"], 3)
                else:
                    self.assertEqual(result["outcome"]["attack_count"], expected)
                    self.assertEqual(len(result["outcome"]["attacks"]), expected)
                self.assertEqual(state["combat"]["combatants"]["enemy"]["hp"], 100 - expected * 3)
                self.assertFalse(state["combat"]["combatants"]["player"]["action_available"])

    def test_attack_results_are_independent_hit_miss_and_critical_hit(self):
        state = self.state_for(5)
        result = self.attack(state, (20, 4, 5, 1))
        attacks = result["outcome"]["attacks"]
        self.assertEqual(len(attacks), 2)
        self.assertTrue(attacks[0]["outcome"]["critical"])
        self.assertFalse(attacks[1]["outcome"]["hit"])
        self.assertEqual(attacks[0]["outcome"]["damage"], 11)
        self.assertIsNone(attacks[1]["outcome"]["damage"])
        self.assertEqual(state["combat"]["combatants"]["enemy"]["hp"], 89)

    def test_attack_results_are_independent_miss_then_hit(self):
        state = self.state_for(5)
        result = self.attack(state, (1, 10, 1))
        attacks = result["outcome"]["attacks"]
        self.assertFalse(attacks[0]["outcome"]["hit"])
        self.assertTrue(attacks[1]["outcome"]["hit"])
        self.assertIsNone(attacks[0]["outcome"]["damage"])
        self.assertEqual(attacks[1]["outcome"]["damage"], 3)
        self.assertEqual(state["combat"]["combatants"]["enemy"]["hp"], 97)

    def test_action_surge_consumes_resource_and_grants_another_action(self):
        state = self.state_for(5)
        self.assertIn("action_surge", [action["type"] for action in state["combat"]["available_actions"]])
        self.attack(state, (10, 1, 10, 1))
        player = state["combat"]["combatants"]["player"]
        self.assertFalse(player["action_available"])
        self.assertEqual(player["character"]["resources"]["action_surge"]["current"], 1)
        body = api.ResolveRequest(action={"type": "action_surge", "actor_id": "player"}, state=state)
        result = api.resolve_request(body)
        state.clear()
        state.update(body.state)
        self.assertEqual(result["outcome"]["resource_current"], 0)
        self.assertTrue(state["combat"]["combatants"]["player"]["action_available"])
        self.assertIn("attack", [action["type"] for action in state["combat"]["available_actions"]])
        self.attack(state, (10, 1, 10, 1))
        self.assertFalse(state["combat"]["combatants"]["player"]["action_available"])
        self.assertNotIn("action_surge", [action["type"] for action in state["combat"]["available_actions"]])

    def test_action_surge_recovery_and_no_resource_failure(self):
        state = {"character": api.character_to_state(self.character(5))}
        state["character"]["resources"]["action_surge"]["current"] = 0
        before = deepcopy(state)
        with self.assertRaisesRegex(ValueError, "insufficient"):
            api.resolve_request(api.ResolveRequest(action={
                "type": "consume_resource", "resource_id": "action_surge", "amount": 1,
            }, state=state))
        self.assertEqual(state, before)
        body = api.ResolveRequest(action={"type": "rest", "rest_type": "short_rest"}, state=state)
        api.resolve_request(body)
        self.assertEqual(body.state["character"]["resources"]["action_surge"]["current"], 1)

    def test_level_seventeen_action_surge_only_once_per_turn(self):
        state = self.state_for(17)
        body = api.ResolveRequest(action={"type": "action_surge", "actor_id": "player"}, state=state)
        api.resolve_request(body)
        state.clear()
        state.update(body.state)
        player = state["combat"]["combatants"]["player"]
        self.assertEqual(player["character"]["resources"]["action_surge"]["current"], 1)
        self.assertNotIn("action_surge", [action["type"] for action in state["combat"]["available_actions"]])
        before = deepcopy(state)
        with self.assertRaisesRegex(ValueError, "already used this turn"):
            api.resolve_request(api.ResolveRequest(
                action={"type": "action_surge", "actor_id": "player"}, state=state,
            ))
        self.assertEqual(state, before)

        for actor_id in ("player", "enemy"):
            end_body = api.ResolveRequest(action={"type": "end_turn", "actor_id": actor_id}, state=state)
            api.resolve_request(end_body)
            state.clear()
            state.update(end_body.state)
        self.assertEqual(state["combat"]["current_actor_id"], "player")
        self.assertIn("action_surge", [action["type"] for action in state["combat"]["available_actions"]])
        second_body = api.ResolveRequest(action={"type": "action_surge", "actor_id": "player"}, state=state)
        api.resolve_request(second_body)
        self.assertEqual(second_body.state["combat"]["combatants"]["player"]["character"]["resources"]["action_surge"]["current"], 0)

    def test_multi_attack_failure_rolls_back_all_mutations(self):
        state = self.state_for(5)
        before = deepcopy(state)
        original = api.resolve_attack
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("forced second attack failure")
            return original(*args, **kwargs)

        body = api.ResolveRequest(action={
            "type": "attack", "actor_id": "player", "target_id": "enemy", "weapon_id": "longsword",
        }, state=state)
        root = body.state
        with patch.object(api, "resolve_attack", side_effect=fail_second):
            with self.assertRaisesRegex(ValueError, "forced second"):
                api.resolve_request(body, randbelow=self.sequence(10, 1, 10, 1))
        self.assertIs(body.state, root)
        self.assertEqual(body.state, before)
        self.assertEqual(body.state["combat"]["combatants"]["enemy"]["hp"], 100)

    def test_multi_attack_round_trips_through_public_state(self):
        state = self.state_for(5)
        result = self.attack(state, (10, 1, 10, 1))
        serialized = json.dumps({"state": state, "resolution": result})
        restored = json.loads(serialized)
        self.assertEqual(restored["state"], state)
        self.assertEqual(restored["resolution"]["schema_version"], "rule-resolution-v1")
        self.assertEqual(restored["resolution"]["outcome"]["attack_count"], 2)

    def test_action_surge_cannot_be_client_forged_or_used_by_level_one(self):
        level_one = self.state_for(1)
        self.assertNotIn("action_surge", [action["type"] for action in level_one["combat"]["available_actions"]])
        with self.assertRaises(ValueError):
            api.ResolveRequest(action={"type": "action_surge", "actor_id": "player", "action_surge": True}, state=level_one)
        with self.assertRaisesRegex(ValueError, "not available"):
            api.resolve_request(api.ResolveRequest(action={"type": "action_surge", "actor_id": "player"}, state=level_one))


if __name__ == "__main__":
    unittest.main()
