"""Bounded deterministic mapping-context tests."""

import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from clinical_mdr_api.models.integrations.mapping_context import (
    MappingContextCandidate,
    MappingContextDataModel,
    MappingContextRequest,
    MappingContextV2Request,
)
from clinical_mdr_api.routers.integrations.mapping_context import canonical_openapi_hash
from clinical_mdr_api.services.integrations.mapping_context import MappingContextService


def test_openapi_hash_is_key_order_independent():
    assert canonical_openapi_hash({"b": 2, "a": {"y": 1, "x": 0}}) == (
        canonical_openapi_hash({"a": {"x": 0, "y": 1}, "b": 2})
    )


def test_openapi_hash_uses_javascript_numeric_canonicalization():
    expected = hashlib.sha256(b'{"large":1e+21,"small":1e-7}').hexdigest()
    assert canonical_openapi_hash({"small": 1e-7, "large": 1e21}) == expected


def test_empty_search_never_dumps_the_osb_library(monkeypatch):
    called = False

    def fail_query(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("unbounded context must not query candidate libraries")

    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_context.db.cypher_query",
        fail_query,
    )
    context = MappingContextService().get_context(
        MappingContextRequest(resource_families=["controlled_terminology"]),
        osb_openapi_hash="openapi",
    )

    assert called is False
    assert context.governed is False
    assert context.candidates == {}
    assert any("candidate lists are empty" in warning for warning in context.warnings)
    assert "MAPPING_CONTEXT_DDF_CT_PACKAGE_MISSING" in context.release_blockers


def test_context_is_bounded_pinned_and_hash_deterministic(monkeypatch):
    standards = [
        SimpleNamespace(
            uid=f"StudyStandardVersion_{index}",
            automatically_created=False,
            ct_package=SimpleNamespace(
                uid=f"{catalogue}-2025",
                catalogue_name=catalogue,
                effective_date="2025-09-26",
            ),
        )
        for index, catalogue in enumerate(("DDF CT", "SDTM CT", "CDASH CT"), start=1)
    ]
    observed = {}

    def query(text, params):
        if "DataModelRoot" in text:
            return ([[params["model_uid"], params["ig_uid"]]], None)
        observed["text"] = text
        observed["params"] = params
        return (
            [
                [
                    "C123",
                    "Blood Pressure",
                    "C123",
                    "Final",
                    "1.0",
                    "DDF CT-2025",
                    "2025-09-26",
                ],
            ],
            None,
        )

    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_context.db.cypher_query",
        query,
    )
    request = MappingContextRequest(
        study_uid="Study_1",
        study_value_version="3",
        requested_data_models=[
            {
                "family": "SDTM",
                "model_uid": "SDTM",
                "model_version": "2.0",
                "implementation_guide_uid": "SDTMIG",
                "implementation_guide_version": "3.4",
            },
            {
                "family": "CDASH",
                "model_uid": "CDASH",
                "model_version": "1.3",
                "implementation_guide_uid": "CDASHIG",
                "implementation_guide_version": "2.3",
            },
        ],
        resource_families=["controlled_terminology"],
        search_strings=[" Blood Pressure "],
        maximum_candidates_per_family=1,
    )

    loaded = []

    def load_standards(**kwargs):
        loaded.append(kwargs)
        return standards

    registered = []

    class Registry:
        @staticmethod
        def save_context(context_hash, content):
            registered.append((context_hash, content))

    service = MappingContextService(
        standard_version_loader=load_standards,
        context_registry=Registry(),
    )
    first = service.get_context(request, "openapi")
    second = service.get_context(request, "openapi")

    assert first.context_hash == second.context_hash
    assert [item[0] for item in registered] == [first.context_hash, first.context_hash]
    assert all(item[1]["governed"] is True for item in registered)
    assert loaded == [
        {"study_uid": "Study_1", "study_value_version": "3"},
        {"study_uid": "Study_1", "study_value_version": "3"},
    ]
    assert first.governed is True
    assert first.release_blockers == []
    assert first.generated_at is not None
    assert second.generated_at is not None
    packages_by_catalogue = {
        package.catalogue_name: package for package in first.selected_packages
    }
    assert packages_by_catalogue["DDF CT"].package_uid == "DDF CT-2025"
    assert first.candidates["controlled_terminology"][0].uid == "C123"
    assert observed["params"]["searches"] == ["blood pressure"]
    assert observed["params"]["package_uids"] == [
        "CDASH CT-2025",
        "DDF CT-2025",
        "SDTM CT-2025",
    ]
    assert observed["params"]["limit"] == 1
    assert "version.status = 'Final'" in observed["text"]
    assert "version.start_date <= datetime" in observed["text"]
    assert "version.end_date IS NULL OR version.end_date > datetime" in observed["text"]
    assert "ORDER BY match_rank" in observed["text"]


