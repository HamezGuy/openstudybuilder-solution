"""Fail-closed semantics for the authoritative native OSB to USDM mapper."""

from types import SimpleNamespace

from clinical_mdr_api.services.ddf.usdm_mapper import USDMMapper


def _mapper(
    *,
    standards=None,
    activities=None,
    elements=None,
    objectives=None,
    endpoints=None,
    compounds=None,
    dosings=None,
):
    empty = lambda *_args, **_kwargs: []
    return USDMMapper(
        get_osb_study_design_cells=empty,
        get_osb_study_arms=empty,
        get_osb_study_epochs=empty,
        get_osb_study_elements=elements or empty,
        get_osb_study_endpoints=endpoints or empty,
        get_osb_study_visits=empty,
        get_osb_study_activities=activities or empty,
        get_osb_activity_schedules=empty,
        get_osb_study_objectives=objectives or empty,
        get_osb_study_standard_versions=standards,
        get_osb_study_compounds=compounds,
        get_osb_study_compound_dosings=dosings,
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


def test_study_element_name_is_native_and_dosing_links_real_intervention():
    compound = SimpleNamespace(
        study_compound_uid="StudyCompound_1",
        order=1,
        compound=SimpleNamespace(
            name="Ranibizumab",
            definition="Anti-VEGF compound",
            external_id="RNB-1",
            library_name="Sponsor",
            version="1.0",
        ),
        compound_alias=None,
        medicinal_product=None,
        type_of_treatment=None,
        dose_frequency=None,
        dispenser=None,
        dispensed_in=None,
        delivery_device=None,
        other_info=None,
    )
    dosing = SimpleNamespace(
        study_compound_dosing_uid="StudyCompoundDosing_1",
        order=1,
        study_compound=compound,
        study_element=SimpleNamespace(element_uid="StudyElement_1"),
        dose_value=SimpleNamespace(
            uid="NumericValue_1",
            value=0.5,
            unit_definition_uid="UnitDefinition_mg",
            unit_label="mg",
        ),
    )
    mapper = _mapper(
        elements=lambda *_args, **_kwargs: [
            SimpleNamespace(
                element_uid="StudyElement_1",
                order=1,
                name="Monthly treatment",
                description="Monthly intravitreal treatment",
            )
        ],
        compounds=lambda *_args, **_kwargs: [compound],
        dosings=lambda *_args, **_kwargs: [dosing],
    )
    study = SimpleNamespace(
        uid="Study_1",
        current_metadata=SimpleNamespace(
            study_intervention=SimpleNamespace(intervention_type_code=None)
        ),
    )
    mapper._load_study_intervention_selections(study.uid)

    interventions = mapper._get_study_interventions(study)
    elements = mapper._get_study_elements(study)

    assert [item.name for item in interventions] == ["Ranibizumab"]
    assert interventions[0].codes[0].code == "RNB-1"
    assert any(
        extension.url.endswith("/compound-dosing")
        for extension in interventions[0].extensionAttributes
    )
    assert elements[0].name == "Monthly treatment"
    assert elements[0].studyInterventionIds == [interventions[0].id]


def test_indication_survives_unknown_rare_disease_state(monkeypatch):
    mapper = _mapper()
    monkeypatch.setattr(
        mapper,
        "get_dictionary_term_as_usdm_code",
        lambda _uid: mapper.get_void_usdm_code(),
    )
    study = SimpleNamespace(
        current_metadata=SimpleNamespace(
            study_population=SimpleNamespace(
                rare_disease_indicator=None,
                rare_disease_indicator_null_value_code=None,
                disease_condition_or_indication_codes=[
                    SimpleNamespace(term_uid="MedDRA_10015488", name="Wet AMD")
                ],
            )
        )
    )

    indications = mapper._get_study_indications(study)

    assert len(indications) == 1
    assert indications[0].name == "Wet AMD"
    assert indications[0].isRareDisease is False
    assert (
        indications[0]
        .extensionAttributes[0]
        .url.endswith("/rare-disease-indicator-unresolved")
    )
    assert indications[0].extensionAttributes[0].valueBoolean is True


def test_endpoint_source_semantics_are_typed_extensions():
    objective = SimpleNamespace(
        study_objective_uid="StudyObjective_1",
        order=1,
        objective=SimpleNamespace(
            uid="Objective_1", name="<p>Assess vision</p>", name_plain="Assess vision"
        ),
        objective_level=None,
    )
    endpoint = SimpleNamespace(
        study_endpoint_uid="StudyEndpoint_1",
        order=1,
        study_objective=objective,
        endpoint=SimpleNamespace(
            uid="Endpoint_1",
            name="<p>Change in BCVA</p>",
            name_plain="Change in BCVA",
        ),
        endpoint_level=None,
        endpoint_sublevel=SimpleNamespace(
            term_uid="C98772", sponsor_preferred_name="Secondary"
        ),
        timeframe=SimpleNamespace(
            uid="Timeframe_1",
            version="1.0",
            name="<p>At month 12</p>",
            name_plain="At month 12",
        ),
        endpoint_units=SimpleNamespace(
            units=[SimpleNamespace(uid="UnitDefinition_1", name="letters")],
            separator=None,
        ),
        collection_disposition="direct",
    )
    mapper = _mapper(
        objectives=lambda *_args, **_kwargs: [objective],
        endpoints=lambda *_args, **_kwargs: [endpoint],
    )

    projected = mapper._get_study_objectives(SimpleNamespace(uid="Study_1"))
    payload = projected[0].endpoints[0].model_dump(by_alias=True)
    extensions = {
        item["url"].rsplit("/", 1)[-1]: item for item in payload["extensionAttributes"]
    }

    assert payload["name"] == "Change in BCVA"
    assert payload["purpose"] == ""
    assert extensions["endpoint-sublevel"]["valueCode"]["code"] == "C98772"
    assert extensions["endpoint-timeframe"]["valueString"] == "At month 12"
    assert (
        extensions["endpoint-units"]["extensionAttributes"][0]["valueId"]
        == "UnitDefinition_1"
    )
    assert extensions["endpoint-collection-disposition"]["valueString"] == "direct"


def test_document_uses_native_identity_and_version_reference(monkeypatch):
    mapper = _mapper(standards=lambda *_args, **_kwargs: [])
    monkeypatch.setattr(mapper, "_load_registid_labels", lambda: {})
    study = SimpleNamespace(
        uid="Study_1",
        current_metadata=SimpleNamespace(
            identification_metadata=SimpleNamespace(
                study_id="TIDE-AMD",
                registry_identifiers=SimpleNamespace(),
            ),
            study_description=SimpleNamespace(
                study_title="Treat-and-extend in wet AMD",
                study_short_title="TIDE AMD",
            ),
            version_metadata=SimpleNamespace(
                study_status="DRAFT",
                version_number="2.0",
                version_description="Protocol amendment 2",
            ),
            high_level_study_design=None,
            study_intervention=None,
            study_population=SimpleNamespace(
                sex_of_participants_code=None,
                planned_minimum_age_of_subjects=None,
                planned_maximum_age_of_subjects=None,
                number_of_expected_subjects=None,
                healthy_subject_indicator=False,
                therapeutic_area_codes=[],
                disease_condition_or_indication_codes=[],
                diagnosis_group_codes=[],
            ),
        ),
    )

    wrapper = mapper.map(study)
    version = wrapper["study"].versions[0]
    document = wrapper["study"].documentedBy[0]

    assert document.name == "TIDE-AMD"
    assert document.label == "TIDE AMD"
    assert document.description == "Treat-and-extend in wet AMD"
    assert document.versions[0].version == "2.0"
    assert version.documentVersionIds == [document.versions[0].id]
    assert version.documentVersionIds != [document.id]


def test_population_preserves_unknown_health_one_sided_age_and_diagnosis_semantics():
    mapper = _mapper()
    population = SimpleNamespace(
        sex_of_participants_code=None,
        planned_minimum_age_of_subjects=SimpleNamespace(
            duration_value=18,
            duration_unit_code=SimpleNamespace(uid="UnitDefinition_year", name="years"),
        ),
        planned_maximum_age_of_subjects=None,
        number_of_expected_subjects=None,
        healthy_subject_indicator=None,
        healthy_subject_indicator_null_value_code=SimpleNamespace(
            term_uid="CTTerm_UNKNOWN", sponsor_preferred_name="Unknown"
        ),
        rare_disease_indicator=None,
        pediatric_study_indicator=False,
        pediatric_postmarket_study_indicator=None,
        pediatric_investigation_plan_indicator=None,
        relapse_criteria="Progression after prior response",
        stable_disease_minimum_duration=None,
        diagnosis_group_codes=[
            SimpleNamespace(term_uid="MedDRA_10029114", name="Neoplasms")
        ],
    )
    projected = mapper._get_study_population(
        SimpleNamespace(current_metadata=SimpleNamespace(study_population=population))
    )
    payload = projected.model_dump(by_alias=True)
    extensions = {
        item["url"].rsplit("/", 1)[-1]: item
        for item in payload["extensionAttributes"]
    }

    assert payload["includesHealthySubjects"] is False
    assert payload["plannedAge"] is None
    assert payload["description"] is None
    assert extensions["healthy-subject-indicator-unresolved"]["valueBoolean"] is True
    assert extensions["planned-minimum-age"]["valueQuantity"]["value"] == 18
    assert extensions["pediatric-study-indicator"]["valueBoolean"] is False
    assert extensions["diagnosis-groups"]["extensionAttributes"][0]["valueCode"][
        "code"
    ] == "MedDRA_10029114"
