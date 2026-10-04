import unittest

from rule_engine.conditions import (
    add_condition,
    advance_condition_durations,
    clear_conditions_for_rest,
    has_condition,
    normalize_conditions,
    remove_condition,
    remove_condition_instance,
)


class ConditionTests(unittest.TestCase):
    def test_new_creature_has_no_conditions(self):
        creature = {}

        self.assertEqual(normalize_conditions(creature.get("conditions")), [])
        self.assertFalse(has_condition(creature, "poisoned"))

    def test_add_condition_creates_structured_instance(self):
        creature = {}

        condition = add_condition(
            creature,
            condition_id="poisoned",
            source_id="trap-1",
            duration={
                "kind": "turns",
                "remaining": 2,
            },
            effects=[
                {
                    "id": "attack_and_check_disadvantage",
                }
            ],
        )

        self.assertEqual(condition["id"], "poisoned")
        self.assertEqual(condition["source_id"], "trap-1")
        self.assertEqual(condition["duration"]["remaining"], 2)
        self.assertTrue(has_condition(creature, "poisoned"))

    def test_same_condition_can_have_independent_instances(self):
        creature = {}

        add_condition(
            creature,
            condition_id="frightened",
            source_id="enemy-a",
            duration={
                "kind": "turns",
                "remaining": 1,
            },
        )

        add_condition(
            creature,
            condition_id="frightened",
            source_id="enemy-b",
            duration={
                "kind": "turns",
                "remaining": 3,
            },
        )

        self.assertEqual(len(creature["conditions"]), 2)
        self.assertTrue(has_condition(creature, "frightened"))

    def test_turn_duration_expires_at_turn_end(self):
        creature = {}

        add_condition(
            creature,
            condition_id="poisoned",
            duration={
                "kind": "turns",
                "remaining": 1,
            },
        )

        expired = advance_condition_durations(
            creature,
            timing="turn_end",
        )

        self.assertEqual(
            [condition["id"] for condition in expired],
            ["poisoned"],
        )
        self.assertFalse(has_condition(creature, "poisoned"))

    def test_turn_duration_decrements_without_expiring(self):
        creature = {}

        add_condition(
            creature,
            condition_id="poisoned",
            duration={
                "kind": "turns",
                "remaining": 2,
            },
        )

        expired = advance_condition_durations(
            creature,
            timing="turn_end",
        )

        self.assertEqual(expired, [])
        self.assertTrue(has_condition(creature, "poisoned"))
        self.assertEqual(
            creature["conditions"][0]["duration"]["remaining"],
            1,
        )

    def test_round_duration_only_decrements_at_round_end(self):
        creature = {}

        add_condition(
            creature,
            condition_id="frightened",
            duration={
                "kind": "rounds",
                "remaining": 2,
            },
        )

        advance_condition_durations(
            creature,
            timing="turn_end",
        )

        self.assertEqual(
            creature["conditions"][0]["duration"]["remaining"],
            2,
        )

        advance_condition_durations(
            creature,
            timing="round_end",
        )

        self.assertEqual(
            creature["conditions"][0]["duration"]["remaining"],
            1,
        )

    def test_remove_condition_removes_all_instances_of_same_condition(self):
        creature = {}

        add_condition(
            creature,
            condition_id="poisoned",
            source_id="a",
        )

        add_condition(
            creature,
            condition_id="poisoned",
            source_id="b",
        )

        removed = remove_condition(
            creature,
            condition_id="poisoned",
        )

        self.assertEqual(removed, 2)
        self.assertFalse(has_condition(creature, "poisoned"))
        self.assertEqual(creature["conditions"], [])

    def test_remove_condition_instance_removes_only_one_instance(self):
        creature = {}

        add_condition(
            creature,
            condition_id="frightened",
            source_id="a",
        )

        add_condition(
            creature,
            condition_id="frightened",
            source_id="b",
        )

        removed = remove_condition_instance(
            creature,
            condition_index=0,
        )

        self.assertEqual(removed["source_id"], "a")
        self.assertEqual(len(creature["conditions"]), 1)
        self.assertEqual(
            creature["conditions"][0]["source_id"],
            "b",
        )

    def test_until_rest_condition_is_removed_by_short_rest(self):
        creature = {}

        add_condition(
            creature,
            condition_id="grappled",
            duration={
                "kind": "until_rest",
            },
        )

        removed = clear_conditions_for_rest(
            creature,
            rest_type="short_rest",
        )

        self.assertEqual(
            [condition["id"] for condition in removed],
            ["grappled"],
        )
        self.assertFalse(has_condition(creature, "grappled"))

    def test_permanent_condition_survives_rest(self):
        creature = {}

        add_condition(
            creature,
            condition_id="poisoned",
            duration={
                "kind": "permanent",
            },
        )

        clear_conditions_for_rest(
            creature,
            rest_type="long_rest",
        )

        self.assertTrue(has_condition(creature, "poisoned"))

    def test_unknown_condition_is_rejected(self):
        creature = {}

        with self.assertRaises(ValueError):
            add_condition(
                creature,
                condition_id="not-a-real-condition",
            )

    def test_invalid_duration_is_rejected(self):
        creature = {}

        with self.assertRaises(ValueError):
            add_condition(
                creature,
                condition_id="poisoned",
                duration={
                    "kind": "turns",
                    "remaining": 0,
                },
            )


if __name__ == "__main__":
    unittest.main()
