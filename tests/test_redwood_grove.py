import unittest
from unittest.mock import Mock

import rule_engine.app as api
from game.contracts import GameTurnRequest
from game.orchestrator import GameOrchestrator, RuleResolutionError


class RedwoodGroveTests(unittest.TestCase):
    def scene(self):
        return api._redwood_grove_scene("character-1")

    def state(self):
        return {
            "character": {"id": "character-1"},
            "scene": self.scene(),
            "adventure": {"id": "dragon-delves-death-at-sunset", "redwood_samples": []},
        }

    def test_redwood_snapshot_contains_only_server_owned_actions(self):
        actions = self.scene()["available_actions"]
        self.assertEqual(
            [action["type"] for action in actions],
            ["adventure_action", "skill_check"],
        )
        self.assertEqual(actions[0]["intent"], "collect_bark_sample")
        self.assertEqual(actions[1]["dc"], 12)
        self.assertEqual(actions[1]["skill"], "persuasion")

    def test_collect_bark_sample_is_a_server_owned_state_transition(self):
        state = self.state()
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
        action = state["scene"]["available_actions"][0]
        api.resolve_game_action(action, state)

        with self.assertRaises(ValueError):
            api.resolve_game_action(action, state)

        with self.assertRaises(ValueError):
            api.resolve_game_action({**action, "tree_id": "r7"}, self.state())

    def test_gate_adventure_intent_binds_to_snapshot_before_resolution(self):
        narrator = Mock()
        narrator.narrate.return_value = "A amostra é guardada com cuidado."
        orchestrator = GameOrchestrator(
            narrator,
            resolve_action=api.resolve_game_action,
            interpret_intent=lambda **_kwargs: {
                "schema_version": "resolution-gate-v1",
                "requires_resolution": False,
                "adventure_action": {
                    "type": "adventure_action",
                    "intent": "collect_bark_sample",
                },
            },
        )
        state = self.state()
        response = orchestrator.turn(GameTurnRequest(
            campaign_id="campaign-redwood",
            state=state,
            player_input="Coleto uma amostra da casca.",
        ))

        self.assertEqual(response.rule_resolution["status"], "resolved")
        self.assertEqual(response.rule_resolution["action"]["tree_id"], "r3")
        self.assertEqual(response.state["adventure"]["redwood_samples"], ["r3"])
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
                    "intent": "collect_bark_sample",
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


if __name__ == "__main__":
    unittest.main()


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
