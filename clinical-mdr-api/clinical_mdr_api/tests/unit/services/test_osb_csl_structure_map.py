"""The OSB to CSL structure map (owned by ClinicalSemanticLayer, copied here byte for byte) must cover this
repository's models exactly: every neomodel node and relationship class in domain_repositories/models and every
relationship type they declare has a stated CSL counterpart or a stated none, and the map names nothing that is not
here. The counting rule is the map's own: classes are the class statements of the model files, relationship types are
the distinct Neo4j type strings across every class.
"""

import hashlib
import importlib
import inspect
import json
import pkgutil
from pathlib import Path

from neomodel import StructuredNode, StructuredRel
from neomodel.sync_.relationship_manager import RelationshipDefinition

import clinical_mdr_api.domain_repositories.models as models_package

MAP_PATH = Path(__file__).resolve().parents[3] / "schemas/platform/osb-csl-structure-map-v1.json"
MAP = json.loads(MAP_PATH.read_text(encoding="utf-8"))


def _canonical_hash(document: dict) -> str:
    normative = {key: document[key] for key in ("catalogTypes", "osbClasses", "osbRelationshipTypes", "osbPlatformNodes")}
    canonical = json.dumps(normative, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _model_classes() -> dict[str, type]:
    found: dict[str, type] = {}
    for module_info in pkgutil.iter_modules(models_package.__path__):
        if module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{models_package.__name__}.{module_info.name}")
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module.__name__:
                continue
            if issubclass(cls, (StructuredNode, StructuredRel)):
                found[name] = cls
    return found


def _relationship_types(classes: dict[str, type]) -> set[str]:
    types: set[str] = set()
    for cls in classes.values():
        for value in vars(cls).values():
            if isinstance(value, RelationshipDefinition):
                types.add(value.definition["relation_type"])
    return types


def test_map_is_the_expected_contract_and_its_hash_covers_its_normative_sections() -> None:
    assert MAP["contractVersion"] == "OsbCslStructureMapV1@1.0.0"
    assert MAP["owner"] == "ClinicalSemanticLayer"
    assert MAP["mapHash"] == _canonical_hash(MAP)


def test_every_model_class_here_is_in_the_map_and_the_map_names_no_other() -> None:
    classes = _model_classes()
    mapped = {entry["osbClass"] for entry in MAP["osbClasses"]}
    assert set(classes) - mapped == set(), f"classes without a map entry: {sorted(set(classes) - mapped)}"
    assert mapped - set(classes) == set(), f"map entries that are not model classes: {sorted(mapped - set(classes))}"
    for entry in MAP["osbClasses"]:
        assert entry["correspondence"] in MAP["correspondenceVocabulary"], entry["osbClass"]
        if entry["correspondence"] == "none":
            assert entry["note"], f"{entry['osbClass']} says none without a reason"


def test_every_relationship_type_declared_here_is_in_the_map_and_the_map_names_no_other() -> None:
    declared = _relationship_types(_model_classes())
    mapped = {entry["type"] for entry in MAP["osbRelationshipTypes"]}
    assert declared - mapped == set(), f"relationship types without a map entry: {sorted(declared - mapped)}"
    assert mapped - declared == set(), f"map entries that no class declares: {sorted(mapped - declared)}"
    for entry in MAP["osbRelationshipTypes"]:
        assert entry["correspondence"] in MAP["relationshipCorrespondenceVocabulary"], entry["type"]
        if entry["correspondence"] == "none":
            assert entry["note"], f"{entry['type']} says none without a reason"


def test_the_schedule_of_activities_cell_and_the_study_spine_map_to_the_catalog_types_the_executors_write() -> None:
    by_class = {entry["osbClass"]: entry for entry in MAP["osbClasses"]}
    assert by_class["StudyActivitySchedule"]["cslObjectType"] == "ScheduleOccurrence"
    assert by_class["StudyVisit"]["cslObjectType"] == "Encounter"
    assert by_class["StudyArm"]["cslObjectType"] == "StudyArm"
    assert by_class["OdmItemRoot"]["cslObjectType"] == "Item"
    assert by_class["CTTermRoot"]["cslObjectType"] == "Code"
    assert by_class["CTPackage"]["correspondence"] == "exact"
    labels = {entry["label"]: entry for entry in MAP["osbPlatformNodes"]}
    assert labels["PlatformManagedStudyConcept"]["cslTable"] == "external_identifier"
    assert labels["StudyMappingDecisionV1"]["cslTable"] == "study_mapping_decision_v1"
