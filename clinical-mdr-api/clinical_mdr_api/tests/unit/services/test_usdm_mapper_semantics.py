"""Fail-closed semantics for the authoritative native OSB to USDM mapper."""

from types import SimpleNamespace

from clinical_mdr_api.services.ddf.usdm_mapper import USDMMapper


def _mapper(*, standards=None, activities=None):
    empty = lambda *_args, **_kwargs: []
    return USDMMapper(
        get_osb_study_design_cells=empty,
        get_osb_study_arms=empty,
        get_osb_study_epochs=empty,
        get_osb_study_elements=empty,
        get_osb_study_endpoints=empty,
        get_osb_study_visits=empty,
        get_osb_study_activities=activities or empty,
        get_osb_activity_schedules=empty,
        get_osb_study_objectives=empty,
        get_osb_study_standard_versions=standards,
    )


def test_ct_code_is_resolved_only_through_the_selected_final_package(monkeypatch):
    observed = {}

    def standards(*, study_uid, study_value_version=None):
        observed["study_uid"] = study_uid
        observed["study_value_version"] = study_value_version
        return [
            SimpleNamespace(
                ct_package=SimpleNamespace(
                    uid="DDF CT 2025-09-26",
                    catalogue_name="DDF CT",
                    effective_date="2025-09-26",
                )
            )
        ]

    def query(text, params):
        observed["query"] = text
        observed["params"] = params
        return ([[{"name": "CDISC"}, {"name": "Study Official Title"}]], None)

    monkeypatch.setattr(
        "clinical_mdr_api.services.ddf.usdm_mapper.db.cypher_query", query
    )
    mapper = _mapper(standards=standards)
    mapper._study_value_version = "3.0"

    assert mapper._resolve_ct_package_effective_date("Study_1") == "2025-09-26"
    code = mapper.get_ct_package_term_as_usdm_code("C207616")

    assert observed["study_uid"] == "Study_1"
    assert observed["study_value_version"] == "3.0"
    assert observed["params"]["package_uid"] == "DDF CT 2025-09-26"
    assert "package:CTPackage {uid: $package_uid}" in observed["query"]
    assert "version.status IN ['Final', 'Retired']" in observed["query"]
    assert "version.start_date <= $package_datetime" in observed["query"]
    assert code.code == "C207616"
    assert code.codeSystemVersion == "DDF CT 2025-09-26"


def test_unpinned_ct_lookup_returns_void_without_querying_global_latest(monkeypatch):
    monkeypatch.setattr(
        "clinical_mdr_api.services.ddf.usdm_mapper.db.cypher_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unpinned lookup must not query a global CT library")
        ),
    )
    mapper = _mapper(standards=lambda *_args, **_kwargs: [])

    assert mapper._resolve_ct_package_effective_date("Study_1") == "UNPINNED"
    code = mapper.get_ct_package_term_as_usdm_code("C207616")

    assert code.code == ""
    assert code.codeSystemVersion == ""


def test_activity_projection_preserves_native_identity_without_false_defaults():
    observed = {}

    def activities(study_uid, *, study_value_version=None):
        observed["study_uid"] = study_uid
        observed["study_value_version"] = study_value_version
        return [
            SimpleNamespace(
                study_activity_uid="StudyActivity_1",
                order=1,
                activity=SimpleNamespace(
                    uid="Activity_42",
                    name="12-lead electrocardiogram",
                    name_sentence_case="12-lead electrocardiogram",
                    definition="Recording of cardiac electrical activity.",
                ),
                study_activity_subgroup=SimpleNamespace(
                    activity_subgroup_name="Cardiac assessments"
                ),
            )
        ]

    mapper = _mapper(activities=activities)
    mapper._study_value_version = "2.0"
    projected = mapper._get_study_activities(SimpleNamespace(uid="Study_1"))
    payload = projected[0].model_dump(by_alias=True)

    assert observed == {
        "study_uid": "Study_1",
        "study_value_version": "2.0",
    }
    assert payload["name"] == "12-lead electrocardiogram"
    assert payload["label"] == "12-lead electrocardiogram"
    assert payload["description"] == "Recording of cardiac electrical activity."
    assert payload["definedProcedures"] == []
    assert "isConditional" not in payload