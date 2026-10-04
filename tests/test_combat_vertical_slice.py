import json
import unittest
from copy import deepcopy

from game.contracts import GameTurnResponse
import rule_engine.app as api
from rule_engine.conditions import add_condition, remove_condition


def json_round_trip(value):
    return json.loads(json.dumps(value)) == value


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

    def apply_through_game_turn_round_trip(self, state, action, *, randbelow=None):
        body = api.ResolveRequest(action=action, state=state)
        resolution = api.resolve_request(body, randbelow=randbelow)
        combat = body.state.get("combat", {})
        public_response = GameTurnResponse(
            campaign_id="campaign-test",
            narration="A resolução continua.",
            rule_resolution=resolution,
            state=body.state,
            available_actions=combat.get("available_actions", []),
        )
        serialized_response = public_response.model_dump_json()
        restored_response = GameTurnResponse.model_validate_json(serialized_response)
        state.clear()
        state.update(restored_response.state)
        return restored_response.rule_resolution

    def end_turn_round_trip(self, state, actor_id):
        return self.apply_through_game_turn_round_trip(
            state,
            {"type": "end_turn", "actor_id": actor_id},
        )

    def attack(self, state, *, d20=15, d8=4, target="goblin-1", rolls=None):
        return self.apply(
            state,
            {
                "type": "attack",
                "actor_id": "player",
                "target_id": target,
                "attack_bonus": 5,
                "damage": {"dice": "1d8", "modifier": 3},
            },
            randbelow=self.sequence(*(rolls or (d20, d8))),
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

    def test_start_combat_emits_initial_round_and_turn_events(self):
        state = {}
        result = self.start(state)

        self.assertEqual(
            result["outcome"]["lifecycle_events"],
            ["round_start", "turn_start"],
        )

    def test_empty_combatants_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "combatants cannot be empty"):
            api._validate_initiative_state(
                {
                    "combatants": {},
                    "turn_order": [],
                    "current_actor_id": None,
                    "turn_index": 0,
                }
            )

    def test_initiative_state_rejects_duplicate_or_unknown_ids(self):
        state = {}
        self.start(state)
        combat = state["combat"]

        combat["turn_order"] = ["player", "player"]
        with self.assertRaisesRegex(ValueError, "duplicate actor"):
            api._validate_initiative_state(combat)

        combat["turn_order"] = ["player", "missing"]
        with self.assertRaisesRegex(ValueError, "unknown actor"):
            api._validate_initiative_state(combat)

    def test_initiative_state_rejects_invalid_current_actor(self):
        state = {}
        self.start(state)
        combat = state["combat"]
        combat["current_actor_id"] = "missing"

        with self.assertRaisesRegex(ValueError, "current actor does not exist"):
            api._validate_initiative_state(combat)

    def test_initiative_state_rejects_order_missing_current_actor(self):
        state = {}
        self.start(state)
        combat = state["combat"]
        combat["turn_order"] = ["player"]
        combat["turn_index"] = 0
        combat["current_actor_id"] = "goblin-1"

        with self.assertRaisesRegex(ValueError, "does not contain every combatant"):
            api._validate_initiative_state(combat)

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

    def test_invalid_move_does_not_normalize_conditions_before_failure(self):
        state = {}
        self.start(state)
        state["combat"]["combatants"]["player"]["conditions"] = [
            {"id": "poisoned"}
        ]
        before = deepcopy(state)

        with self.assertRaisesRegex(ValueError, "movement exceeds remaining movement"):
            self.apply(state, {"type": "move", "actor_id": "player", "distance": 31})

        self.assertEqual(state, before)

    def test_invalid_target_does_not_consume_action_or_mutate_state(self):
        state = {}
        self.start(state)
        before = deepcopy(state)

        with self.assertRaisesRegex(ValueError, "target does not exist"):
            self.apply(
                state,
                {
                    "type": "attack",
                    "actor_id": "player",
                    "target_id": "missing",
                    "attack_bonus": 5,
                    "damage": {"dice": "1d8", "modifier": 3},
                },
            )

        self.assertEqual(state, before)

    def test_partial_attack_failure_restores_full_state_and_root_identity(self):
        state = {}
        self.start(state, combatants=self.combatants(goblin_hp=20))
        combat = state["combat"]
        player = combat["combatants"]["player"]
        player["movement_remaining"] = -1
        before = deepcopy(state)
        state_id = id(state)

        with self.assertRaisesRegex(ValueError, "invalid movement_remaining"):
            self.attack(state, d20=20, d8=4)

        self.assertEqual(state, before)
        self.assertEqual(id(state), state_id)
        self.assertIs(state["combat"], combat)
        self.assertEqual(
            state["combat"]["combatants"]["player"]["conditions"],
            before["combat"]["combatants"]["player"]["conditions"],
        )
        self.assertTrue(json_round_trip(state))

    def test_partial_end_turn_failure_restores_lifecycle_and_resources(self):
        state = {}
        self.start(state)
        combat = state["combat"]
        combat["turn_index"] = 1
        combat["current_actor_id"] = "goblin-1"
        goblin = combat["combatants"]["goblin-1"]
        player = combat["combatants"]["player"]
        goblin["conditions"] = [
            {"id": "poisoned", "duration": {"kind": "turns", "remaining": 2}}
        ]
        player["movement_speed"] = -1
        before = deepcopy(state)
        state_id = id(state)

        with self.assertRaisesRegex(ValueError, "invalid movement_speed"):
            self.apply(state, {"type": "end_turn", "actor_id": "goblin-1"})

        self.assertEqual(state, before)
        self.assertEqual(id(state), state_id)
        self.assertEqual(state["combat"]["round"], 1)
        self.assertEqual(state["combat"]["turn_index"], 1)
        self.assertEqual(state["combat"]["current_actor_id"], "goblin-1")
        self.assertEqual(state["combat"]["combatants"], before["combat"]["combatants"])
        self.assertTrue(json_round_trip(state))

    def test_restore_state_removes_fields_created_after_snapshot(self):
        state = {
            "combat": {
                "round": 1,
                "combatants": {"player": {"conditions": []}},
            }
        }
        before = deepcopy(state)
        state["combat"]["partial_field"] = "must be removed"
        state["partial_top_level"] = True

        api._restore_state_in_place(state, before)

        self.assertEqual(state, before)
        self.assertNotIn("partial_field", state["combat"])
        self.assertNotIn("partial_top_level", state)
        self.assertTrue(json_round_trip(state))

    def test_invalid_end_turn_actor_does_not_mutate_initiative(self):
        state = {}
        self.start(state)
        before = deepcopy(state)

        with self.assertRaisesRegex(ValueError, "actor is not the current actor"):
            self.apply(state, {"type": "end_turn", "actor_id": "goblin-1"})

        self.assertEqual(state, before)

    def test_invalid_hp_does_not_replace_existing_state(self):
        state = {"sentinel": "preserve"}
        body = api.ResolveRequest(
            action={
                "type": "start_combat",
                "combatants": [
                    {
                        "id": "player",
                        "hp": 9,
                        "max_hp": 8,
                        "ac": 16,
                        "initiative_modifier": 3,
                    },
                    {
                        "id": "goblin-1",
                        "hp": 7,
                        "max_hp": 7,
                        "ac": 13,
                        "initiative_modifier": 2,
                    },
                ],
            },
            state=state,
        )
        before = deepcopy(state)

        with self.assertRaisesRegex(ValueError, "hp must be between zero and max_hp"):
            api.resolve_request(body)

        self.assertEqual(state, before)

    def test_move_consumes_exact_remaining_movement(self):
        state = {}
        self.start(state)

        self.apply(
            state,
            {"type": "move", "actor_id": "player", "distance": 30},
        )

        player = state["combat"]["combatants"]["player"]

        self.assertEqual(player["position"], 30)
        self.assertEqual(player["movement_remaining"], 0)
        self.assertNotIn(
            {"type": "move"},
            state["combat"]["available_actions"],
        )

    def test_zero_distance_does_not_create_negative_movement(self):
        state = {}
        self.start(state)

        self.apply(
            state,
            {"type": "move", "actor_id": "player", "distance": 0},
        )

        player = state["combat"]["combatants"]["player"]

        self.assertGreaterEqual(player["movement_remaining"], 0)
        self.assertLessEqual(
            player["movement_remaining"],
            player["movement_speed"],
        )
        self.assertEqual(player["position"], 0)

    def test_zero_movement_speed_has_no_move_action(self):
        combatants = self.combatants(player_speed=0)
        state = {}

        self.start(state, combatants=combatants)

        player = state["combat"]["combatants"]["player"]

        self.assertEqual(player["movement_speed"], 0)
        self.assertEqual(player["movement_remaining"], 0)
        self.assertNotIn(
            {"type": "move"},
            state["combat"]["available_actions"],
        )

        with self.assertRaises(ValueError):
            self.apply(
                state,
                {"type": "move", "actor_id": "player", "distance": 1},
            )

    def test_invalid_movement_remaining_above_speed_is_rejected(self):
        state = {}
        self.start(state)

        player = state["combat"]["combatants"]["player"]
        player["movement_remaining"] = 31

        with self.assertRaisesRegex(
            ValueError,
            "movement_remaining exceeds movement_speed",
        ):
            api._combat_available_actions(state["combat"])

    def test_negative_movement_remaining_is_rejected(self):
        state = {}
        self.start(state)

        player = state["combat"]["combatants"]["player"]
        player["movement_remaining"] = -1

        with self.assertRaisesRegex(
            ValueError,
            "invalid movement_remaining",
        ):
            api._combat_available_actions(state["combat"])

    def test_negative_movement_speed_is_rejected(self):
        state = {}
        self.start(state)

        player = state["combat"]["combatants"]["player"]
        player["movement_speed"] = -1

        with self.assertRaisesRegex(ValueError, "invalid movement_speed"):
            api._combat_available_actions(state["combat"])

    def test_negative_position_is_rejected(self):
        state = {}
        self.start(state)

        player = state["combat"]["combatants"]["player"]
        player["position"] = -1

        with self.assertRaisesRegex(ValueError, "invalid position"):
            api._combat_available_actions(state["combat"])

    def test_move_preserves_action_bonus_action_and_reaction(self):
        state = {}
        self.start(state)

        player = state["combat"]["combatants"]["player"]

        self.assertTrue(player["action_available"])
        self.assertTrue(player["bonus_action_available"])
        self.assertTrue(player["reaction_available"])

        self.apply(
            state,
            {"type": "move", "actor_id": "player", "distance": 10},
        )

        self.assertTrue(player["action_available"])
        self.assertTrue(player["bonus_action_available"])
        self.assertTrue(player["reaction_available"])
        self.assertEqual(player["movement_remaining"], 20)

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

        self.assertEqual(
            state["combat"]["available_actions"],
            [{"type": "move"}, {"type": "attack"}, {"type": "end_turn"}],
        )

    def test_end_turn_emits_turn_lifecycle_events(self):
        state = {}
        self.start(state)

        result = self.apply(state, {"type": "end_turn", "actor_id": "player"})

        self.assertEqual(
            result["outcome"]["lifecycle_events"],
            ["turn_end", "turn_start"],
        )

    def test_until_end_of_turn_blocker_expires_before_next_turn(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        add_condition(
            player,
            condition_id="grappled",
            duration={"kind": "until_end_of_turn"},
            timing={
                "applied_round": 1,
                "applied_turn_index": 0,
                "applied_phase": "turn_start",
            },
        )
        self.assertEqual(player["movement_remaining"], 0)

        self.apply(state, {"type": "end_turn", "actor_id": "player"})

        self.assertEqual(player["conditions"], [])
        self.assertEqual(player["movement_remaining"], 30)

    def test_until_start_of_turn_blocker_expires_at_target_start(self):
        state = {}
        self.start(state)
        goblin = state["combat"]["combatants"]["goblin-1"]
        add_condition(
            goblin,
            condition_id="restrained",
            duration={"kind": "until_start_of_turn"},
            timing={
                "applied_round": 1,
                "applied_turn_index": 0,
                "applied_phase": "turn_end",
            },
        )

        self.apply(state, {"type": "end_turn", "actor_id": "player"})

        self.assertEqual(goblin["conditions"], [])
        self.assertEqual(goblin["movement_remaining"], 30)

    def test_end_turn_skips_unconscious_actor_and_keeps_lifecycle_coherent(self):
        state = {}
        combatants = self.combatants()
        combatants.insert(
            1,
            {
                "id": "player-2",
                "hp": 10,
                "max_hp": 10,
                "ac": 14,
                "initiative_modifier": 1,
                "position": 0,
                "movement_speed": 30,
                "side": "player",
            },
        )
        self.start(state, initiative=(20, 10, 1), combatants=combatants)
        player = state["combat"]["combatants"]["player"]
        player["hp"] = 0
        player["unconscious"] = True

        result = self.apply(state, {"type": "end_turn", "actor_id": "player"})

        self.assertEqual(result["outcome"]["current_actor_id"], "player-2")
        self.assertEqual(state["combat"]["current_actor_id"], "player-2")
        self.assertEqual(
            state["combat"]["available_actions"],
            [{"type": "move"}, {"type": "attack"}, {"type": "end_turn"}],
        )

    def test_end_turn_wraps_and_starts_new_round(self):
        state = {}
        self.start(state)
        self.apply(state, {"type": "end_turn", "actor_id": "player"})
        result = self.apply(state, {"type": "end_turn", "actor_id": "goblin-1"})

        self.assertEqual(result["outcome"]["round"], 2)
        self.assertEqual(state["combat"]["round"], 2)
        self.assertEqual(state["combat"]["turn_index"], 0)
        self.assertEqual(state["combat"]["current_actor_id"], "player")
        self.assertEqual(
            result["outcome"]["lifecycle_events"],
            ["turn_end", "round_end", "round_start", "turn_start"],
        )

    def test_end_turn_with_no_eligible_actor_has_bounded_terminal_result(self):
        state = {}
        self.start(state)
        for actor in state["combat"]["combatants"].values():
            actor["hp"] = 0
            actor["unconscious"] = True

        result = self.apply(state, {"type": "end_turn", "actor_id": "player"})

        self.assertFalse(result["outcome"]["combat_active"])
        self.assertTrue(result["outcome"]["combat_ended"])
        self.assertEqual(result["outcome"]["lifecycle_events"], ["turn_end"])

    def test_hp_is_clamped_to_zero_and_combat_ends(self):
        state = {}
        self.start(state, combatants=self.combatants(goblin_hp=1))
        result = self.attack(state, d20=20, d8=8)

        self.assertEqual(result["outcome"]["damage"], 11)
        self.assertEqual(state["combat"]["combatants"]["goblin-1"]["hp"], 0)
        self.assertFalse(state["combat"]["active"])
        self.assertEqual(state["combat"]["winner_side"], "player")

    def test_negative_damage_is_rejected_before_combat_resolution(self):
        state = {}
        self.start(state)
        before = repr(state)

        with self.assertRaises(ValueError):
            self.apply(
                state,
                {
                    "type": "attack",
                    "actor_id": "player",
                    "target_id": "goblin-1",
                    "attack_bonus": 5,
                    "damage": {"dice": "1d8", "modifier": -1},
                },
            )

        self.assertEqual(repr(state), before)

    def test_hp_zero_requires_consistent_unconscious_state(self):
        state = {}
        self.start(state)
        state["combat"]["combatants"]["player"]["hp"] = 0

        with self.assertRaisesRegex(ValueError, "unconscious state"):
            api._combat_available_actions(state["combat"])

    def test_damage_above_zero_keeps_target_conscious(self):
        state = {}
        self.start(state)
        self.attack(state, d20=20, d8=1)

        target = state["combat"]["combatants"]["goblin-1"]
        self.assertEqual(target["hp"], 3)
        self.assertFalse(target["unconscious"])

    def test_unconscious_current_actor_is_not_selected_at_combat_start(self):
        state = {}
        combatants = self.combatants(player_hp=0)
        combatants.append(
            {
                "id": "player-2",
                "hp": 10,
                "max_hp": 10,
                "ac": 14,
                "initiative_modifier": 1,
                "position": 0,
                "movement_speed": 30,
                "side": "player",
            }
        )
        result = self.start(
            state,
            initiative=(20, 10, 1),
            combatants=combatants,
        )

        self.assertEqual(state["combat"]["turn_index"], 1)
        self.assertEqual(
            result["outcome"]["turn_index"],
            state["combat"]["turn_index"],
        )
        self.assertEqual(state["combat"]["current_actor_id"], "goblin-1")
        self.assertEqual(result["outcome"]["current_actor_id"], "goblin-1")
        self.assertEqual(
            state["combat"]["current_actor_id"],
            result["outcome"]["current_actor_id"],
        )
        self.assertTrue(state["combat"]["active"])

    def test_current_actor_becoming_unconscious_closes_defeat_before_advancing(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["hp"] = 0
        player["unconscious"] = True

        result = self.apply(state, {"type": "end_turn", "actor_id": "player"})

        self.assertFalse(state["combat"]["active"])
        self.assertEqual(state["combat"]["winner_side"], "enemy")
        self.assertTrue(result["outcome"]["combat_ended"])

    def test_unconscious_actor_has_only_end_turn_available(self):
        state = {}
        self.start(state)
        combat = state["combat"]
        combat["current_actor_id"] = "player"
        combat["turn_index"] = 0
        combat["combatants"]["player"]["hp"] = 0
        combat["combatants"]["player"]["unconscious"] = True

        self.assertEqual(api._combat_available_actions(combat), [{"type": "end_turn"}])

    def test_unconscious_actor_cannot_use_resources_without_consuming_them(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["unconscious"] = True

        with self.assertRaisesRegex(ValueError, "unconscious"):
            api._require_bonus_action(state["combat"], "player")
        with self.assertRaisesRegex(ValueError, "unconscious"):
            api._require_reaction(state["combat"], "player")
        self.assertTrue(player["bonus_action_available"])
        self.assertTrue(player["reaction_available"])

    def test_all_unconscious_combatants_end_in_a_draw(self):
        state = {}
        result = self.start(
            state,
            combatants=self.combatants(player_hp=0, goblin_hp=0),
        )

        combat = state["combat"]
        self.assertFalse(combat["active"])
        self.assertIsNone(combat["winner_side"])
        self.assertEqual(combat["available_actions"], [])
        self.assertFalse(result["outcome"]["combat_started"])
        self.assertEqual(result["outcome"]["lifecycle_events"], [])

    def test_post_combat_attack_and_move_are_rejected(self):
        state = {}
        self.start(state, combatants=self.combatants(goblin_hp=1))
        self.attack(state, d20=20, d8=8)

        with self.assertRaisesRegex(ValueError, "combat is not active"):
            self.apply(state, {"type": "move", "actor_id": "player", "distance": 1})
        with self.assertRaisesRegex(ValueError, "combat is not active"):
            self.apply(
                state,
                {
                    "type": "attack",
                    "actor_id": "player",
                    "target_id": "goblin-1",
                    "attack_bonus": 5,
                    "damage": {"dice": "1d8", "modifier": 3},
                },
            )

    def test_post_combat_bonus_and_reaction_are_rejected_without_consuming(self):
        state = {}
        self.start(state, combatants=self.combatants(goblin_hp=1))
        self.attack(state, d20=20, d8=8)
        player = state["combat"]["combatants"]["player"]

        with self.assertRaisesRegex(ValueError, "combat is not active"):
            api._require_bonus_action(state["combat"], "player")
        with self.assertRaisesRegex(ValueError, "combat is not active"):
            api._require_reaction(state["combat"], "player")
        self.assertTrue(player["bonus_action_available"])
        self.assertTrue(player["reaction_available"])

    def test_end_turn_closes_combat_when_defeat_is_already_present(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["hp"] = 0
        player["unconscious"] = True

        result = self.apply(state, {"type": "end_turn", "actor_id": "player"})

        self.assertFalse(state["combat"]["active"])
        self.assertEqual(state["combat"]["winner_side"], "enemy")
        self.assertEqual(state["combat"]["available_actions"], [])
        self.assertFalse(result["outcome"]["combat_active"])
        self.assertTrue(result["outcome"]["combat_ended"])

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

    def test_grappled_actor_cannot_move_or_receive_move_action(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["conditions"] = [{"id": "grappled"}]

        self.assertNotIn(
            {"type": "move"},
            api._combat_available_actions(state["combat"]),
        )
        with self.assertRaises(ValueError):
            self.apply(
                state,
                {"type": "move", "actor_id": "player", "distance": 1},
            )

    def test_grappled_actor_stays_blocked_on_next_turn(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["conditions"] = [{"id": "grappled"}]

        self.apply(state, {"type": "end_turn", "actor_id": "player"})
        self.apply(state, {"type": "end_turn", "actor_id": "goblin-1"})

        self.assertEqual(state["combat"]["current_actor_id"], "player")
        self.assertEqual(player["movement_remaining"], 0)
        self.assertNotIn(
            {"type": "move"},
            state["combat"]["available_actions"],
        )

    def test_applying_and_removing_grappled_restores_unspent_movement(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["movement_remaining"] = 25

        add_condition(player, condition_id="grappled")
        self.assertEqual(player["movement_remaining"], 0)
        self.assertNotIn(
            {"type": "move"},
            api._combat_available_actions(state["combat"]),
        )

        remove_condition(player, condition_id="grappled")
        result = self.apply(
            state,
            {"type": "move", "actor_id": "player", "distance": 5},
        )
        self.assertEqual(result["outcome"]["movement_remaining"], 20)

    def test_saved_movement_survives_public_round_trip(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["movement_remaining"] = 25
        add_condition(player, condition_id="grappled")

        self.apply_through_game_turn_round_trip(
            state,
            {"type": "move", "actor_id": "player", "distance": 0},
        )
        remove_condition(state["combat"]["combatants"]["player"], condition_id="grappled")

        result = self.apply(
            state,
            {"type": "move", "actor_id": "player", "distance": 5},
        )
        self.assertEqual(result["outcome"]["movement_remaining"], 20)

    def test_restrained_blocks_and_removal_restores_unspent_movement(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["movement_remaining"] = 25

        add_condition(player, condition_id="restrained")
        self.assertEqual(player["movement_remaining"], 0)
        with self.assertRaisesRegex(ValueError, "conditioned actor cannot move"):
            self.apply(
                state,
                {"type": "move", "actor_id": "player", "distance": 1},
            )

        remove_condition(player, condition_id="restrained")
        result = self.apply(
            state,
            {"type": "move", "actor_id": "player", "distance": 5},
        )
        self.assertEqual(result["outcome"]["movement_remaining"], 20)

    def test_grappled_and_restrained_remove_in_any_order(self):
        for first_removed in ("grappled", "restrained"):
            with self.subTest(first_removed=first_removed):
                state = {}
                self.start(state)
                player = state["combat"]["combatants"]["player"]
                player["movement_remaining"] = 20
                add_condition(player, condition_id="grappled")
                add_condition(player, condition_id="restrained")

                remove_condition(player, condition_id=first_removed)
                self.assertEqual(player["movement_remaining"], 0)
                self.assertNotIn(
                    {"type": "move"},
                    api._combat_available_actions(state["combat"]),
                )

                remove_condition(
                    player,
                    condition_id=(
                        "restrained"
                        if first_removed == "grappled"
                        else "grappled"
                    ),
                )
                self.assertEqual(player["movement_remaining"], 20)

    def test_prone_with_grappled_or_restrained_restores_and_pays_stand_cost(self):
        for blocker in ("grappled", "restrained"):
            with self.subTest(blocker=blocker):
                state = {}
                self.start(state)
                player = state["combat"]["combatants"]["player"]
                add_condition(player, condition_id="prone")
                add_condition(player, condition_id=blocker)
                self.assertEqual(player["movement_remaining"], 0)

                remove_condition(player, condition_id=blocker)
                result = self.apply(
                    state,
                    {"type": "move", "actor_id": "player", "distance": 5},
                )
                self.assertEqual(player["conditions"], [])
                self.assertEqual(result["outcome"]["movement_remaining"], 10)

    def test_all_three_conditions_require_both_blockers_removed(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        add_condition(player, condition_id="prone")
        add_condition(player, condition_id="grappled")
        add_condition(player, condition_id="restrained")

        remove_condition(player, condition_id="grappled")
        self.assertEqual(player["movement_remaining"], 0)
        remove_condition(player, condition_id="restrained")
        self.assertEqual(player["movement_remaining"], 30)

        result = self.apply(
            state,
            {"type": "move", "actor_id": "player", "distance": 5},
        )
        self.assertEqual(player["conditions"], [])
        self.assertEqual(result["outcome"]["movement_remaining"], 10)

    def test_expiring_grappled_restores_movement_on_next_turn(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        add_condition(
            player,
            condition_id="grappled",
            duration={"kind": "turns", "remaining": 1},
        )
        self.assertEqual(player["movement_remaining"], 0)

        self.apply(state, {"type": "end_turn", "actor_id": "player"})
        self.apply(state, {"type": "end_turn", "actor_id": "goblin-1"})

        self.assertEqual(player["conditions"], [])
        self.assertEqual(player["movement_remaining"], 30)

    def test_removing_grappled_allows_movement(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["conditions"] = [{"id": "grappled"}]
        player["conditions"] = []

        result = self.apply(
            state,
            {"type": "move", "actor_id": "player", "distance": 1},
        )

        self.assertEqual(result["outcome"]["movement_remaining"], 29)

    def test_expiring_grappled_allows_movement(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["conditions"] = [
            {
                "id": "grappled",
                "duration": {"kind": "turns", "remaining": 1},
            }
        ]

        self.apply(state, {"type": "end_turn", "actor_id": "player"})
        self.apply(state, {"type": "end_turn", "actor_id": "goblin-1"})

        self.assertEqual(player["conditions"], [])
        result = self.apply(
            state,
            {"type": "move", "actor_id": "player", "distance": 1},
        )
        self.assertEqual(result["outcome"]["movement_remaining"], 29)

    def test_grappled_does_not_change_other_combat_resources(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["conditions"] = [{"id": "grappled"}]

        self.assertTrue(player["action_available"])
        self.assertTrue(player["bonus_action_available"])
        self.assertTrue(player["reaction_available"])
        self.assertEqual(player["movement_remaining"], 30)

    def test_prone_movement_pays_half_speed_to_stand(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["conditions"] = [{"id": "prone"}]

        self.assertIn(
            {"type": "move"},
            state["combat"]["available_actions"],
        )
        result = self.apply(
            state,
            {"type": "move", "actor_id": "player", "distance": 5},
        )

        self.assertEqual(player["conditions"], [])
        self.assertEqual(result["outcome"]["movement_remaining"], 10)
        self.assertEqual(player["movement_remaining"], 10)

    def test_prone_cannot_stand_without_half_speed_available(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["conditions"] = [{"id": "prone"}]
        player["movement_remaining"] = 10
        state["combat"]["available_actions"] = api._combat_available_actions(state["combat"])

        self.assertNotIn(
            {"type": "move"},
            state["combat"]["available_actions"],
        )
        with self.assertRaises(ValueError):
            self.apply(
                state,
                {"type": "move", "actor_id": "player", "distance": 1},
            )
        self.assertEqual(
            [condition["id"] for condition in player["conditions"]],
            ["prone"],
        )
        self.assertEqual(player["movement_remaining"], 10)

    def test_prone_state_is_consistent_on_next_turn(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["conditions"] = [{"id": "prone"}]

        self.apply(state, {"type": "end_turn", "actor_id": "player"})
        self.apply(state, {"type": "end_turn", "actor_id": "goblin-1"})

        self.assertEqual(player["movement_remaining"], 30)
        result = self.apply(
            state,
            {"type": "move", "actor_id": "player", "distance": 5},
        )
        self.assertEqual(result["outcome"]["movement_remaining"], 10)
        self.assertEqual(player["conditions"], [])

    def test_removing_prone_allows_normal_movement(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["conditions"] = [{"id": "prone"}]
        player["conditions"] = []

        result = self.apply(
            state,
            {"type": "move", "actor_id": "player", "distance": 5},
        )

        self.assertEqual(result["outcome"]["movement_remaining"], 25)

    def test_expiring_prone_allows_normal_movement(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["conditions"] = [
            {
                "id": "prone",
                "duration": {"kind": "turns", "remaining": 1},
            }
        ]

        self.apply(state, {"type": "end_turn", "actor_id": "player"})
        self.apply(state, {"type": "end_turn", "actor_id": "goblin-1"})

        self.assertEqual(player["conditions"], [])
        result = self.apply(
            state,
            {"type": "move", "actor_id": "player", "distance": 5},
        )
        self.assertEqual(result["outcome"]["movement_remaining"], 25)

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

    def test_poisoned_ability_check_uses_disadvantage(self):
        selection = api.GuidedCharacterRequest(
            name="Poisoned",
            class_id="fighter",
            level=1,
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
        )
        character = api.build_guided_character(selection)
        state = {"character": api.character_to_state(character)}
        state["character"]["id"] = "player"
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["conditions"] = [
            {
                "id": "poisoned",
                "source_id": "test",
                "duration": {"kind": "permanent"},
                "effects": [],
            }
        ]

        result = self.apply(
            state,
            {
                "type": "ability_check",
                "ability": "wisdom",
                "dc": 10,
                "character_id": "player",
            },
            randbelow=self.sequence(17, 4),
        )

        self.assertEqual(result["rolls"][0]["mode"], "disadvantage")
        self.assertEqual(result["rolls"][0]["rolls"], [17, 4])
        self.assertEqual(result["rolls"][0]["result"], 4)
        self.assertEqual(result["outcome"]["total"], 4)

    def test_unpoisoned_ability_check_uses_one_d20(self):
        selection = api.GuidedCharacterRequest(
            name="Healthy",
            class_id="fighter",
            level=1,
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
        )
        character = api.build_guided_character(selection)
        state = {"character": api.character_to_state(character)}
        state["character"]["id"] = "player"
        self.start(state)

        result = self.apply(
            state,
            {
                "type": "ability_check",
                "ability": "wisdom",
                "dc": 10,
                "character_id": "player",
            },
            randbelow=self.sequence(17),
        )

        self.assertEqual(result["rolls"], [{"type": "d20", "result": 17}])
        self.assertEqual(result["outcome"]["total"], 17)

    def test_poisoned_attack_uses_disadvantage_and_keeps_damage_resolution(self):
        state = {}
        self.start(state)
        state["combat"]["combatants"]["player"]["conditions"] = [
            {
                "id": "poisoned",
                "source_id": "test-effect",
                "duration": {"kind": "permanent"},
                "effects": [],
            }
        ]
        result = self.apply(
            state,
            {
                "type": "attack",
                "actor_id": "player",
                "target_id": "goblin-1",
                "attack_bonus": 5,
                "damage": {"dice": "1d8", "modifier": 3},
            },
            randbelow=self.sequence(19, 17, 3),
        )

        self.assertEqual(result["rolls"][0]["mode"], "disadvantage")
        self.assertEqual(result["rolls"][0]["rolls"], [19, 17])
        self.assertEqual(result["rolls"][0]["result"], 17)
        self.assertTrue(result["outcome"]["hit"])
        self.assertEqual(result["outcome"]["total"], 22)
        self.assertEqual(result["outcome"]["damage"], 6)

    def test_unpoisoned_attack_uses_normal_roll_mode(self):
        state = {}
        self.start(state)

        result = self.attack(state, d20=17, d8=4)

        self.assertEqual(result["rolls"][0], {"type": "d20", "result": 17})
        self.assertEqual(result["outcome"]["total"], 22)

    def test_restrained_blocks_movement_and_resets_to_zero_next_turn(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["conditions"] = [{"id": "restrained"}]
        state["combat"]["available_actions"] = api._combat_available_actions(state["combat"])

        self.assertNotIn({"type": "move"}, state["combat"]["available_actions"])
        with self.assertRaises(ValueError):
            self.apply(
                state,
                {"type": "move", "actor_id": "player", "distance": 1},
            )

        self.apply(state, {"type": "end_turn", "actor_id": "player"})
        self.apply(state, {"type": "end_turn", "actor_id": "goblin-1"})
        self.assertEqual(player["movement_remaining"], 0)

    def test_restrained_attack_is_disadvantage(self):
        state = {}
        self.start(state)
        state["combat"]["combatants"]["player"]["conditions"] = [
            {"id": "restrained"}
        ]

        result = self.attack(state, rolls=(19, 17, 3))

        self.assertEqual(result["rolls"][0]["mode"], "disadvantage")
        self.assertEqual(result["rolls"][0]["rolls"], [19, 17])
        self.assertEqual(result["rolls"][0]["result"], 17)

    def test_attack_against_restrained_is_advantage(self):
        state = {}
        self.start(state)
        state["combat"]["combatants"]["goblin-1"]["conditions"] = [
            {"id": "restrained"}
        ]

        result = self.attack(state, rolls=(2, 17, 3))

        self.assertEqual(result["rolls"][0]["mode"], "advantage")
        self.assertEqual(result["rolls"][0]["rolls"], [2, 17])
        self.assertEqual(result["rolls"][0]["result"], 17)

    def test_restrained_dexterity_save_is_disadvantage(self):
        selection = api.GuidedCharacterRequest(
            name="Restrained",
            class_id="fighter",
            level=1,
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
        )
        character = api.build_guided_character(selection)
        state = {"character": api.character_to_state(character)}
        state["character"]["id"] = "player"
        self.start(state)
        state["combat"]["combatants"]["player"]["conditions"] = [
            {"id": "restrained"}
        ]

        result = self.apply(
            state,
            {
                "type": "saving_throw",
                "ability": "dexterity",
                "dc": 10,
                "character_id": "player",
            },
            randbelow=self.sequence(19, 3),
        )

        self.assertEqual(result["rolls"][0]["mode"], "disadvantage")
        self.assertEqual(result["rolls"][0]["rolls"], [19, 3])
        self.assertEqual(result["rolls"][0]["result"], 3)

    def test_restrained_strength_save_is_not_disadvantage(self):
        selection = api.GuidedCharacterRequest(
            name="Restrained",
            class_id="fighter",
            level=1,
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
        )
        character = api.build_guided_character(selection)
        state = {"character": api.character_to_state(character)}
        state["character"]["id"] = "player"
        self.start(state)
        state["combat"]["combatants"]["player"]["conditions"] = [
            {"id": "restrained"}
        ]

        result = self.apply(
            state,
            {
                "type": "saving_throw",
                "ability": "strength",
                "dc": 10,
                "character_id": "player",
            },
            randbelow=self.sequence(19),
        )

        self.assertEqual(result["rolls"], [{"type": "d20", "result": 19}])

    def test_restrained_and_poisoned_do_not_stack_disadvantage(self):
        state = {}
        self.start(state)
        state["combat"]["combatants"]["player"]["conditions"] = [
            {"id": "restrained"},
            {"id": "poisoned"},
        ]

        result = self.attack(state, rolls=(19, 17, 3))

        self.assertEqual(result["rolls"][0]["mode"], "disadvantage")
        self.assertEqual(result["rolls"][0]["rolls"], [19, 17])

    def test_restrained_with_prone_target_keeps_single_advantage(self):
        state = {}
        self.start(state)
        state["combat"]["combatants"]["goblin-1"]["conditions"] = [
            {"id": "restrained"},
            {"id": "prone"},
        ]

        result = self.attack(state, rolls=(2, 17, 3))

        self.assertEqual(result["rolls"][0]["mode"], "advantage")
        self.assertEqual(result["rolls"][0]["rolls"], [2, 17])

    def test_removing_restrained_restores_movement(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["conditions"] = [{"id": "restrained"}]
        player["conditions"] = []

        result = self.apply(
            state,
            {"type": "move", "actor_id": "player", "distance": 5},
        )

        self.assertEqual(result["outcome"]["movement_remaining"], 25)

    def test_bonus_action_resource_consumes_once_without_affecting_other_resources(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["movement_remaining"] = 12

        actor = api._require_bonus_action(state["combat"], "player")
        api._consume_bonus_action(actor)

        self.assertFalse(player["bonus_action_available"])
        self.assertTrue(player["action_available"])
        self.assertTrue(player["reaction_available"])
        self.assertEqual(player["movement_remaining"], 12)
        with self.assertRaisesRegex(ValueError, "BLOCKED_ACTION"):
            api._require_bonus_action(state["combat"], "player")
        self.assertFalse(player["bonus_action_available"])

    def test_bonus_action_resource_rejects_unconscious_actor_without_consuming(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["hp"] = 0
        player["unconscious"] = True

        with self.assertRaisesRegex(ValueError, "unconscious"):
            api._require_bonus_action(state["combat"], "player")
        self.assertTrue(player["bonus_action_available"])

    def test_reaction_resource_consumes_once_without_affecting_other_resources(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["movement_remaining"] = 12

        actor = api._require_reaction(state["combat"], "player")
        api._consume_reaction(actor)

        self.assertFalse(player["reaction_available"])
        self.assertTrue(player["action_available"])
        self.assertTrue(player["bonus_action_available"])
        self.assertEqual(player["movement_remaining"], 12)
        with self.assertRaisesRegex(ValueError, "BLOCKED_ACTION"):
            api._require_reaction(state["combat"], "player")
        self.assertFalse(player["reaction_available"])

    def test_reaction_resource_rejects_unconscious_actor_without_consuming(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["hp"] = 0
        player["unconscious"] = True

        with self.assertRaisesRegex(ValueError, "unconscious"):
            api._require_reaction(state["combat"], "player")
        self.assertTrue(player["reaction_available"])

    def test_reaction_resource_recovers_on_next_turn(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        api._consume_reaction(api._require_reaction(state["combat"], "player"))

        self.apply(state, {"type": "end_turn", "actor_id": "player"})
        self.apply(state, {"type": "end_turn", "actor_id": "goblin-1"})

        self.assertTrue(player["reaction_available"])

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

    def test_until_end_of_turn_expires_for_current_actor(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["conditions"] = [
            {
                "id": "poisoned",
                "duration": {"kind": "until_end_of_turn"},
                "_timing": {
                    "applied_round": 1,
                    "applied_turn_index": 0,
                    "applied_phase": "turn_start",
                },
            }
        ]

        self.apply(state, {"type": "end_turn", "actor_id": "player"})

        self.assertEqual(player["conditions"], [])

    def test_until_start_of_turn_expires_on_next_actor_start(self):
        state = {}
        self.start(state)
        goblin = state["combat"]["combatants"]["goblin-1"]
        goblin["conditions"] = [
            {
                "id": "poisoned",
                "duration": {"kind": "until_start_of_turn"},
                "_timing": {
                    "applied_round": 1,
                    "applied_turn_index": 0,
                    "applied_phase": "turn_end",
                },
            }
        ]

        self.apply(state, {"type": "end_turn", "actor_id": "player"})

        self.assertEqual(goblin["conditions"], [])

    def test_round_duration_survives_current_round_and_expires_after_next_complete_round(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["conditions"] = [
            {
                "id": "frightened",
                "duration": {"kind": "rounds", "remaining": 1},
                "_timing": {
                    "applied_round": 1,
                    "applied_turn_index": 0,
                    "applied_phase": "turn_start",
                },
            }
        ]

        self.apply(state, {"type": "end_turn", "actor_id": "player"})
        self.assertEqual(len(player["conditions"]), 1)
        self.apply(state, {"type": "end_turn", "actor_id": "goblin-1"})
        self.assertEqual(len(player["conditions"]), 1)
        self.assertEqual(state["combat"]["round"], 2)
        self.apply(state, {"type": "end_turn", "actor_id": "player"})
        self.apply(state, {"type": "end_turn", "actor_id": "goblin-1"})

        self.assertEqual(player["conditions"], [])

    def test_condition_timing_survives_state_round_trip(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["conditions"] = [
            {
                "id": "frightened",
                "duration": {"kind": "rounds", "remaining": 2},
                "_timing": {
                    "applied_round": 1,
                    "applied_turn_index": 0,
                    "applied_phase": "turn_start",
                },
            }
        ]

        self.apply(state, {"type": "end_turn", "actor_id": "player"})

        self.assertEqual(
            state["combat"]["combatants"]["player"]["conditions"][0]["_timing"],
            {
                "applied_round": 1,
                "applied_turn_index": 0,
                "applied_phase": "turn_start",
            },
        )

    def test_rounds_one_survives_application_at_beginning_middle_and_end(self):
        scenarios = {
            "beginning": (0, "turn_start"),
            "middle": (1, "turn_start"),
            "end": (1, "turn_end"),
        }
        for name, (application_turn_index, application_phase) in scenarios.items():
            with self.subTest(name=name):
                state = {}
                self.start(state)
                if application_turn_index == 1:
                    self.end_turn_round_trip(state, "player")
                player = state["combat"]["combatants"]["player"]
                player["conditions"] = [
                    {
                        "id": "frightened",
                        "duration": {"kind": "rounds", "remaining": 1},
                        "_timing": {
                            "applied_round": 1,
                            "applied_turn_index": application_turn_index,
                            "applied_phase": application_phase,
                        },
                    }
                ]

                if application_turn_index == 0:
                    self.end_turn_round_trip(state, "player")
                self.assertEqual(
                    len(state["combat"]["combatants"]["player"]["conditions"]),
                    1,
                )
                self.end_turn_round_trip(state, "goblin-1")
                self.assertEqual(state["combat"]["round"], 2)
                player = state["combat"]["combatants"]["player"]
                self.assertEqual(len(player["conditions"]), 1)
                self.assertIn("_timing", player["conditions"][0])

                self.end_turn_round_trip(state, "player")
                self.end_turn_round_trip(state, "goblin-1")
                player = state["combat"]["combatants"]["player"]
                self.assertEqual(player["conditions"], [])

    def test_rounds_two_survives_application_at_beginning_middle_and_end(self):
        scenarios = {
            "beginning": (0, "turn_start"),
            "middle": (1, "turn_start"),
            "end": (1, "turn_end"),
        }
        for name, (application_turn_index, application_phase) in scenarios.items():
            with self.subTest(name=name):
                state = {}
                self.start(state)
                if application_turn_index == 1:
                    self.end_turn_round_trip(state, "player")
                player = state["combat"]["combatants"]["player"]
                player["conditions"] = [
                    {
                        "id": "frightened",
                        "duration": {"kind": "rounds", "remaining": 2},
                        "_timing": {
                            "applied_round": 1,
                            "applied_turn_index": application_turn_index,
                            "applied_phase": application_phase,
                        },
                    }
                ]

                if application_turn_index == 0:
                    self.end_turn_round_trip(state, "player")
                self.end_turn_round_trip(state, "goblin-1")
                self.assertEqual(state["combat"]["round"], 2)
                player = state["combat"]["combatants"]["player"]
                self.assertEqual(len(player["conditions"]), 1)
                self.assertEqual(player["conditions"][0]["duration"]["remaining"], 2)

                self.end_turn_round_trip(state, "player")
                self.end_turn_round_trip(state, "goblin-1")
                self.assertEqual(state["combat"]["round"], 3)
                player = state["combat"]["combatants"]["player"]
                self.assertEqual(len(player["conditions"]), 1)
                self.assertEqual(player["conditions"][0]["duration"]["remaining"], 1)

                self.end_turn_round_trip(state, "player")
                self.end_turn_round_trip(state, "goblin-1")
                player = state["combat"]["combatants"]["player"]
                self.assertEqual(player["conditions"], [])

    def test_round_trip_removes_timing_when_until_condition_expires(self):
        state = {}
        self.start(state)
        player = state["combat"]["combatants"]["player"]
        player["conditions"] = [
            {
                "id": "poisoned",
                "duration": {"kind": "until_end_of_turn"},
                "_timing": {
                    "applied_round": 1,
                    "applied_turn_index": 0,
                    "applied_phase": "turn_start",
                },
            }
        ]

        result = self.end_turn_round_trip(state, "player")

        self.assertEqual(result["outcome"]["expired_conditions"], ["poisoned"])
        self.assertEqual(
            state["combat"]["combatants"]["player"]["conditions"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