def test_data_model_family_is_graph_owned_and_cannot_be_relabelled(monkeypatch):
    observed = []

    def query(text, params):
        observed.append((text, params))
        # Mirror the graph: only SDTM/SDTMIG exists. A caller that labels the
        # same pair CDASH must not make it pass merely by changing `family`.
        if (
            params["model_catalogue"] == "SDTM"
            and params["ig_catalogue"] == "SDTMIG"
            and params["model_uid"] == "SDTM"
            and params["ig_uid"] == "SDTMIG"
        ):
            return ([["SDTM", "SDTMIG"]], None)
        return ([], None)

    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_context.db.cypher_query",
        query,
    )
    blockers = []
    selected = MappingContextService._selected_data_models(
        MappingContextRequest(
            requested_data_models=[
                {
                    "family": "SDTM",
                    "model_uid": "SDTM",
                    "model_version": "2.0",
                    "implementation_guide_uid": "SDTMIG",
                    "implementation_guide_version": "3.4",
                },
                {
                    "family": "CDASH",
                    "model_uid": "SDTM",
                    "model_version": "2.0",
                    "implementation_guide_uid": "SDTMIG",
                    "implementation_guide_version": "3.4",
                },
            ]
        ),
        blockers,
    )

    assert [item.family for item in selected] == ["SDTM"]
    assert blockers == ["MAPPING_CONTEXT_MODEL_IG_NOT_FOUND:CDASH:SDTM:SDTMIG"]
    assert all(
        "DataModelCatalogue {name: $model_catalogue}" in text for text, _ in observed
    )
    assert all(
        "DataModelCatalogue {name: $ig_catalogue}" in text for text, _ in observed
    )
    assert observed[0][1]["model_catalogue"] == "SDTM"
    assert observed[1][1]["model_catalogue"] == "CDASH"


def _v2_request(groups, maximum=1, as_of=None):
    return MappingContextV2Request(
        requested_packages=[
            {
                "catalogue_name": catalogue,
                "package_uid": f"{catalogue}-2025",
                "effective_date": "2025-09-26",
            }
            for catalogue in ("DDF CT", "SDTM CT", "CDASH CT")
        ],
        requested_data_models=[
            {
                "family": family,
                "model_uid": family,
                "model_version": "1.0",
                "implementation_guide_uid": f"{family}IG",
                "implementation_guide_version": "1.0",
            }
            for family in ("SDTM", "CDASH")
        ],
        candidate_groups=groups,
        maximum_candidates_per_group=maximum,
        as_of=as_of,
    )


