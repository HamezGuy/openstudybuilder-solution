"""Offline checks for the descriptive multi-design platform document (DD-4)."""

from copy import deepcopy

import pytest
from pydantic import ValidationError
from usdm_model import Wrapper

from clinical_mdr_api.services.ddf.usdm_mapping_context import native_json
from clinical_mdr_api.services.ddf.usdm_platform_designs import (
    DesignScopedIdManager,
    PlatformDesignSource,
    compose_platform_document,
)
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource
from clinical_mdr_api.tests.fixtures.usdm_native_study import STUDY_UID, VERSION

SUBPART_UID = "Study_000099"


def report(scope=None):
    source = NativeStudySource()
    with source.isolated():
        mapper = source.mapper()
        if scope is not None:
            mapper._id_manager = DesignScopedIdManager(scope)
        return mapper.map_with_report(source.graph["study"], VERSION)


def sources():
    master = PlatformDesignSource("master", STUDY_UID, VERSION, report())
    subpart = PlatformDesignSource(
        "subpart",
        SUBPART_UID,
        "1",
        report(SUBPART_UID),
        parent_study_uid=STUDY_UID,
        subpart_acronym="SYN-A",
    )
    return master, subpart


def ids(value, found=None):
    found = [] if found is None else found
    if isinstance(value, dict):
        if isinstance(value.get("id"), str) and value.get("instanceType"):
            found.append(value["id"])
        for item in value.values():
            ids(item, found)
    elif isinstance(value, list):
        for item in value:
            ids(item, found)
    return found


def schema_errors(document):
    try:
        Wrapper.model_validate(document)
    except ValidationError as error:
        return {
            (
                ".".join(
                    "*" if isinstance(part, int) else str(part) for part in row["loc"]
                ),
                row["type"],
            )
            for row in error.errors()
        }
    return set()


def without_scope(design):
    design = deepcopy(design)
    design["extensionAttributes"] = [
        item
        for item in design["extensionAttributes"]
        if "designScope" not in item["url"]
    ]
    return design


def test_master_and_subpart_designs_share_one_version_with_scoped_identities():
    master, subpart = sources()
    before = deepcopy(master.report)
    result = compose_platform_document(master, [subpart])
    assert master.report == before, "Composition never edits a design report"
    version = result["document"]["study"]["versions"][0]
    designs = version["studyDesigns"]
    assert len(designs) == 2
    # The master design is the single-design export plus its scope extension.
    single = before["document"]["study"]["versions"][0]["studyDesigns"][0]
    assert without_scope(designs[0]) == single
    # Every subpart identity is scoped; only the platform's own designScope
    # extension carries the platform scope.
    assert {identity.split(":")[0] for identity in ids(designs[1])} == {
        SUBPART_UID,
        "platform",
    }
    assert not any(identity.startswith(SUBPART_UID) for identity in ids(designs[0]))
    assert [scope["level"] for scope in result["designScopes"]] == [
        "master",
        "subpart",
    ]
    assert result["designScopes"][1] == {
        "designId": designs[1]["id"],
        "level": "subpart",
        "studyUid": SUBPART_UID,
        "studyValueVersion": "1",
        "parentStudyUid": STUDY_UID,
        "subpartAcronym": "SYN-A",
    }
    assert result["executableRelease"]["selected"] is False
    # Every entity identity is unique and the design references resolve.
    issues = [issue["code"] for issue in result["mappingReport"]["issues"]]
    assert "USDM_DUPLICATE_ENTITY_ID" not in issues
    interventions = {item["id"] for item in version["studyInterventions"]}
    for design in designs:
        assert set(design.get("studyInterventionIds") or []) <= interventions
    items = {item["id"] for item in version["eligibilityCriterionItems"]}
    for design in designs:
        for criterion in design.get("eligibilityCriteria") or []:
            assert criterion["criterionItemId"] in items
    # Draft exports may be incomplete. Merging must add no new kind of
    # structural error beyond those of the single-design documents.
    assert schema_errors(result["document"]) <= schema_errors(
        master.report["document"]
    ) | schema_errors(subpart.report["document"])
    assert [row["studyUid"] for row in result["mappingReport"]["designs"]] == [
        STUDY_UID,
        SUBPART_UID,
    ]


