"""Native metadata proposals and execution, with real OSB DTO validation.

Only native persistence and library reads are replaced. These tests do not
create signatures, access a database or change any client study.
"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from clinical_mdr_api.services.integrations import study_metadata_mapping as mapping
from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError
from common.exceptions import BusinessLogicException

UID = "Study_metadata_fixture"
COUNT = "study_population.number_of_expected_subjects"
TITLE = "study_description.study_title"
RANDOMISED = "study_intervention.is_trial_randomised"
TERMS = "high_level_study_design.trial_type_codes"
CONTEXT = SimpleNamespace(selected_packages=[])


def test_native_term_resolution_requires_context_instead_of_crashing():
    with pytest.raises(OsbCandidateSetError) as error:
        mapping.NativeStudyMetadataPort().resolve(
            "termRef", {"termName": "Observational", "codelistName": "Study Type"},
            None, {}
        )
    assert error.value.code == "OSB_STUDY_METADATA_CONTEXT_REQUIRED"


def test_source_stage_context_reads_only_the_selected_native_study_packages(monkeypatch):
    from clinical_mdr_api.services.integrations.mapping_context import MappingContextService

    observed = []

    def selected(_self, request, warnings, blockers):
        observed.append(request.study_uid)
        return [SimpleNamespace(package_uid="DDF CT selected", catalogue_name="DDF CT",
                                effective_date="2024-09-27")]

    monkeypatch.setattr(MappingContextService, "_selected_packages", selected)
    assert mapping.NativeStudyMetadataPort().stage_context(UID) == {
        "selectedPackages": [{"packageUid": "DDF CT selected", "catalogueName": "DDF CT",
                              "effectiveDate": "2024-09-27"}]
    }
    assert observed == [UID]


def intent(fact, path, value, **extra):
    return {
        "factId": fact,
        "revision": 1,
        "targetKey": "primary",
        "resourceFamily": "study_metadata",
        "nativeStudyOperation": {
            "contractVersion": mapping.PLAN_CONTRACT,
            "assertionType": "STUDY_DESIGN_ATTRIBUTE",
            "ruled": True,
            "osbResourceType": "StudyMetadata",
            "osbCapability": "native_study_mutation",
            "kind": "native-metadata",
            "method": "PATCH",
            "route": "/studies/{study_uid}",
            "metadataPath": path,
            "metadataValue": value,
            **extra,
        },
    }


def key(value):
    return f'{value["factId"]}@{value["revision"]}:{value["targetKey"]}'


class NativePort:
    def __init__(self, uid=UID):
        self.uid = uid
        self.study = {
            "uid": uid,
            "study_parent_part": None,
            "current_metadata": {
                "version_metadata": {
                    "study_status": "DRAFT",
                    "version_timestamp": "2026-09-10T10:00:00Z",
                },
                "study_population": {
                    "number_of_expected_subjects": None,
                    "number_of_expected_subjects_null_value_code": {
                        "term_uid": "Null_not_provided"
                    },
                    "rare_disease_indicator": False,
                },
                "study_description": {
                    "study_title": None,
                    "study_short_title": "Preserved short title",
                },
            },
        }
        self.patches = []
        self.locked = []
        self.reference_version = "1.0"
        self.ignore_patch = False
        self.preview_error = None
        self.change_reference_on_patch = False
        self.resolved_plans = []
        self.reference_locks = []

    def read(self, uid):
        assert uid == self.uid
        return deepcopy(self.study)

    def lock(self, uid):
        self.locked.append(uid)

    def lock_references(self, bindings):
        self.reference_locks.extend(deepcopy(bindings))

    def patch(self, uid, body):
        assert uid == self.uid
        self.patches.append(deepcopy(body))
        if self.ignore_patch:
            return

        def merge(target, changes):
            for name, value in changes.items():
                if isinstance(value, dict):
                    merge(target.setdefault(name, {}), value)
                else:
                    target[name] = deepcopy(value)

        merge(self.study["current_metadata"], body["current_metadata"])
        self.study["current_metadata"]["version_metadata"][
            "version_timestamp"
        ] = "2026-09-10T11:00:00Z"
        if self.change_reference_on_patch:
            self.reference_version = "2.0"

    def preview(self, uid, body):
        assert uid == self.uid
        if self.preview_error:
            raise self.preview_error
        preview = deepcopy(self.study)
        for section, changes in body["current_metadata"].items():
            preview["current_metadata"].setdefault(section, {}).update(
                deepcopy(changes)
            )
        return preview

    def resolve(self, kind, reference, _context, plan):
        self.resolved_plans.append(deepcopy(plan))
        name = reference.get("termName", reference.get("unitName"))
        uid = f"Native_{name}"
        value = {"uid": uid} if kind == "unitRef" else {"term_uid": uid}
        return {
            "value": value,
            "identity": {"uid": uid, "version": self.reference_version, "kind": kind},
        }


def prepared(port, *intents):
    offers = mapping.prepare_metadata_offers(list(intents), UID, CONTEXT, port=port)
    return [{"intent": item, "candidate": offers[key(item)]} for item in intents]


def test_proposal_is_read_only_and_pins_the_exact_source_plan_and_native_property():
    port = NativePort()
    source = intent("enrollment", COUNT, 30000)
    original = deepcopy(source)
    before = deepcopy(port.study)
    item = prepared(port, source)[0]
    offer = item["candidate"]["createOption"]["nativeStudyOperation"]
    assert source == original
    assert port.study == before
    assert not port.patches
    assert not port.locked
    assert offer["metadataValue"] == 30000
    assert offer["sourcePlanHash"] == mapping.canonical_json_hash_ref(
        source["nativeStudyOperation"], schema_version=mapping.PLAN_CONTRACT
    )
    assert offer["nativeStudyId"] == UID


@pytest.mark.parametrize("count", [0, 30000])
def test_native_batch_writes_typed_properties_once_and_preserves_unrelated_values(
    count,
):
    port = NativePort()
    sources = [
        intent("enrollment", COUNT, count),
        intent("title", TITLE, "A source-stated study"),
        intent("allocation", RANDOMISED, False),
    ]
    observations = mapping.apply_metadata_selections(
        prepared(port, *sources), UID, port=port
    )
    assert len(port.patches) == 1
    assert port.locked == [UID]
    assert port.patches[0]["study_parent_part_uid"] is None
    assert port.study["current_metadata"]["study_population"] == {
        "number_of_expected_subjects": count,
        "number_of_expected_subjects_null_value_code": None,
        "rare_disease_indicator": False,
    }
    assert (
        port.study["current_metadata"]["study_description"]["study_short_title"]
        == "Preserved short title"
    )
    assert observations[key(sources[0])]["metadataValue"] == count
    assert observations[key(sources[2])]["metadataValue"] is False
    assert {item["version"] for item in observations.values()} == {
        "2026-09-10T11:00:00Z"
    }


def test_conflicting_source_counts_are_rejected_before_any_patch():
    port = NativePort()
    items = prepared(
        port, intent("count-a", COUNT, 30000), intent("count-b", COUNT, 30001)
    )
    with pytest.raises(OsbCandidateSetError, match="disagree"):
        mapping.apply_metadata_selections(items, UID, port=port)
    assert not port.patches


@pytest.mark.parametrize("change", ["value", "null-value", "parent"])
def test_native_changes_after_review_invalidate_the_property_offer(change):
    port = NativePort()
    items = prepared(port, intent("count", COUNT, 30000))
    if change == "value":
        port.study["current_metadata"]["study_population"][
            "number_of_expected_subjects"
        ] = 12
    elif change == "null-value":
        port.study["current_metadata"]["study_population"][
            "number_of_expected_subjects_null_value_code"
        ] = None
    else:
        port.study["study_parent_part"] = {"uid": "A_different_parent"}
    with pytest.raises(OsbCandidateSetError) as error:
        mapping.apply_metadata_selections(items, UID, port=port)
    assert error.value.code == "OSB_STUDY_METADATA_PRECONDITION_FAILED"
    assert not port.patches


def test_an_unrelated_native_edit_is_preserved_without_invalidating_this_property():
    port = NativePort()
    items = prepared(port, intent("count", COUNT, 30000))
    port.study["current_metadata"]["study_description"][
        "study_short_title"
    ] = "A later human edit"
    mapping.apply_metadata_selections(items, UID, port=port)
    assert (
        port.study["current_metadata"]["study_description"]["study_short_title"]
        == "A later human edit"
    )


def test_multiselect_contributions_become_one_native_list_with_all_selected_terms():
    port = NativePort()
    sources = [
        intent(
            f"term-{name}",
            TERMS,
            {"termRef": "value"},
            metadataMultiValued=True,
            termRefs={
                "value": {
                    "termName": name,
                    "codelistName": "Trial Type",
                    "basis": "stated:value",
                }
            },
        )
        for name in ["Safety", "Efficacy", "Safety"]
    ]
    items = prepared(port, *sources)
    assert items[0]["candidate"]["createOption"]["nativeStudyOperation"][
        "metadataValue"
    ] == [{"term_uid": "Native_Safety"}]
    observations = mapping.apply_metadata_selections(
        items, UID, context=CONTEXT, port=port
    )
    actual = port.patches[0]["current_metadata"]["high_level_study_design"][
        "trial_type_codes"
    ]
    assert actual == [{"term_uid": "Native_Safety"}, {"term_uid": "Native_Efficacy"}]
    assert all(
        value["metadataValue"]
        == [{"term_uid": "Native_Efficacy"}, {"term_uid": "Native_Safety"}]
        for value in observations.values()
    )


def test_reference_version_changes_after_review_are_rejected():
    port = NativePort()
    source = intent(
        "study-type",
        "high_level_study_design.study_type_code",
        {"termRef": "value"},
        termRefs={
            "value": {
                "termName": "Observational",
                "codelistName": "Study Type",
                "basis": "stated:value",
            }
        },
    )
    items = prepared(port, source)
    port.reference_version = "2.0"
    with pytest.raises(OsbCandidateSetError) as error:
        mapping.apply_metadata_selections(items, UID, context=CONTEXT, port=port)
    assert error.value.code == "OSB_STUDY_METADATA_REFERENCE_CHANGED"
    assert not port.patches


def test_subpart_parent_is_preserved_for_a_supported_population_edit():
    port = NativePort()
    port.study["study_parent_part"] = {"uid": "Study_parent_fixture"}
    mapping.apply_metadata_selections(
        prepared(port, intent("count", COUNT, 30000)), UID, port=port
    )
    assert port.patches[0]["study_parent_part_uid"] == "Study_parent_fixture"


def test_native_business_rule_rejection_is_a_blocked_proposal_before_review():
    port = NativePort()
    port.preview_error = BusinessLogicException(
        msg="Cannot add or edit Study Description of Study Subparts."
    )
    offered = prepared(port, intent("title", TITLE, "A source title"))[0]["candidate"]
    assert offered == {
        "createOption": None,
        "blockers": ["OSB_STUDY_METADATA_NATIVE_RULE_REJECTED"],
    }
    assert not port.patches


@pytest.mark.parametrize(
    "path,value",
    [
        (TITLE, "A source title"),
        ("identification_metadata.registry_identifiers.ct_gov_id", "NCT01234567"),
    ],
)
def test_preflight_uses_the_actual_native_subpart_rule(monkeypatch, path, value):
    from clinical_mdr_api.services.studies import study as study_service

    native_patch = study_service.StudyService.patch.__wrapped__
    aggregate = SimpleNamespace(study_parent_part_uid="Parent")
    repository = SimpleNamespace(find_by_uid=lambda *_args, **_kwargs: aggregate)
    service = SimpleNamespace(
        _repos=SimpleNamespace(study_definition_repository=repository),
        _close_all_repos=lambda: None,
    )
    calls = []

    def preview_patch(**kwargs):
        calls.append(kwargs)
        return native_patch(service, **kwargs)

    monkeypatch.setattr(
        study_service, "StudyService", lambda: SimpleNamespace(patch=preview_patch)
    )
    port = NativePort()
    port.study["study_parent_part"] = {"uid": "Parent"}
    port.preview = mapping.NativeStudyMetadataPort().preview
    offered = prepared(port, intent("subpart-property", path, value))[0]["candidate"]
    assert offered["blockers"] == ["OSB_STUDY_METADATA_NATIVE_RULE_REJECTED"]
    assert offered["createOption"] is None
    assert calls[0]["dry"] is True
    assert calls[0]["study_patch_request"].study_parent_part_uid == "Parent"
    assert not port.patches


def test_a_changed_native_business_rule_is_rechecked_before_patch():
    port = NativePort()
    items = prepared(port, intent("count", COUNT, 30000))
    port.preview_error = BusinessLogicException(msg="Study parent is locked.")
    with pytest.raises(OsbCandidateSetError) as error:
        mapping.apply_metadata_selections(items, UID, port=port)
    assert error.value.code == "OSB_STUDY_METADATA_NATIVE_RULE_REJECTED"
    assert not port.patches


def test_an_internal_preview_failure_remains_an_error():
    port = NativePort()
    port.preview_error = RuntimeError("Native service unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        prepared(port, intent("count", COUNT, 30000))


def test_references_are_rechecked_after_the_patch_before_success_evidence():
    port = NativePort()
    source = intent(
        "study-type",
        "high_level_study_design.study_type_code",
        {"termRef": "value"},
        termRefs={
            "value": {
                "termName": "Observational",
                "codelistName": "Study Type",
                "basis": "stated:value",
            }
        },
    )
    items = prepared(port, source)
    port.change_reference_on_patch = True
    with pytest.raises(OsbCandidateSetError) as error:
        mapping.apply_metadata_selections(items, UID, context=CONTEXT, port=port)
    assert error.value.code == "OSB_STUDY_METADATA_REFERENCE_CHANGED"
    # The caller's real Neo4j transaction rolls this write back on the error.
    assert len(port.patches) == 1


def test_prepare_and_apply_resolve_units_against_the_identical_full_source_plan():
    port = NativePort()
    source = intent(
        "age",
        "study_population.planned_minimum_age_of_subjects",
        {"duration_value": 18, "duration_unit_code": {"unitRef": "unit"}},
        unitRefs={"unit": {"unitName": "years", "basis": "stated:unit"}},
    )
    items = prepared(port, source)
    mapping.apply_metadata_selections(items, UID, context=CONTEXT, port=port)
    assert len(port.resolved_plans) == 3
    assert all(plan == source["nativeStudyOperation"] for plan in port.resolved_plans)


def test_all_stated_stratification_factors_compose_in_selected_order():
    port = NativePort()
    sources = [
        intent(
            f"factor-{index}",
            "study_intervention.stratification_factor",
            value,
            metadataJoinedText=True,
        )
        for index, value in enumerate(["Site", "Disease severity", "Site"])
    ]
    observed = mapping.apply_metadata_selections(
        prepared(port, *sources), UID, port=port
    )
    assert len(port.patches) == 1
    assert all(
        value["metadataValue"] == "Site\nDisease severity"
        for value in observed.values()
    )


@pytest.mark.parametrize("value", ["30000", False, None, 3.5])
def test_invalid_numeric_values_are_not_offered_or_silently_coerced(value):
    port = NativePort()
    offered = prepared(port, intent("count", COUNT, value))[0]["candidate"]
    assert offered["createOption"] is None
    assert offered["blockers"] == ["OSB_STUDY_METADATA_VALUE_INVALID"]
    assert not port.patches


@pytest.mark.parametrize(
    "path",
    [
        "identification_metadata.study_id",
        "version_metadata.study_status",
        "account.admin",
    ],
)
def test_derived_or_unrelated_paths_cannot_be_offered(path):
    with pytest.raises(OsbCandidateSetError) as error:
        prepared(NativePort(), intent("invalid", path, "value"))
    assert error.value.code == "OSB_STUDY_METADATA_PLAN_INVALID"


def test_source_plans_cannot_supply_native_term_identities():
    port = NativePort()
    offered = prepared(
        port,
        intent("term", TERMS, {"term_uid": "Invented_uid"}, metadataMultiValued=True),
    )[0]["candidate"]
    assert offered["createOption"] is None
    assert offered["blockers"] == ["OSB_STUDY_METADATA_REFERENCE_INVALID"]


def test_different_source_plan_or_offer_is_rejected_before_patch():
    for changed in ["intent", "offer"]:
        port = NativePort()
        items = prepared(port, intent("count", COUNT, 30000))
        if changed == "intent":
            items[0]["intent"]["nativeStudyOperation"]["metadataValue"] = 1
        else:
            items[0]["candidate"]["createOption"]["nativeStudyOperation"][
                "metadataValue"
            ] = 1
        with pytest.raises(OsbCandidateSetError) as error:
            mapping.apply_metadata_selections(items, UID, port=port)
        assert error.value.code == "OSB_STUDY_METADATA_OFFER_MISMATCH"
        assert not port.patches


def test_unchanged_native_value_after_patch_cannot_generate_successful_readback():
    port = NativePort()
    port.ignore_patch = True
    with pytest.raises(OsbCandidateSetError) as error:
        mapping.apply_metadata_selections(
            prepared(port, intent("count", COUNT, 30000)), UID, port=port
        )
    assert error.value.code == "OSB_STUDY_METADATA_READBACK_MISMATCH"


def test_locked_native_study_is_rejected():
    port = NativePort()
    port.study["current_metadata"]["version_metadata"]["study_status"] = "LOCKED"
    with pytest.raises(OsbCandidateSetError) as error:
        prepared(port, intent("count", COUNT, 30000))
    assert error.value.code == "OSB_STUDY_METADATA_DRAFT_REQUIRED"