def test_v2_usdm_context_requires_only_pinned_ddf_package(monkeypatch):
    saved = []
    service = MappingContextService(
        context_registry=SimpleNamespace(
            save_context=lambda context_hash, content: saved.append(
                (context_hash, content)
            )
        )
    )
    monkeypatch.setattr(
        service,
        "_selected_packages",
        lambda *_: [
            SimpleNamespace(
                catalogue_name="DDF CT",
                package_uid="DDF CT-2025",
                effective_date="2025-09-26",
                automatically_created=False,
            )
        ],
    )
    monkeypatch.setattr(service, "_selected_data_models", lambda *_: [])

    context = service.get_context_v2(
        MappingContextV2Request(
            requested_packages=[
                {
                    "catalogue_name": "DDF CT",
                    "package_uid": "DDF CT-2025",
                    "effective_date": "2025-09-26",
                }
            ],
            requested_data_models=[],
            candidate_groups=[],
        ),
        "a" * 64,
    )

    assert context.governed is True
    assert context.release_blockers == []
    assert context.selected_data_models == []
    assert len(saved) == 1


def test_v2_cdash_family_still_requires_collection_packages_and_models(monkeypatch):
    service = MappingContextService(
        context_registry=SimpleNamespace(save_context=lambda *_: None)
    )
    monkeypatch.setattr(
        service,
        "_selected_packages",
        lambda *_: [
            SimpleNamespace(
                catalogue_name="DDF CT",
                package_uid="DDF CT-2025",
                effective_date="2025-09-26",
                automatically_created=False,
            )
        ],
    )
    monkeypatch.setattr(service, "_selected_data_models", lambda *_: [])

    context = service.get_context_v2(
        MappingContextV2Request(
            candidate_groups=[
                {
                    "fact_id": "fact-1",
                    "concept_id": "concept-1",
                    "target_key": "collection-variable",
                    "semantic_role": "explicit auxiliary CDASH export mapping",
                    "resource_family": "cdash_variables",
                    "search_strings": ["systolic blood pressure"],
                }
            ]
        ),
        "a" * 64,
    )

    assert context.governed is False
    assert "MAPPING_CONTEXT_CDASH_CT_PACKAGE_MISSING" in context.release_blockers
    assert "MAPPING_CONTEXT_SDTM_CT_PACKAGE_MISSING" in context.release_blockers
    assert "MAPPING_CONTEXT_CDASH_MODEL_IG_MISSING" in context.release_blockers
    assert "MAPPING_CONTEXT_SDTM_MODEL_IG_MISSING" in context.release_blockers


def test_v2_request_rejects_duplicate_groups_and_oversized_search_terms():
    group = {
        "fact_id": "fact-1",
        "concept_id": "concept-1",
        "target_key": "activity",
        "semantic_role": "activity",
        "resource_family": "activities",
        "search_strings": ["blood pressure"],
    }
    with pytest.raises(ValidationError, match="DUPLICATE_CANDIDATE_GROUP"):
        _v2_request([group, group])
    with pytest.raises(ValidationError, match="at most 256 characters"):
        _v2_request([{**group, "search_strings": ["x" * 257]}])


def test_v2_ct_group_without_governing_codelist_is_blocked_without_query(monkeypatch):
    service = MappingContextService(
        context_registry=SimpleNamespace(save_context=lambda *_: None)
    )
    monkeypatch.setattr(
        service,
        "_selected_packages",
        lambda *_: [
            SimpleNamespace(
                catalogue_name=catalogue,
                package_uid=f"{catalogue}-2025",
                effective_date="2025-09-26",
                automatically_created=False,
            )
            for catalogue in ("DDF CT", "SDTM CT", "CDASH CT")
        ],
    )
    monkeypatch.setattr(
        service,
        "_selected_data_models",
        lambda *_: [
            SimpleNamespace(
                family=family,
                model_uid=family,
                model_version="1.0",
                implementation_guide_uid=f"{family}IG",
                implementation_guide_version="1.0",
            )
            for family in ("SDTM", "CDASH")
        ],
    )
    monkeypatch.setattr(
        service,
        "_controlled_terminology_v2",
        lambda *_: pytest.fail("unconstrained CT retrieval must not execute"),
    )
    context = service.get_context_v2(
        _v2_request(
            [
                {
                    "fact_id": "fact-1",
                    "concept_id": "concept-1",
                    "target_key": "endpoint-level",
                    "semantic_role": "endpoint level",
                    "resource_family": "controlled_terminology",
                    "search_strings": ["Primary Outcome Measure"],
                }
            ]
        ),
        "a" * 64,
    )
    assert context.candidate_groups[0].release_blockers == [
        "MAPPING_CONTEXT_CT_PARENT_REQUIRED"
    ]
    assert context.candidate_groups[0].candidates == []


