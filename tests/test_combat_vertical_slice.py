import unittest

import rule_engine.app as api


class CombatVerticalSliceTests(unittest.TestCase):
    def sequence(self, *results):
        values = iter(results)
        return lambda upper_bound: next(values) - 1

    def combatants(self, *, goblin_hp=7, player_hp=20, player_speed=30):
        return [
            {
                "id": "player",
                "hp": player_hp,
                "max_hp": 20,
                "ac": 16,
                "initiative_modifier": 3,
                "position": 0,
                "movement_speed": player_speed,
                "side": "player",
            },
            {
                "id": "goblin-1",
                "hp": goblin_hp,
                "max_hp": 7 if goblin_hp <= 7 else goblin_hp,
                "ac": 13,
                "initiative_modifier": 2,
                "position": 5,
                "movement_speed": 30,
                "side": "enemy",
            },
        ]

    def apply(self, state, action, *, randbelow=None):
        body = api.ResolveRequest(action=action, state=state)
        result = api.resolve_request(body, randbelow=randbelow)
        state.clear()
        state.update(body.state)
        return result

    def start(self, state, *, initiative=(10, 8), combatants=None):
        return self.apply(
            state,
            {"type": "start_combat", "combatants": combatants or self.combatants()},
            randbelow=self.sequence(*initiative),
        )

    def attack(self, state, *, d20=15, d8=4, target="goblin-1"):
        return self.apply(
            state,
            {
                "type": "attack",
                "actor_id": "player",
                "target_id": target,
                "attack_bonus": 5,
                "damage": {"dice": "1d8", "modifier": 3},
            },
            randbelow=self.sequence(d20, d8),
        )

    def test_start_combat_creates_state_and_resources(self):
        state = {}
        result = self.start(state)
        combat = state["combat"]

        self.assertEqual(result["schema_version"], "rule-resolution-v1")
        self.assertEqual(result["rules_used"], ["initiative.mvp.v1", "combat.mvp.v1"])
        self.assertEqual(len(result["rolls"]), 2)
        self.assertTrue(combat["active"])
        self.assertEqual(combat["round"], 1)
        self.assertEqual(combat["turn_index"], 0)
        self.assertEqual(combat["current_actor_id"], "player")
        self.assertEqual(combat["turn_order"], ["player", "goblin-1"])
        self.assertEqual(combat["combatants"]["player"]["movement_remaining"], 30)
        self.assertEqual(combat["combatants"]["player"]["conditions"], [])
        self.assertTrue(combat["combatants"]["player"]["action_available"])
        self.assertTrue(combat["combatants"]["player"]["bonus_action_available"])
        self.assertTrue(combat["combatants"]["player"]["reaction_available"])
        self.assertEqual(
            combat["available_actions"],
            [{"type": "move"}, {"type": "attack"}, {"type": "end_turn"}],
        )

    def test_initiative_tie_is_total_then_modifier_then_id(self):
        combatants = [
            {
                "id": "zeta",
                "hp": 5,
                "max_hp": 5,
                "ac": 10,
                "initiative_modifier": 0,
                "position": 0,
                "movement_speed": 0,
                "side": "enemy",
            },
            {
                "id": "alpha",
                "hp": 5,
                "max_hp": 5,
                "ac": 10,
                "initiative_modifier": 0,
                "position": 0,
                "movement_speed": 0,
                "side": "player",
            },
            {
                "id": "bravo",
                "hp": 5,
                "max_hp": 5,
                "ac": 10,
                "initiative_modifier": 0,
                "position": 0,
                "movement_speed": 0,
                "side": "enemy",
            },
        ]
        state = {}
        self.start(state, initiative=(10, 10, 10), combatants=combatants)
        self.assertEqual(state["combat"]["turn_order"], ["alpha", "bravo", "zeta"])

    def test_move_updates_position_and_remaining_movement(self):
        state = {}
        self.start(state)
        result = self.apply(state, {"type": "move", "actor_id": "player", "distance": 10})
        player = state["combat"]["combatants"]["player"]

        self.assertEqual(result["outcome"]["position"], 10)
        self.assertEqual(player["position"], 10)
        self.assertEqual(player["movement_remaining"], 20)
        self.assertEqual(state["combat"]["available_actions"], [{"type": "move"}, {"type": "attack"}, {"type": "end_turn"}])

    def test_move_over_remaining_is_rejected_without_state_change(self):
        state = {}
        self.start(state)
        before = state.copy()
        with self.assertRaises(ValueError):
            self.apply(state, {"type": "move", "actor_id": "player", "distance": 31})
        self.assertEqual(state, before)

    def test_move_outside_current_turn_is_rejected(self):
        state = {}
        self.start(state)
        with self.assertRaises(ValueError):
            self.apply(state, {"type": "move", "actor_id": "goblin-1", "distance": 1})
        self.assertEqual(state["combat"]["current_actor_id"], "player")

    def test_attack_hit_applies_damage_and_updates_hp(self):
        state = {}
        self.start(state)
        result = self.attack(state, d20=15, d8=4)
        target = state["combat"]["combatants"]["goblin-1"]

        self.assertTrue(result["outcome"]["hit"])
        self.assertEqual(result["outcome"]["damage"], 7)
        self.assertEqual(result["outcome"]["target_hp_before"], 7)
        self.assertEqual(result["outcome"]["target_hp_after"], 0)
        self.assertEqual(target["hp"], 0)
        self.assertTrue(target["unconscious"])
        self.assertFalse(state["combat"]["active"])
        self.assertFalse(state["combat"]["combatants"]["player"]["action_available"])

    def test_attack_miss_consumes_action_without_damage_or_hp_change(self):
        state = {}
        self.start(state)
        result = self.attack(state, d20=2, d8=8)
        target = state["combat"]["combatants"]["goblin-1"]

        self.assertFalse(result["outcome"]["hit"])
        self.assertIsNone(result["outcome"]["damage"])
        self.assertEqual(result["rolls"], [{"type": "d20", "result": 2}])
        self.assertEqual(target["hp"], 7)
        self.assertFalse(state["combat"]["combatants"]["player"]["action_available"])

    def test_attack_uses_ac_from_state_not_client_target_ac(self):
        state = {}
        self.start(state)
        result = self.apply(
            state,
            {
                "type": "attack",
                "actor_id": "player",
                "target_id": "goblin-1",
                "attack_bonus": 5,
                "damage": {"dice": "1d8", "modifier": 0},
            },
            randbelow=self.sequence(7, 1),
        )
        self.assertEqual(result["check"]["target_ac"], 13)
        self.assertFalse(result["outcome"]["hit"])

    def test_attack_outside_current_turn_does_not_roll_or_mutate(self):
        state = {}
        self.start(state)
        before = repr(state)
        calls = []

        def randbelow(upper_bound):
            calls.append(upper_bound)
            return 19

        with self.assertRaises(ValueError):
            self.apply(
                state,
                {
                    "type": "attack",
                    "actor_id": "goblin-1",
                    "target_id": "player",
                    "attack_bonus": 5,
                    "damage": {"dice": "1d8", "modifier": 3},
                },
                randbelow=randbelow,
            )
        self.assertEqual(calls, [])
        self.assertEqual(repr(state), before)

    def test_end_turn_advances_actor_and_resets_next_resources(self):
        state = {}
        self.start(state)
        self.apply(state, {"type": "move", "actor_id": "player", "distance": 10})
        self.apply(state, {"type": "end_turn", "actor_id": "player"})

        combat = state["combat"]
        goblin = combat["combatants"]["goblin-1"]
        self.assertEqual(combat["current_actor_id"], "goblin-1")
        self.assertEqual(combat["round"], 1)
        self.assertEqual(goblin["movement_remaining"], 30)
        self.assertTrue(goblin["action_available"])
        self.assertTrue(goblin["bonus_action_available"])
        self.assertTrue(goblin["reaction_available"])

    def test_end_turn_wraps_and_starts_new_round(self):
        state = {}
        self.start(state)
        self.apply(state, {"type": "end_turn", "actor_id": "player"})
        result = self.apply(state, {"type": "end_turn", "actor_id": "goblin-1"})

        self.assertEqual(result["outcome"]["round"], 2)
        self.assertEqual(state["combat"]["round"], 2)
        self.assertEqual(state["combat"]["turn_index"], 0)
        self.assertEqual(state["combat"]["current_actor_id"], "player")

    def test_hp_is_clamped_to_zero_and_combat_ends(self):
        state = {}
        self.start(state, combatants=self.combatants(goblin_hp=1))
        result = self.attack(state, d20=20, d8=8)

        self.assertEqual(result["outcome"]["damage"], 11)
        self.assertEqual(state["combat"]["combatants"]["goblin-1"]["hp"], 0)
        self.assertFalse(state["combat"]["active"])
        self.assertEqual(state["combat"]["winner_side"], "player")

    def test_unconscious_actor_cannot_move_or_attack(self):
        combatants = self.combatants(player_hp=0)
        state = {}
        self.start(state, initiative=(20, 1), combatants=combatants)
        self.assertTrue(state["combat"]["combatants"]["player"]["unconscious"])
        with self.assertRaises(ValueError):
            self.apply(state, {"type": "move", "actor_id": "player", "distance": 1})
        with self.assertRaises(ValueError):
            self.apply(
                state,
                {
                    "type": "attack",
                    "actor_id": "player",
                    "target_id": "goblin-1",
                    "attack_bonus": 5,
                    "damage": {"dice": "1d8", "modifier": 0},
                },
                randbelow=self.sequence(20, 8),
            )

    def test_start_combat_rejects_invalid_combatant(self):
        body = api.ResolveRequest(
                action={
                    "type": "start_combat",
                    "combatants": [
                        {
                            "id": "player",
                            "hp": 21,
                            "max_hp": 20,
                            "ac": 16,
                            "initiative_modifier": 3,
                            "position": 0,
                            "movement_speed": 30,
                        },
                        {
                            "id": "goblin-1",
                            "hp": 7,
                            "max_hp": 7,
                            "ac": 13,
                            "initiative_modifier": 2,
                            "position": 5,
                            "movement_speed": 30,
                        },
                    ],
                }
            )
        with self.assertRaises(ValueError):
            api.resolve_request(body, randbelow=self.sequence(10, 10))

    def test_end_turn_expires_actor_turn_conditions(self):
        state = {}
        self.start(state)

        player = state["combat"]["combatants"]["player"]

        player["conditions"] = [
            {
                "id": "poisoned",
                "source_id": "test-effect",
                "duration": {
                    "kind": "turns",
                    "remaining": 1,
                },
                "effects": [],
            }
        ]

        result = self.apply(
            state,
            {
                "type": "end_turn",
                "actor_id": "player",
            },
        )

        self.assertEqual(
            result["outcome"]["expired_conditions"],
            ["poisoned"],
        )

        self.assertEqual(
            state["combat"]["combatants"]["player"]["conditions"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
