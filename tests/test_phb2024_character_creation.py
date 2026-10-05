import unittest

from pydantic import ValidationError

from game.contracts import PHB2024GuidedCharacterRequest
from rule_engine.character_creation_catalog import character_options_phb2024
from rule_engine.character_creation_phb2024 import build_phb2024_character


SPECIES_CHOICES = {
    "aasimar": {"size": "medium"},
    "dragonborn": {"draconic_ancestry": "black"},
    "dwarf": {},
    "elf": {"elven_lineage": "high_elf", "spellcasting_ability": "intelligence", "keen_senses_skill": "perception"},
    "gnome": {"gnomish_lineage": "forest_gnome", "spellcasting_ability": "wisdom"},
    "goliath": {"giant_ancestry": "stone"},
    "halfling": {},
    "human": {"size": "small", "skillful_skill": "arcana", "versatile_feat": "alert"},
    "orc": {},
    "tiefling": {"size": "medium", "fiendish_legacy": "chthonic", "spellcasting_ability": "charisma"},
}


class PHB2024CharacterCreationTests(unittest.TestCase):
    def request(self, class_id="barbarian", species_id="dwarf", **overrides):
        options = character_options_phb2024()
        class_option = next(item for item in options["classes"] if item["id"] == class_id)
        count = class_option["skill_choices"]["count"]
        raw = {
            "name": "Teste 2024",
            "class_id": class_id,
            "level": 1,
            "species_id": species_id,
            "species_choices": SPECIES_CHOICES[species_id],
            "background_id": "farmer",
            "alignment_id": "neutral_good",
            "ability_method_id": "standard_array",
            "base_abilities": {
                "strength": 15, "dexterity": 14, "constitution": 13,
                "intelligence": 12, "wisdom": 10, "charisma": 8,
            },
            "background_ability_increases": {"strength": 2, "constitution": 1},
            "abilities": {
                "strength": 17, "dexterity": 14, "constitution": 14,
                "intelligence": 12, "wisdom": 10, "charisma": 8,
            },
            "skills": class_option["skill_choices"]["options"][:count],
            "language_choices": ["draconic", "dwarvish"],
            "class_equipment_option": "A",
            "background_equipment_option": "A",
        }
        raw.update(overrides)
        return PHB2024GuidedCharacterRequest(**raw)

    def test_catalog_contains_only_full_phb2024_creation_rosters(self):
        catalog = character_options_phb2024()
        self.assertEqual(catalog["edition"], 2024)
        self.assertEqual(catalog["ruleset"], "dnd-2024-phb")
        self.assertEqual(len(catalog["classes"]), 12)
        self.assertEqual(len(catalog["species"]), 10)
        self.assertEqual(len(catalog["backgrounds"]), 16)
        for category in ("classes", "species", "backgrounds"):
            for option in catalog[category]:
                self.assertNotIn("summary", option)
                self.assertNotIn("details", option)
        self.assertEqual({item["id"] for item in catalog["classes"]}, {
            "barbarian", "bard", "cleric", "druid", "fighter", "monk",
            "paladin", "ranger", "rogue", "sorcerer", "warlock", "wizard",
        })
        self.assertEqual({item["id"] for item in catalog["species"]}, set(SPECIES_CHOICES))
        self.assertEqual(len(catalog["alignment_options"]), 9)
        self.assertEqual(len(catalog["language_rules"]["additional_options"]), 9)
        self.assertEqual(catalog["ability_score_methods"]["point_buy"]["budget"], 27)

    def test_all_twelve_classes_can_build_a_level_one_character(self):
        for class_option in character_options_phb2024()["classes"]:
            with self.subTest(class_id=class_option["id"]):
                character = build_phb2024_character(self.request(class_id=class_option["id"]))
                self.assertEqual(character.class_.id, class_option["id"])
                self.assertEqual(character.level, 1)
                self.assertEqual(character.background_id, "farmer")
                self.assertEqual(character.species_id, "dwarf")
                self.assertIn("animal_handling", character.proficiencies.skills)
                self.assertIn("nature", character.proficiencies.skills)
                self.assertEqual(character.languages, ["common", "draconic", "dwarvish"])
                self.assertEqual(character.derived()["hp"]["max"], class_option["hit_die"] + 2)

    def test_all_ten_species_accept_only_their_2024_choice_shapes(self):
        for species_id, choices in SPECIES_CHOICES.items():
            with self.subTest(species_id=species_id):
                character = build_phb2024_character(self.request(species_id=species_id, species_choices=choices))
                self.assertEqual(character.species_id, species_id)

    def test_point_buy_accepts_2024_costs_and_rejects_over_budget(self):
        base = {
            "strength": 15, "dexterity": 15, "constitution": 15,
            "intelligence": 8, "wisdom": 8, "charisma": 8,
        }
        final = {**base, "strength": 17, "constitution": 16}
        request = self.request(ability_method_id="point_buy", base_abilities=base,
                               abilities=final, background_ability_increases={"strength": 2, "constitution": 1})
        character = build_phb2024_character(request)
        self.assertEqual(character.abilities["strength"], 17)
        invalid = dict(base)
        invalid["dexterity"] = 14
        with self.assertRaises(ValueError):
            build_phb2024_character(self.request(ability_method_id="point_buy", base_abilities=invalid,
                abilities={**invalid, "strength": 17, "constitution": 14},
                background_ability_increases={"strength": 2, "constitution": 1}))

    def test_background_bonus_must_match_phb_2024_eligible_abilities(self):
        with self.assertRaises(ValueError):
            build_phb2024_character(self.request(background_ability_increases={"intelligence": 2, "wisdom": 1}))

    def test_invalid_species_option_and_non_phb_extra_field_are_rejected(self):
        with self.assertRaises(ValueError):
            build_phb2024_character(self.request(species_id="dragonborn", species_choices={"draconic_ancestry": "purple"}))
        with self.assertRaises(ValidationError):
            self.request(race_id="elf")

    def test_origin_skills_languages_and_equipment_are_stored_in_character(self):
        character = build_phb2024_character(self.request(species_id="human"))
        self.assertEqual(character.species_skill_choices, ["arcana"])
        self.assertEqual(character.origin_feat["name"], "Tough")
        self.assertEqual(character.starting_equipment["class_option"], "A")
        self.assertEqual(character.starting_equipment["background_option"], "A")
        self.assertGreaterEqual(character.starting_equipment["gold_gp"], 0)
        self.assertGreater(len(character.starting_equipment["items"]), 0)

    def test_character_creation_is_level_one_only_outside_existing_fighter_v1(self):
        with self.assertRaises(ValueError):
            build_phb2024_character(self.request(level=2))


if __name__ == "__main__":
    unittest.main()