def test_v2_ct_query_is_constrained_to_requested_parent_codelist(monkeypatch):
    observed = {}

    def query(text, params):
        observed["text"] = text
        observed["params"] = params
        return (
            [
                [
                    "C123",
                    "Primary Outcome Measure",
                    "C123",
                    "1.0",
                    "Final",
                    None,
                    None,
                    "DDF CT",
                    "DDF CT-2025",
                    "2025-09-26",
                    "Codelist_EndpointLevel",
                    "1.0",
                    "Endpoint Level",
                    False,
                    "PRIMARY",
                ]
            ],
            None,
        )

    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_context.db.cypher_query",
        query,
    )
    candidates, incomplete = MappingContextService._controlled_terminology_v2(
        ["primary outcome measure"],
        [],
        ["endpoint level"],
        ["DDF CT-2025"],
        2,
    )
    assert incomplete == 0
    assert candidates[0].parent_submission_value == "Endpoint Level"
    assert observed["params"]["parent_searches"] == ["endpoint level"]
    assert "codelist_attributes.submission_value" in observed["text"]


def test_v2_empty_groups_persist_a_governed_prerequisite_snapshot(monkeypatch):
    saved = []
    service = MappingContextService(
        context_registry=SimpleNamespace(
            save_context=lambda context_hash, content: saved.append(
                (context_hash, content)
            )
        )
    )
    monkeypatch.setattr(
        service,
        "_selected_packages",
        lambda *_: [
            SimpleNamespace(
                study_standard_version_uid=None,
                catalogue_name=catalogue,
                package_uid=f"{catalogue}-2025",
                effective_date="2025-09-26",
                automatically_created=False,
            )
            for catalogue in ("DDF CT", "SDTM CT", "CDASH CT")
        ],
    )
    monkeypatch.setattr(
        service,
        "_selected_data_models",
        lambda *_: [
            SimpleNamespace(
                family=family,
                model_uid=family,
                model_version="1.0",
                implementation_guide_uid=f"{family}IG",
                implementation_guide_version="1.0",
            )
            for family in ("SDTM", "CDASH")
        ],
    )

    context = service.get_context_v2(_v2_request([]), "a" * 64)

    assert context.governed is True
    assert context.candidate_groups == []
    assert context.release_blockers == []
    assert len(saved) == 1
    assert saved[0][0] == context.context_hash
    assert saved[0][1]["candidateGroups"] == []