def test_a_master_without_subparts_is_the_single_design_document_plus_scope():
    master, _ = sources()
    result = compose_platform_document(master, [])
    single = master.report["document"]
    composed = result["document"]
    design = composed["study"]["versions"][0]["studyDesigns"][0]
    assert without_scope(design) == single["study"]["versions"][0]["studyDesigns"][0]
    composed["study"]["versions"][0]["studyDesigns"][0] = without_scope(design)
    assert composed == single


def test_conflicting_resource_identity_is_reported_not_overwritten():
    master, subpart = sources()
    changed = deepcopy(subpart.report)
    version = changed["document"]["study"]["versions"][0]
    master_version = master.report["document"]["study"]["versions"][0]
    field = next(
        name
        for name in ("studyInterventions", "organizations", "biomedicalConcepts")
        if master_version.get(name)
    )
    shared = deepcopy(master_version[field][0])
    shared["description"] = "Synthetic conflicting authored text"
    version[field].append(shared)
    result = compose_platform_document(
        master,
        [
            PlatformDesignSource(
                "subpart", SUBPART_UID, "1", changed, parent_study_uid=STUDY_UID
            )
        ],
    )
    merged = result["document"]["study"]["versions"][0][field]
    kept = [item for item in merged if item["id"] == shared["id"]]
    assert kept == [master_version[field][0]]
    assert "USDM_PLATFORM_RESOURCE_ID_COLLISION" in [
        issue["code"] for issue in result["mappingReport"]["issues"]
    ]
    assert result["mappingReport"]["state"] == "incomplete"


@pytest.mark.parametrize("case", ["duplicate", "master-as-subpart", "two-designs"])
def test_invalid_platform_compositions_refuse(case):
    master, subpart = sources()
    if case == "duplicate":
        subparts = [subpart, subpart]
    elif case == "master-as-subpart":
        subparts = [PlatformDesignSource("subpart", STUDY_UID, VERSION, master.report)]
    else:
        changed = deepcopy(subpart.report)
        version = changed["document"]["study"]["versions"][0]
        version["studyDesigns"].append(deepcopy(version["studyDesigns"][0]))
        subparts = [PlatformDesignSource("subpart", SUBPART_UID, "1", changed)]
    with pytest.raises(ValueError):
        compose_platform_document(master, subparts)


def test_scoped_identifiers_are_stable_and_refuse_ambiguous_keys():
    manager = DesignScopedIdManager(SUBPART_UID)
    first = manager.get_id("StudyArm", "StudyArm_000001")
    assert first == manager.get_id("StudyArm", "StudyArm_000001")
    assert first.startswith(SUBPART_UID + ":")
    manager.clear_all_ids()
    assert manager.get_id("StudyArm", "StudyArm_000001") == first
    for key in ("", " ", "a:b"):
        with pytest.raises(ValueError):
            DesignScopedIdManager(key)
    assert native_json(None) is None


def test_a_study_without_design_content_is_reported_not_invented():
    master, subpart = sources()
    empty = deepcopy(master.report)
    empty["document"]["study"]["versions"][0]["studyDesigns"] = []
    result = compose_platform_document(
        PlatformDesignSource("master", STUDY_UID, VERSION, empty), [subpart]
    )
    designs = result["document"]["study"]["versions"][0]["studyDesigns"]
    assert len(designs) == 1
    assert [scope["designId"] for scope in result["designScopes"]] == [
        None,
        designs[0]["id"],
    ]
    issues = [issue["code"] for issue in result["mappingReport"]["issues"]]
    assert issues.count("USDM_PLATFORM_DESIGN_ABSENT") == 1
    assert result["mappingReport"]["state"] == "incomplete"
