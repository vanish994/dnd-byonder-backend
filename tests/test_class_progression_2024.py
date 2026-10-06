import unittest

from rule_engine.classes import (
    class_attack_count,
    class_features,
    class_resource_maximum,
    class_resource_ids,
    class_spellcasting,
    class_subclass_features,
    class_subclass_options,
)


CLASS_IDS = (
    "barbarian", "bard", "cleric", "druid", "fighter", "monk",
    "paladin", "ranger", "rogue", "sorcerer", "warlock", "wizard",
)


class PHB2024ClassProgressionTests(unittest.TestCase):
    def test_all_twelve_classes_are_implemented_through_level_three(self):
        for class_id in CLASS_IDS:
            with self.subTest(class_id=class_id):
                level_one = class_features(class_id, 1)
                level_two = class_features(class_id, 2)
                level_three = class_features(class_id, 3)
                self.assertTrue(level_one)
                self.assertGreaterEqual(len(level_two), len(level_one))
                self.assertGreaterEqual(len(level_three), len(level_two))
                self.assertEqual(class_attack_count(class_id, 3), 1)
                self.assertTrue(class_subclass_options(class_id))

    def test_spellcasting_progression_matches_confirmed_phb_2024_tables(self):
        expected = {
            "bard": ({1: 2}, {1: 3}, {1: 4, 2: 2}),
            "cleric": ({1: 2}, {1: 3}, {1: 4, 2: 2}),
            "druid": ({1: 2}, {1: 3}, {1: 4, 2: 2}),
            "paladin": ({1: 2}, {1: 2}, {1: 3}),
            "ranger": ({1: 2}, {1: 2}, {1: 3}),
            "sorcerer": ({1: 2}, {1: 3}, {1: 4, 2: 2}),
            "warlock": ({1: 1}, {1: 2}, {2: 2}),
            "wizard": ({1: 2}, {1: 3}, {1: 4, 2: 2}),
        }
        for class_id, slots in expected.items():
            with self.subTest(class_id=class_id):
                self.assertEqual(class_spellcasting(class_id, 1)["slots"], slots[0])
                self.assertEqual(class_spellcasting(class_id, 2)["slots"], slots[1])
                self.assertEqual(class_spellcasting(class_id, 3)["slots"], slots[2])

    def test_non_spellcasting_classes_do_not_receive_invented_slots(self):
        for class_id in ("barbarian", "fighter", "monk", "rogue"):
            with self.subTest(class_id=class_id):
                self.assertIsNone(class_spellcasting(class_id, 3))

    def test_resource_counts_are_server_owned_and_level_bound(self):
        self.assertEqual(class_resource_maximum("barbarian", "rages", 3), 3)
        self.assertEqual(class_resource_maximum("monk", "focus_points", 3), 3)
        self.assertEqual(class_resource_maximum("paladin", "lay_on_hands", 3), 15)
        self.assertEqual(class_resource_maximum("sorcerer", "sorcery_points", 3), 3)
        self.assertEqual(class_resource_maximum("bard", "bardic_inspiration", 3, ability_modifier=4), 4)
        self.assertEqual(class_resource_ids("wizard", 3), [])

    def test_unconfirmed_or_unavailable_level_is_rejected(self):
        with self.assertRaises(ValueError):
            class_features("wizard", 7)
        with self.assertRaises(ValueError):
            class_resource_maximum("barbarian", "rages", 0)

    def test_all_twelve_classes_are_implemented_through_level_six(self):
        for class_id in CLASS_IDS:
            with self.subTest(class_id=class_id):
                features = class_features(class_id, 6)
                self.assertTrue(features)
                self.assertIsInstance(class_subclass_features(class_id, 6), list)

    def test_level_five_extra_attack_is_only_base_class_feature_where_confirmed(self):
        for class_id in ("barbarian", "monk", "paladin", "ranger"):
            with self.subTest(class_id=class_id):
                self.assertEqual(class_attack_count(class_id, 5), 2)
        for class_id in ("bard", "cleric", "druid", "rogue", "sorcerer", "warlock", "wizard"):
            with self.subTest(class_id=class_id):
                self.assertEqual(class_attack_count(class_id, 5), 1)

    def test_level_four_to_six_spell_slots_are_server_owned(self):
        self.assertEqual(class_spellcasting("bard", 4)["slots"], {1: 4, 2: 3})
        self.assertEqual(class_spellcasting("cleric", 6)["prepared"], 10)
        self.assertEqual(class_spellcasting("paladin", 5)["slots"], {1: 4, 2: 2})
        self.assertEqual(class_spellcasting("warlock", 5)["slots"], {3: 2})
        self.assertEqual(class_spellcasting("wizard", 6)["slots"], {1: 4, 2: 3, 3: 3})


if __name__ == "__main__":
    unittest.main()