def test_v2_groups_are_independently_bounded_and_cannot_starve_each_other(monkeypatch):
    service = MappingContextService(
        context_registry=SimpleNamespace(save_context=lambda *_: None)
    )
    monkeypatch.setattr(
        service,
        "_selected_packages",
        lambda *_: [
            SimpleNamespace(
                catalogue_name=catalogue,
                package_uid=f"{catalogue}-2025",
                effective_date="2025-09-26",
                automatically_created=False,
            )
            for catalogue in ("DDF CT", "SDTM CT", "CDASH CT")
        ],
    )
    monkeypatch.setattr(
        service,
        "_selected_data_models",
        lambda *_: [
            SimpleNamespace(
                family=family,
                model_uid=family,
                model_version="1.0",
                implementation_guide_uid=f"{family}IG",
                implementation_guide_version="1.0",
            )
            for family in ("SDTM", "CDASH")
        ],
    )
    observed = []

    def retrieve(family, searches, codes, limit, as_of):
        observed.append((family, searches, limit, as_of))
        return [
            MappingContextCandidate(
                resource_family="activities",
                resource_type="Activity",
                uid=f"{searches[0]}-{index}",
                version="1.0",
                status="Final",
                label=searches[0],
                library_name="Sponsor",
                parent_resource_type="Library",
                parent_uid="Sponsor",
                parent_version="unversioned-library-name",
            )
            for index in range(2)
        ], 0

    monkeypatch.setattr(service, "_versioned_library_family_v2", retrieve)
    context = service.get_context_v2(
        _v2_request(
            [
                {
                    "fact_id": "fact-1",
                    "concept_id": "concept-1",
                    "target_key": "activity-1",
                    "semantic_role": "activity",
                    "resource_family": "activities",
                    "search_strings": ["alpha"],
                },
                {
                    "fact_id": "fact-2",
                    "concept_id": "concept-2",
                    "target_key": "activity-2",
                    "semantic_role": "activity",
                    "resource_family": "activities",
                    "search_strings": ["omega"],
                },
            ],
            maximum=1,
        ),
        "a" * 64,
    )

    assert [row[1] for row in observed] == [["alpha"], ["omega"]]
    assert all(row[2] == 2 for row in observed)
    assert [group.candidates[0].uid for group in context.candidate_groups] == [
        "alpha-0",
        "omega-0",
    ]
    assert all(group.truncated for group in context.candidate_groups)
    assert context.governed is True
    assert (
        len([code for code in context.release_blockers if "GROUP_TRUNCATED" in code])
        == 2
    )


def test_v2_unit_current_query_uses_real_ucum_expression_and_relationship_version(
    monkeypatch,
):
    observed = {}

    def query(text, params):
        observed["text"] = text
        observed["params"] = params
        return (
            [
                [
                    "UnitDefinition_1",
                    "milligram",
                    "2.0",
                    "Final",
                    "2025-01-01T00:00:00Z",
                    None,
                    "mg",
                    "Mass",
                    0.001,
                ]
            ],
            None,
        )

    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_context.db.cypher_query",
        query,
    )
    candidates, incomplete = MappingContextService._units_v2(
        ["milligram"], ["mg"], 2, None
    )
    assert incomplete == 0
    assert candidates[0].ucum_expression == "mg"
    assert candidates[0].version == "2.0"
    assert candidates[0].dimension == "Mass"
    assert "UCUMTermValue" in observed["text"]
    assert "ucum_value.name" in observed["text"]
    assert "LATEST_FINAL" in observed["text"]
    assert "version.end_date IS NULL" in observed["text"]
    assert "value.version" not in observed["text"]


def test_v2_historical_family_query_uses_only_explicit_temporal_validity(monkeypatch):
    observed = {}

    def query(text, params):
        observed["text"] = text
        observed["params"] = params
        return (
            [
                [
                    "Activity_1",
                    "Blood Pressure",
                    "1.0",
                    "Final",
                    "2024-01-01T00:00:00Z",
                    "2025-01-01T00:00:00Z",
                    "Sponsor",
                    None,
                    None,
                    0,
                ]
            ],
            None,
        )

    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_context.db.cypher_query",
        query,
    )
    as_of = datetime(2024, 6, 1, tzinfo=timezone.utc)
    candidates, incomplete = MappingContextService._versioned_library_family_v2(
        "activities", ["blood pressure"], [], 2, as_of
    )
    assert incomplete == 0
    assert candidates[0].version == "1.0"
    assert "LATEST_FINAL" not in observed["text"]
    assert "version.start_date <= datetime($as_of)" in observed["text"]
    assert "version.end_date > datetime($as_of)" in observed["text"]
    assert observed["params"]["as_of"] == as_of.isoformat()


