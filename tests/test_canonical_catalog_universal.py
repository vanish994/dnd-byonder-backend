import unittest

from rule_engine.canonical_catalog import CANONICAL_CATALOG, canonical_catalog_manifest, ensure_selectable
from rule_engine.universal_character import UniversalCharacter


class CanonicalCatalogTests(unittest.TestCase):
    def test_manifest_is_2024_versioned_and_has_expected_creation_records(self):
        manifest = canonical_catalog_manifest()
        self.assertEqual(manifest["schema_version"], "canonical-rules-v1")
        self.assertEqual(manifest["ruleset"], "dnd-2024-phb")
        self.assertEqual(manifest["edition"], 2024)
        self.assertEqual(manifest["record_counts"], {"class": 12, "subclass": 48, "species": 10, "background": 16})
        self.assertTrue(all(item["status"] == "canonical" for item in manifest["records"]))
        self.assertTrue(all(len(item["evidence_hash"]) == 64 for item in manifest["records"]))

    def test_subclass_selection_has_canonical_provenance(self):
        selected = CANONICAL_CATALOG.select("subclass", "warrior_of_mercy")
        self.assertEqual(selected["kind"], "subclass")
        self.assertEqual(selected["data"]["class_id"], "monk")
        self.assertEqual(len(selected["provenance"]["evidence_hash"]), 64)

    def test_select_returns_provenance_and_does_not_expose_mutable_catalog_data(self):
        selected = CANONICAL_CATALOG.select("class", "fighter")
        selected["data"]["runtime"]["hit_die"] = 999
        self.assertEqual(CANONICAL_CATALOG.get("class", "fighter").data["runtime"]["hit_die"], 10)
        self.assertEqual(selected["provenance"]["source_document"], "Players_Handbook_2024")

    def test_unknown_or_noncanonical_selection_is_rejected(self):
        with self.assertRaises(ValueError):
            CANONICAL_CATALOG.get("class", "not-a-phb-2024-class")
        candidate = CANONICAL_CATALOG.get("class", "fighter").model_copy(update={"status": "candidate"})
        with self.assertRaises(ValueError):
            ensure_selectable(candidate)


class UniversalCharacterTests(unittest.TestCase):
    def _fighter(self):
        record = CANONICAL_CATALOG.get("class", "fighter")
        return UniversalCharacter(
            id="character-1",
            name="Ayla",
            class_selection={"id": "fighter", "kind": "class", "evidence_hash": record.provenance.evidence_hash},
            base_abilities={"strength": 15, "dexterity": 14, "constitution": 13, "intelligence": 12, "wisdom": 10, "charisma": 8},
            abilities={"strength": 15, "dexterity": 14, "constitution": 13, "intelligence": 12, "wisdom": 10, "charisma": 8},
        )

    def test_universal_character_is_deterministic_and_versioned(self):
        character = self._fighter()
        self.assertEqual(character.schema_version, "universal-character-v1")
        self.assertEqual(character.runtime_selection()["class_id"], "fighter")
        self.assertEqual(character.to_snapshot(), UniversalCharacter.model_validate(character.to_snapshot()).to_snapshot())

    def test_provenance_tampering_is_rejected(self):
        tampered = self._fighter().to_snapshot()
        tampered["class_selection"]["evidence_hash"] = "0" * 64
        with self.assertRaises(ValueError):
            UniversalCharacter.model_validate(tampered)

    def test_legacy_snapshot_adapts_without_inventing_species_or_background(self):
        character = UniversalCharacter.from_legacy_state({
            "id": "legacy-1",
            "name": "Ayla",
            "level": 1,
            "experience_points": 0,
            "class": {"id": "fighter", "level": 1},
            "abilities": {"strength": 15, "dexterity": 14, "constitution": 13, "intelligence": 12, "wisdom": 10, "charisma": 8},
            "proficiencies": {"skills": {"athletics": True}, "saving_throws": {"strength": True}},
        })
        self.assertIsNone(character.species_selection)
        self.assertIsNone(character.background_selection)
        self.assertEqual(character.provenance[0]["schema"], "pre-universal-character")

    def test_unknown_catalog_selection_is_rejected(self):
        record = CANONICAL_CATALOG.get("class", "fighter")
        with self.assertRaises(ValueError):
            UniversalCharacter(
                id="character-2",
                class_selection={"id": "wizard-not-confirmed", "kind": "class", "evidence_hash": record.provenance.evidence_hash},
                base_abilities={"strength": 15, "dexterity": 14, "constitution": 13, "intelligence": 12, "wisdom": 10, "charisma": 8},
                abilities={"strength": 15, "dexterity": 14, "constitution": 13, "intelligence": 12, "wisdom": 10, "charisma": 8},
            )


if __name__ == "__main__":
    unittest.main()
