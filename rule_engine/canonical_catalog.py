"""Canonical PHB 2024 catalog boundary.

This module deliberately treats the existing curated PHB 2024 JSON as input data,
not as executable rules. Only records with the required 2024 provenance can be
selected by the Rule Engine.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

CATALOG_PATH = Path(__file__).with_name("character_creation_phb2024.json")
CATALOG_SCHEMA_VERSION = "canonical-rules-v1"
SOURCE_DATASET_SCHEMA = "character-options-phb2024-v1"
RULESET = "dnd-2024-phb"
SUBCLASS_OPTIONS_BY_CLASS = {
    "barbarian": ["path_of_the_berserker", "path_of_the_wild_heart", "path_of_the_world_tree", "path_of_the_zealot"],
    "bard": ["college_of_dance", "college_of_glamour", "college_of_lore", "college_of_valor"],
    "cleric": ["life_domain", "light_domain", "trickery_domain", "war_domain"],
    "druid": ["circle_of_the_land", "circle_of_the_moon", "circle_of_the_sea", "circle_of_the_stars"],
    "fighter": ["battle_master", "champion", "eldritch_knight", "psi_warrior"],
    "monk": ["warrior_of_mercy", "warrior_of_shadow", "warrior_of_the_elements", "warrior_of_the_open_hand"],
    "paladin": ["oath_of_devotion", "oath_of_glory", "oath_of_the_ancients", "oath_of_vengeance"],
    "ranger": ["beast_master", "fey_wanderer", "gloom_stalker", "hunter"],
    "rogue": ["arcane_trickster", "assassin", "soulknife", "thief"],
    "sorcerer": ["aberrant_sorcery", "clockwork_sorcery", "draconic_sorcery", "wild_magic_sorcery"],
    "warlock": ["archfey_patron", "celestial_patron", "fiend_patron", "great_old_one_patron"],
    "wizard": ["abjurer", "diviner", "evoker", "illusionist"],
}

CatalogStatus = Literal["canonical", "candidate", "rejected"]
CatalogKind = Literal["class", "subclass", "species", "background"]


class SourceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_document: StrictStr = Field(min_length=1)
    source_name: StrictStr = Field(min_length=1)
    source_section: StrictStr = Field(min_length=1)
    source_page: int | None = Field(default=None, ge=1)
    evidence_hash: StrictStr = Field(min_length=64, max_length=64)


class CanonicalCatalogRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: StrictStr = Field(min_length=1, max_length=128)
    kind: CatalogKind
    label_pt_br: StrictStr = Field(min_length=1)
    status: CatalogStatus
    ruleset: Literal["dnd-2024-phb"]
    provenance: SourceProvenance
    data: dict[str, Any]

    @field_validator("data")
    @classmethod
    def copy_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        return deepcopy(value)


def ensure_selectable(record: CanonicalCatalogRecord) -> CanonicalCatalogRecord:
    """Reject records that are not explicitly canonical before mechanics use them."""
    if record.status != "canonical":
        raise ValueError(f"catalog record is not selectable: {record.id}")
    if record.ruleset != RULESET:
        raise ValueError(f"catalog record uses an unsupported ruleset: {record.id}")
    return record


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _evidence_hash(record: dict[str, Any]) -> str:
    payload = {
        "id": record.get("id"),
        "kind": record.get("kind"),
        "source_name": record.get("source_name"),
        "source_location": record.get("source_location"),
        "data": record.get("data"),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _page_from_location(location: str) -> int | None:
    # Page numbers are optional because some extracted source markers use
    # chapter/section references without a stable printed page.
    import re

    match = re.search(r"(?:p(?:ages?)?\.?|páginas?)\s*(\d+)", location, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _provenance(record: dict[str, Any], kind: CatalogKind) -> SourceProvenance:
    source_name = record.get("source_name")
    source_location = record.get("source_location")
    if not isinstance(source_name, str) or not source_name.strip():
        raise ValueError(f"{kind} {record.get('id')} lacks PHB 2024 source_name")
    if not isinstance(source_location, str) or not source_location.strip():
        raise ValueError(f"{kind} {record.get('id')} lacks PHB 2024 source_location")
    data = {key: value for key, value in record.items() if key not in {"id", "label_pt_br", "source_name", "source_location"}}
    return SourceProvenance(
        source_document="Players_Handbook_2024",
        source_name=source_name,
        source_section=source_location,
        source_page=_page_from_location(source_location),
        evidence_hash=_evidence_hash({**record, "kind": kind, "data": data}),
    )


def _load_raw_catalog() -> dict[str, Any]:
    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if raw.get("edition") != "2024":
        raise RuntimeError("canonical catalog must be restricted to edition 2024")
    if raw.get("schema_version") != SOURCE_DATASET_SCHEMA:
        raise RuntimeError("unexpected PHB 2024 source dataset schema")
    return raw


class CanonicalCatalog:
    """Immutable-in-practice access boundary for PHB 2024 option records."""

    def __init__(self, raw: dict[str, Any] | None = None) -> None:
        source = deepcopy(raw if raw is not None else _load_raw_catalog())
        if source.get("edition") != "2024" or source.get("schema_version") != SOURCE_DATASET_SCHEMA:
            raise ValueError("catalog source must be the project's PHB 2024 dataset")
        self.schema_version = CATALOG_SCHEMA_VERSION
        self.ruleset = RULESET
        self.edition = 2024
        self._records: dict[CatalogKind, dict[str, CanonicalCatalogRecord]] = {
            "class": {}, "subclass": {}, "species": {}, "background": {},
        }
        self._build_records(source)

    def _build_records(self, source: dict[str, Any]) -> None:
        mapping: tuple[tuple[CatalogKind, str], ...] = (
            ("class", "classes"), ("species", "species"), ("background", "backgrounds"),
        )
        for kind, key in mapping:
            records = source.get(key)
            if not isinstance(records, list) or not records:
                raise ValueError(f"canonical catalog section {key} must be a non-empty list")
            for raw in records:
                if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                    raise ValueError(f"invalid {kind} record in PHB 2024 catalog")
                record_id = raw["id"]
                if record_id in self._records[kind]:
                    raise ValueError(f"duplicate canonical {kind} id: {record_id}")
                data = deepcopy(raw)
                label = data.pop("label_pt_br", None)
                data.pop("source_name", None)
                data.pop("source_location", None)
                self._records[kind][record_id] = CanonicalCatalogRecord(
                    id=record_id,
                    kind=kind,
                    label_pt_br=label if isinstance(label, str) and label.strip() else record_id,
                    status="canonical",
                    ruleset=RULESET,
                    provenance=_provenance(raw, kind),
                    data=data,
                )
                if kind == "class":
                    runtime = raw.get("runtime") or {}
                    subclass_options = runtime.get("subclass_options", []) or SUBCLASS_OPTIONS_BY_CLASS.get(record_id, [])
                    if not isinstance(subclass_options, list):
                        raise ValueError(f"class {record_id} has invalid subclass options")
                    for subclass_id in subclass_options:
                        if not isinstance(subclass_id, str) or not subclass_id:
                            raise ValueError(f"class {record_id} has an invalid subclass id")
                        if subclass_id in self._records["subclass"]:
                            raise ValueError(f"duplicate canonical subclass id: {subclass_id}")
                        subclass_raw = {
                            "id": subclass_id,
                            "source_name": raw.get("source_name"),
                            "source_location": raw.get("source_location"),
                            "data": {"class_id": record_id},
                        }
                        self._records["subclass"][subclass_id] = CanonicalCatalogRecord(
                            id=subclass_id,
                            kind="subclass",
                            label_pt_br=subclass_id,
                            status="canonical",
                            ruleset=RULESET,
                            provenance=_provenance(subclass_raw, "subclass"),
                            data={"class_id": record_id},
                        )

    def records(self, kind: CatalogKind) -> list[CanonicalCatalogRecord]:
        return list(self._records[kind].values())

    def get(self, kind: CatalogKind, record_id: str, *, selectable: bool = True) -> CanonicalCatalogRecord:
        record = self._records.get(kind, {}).get(record_id)
        if record is None:
            raise ValueError(f"unknown PHB 2024 {kind}: {record_id}")
        return ensure_selectable(record) if selectable else record

    def select(self, kind: CatalogKind, record_id: str) -> dict[str, Any]:
        record = self.get(kind, record_id, selectable=True)
        return {
            "id": record.id,
            "kind": record.kind,
            "label_pt_br": record.label_pt_br,
            "status": record.status,
            "ruleset": record.ruleset,
            "provenance": record.provenance.model_dump(mode="json"),
            "data": deepcopy(record.data),
        }

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ruleset": self.ruleset,
            "edition": self.edition,
            "source_dataset_schema": SOURCE_DATASET_SCHEMA,
            "record_counts": {kind: len(records) for kind, records in self._records.items()},
            "records": [
                {
                    "id": record.id,
                    "kind": record.kind,
                    "status": record.status,
                    "evidence_hash": record.provenance.evidence_hash,
                }
                for kind in self._records
                for record in self.records(kind)
            ],
        }


CANONICAL_CATALOG = CanonicalCatalog()


def canonical_catalog_manifest() -> dict[str, Any]:
    return CANONICAL_CATALOG.manifest()