def test_v2_timeframe_family_returns_instance_identity_not_template(monkeypatch):
    observed = {}

    def query(text, params):
        observed["text"] = text
        observed["params"] = params
        return (
            [
                [
                    "Timeframe_1",
                    "Week 24",
                    "1.0",
                    "Final",
                    "2025-01-01T00:00:00Z",
                    None,
                    "Sponsor",
                    None,
                    None,
                    0,
                ]
            ],
            None,
        )

    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_context.db.cypher_query",
        query,
    )

    candidates, incomplete = MappingContextService._versioned_library_family_v2(
        "timeframes", ["week 24"], [], 2, None
    )

    assert incomplete == 0
    assert candidates[0].resource_family == "timeframes"
    assert candidates[0].resource_type == "Timeframe"
    assert candidates[0].uid == "Timeframe_1"
    assert "TimeframeRoot" in observed["text"]
    assert "TimeframeValue" in observed["text"]
    assert "TimeframeTemplateRoot" not in observed["text"]


def test_v2_criteria_template_requires_native_type_and_parameter_identity(monkeypatch):
    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_context.db.cypher_query",
        lambda *_: (
            [
                [
                    "CriteriaTemplate_1",
                    "Age at least 18",
                    "1.0",
                    "Final",
                    None,
                    None,
                    "Sponsor",
                    None,
                    None,
                    0,
                ]
            ],
            None,
        ),
    )
    candidates, incomplete = MappingContextService._versioned_library_family_v2(
        "criteria_templates", ["age at least 18"], [], 2, None
    )
    assert candidates == []
    assert incomplete == 1

    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_context.db.cypher_query",
        lambda *_: (
            [
                [
                    "CriteriaTemplate_1",
                    "Age at least 18",
                    "1.0",
                    "Final",
                    None,
                    None,
                    "Sponsor",
                    None,
                    "CriteriaType_Inclusion",
                    0,
                ]
            ],
            None,
        ),
    )
    candidates, incomplete = MappingContextService._versioned_library_family_v2(
        "criteria_templates", ["age at least 18"], [], 2, None
    )
    assert incomplete == 0
    assert candidates[0].criteria_type_uid == "CriteriaType_Inclusion"
    assert candidates[0].parameter_count == 0


def test_v2_incomplete_candidate_identity_is_removed_and_release_blocking(monkeypatch):
    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_context.db.cypher_query",
        lambda *_: (
            [
                [
                    "UnitDefinition_1",
                    "milligram",
                    None,
                    "Final",
                    None,
                    None,
                    None,
                    None,
                    None,
                ]
            ],
            None,
        ),
    )
    candidates, incomplete = MappingContextService._units_v2(["milligram"], [], 2, None)
    assert candidates == []
    assert incomplete == 1


def _selected_models():
    return [
        MappingContextDataModel(
            family=family,
            model_uid=family,
            model_version="1.0",
            implementation_guide_uid=f"{family}IG",
            implementation_guide_version="1.0",
        )
        for family in ("SDTM", "CDASH")
    ]


def test_v2_cdash_variable_is_pinned_and_carries_governed_sdtm_target(monkeypatch):
    observed = {}

    def query(text, params):
        observed["text"] = text
        observed["params"] = params
        return (
            [
                [
                    "DatasetVariable_SYSBP",
                    "Systolic Blood Pressure",
                    "SYSBP",
                    "1.0",
                    "Final",
                    "2025-01-01T00:00:00Z",
                    None,
                    "VS",
                    "1.0",
                    "CDASH",
                    "1.0",
                    "CDASHIG",
                    "1.0",
                    "DatasetVariable_VSORRES",
                    "3.4",
                ]
            ],
            None,
        )

    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_context.db.cypher_query",
        query,
    )
    candidates, incomplete = MappingContextService._cdash_variables_v2(
        ["systolic blood pressure"], ["sysbp"], 3, None, _selected_models()
    )

    assert incomplete == 0
    assert candidates[0].model_uid == "CDASH"
    assert candidates[0].implementation_guide_uid == "CDASHIG"
    assert candidates[0].parent_uid == "VS"
    assert candidates[0].code == "SYSBP"
    assert candidates[0].mapping_target_uid == "DatasetVariable_VSORRES"
    assert candidates[0].mapping_target_version == "3.4"
    assert "variable_value.name" in observed["text"]
    assert "HAS_MAPPING_TARGET" in observed["text"]
    assert "sdtm_ig_value.version_number" in observed["text"]
    assert "LATEST_FINAL" not in observed["text"]
    assert observed["params"]["model_uid"] == "CDASH"
    assert observed["params"]["sdtm_ig_uid"] == "SDTMIG"
    assert observed["params"]["as_of"] is None


