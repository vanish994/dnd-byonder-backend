import unittest
from unittest.mock import Mock, patch

import rule_engine.app as api
from game.contracts import GameTurnRequest, PHB2024GuidedCharacterRequest
from game.orchestrator import GameOrchestrator, RuleResolutionError
from rule_engine.character import character_to_state
from rule_engine.character_creation_phb2024 import build_phb2024_character


class RedwoodGroveTests(unittest.TestCase):
    def scene(self):
        return api._redwood_grove_scene("character-1")

    def state(self):
        character = build_phb2024_character(PHB2024GuidedCharacterRequest(
            name='Redwood Tester', class_id='fighter', level=1, species_id='dwarf',
            species_choices={}, background_id='farmer', alignment_id='neutral_good',
            ability_method_id='standard_array',
            base_abilities={'strength': 15, 'dexterity': 14, 'constitution': 13,
                            'intelligence': 12, 'wisdom': 10, 'charisma': 8},
            background_ability_increases={'strength': 2, 'constitution': 1},
            abilities={'strength': 17, 'dexterity': 14, 'constitution': 14,
                       'intelligence': 12, 'wisdom': 10, 'charisma': 8},
            skills=['athletics', 'persuasion'], language_choices=['draconic', 'dwarvish'],
            class_equipment_option='A', background_equipment_option='A', class_choices={},
        ))
        character_state = character_to_state(character)
        character_state['id'] = 'character-1'
        return {
            "character": character_state,
            "scene": self.scene(),
            "adventure": {
                "id": "dragon-delves-death-at-sunset",
                "redwood_samples": [],
                "kaynen_attitude": "hostile",
            },
        }

    def make_friendly(self, state):
        api.resolve_game_action(state["scene"]["available_actions"][0], state)
        persuasion = state["scene"]["available_actions"][0]
        with patch("rule_engine.app.roll_dice", return_value={"rolls": [20]}):
            api.resolve_game_action(persuasion, state)

    def test_redwood_snapshot_contains_only_server_owned_actions(self):
        actions = self.scene()["available_actions"]
        self.assertEqual([action["type"] for action in actions], ["adventure_action", "skill_check"])
        self.assertEqual(actions[0]["intent"], "show_respect_to_kaynen")
        self.assertEqual(actions[1]["dc"], 14)
        self.assertEqual(actions[1]["skill"], "perception")

    def test_respecting_kaynen_unlocks_server_owned_influence_check(self):
        state = self.state()
        resolution = api.resolve_game_action(state["scene"]["available_actions"][0], state)
        self.assertEqual(resolution["status"], "resolved")
        self.assertEqual(state["adventure"]["kaynen_attitude"], "indifferent")
        self.assertEqual(
            [item["skill"] for item in state["scene"]["available_actions"]],
            ["persuasion", "perception"],
        )

    def test_bark_sample_is_locked_until_kaynen_becomes_friendly(self):
        state = self.state()
        forged = {"type": "adventure_action", "intent": "collect_bark_sample", "tree_id": "r3"}
        with self.assertRaises(ValueError):
            api.resolve_game_action(forged, state)

    def test_risky_investigation_reveals_only_authorized_fact_on_success(self):
        state = self.state()
        action = state["scene"]["available_actions"][1]
        with patch("rule_engine.app.roll_dice", return_value={"rolls": [20]}):
            resolution = api.resolve_game_action(action, state)
        self.assertTrue(resolution["outcome"]["success"])
        self.assertEqual(
            resolution["outcome"]["narrative_facts"],
            [{"id": "redwood-grove-armin-tracks", "location": "redwood-grove-r4"}],
        )
        self.assertEqual(state["adventure"]["discoveries"], [
            {"id": "redwood-grove-armin-tracks", "location": "redwood-grove-r4"},
        ])

    def test_risky_investigation_does_not_leak_protected_fact_on_failure(self):
        state = self.state()
        action = state["scene"]["available_actions"][1]
        with patch("rule_engine.app.roll_dice", return_value={"rolls": [1]}):
            resolution = api.resolve_game_action(action, state)
        self.assertFalse(resolution["outcome"]["success"])
        self.assertEqual(resolution["outcome"]["narrative_facts"], [])
        self.assertEqual(state["adventure"].get("discoveries", []), [])

    def test_successful_influence_promotes_kaynen_and_unlocks_sample(self):
        state = self.state()
        self.make_friendly(state)
        self.assertEqual(state["adventure"]["kaynen_attitude"], "friendly")
        self.assertEqual(state["scene"]["available_actions"][0]["intent"], "collect_bark_sample")
        self.assertEqual(state["scene"]["available_actions"][0]["tree_id"], "r3")

    def test_collect_bark_sample_is_a_server_owned_state_transition(self):
        state = self.state()
        self.make_friendly(state)
        action = state["scene"]["available_actions"][0]
        resolution = api.resolve_game_action(action, state)
        self.assertEqual(resolution["status"], "resolved")
        self.assertEqual(resolution["rolls"], [])
        self.assertEqual(state["adventure"]["redwood_samples"], ["r3"])
        self.assertTrue(resolution["outcome"]["sample_collected"])
        self.assertEqual(
            resolution["outcome"]["narrative_facts"],
            [{"id": "redwood-bark-sample-collected", "tree_id": "r3"}],
        )
        self.assertFalse(any(item.get("intent") == "collect_bark_sample" for item in state["scene"]["available_actions"]))

    def test_duplicate_or_forged_tree_action_is_rejected(self):
        state = self.state()
        self.make_friendly(state)
        action = state["scene"]["available_actions"][0]
        api.resolve_game_action(action, state)
        with self.assertRaises(ValueError):
            api.resolve_game_action(action, state)
        with self.assertRaises(ValueError):
            api.resolve_game_action({**action, "tree_id": "r7"}, self.state())

    def test_gate_adventure_intent_binds_to_snapshot_before_resolution(self):
        narrator = Mock()
        narrator.narrate.return_value = "Você respeita o pedido de Kaynen."
        orchestrator = GameOrchestrator(
            narrator,
            resolve_action=api.resolve_game_action,
            interpret_intent=lambda **_kwargs: {
                "schema_version": "resolution-gate-v1",
                "requires_resolution": False,
                "adventure_action": {
                    "type": "adventure_action",
                    "intent": "show_respect_to_kaynen",
                },
            },
        )
        state = self.state()
        response = orchestrator.turn(GameTurnRequest(
            campaign_id="campaign-redwood",
            state=state,
            player_input="Respeito o pedido de Kaynen.",
        ))
        self.assertEqual(response.rule_resolution["status"], "resolved")
        self.assertEqual(response.rule_resolution["action"]["intent"], "show_respect_to_kaynen")
        self.assertEqual(response.state["adventure"]["kaynen_attitude"], "indifferent")
        narrator.narrate.assert_called_once()

    def test_gate_cannot_create_unlisted_adventure_action(self):
        narrator = Mock()
        orchestrator = GameOrchestrator(
            narrator,
            resolve_action=api.resolve_game_action,
            interpret_intent=lambda **_kwargs: {
                "schema_version": "resolution-gate-v1",
                "requires_resolution": False,
                "adventure_action": {
                    "type": "adventure_action",
                    "intent": "show_respect_to_kaynen",
                },
            },
        )
        state = self.state()
        state["scene"]["available_actions"] = [
            action for action in state["scene"]["available_actions"]
            if action["type"] != "adventure_action"
        ]
        with self.assertRaises(RuleResolutionError):
            orchestrator.turn(GameTurnRequest(
                campaign_id="campaign-redwood",
                state=state,
                player_input="Faço algo criativo na árvore.",
            ))
        narrator.narrate.assert_not_called()


def test_adventure_catalog_and_bootstrap_are_server_owned():
    from game.adventure_catalog import get_adventure, list_adventures
    catalog = list_adventures()
    adventure = get_adventure('dragon-delves-death-at-sunset')
    assert catalog['schema_version'] == 'adventure-catalog-v1'
    assert catalog['ruleset'] == 'dnd-2024-phb'
    assert adventure['initial_scene_id'] == 'redwood-watch'
    scene = api._redwood_watch_scene('character-1')
    state = api._adventure_state(adventure, scene)
    assert state['scene_id'] == 'redwood-watch'
    assert state['objective'] == 'Investigar a corrupção e os desaparecimentos.'
    assert scene['available_actions'][0]['skill'] == 'persuasion'
    assert scene['available_actions'][0]['dc'] == 12
