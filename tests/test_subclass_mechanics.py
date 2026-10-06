import unittest
from copy import deepcopy

from rule_engine.app import ResolveRequest, resolve_request


BASE = {
    "id": "monk-1",
    "level": 6,
    "experience_points": 14000,
    "class": {"id": "monk", "level": 6},
    "subclass_id": "warrior_of_mercy",
    "abilities": {"strength": 10, "dexterity": 16, "constitution": 12, "intelligence": 10, "wisdom": 16, "charisma": 10},
    "resources": {"focus_points": {"id": "focus_points", "current": 6, "maximum": 6, "recovery": "short_rest"}},
    "conditions": [],
}


class SubclassMechanicsTests(unittest.TestCase):
    @staticmethod
    def roll_one(_upper_bound):
        return 0

    def apply(self, action, state):
        body = ResolveRequest(action=action, state=state)
        return resolve_request(body, randbelow=self.roll_one)

    def test_physicians_touch_applies_and_removes_confirmed_conditions(self):
        state = {"character": deepcopy(BASE)}
        state["combat"] = {"active": True, "combatants": {"monk-1": {"id": "monk-1", "character": state["character"]}, "target": {"id": "target", "character": {"id": "target", "conditions": []}}}}
        result = self.apply({"type": "use_subclass_feature", "feature_id": "physicians_touch", "actor_id": "monk-1", "target_id": "target", "mode": "harm"}, state)
        self.assertEqual(result["outcome"]["condition"]["id"], "poisoned")
        self.assertTrue(state["combat"]["combatants"]["target"]["character"]["conditions"])

    def test_select_subclass_is_canonical_and_class_bound(self):
        state = {"character": {"id": "fighter-1", "level": 3, "experience_points": 2700, "class": {"id": "fighter", "level": 3}, "abilities": {"strength": 16, "dexterity": 14, "constitution": 14, "intelligence": 10, "wisdom": 12, "charisma": 8}, "proficiencies": {"skills": ["athletics"], "saving_throws": ["strength", "constitution"]}}}
        result = self.apply({"type": "select_subclass", "character_id": "fighter-1", "subclass_id": "battle_master"}, state)
        self.assertEqual(result["outcome"]["subclass_id"], "battle_master")
        self.assertEqual(state["character"]["subclass_id"], "battle_master")

        state = {"character": {"id": "fighter-2", "level": 3, "experience_points": 2700, "class": {"id": "fighter", "level": 3}, "abilities": BASE["abilities"], "proficiencies": {"skills": ["athletics"], "saving_throws": ["strength", "constitution"]}}}
        with self.assertRaises(ValueError):
            self.apply({"type": "select_subclass", "character_id": "fighter-2", "subclass_id": "warrior_of_mercy"}, state)

    def test_elemental_burst_consumes_focus_and_rolls_server_owned_damage(self):
        state = {"character": {**deepcopy(BASE), "subclass_id": "warrior_of_the_elements"}}
        result = self.apply({"type": "use_subclass_feature", "feature_id": "elemental_burst", "actor_id": "monk-1", "mode": "fire"}, state)
        self.assertEqual(result["outcome"]["focus_spent"], 2)
        self.assertEqual(result["outcome"]["damage"], 3)
        self.assertEqual(state["character"]["resources"]["focus_points"]["current"], 4)

    def test_wrong_subclass_and_unsupported_feature_fail_closed(self):
        state = {"character": deepcopy(BASE)}
        state["character"]["subclass_id"] = "warrior_of_shadow"
        with self.assertRaises(ValueError):
            self.apply({"type": "use_subclass_feature", "feature_id": "physicians_touch", "actor_id": "monk-1"}, state)
        with self.assertRaises(ValueError):
            self.apply({"type": "use_subclass_feature", "feature_id": "invented_feature", "actor_id": "monk-1"}, {"character": deepcopy(BASE)})

    def test_wholeness_of_body_materializes_its_long_rest_resource(self):
        state = {"character": {**deepcopy(BASE), "subclass_id": "warrior_of_the_open_hand"}}
        result = self.apply({"type": "use_subclass_feature", "feature_id": "wholeness_of_body", "actor_id": "monk-1"}, state)
        self.assertEqual(result["outcome"]["healing"], 4)
        self.assertEqual(state["character"]["resources"]["wholeness_of_body"]["maximum"], 3)


if __name__ == "__main__":
    unittest.main()