def test_v2_historical_cdash_variable_uses_explicit_ig_validity(monkeypatch):
    observed = {}

    def query(text, params):
        observed["text"] = text
        observed["params"] = params
        return ([], None)

    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_context.db.cypher_query",
        query,
    )
    as_of = datetime(2024, 6, 1, tzinfo=timezone.utc)
    MappingContextService._cdash_variables_v2(
        ["medical history term"], ["mhterm"], 3, as_of, _selected_models()
    )

    assert "ig_version.start_date <= datetime($as_of)" in observed["text"]
    assert "sdtm_ig_version.start_date <= datetime($as_of)" in observed["text"]
    assert "LATEST_FINAL" not in observed["text"]
    assert observed["params"]["as_of"] == as_of.isoformat()


def test_v2_cdash_variable_without_sdtm_target_identity_is_release_blocking(
    monkeypatch,
):
    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_context.db.cypher_query",
        lambda *_: (
            [
                [
                    "DatasetVariable_SYSBP",
                    "Systolic Blood Pressure",
                    "SYSBP",
                    "1.0",
                    "Final",
                    None,
                    None,
                    "VS",
                    "1.0",
                    "CDASH",
                    "1.0",
                    "CDASHIG",
                    "1.0",
                    None,
                    None,
                ]
            ],
            None,
        ),
    )
    candidates, incomplete = MappingContextService._cdash_variables_v2(
        ["systolic blood pressure"], ["sysbp"], 3, None, _selected_models()
    )
    assert candidates == []
    assert incomplete == 1


def test_v2_codelist_retrieval_is_package_pinned_and_uses_submission_value(monkeypatch):
    observed = {}

    def query(text, params):
        observed["text"] = text
        observed["params"] = params
        return (
            [
                [
                    "C66741",
                    "Vital Signs Test Code",
                    "C66741",
                    "7.0",
                    "Final",
                    "2025-01-01T00:00:00Z",
                    None,
                    "CDASH CT",
                    "CDASH CT-2025",
                    "2025-09-26",
                    "VSTESTCD",
                    True,
                ]
            ],
            None,
        )

    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_context.db.cypher_query",
        query,
    )
    candidates, incomplete = MappingContextService._controlled_terminology_codelists_v2(
        ["vital signs test code"], ["vstestcd"], ["CDASH CT-2025"], 3
    )

    assert incomplete == 0
    assert candidates[0].resource_type == "CTCodelist"
    assert candidates[0].uid == "C66741"
    assert candidates[0].submission_value == "VSTESTCD"
    assert candidates[0].package_uid == "CDASH CT-2025"
    assert candidates[0].extensible is True
    assert "attributes.submission_value" in observed["text"]
    assert "package.effective_date" in observed["text"]
    assert observed["params"]["package_uids"] == ["CDASH CT-2025"]


def test_v2_candidate_group_bound_is_protocol_sized_but_fail_closed():
    group = {
        "fact_id": "fact-1",
        "concept_id": "concept-1",
        "target_key": "activity",
        "semantic_role": "activity",
        "resource_family": "activities",
        "search_strings": ["blood pressure"],
    }
    request = _v2_request(
        [
            {
                **group,
                "fact_id": f"fact-{index}",
                "concept_id": f"concept-{index}",
            }
            for index in range(10_000)
        ]
    )
    assert len(request.candidate_groups) == 10_000
    with pytest.raises(ValidationError, match="at most 10000 items"):
        _v2_request(
            [
                {
                    **group,
                    "fact_id": f"fact-{index}",
                    "concept_id": f"concept-{index}",
                }
                for index in range(10_001)
            ]
        )
