"""Native model -> USDM4 mapping, with real schema and isolated source readers."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import pytest

from clinical_mdr_api.services.ddf.usdm_mapper import USDMMapper, USDMMappingAuthorityRequired
from clinical_mdr_api.services.ddf.usdm_mapping_context import MappingContext, native_json
from clinical_mdr_api.tests.fixtures.usdm_native_study import native_study_graph, STUDY_UID, VERSION, AS_OF
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource


SCHEMA_PATH = Path(__file__).parents[2] / "fixtures" / "usdm_native_model4_schema.json"
SCHEMA_SHA256 = "dc4303bca26256c56e5cb83222e898a09a5244472f7fd092b425cc2b7568fe19"


def mapping(graph, monkeypatch):
    source = NativeStudySource(graph)
    monkeypatch.setattr("clinical_mdr_api.services.ddf.usdm_mapper.db.cypher_query", source.query)
    return source.mapper(), source.calls


def run_mapping(monkeypatch, graph=None):
    graph = native_study_graph() if graph is None else graph
    mapper, calls = mapping(graph, monkeypatch)
    return mapper.map_with_report(graph["study"], VERSION), graph, mapper, calls


def nodes(value):
    if isinstance(value, dict):
        if isinstance(value.get("instanceType"), str) and isinstance(value.get("id"), str):
            yield value
        for child in value.values():
            yield from nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from nodes(child)


def design(report):
    return report["document"]["study"]["versions"][0]["studyDesigns"][0]


def test_actual_native_models_map_all_new_domains_without_mutating_sources(monkeypatch):
    graph = native_study_graph()
    before = native_json(graph)
    report, _, _, calls = run_mapping(monkeypatch, graph)
    assert native_json(graph) == before
    kinds = {row["kind"] for row in report["nativeRecords"]}
    assert {
        "studyCohort", "studyBranchArm", "studyActivityInstance", "studyActivityInstruction",
        "studySoAFootnote", "studyDiseaseMilestone", "studyActivityGroup", "studyActivitySubGroup",
        "activityInstanceDefinition", "studySnapshotHistory", "studyProtocolHeader",
        "studyOperationalActivitySchedule", "studyVisit", "dictionaryTermDefinition",
        "studyDataSupplier", "studyDesignClass", "studySourceVariable",
    } <= kinds
    for name in (
        "arms", "visits", "activities", "cohorts", "branches", "instructions",
        "footnotes", "milestones", "data_suppliers", "design_classes", "source_variables",
    ):
        assert len([call for call in calls if call[0] == name]) == 1
    assert design(report)["population"]["plannedEnrollmentNumber"]["value"] == 30
    retained = next(row["record"] for row in report["nativeRecords"] if row["kind"] == "activityInstanceDefinition")
    assert retained["activity_items"][0]["text_value"] == ""
    assert retained["activity_items"][0]["is_adam_param_specific"] is False
    assert retained["activity_items"][0]["is_activity_instance_id_specific"] is None
    assert retained["molecular_weight"] == 0


def test_sex_design_intent_and_blinding_use_codes_not_sponsor_labels(monkeypatch):
    report, *_ = run_mapping(monkeypatch)
    mapped = design(report)
    assert [entry["code"] for entry in mapped["population"]["plannedSex"]] == ["C16576", "C20197"]
    assert mapped["instanceType"] == "InterventionalStudyDesign"
    assert mapped["blindingSchema"]["standardCode"]["code"] == "C49659"
    assert [entry["code"] for entry in mapped["intentTypes"]] == ["C49656"]
    assert [entry["code"] for entry in mapped["characteristics"]] == ["C207613"]


@pytest.mark.parametrize("healthy", [None, False, True])
def test_unknown_health_is_omitted_and_explicit_booleans_are_not_changed(monkeypatch, healthy):
    graph = native_study_graph()
    graph["study"].current_metadata.study_population.healthy_subject_indicator = healthy
    report, *_ = run_mapping(monkeypatch, graph)
    population = design(report)["population"]
    if healthy is None:
        assert "includesHealthySubjects" not in population
    else:
        assert population["includesHealthySubjects"] is healthy
    for cohort in population["cohorts"]:
        if healthy is False:
            assert cohort["includesHealthySubjects"] is False
        else:
            assert "includesHealthySubjects" not in cohort


def test_unknown_rare_disease_is_a_draft_issue_not_false(monkeypatch):
    graph = native_study_graph()
    graph["study"].current_metadata.study_population.rare_disease_indicator = None
    report, *_ = run_mapping(monkeypatch, graph)
    assert "isRareDisease" not in design(report)["indications"][0]
    assert any(issue["targetPath"] == "Indication/isRareDisease" for issue in report["mappingReport"]["issues"])


def test_branch_cells_and_cohorts_keep_exact_selection_relationships(monkeypatch):
    report, *_ = run_mapping(monkeypatch)
    mapped = design(report)
    parent = next(arm for arm in mapped["arms"] if arm["name"] == "Native arm")
    branch = next(arm for arm in mapped["arms"] if arm["name"] == "Native branch")
    cohorts = mapped["population"]["cohorts"]
    assert mapped["studyCells"][0]["armId"] == branch["id"]
    assert mapped["studyCells"][0]["armId"] != parent["id"]
    assert branch["populationIds"] == [cohorts[0]["id"]]
    assert parent["populationIds"] == [cohort["id"] for cohort in cohorts]
    assert all("dataOriginType" not in arm for arm in mapped["arms"])


def test_population_count_has_no_unit_and_cohort_counts_remain_exact_source_values(monkeypatch):
    report, *_ = run_mapping(monkeypatch)
    population = design(report)["population"]
    assert population["plannedEnrollmentNumber"]["value"] == 30
    assert population["plannedEnrollmentNumber"].get("unit") is None
    assert all(cohort["plannedEnrollmentNumber"] is None for cohort in population["cohorts"])
    assert [row["record"]["number_of_subjects"] for row in report["nativeRecords"]
            if row["kind"] == "studyCohort"] == [0, 20]
    assert any(issue["code"] == "USDM_ENROLLMENT_SCOPE_REVIEW_REQUIRED"
               for issue in report["mappingReport"]["issues"])


def test_complete_cohort_counts_preserve_zero_without_a_count_unit(monkeypatch):
    graph = native_study_graph()
    graph["study"].current_metadata.study_population.number_of_expected_subjects = None
    report, *_ = run_mapping(monkeypatch, graph)
    population = design(report)["population"]
    assert population["plannedEnrollmentNumber"] is None
    assert [cohort["plannedEnrollmentNumber"]["value"] for cohort in population["cohorts"]] == [0, 20]
    assert all(cohort["plannedEnrollmentNumber"].get("unit") is None for cohort in population["cohorts"])
    assert not any(issue["code"].startswith("USDM_ENROLLMENT_SCOPE")
                   or issue["code"] == "USDM_COHORT_ENROLLMENT_INCOMPLETE"
                   for issue in report["mappingReport"]["issues"])


def test_partial_cohort_counts_are_not_completed_with_an_invented_zero(monkeypatch):
    graph = native_study_graph()
    graph["study"].current_metadata.study_population.number_of_expected_subjects = None
    graph["cohorts"][0].number_of_subjects = None
    report, *_ = run_mapping(monkeypatch, graph)
    cohorts = design(report)["population"]["cohorts"]
    assert cohorts[0]["plannedEnrollmentNumber"] is None
    assert cohorts[1]["plannedEnrollmentNumber"]["value"] == 20
    assert any(issue["code"] == "USDM_COHORT_ENROLLMENT_INCOMPLETE"
               for issue in report["mappingReport"]["issues"])


@pytest.mark.parametrize("field", ["study_epoch_uid", "study_element_uid", "study_branch_arm_uid"])
def test_design_cell_cannot_mint_a_reference_to_an_absent_selected_entity(monkeypatch, field):
    graph = native_study_graph()
    setattr(graph["cells"][0], field, "Absent_selection")
    report, *_ = run_mapping(monkeypatch, graph)
    assert design(report)["studyCells"] == []
    raw = next(row["record"] for row in report["nativeRecords"] if row["kind"] == "studyDesignCell")
    assert raw[field] == "Absent_selection"
    assert any(issue["code"] in {"USDM_CELL_BRANCH_UNRESOLVED", "USDM_CELL_SELECTION_UNRESOLVED"}
               for issue in report["mappingReport"]["issues"])


def test_soa_group_is_a_typed_activity_with_exact_children_and_source_metadata(monkeypatch):
    report, *_ = run_mapping(monkeypatch)
    activities = design(report)["activities"]
    soa = next(activity for activity in activities if activity["name"] == "Study assessments")
    group = next(activity for activity in activities if activity["name"] == "Assessments")
    assert soa["childIds"] == [group["id"]]
    source = next(row["record"] for row in report["nativeRecords"] if row["kind"] == "studySoAGroup")
    assert source["study_activity_group_uids"] == ["Group_1"]
    assert source["show_soa_group_in_protocol_flowchart"] is False


def test_instance_instruction_and_footnote_use_selected_versions_and_exact_targets(monkeypatch):
    report, _, _, calls = run_mapping(monkeypatch)
    version = report["document"]["study"]["versions"][0]
    mapped = design(report)
    concept = version["biomedicalConcepts"][0]
    assert concept["name"] == "Platelets at selected version"
    assert ("definition", "LibraryInstance_1", "1.0") in calls
    leaf = next(activity for activity in mapped["activities"] if activity["biomedicalConceptIds"])
    parent = next(activity for activity in mapped["activities"] if leaf["id"] in activity["childIds"])
    assert leaf["biomedicalConceptIds"] == [concept["id"]]
    assert leaf["bcSurrogateIds"] == []
    assert len(concept["properties"]) == 1
    assert "isRequired" not in concept["properties"][0]
    assert "isEnabled" not in concept["properties"][0]
    assert parent["notes"][0]["text"] == "Collect before dosing; preserve <b>fasting</b> text."
    assert leaf["notes"][0]["text"] == "Use <i>local</i> sample handling."
    assert version["notes"][0]["text"] == "Use <i>local</i> sample handling."
    second = next(encounter for encounter in mapped["encounters"] if encounter["label"] == "Visit 2")
    first = next(encounter for encounter in mapped["encounters"] if encounter["label"] == "Visit 1")
    assert second["notes"][0]["text"] == "Use <i>local</i> sample handling."
    assert first["notes"] == []


def test_timeline_has_entry_exit_and_uses_actual_non_global_anchor(monkeypatch):
    report, *_ = run_mapping(monkeypatch)
    timeline = design(report)["scheduleTimelines"][0]
    instances = timeline["instances"]
    assert timeline["entryId"] == instances[0]["id"]
    assert [entry["defaultConditionId"] for entry in instances[:-1]] == [entry["id"] for entry in instances[1:]]
    assert instances[-1]["timelineExitId"] == timeline["exits"][0]["id"]
    assert instances[-1]["defaultConditionId"] is None
    assert all(entry["timelineId"] is None for entry in instances)
    assert len({entry["name"] for entry in instances}) == 3
    assert timeline["timings"][2]["relativeFromScheduledInstanceId"] == instances[2]["id"]
    assert timeline["timings"][2]["relativeToScheduledInstanceId"] == instances[1]["id"]
    leaf = next(activity for activity in design(report)["activities"] if activity["biomedicalConceptIds"])
    assert instances[1]["activityIds"] == [leaf["id"]]


def test_zero_offset_relative_visit_is_not_a_new_fixed_anchor(monkeypatch):
    graph = native_study_graph()
    graph["visits"][2].value.time_value = 0
    report, *_ = run_mapping(monkeypatch, graph)
    timing = design(report)["scheduleTimelines"][0]["timings"][2]
    assert timing["type"]["code"] == "C201356"
    assert timing["relativeFromScheduledInstanceId"] != timing["relativeToScheduledInstanceId"]
    assert timing["windowLower"] == "P2D"


def test_metadata_lock_does_not_invent_document_approval_or_future_history(monkeypatch):
    report, *_ = run_mapping(monkeypatch)
    study = report["document"]["study"]
    document = study["documentedBy"][0]
    version = document["versions"][0]
    assert version["version"] == "1.1"
    assert "status" not in version
    assert "language" not in document
    assert "templateName" not in document
    assert study["versions"][0]["documentVersionIds"] == [version["id"]]
    retained_history = [row["record"]["metadata_version"] for row in report["nativeRecords"] if row["kind"] == "studySnapshotHistory"]
    assert retained_history == ["1.0", VERSION]
    assert any(activity["name"] == "Diagnosis" for activity in design(report)["activities"])
    assert len(design(report)["encounters"]) == 3


def test_wrong_library_version_and_source_study_fail_closed(monkeypatch):
    graph = native_study_graph()
    graph["definition"].version = "2.0"
    mapper, _ = mapping(graph, monkeypatch)
    with pytest.raises(USDMMappingAuthorityRequired, match="VERSION_MISMATCH"):
        mapper.map_with_report(graph["study"], VERSION)
    graph = native_study_graph()
    graph["cohorts"][0].study_uid = "Foreign"
    mapper, _ = mapping(graph, monkeypatch)
    with pytest.raises(USDMMappingAuthorityRequired, match="SOURCE_STUDY_MISMATCH"):
        mapper.map_with_report(graph["study"], VERSION)


def test_explicit_version_never_falls_back_to_reader_without_version_support(monkeypatch):
    graph = native_study_graph()
    mapper, _ = mapping(graph, monkeypatch)
    mapper._get_osb_study_standard_versions = lambda study_uid: []
    with pytest.raises(USDMMappingAuthorityRequired, match="SELECTED_VERSION_READER_REQUIRED"):
        mapper.map_with_report(graph["study"], VERSION)


@pytest.mark.parametrize("kind", ["selection", "history"])
def test_partial_native_pages_fail_and_leave_mapper_reusable(monkeypatch, kind):
    graph = native_study_graph()
    mapper, _ = mapping(graph, monkeypatch)
    attribute = "_get_osb_study_objectives" if kind == "selection" else "_get_snapshot_history"
    original = getattr(mapper, attribute)

    def partial(*args, **kwargs):
        assert kwargs["page_size"] == 0
        return {"items": [], "total": 1}

    setattr(mapper, attribute, partial)
    with pytest.raises(USDMMappingAuthorityRequired, match="SOURCE_COLLECTION_TRUNCATED"):
        mapper.map_with_report(graph["study"], VERSION)
    assert mapper._mapping_active is False
    assert mapper._call_cache == {}
    setattr(mapper, attribute, original)
    assert mapper.map_with_report(graph["study"], VERSION)["document"]["study"]["name"]


def test_mapping_is_deterministic_and_inline_ids_are_unique(monkeypatch):
    graph = native_study_graph()
    mapper, _ = mapping(graph, monkeypatch)
    first = mapper.map_with_report(graph["study"], VERSION)
    second = mapper.map_with_report(graph["study"], VERSION)
    assert first == second
    identifiers = [node["id"] for node in nodes(first["document"])]
    assert len(identifiers) == len(set(identifiers))


def test_pinned_model4_schema_accepts_only_explicit_test_author_resolution(monkeypatch):
    assert sha256(SCHEMA_PATH.read_bytes()).hexdigest() == SCHEMA_SHA256
    spec = json.loads(SCHEMA_PATH.read_text())
    validator = Draft202012Validator(
        {"$ref": "#/components/schemas/Study-Input", "components": spec["components"]},
        format_checker=FormatChecker(),
    )
    report, *_ = run_mapping(monkeypatch)
    draft = report["document"]["study"]
    assert not validator.is_valid(draft)
    assert report["mappingReport"]["state"] == "incomplete"
    # Independent test-only review supplies the explicitly missing source facts.
    # This is not a runtime default and does not claim that OSB already authored them.
    reviewed = deepcopy(draft)
    mapped = reviewed["versions"][0]["studyDesigns"][0]
    mapped["rationale"] = "Explicit synthetic author rationale"
    assert "isApproximate" not in draft["versions"][0]["studyDesigns"][0]["population"]["plannedAge"]
    # This is an explicit synthetic review decision, not a native OSB default.
    mapped["population"]["plannedAge"]["isApproximate"] = False
    code = lambda uid, text: {
        "id": uid, "instanceType": "Code", "code": uid, "decode": text,
        "codeSystem": "Synthetic test author vocabulary", "codeSystemVersion": "1.0",
    }
    for index, arm in enumerate(mapped["arms"]):
        arm["dataOriginType"] = code(f"Origin_{index}", "Explicitly reviewed origin")
        arm["dataOriginDescription"] = "Explicit test author origin description"
    protocol = reviewed["documentedBy"][0]
    protocol["language"] = code("Language_1", "Explicit author language")
    protocol["templateName"] = "Explicit author template"
    protocol["versions"][0]["status"] = code("DocumentStatus_1", "Explicit author status")
    for concept_index, concept in enumerate(reviewed["versions"][0]["biomedicalConcepts"]):
        concept["code"] = {
            "id": f"ConceptAlias_{concept_index}", "instanceType": "AliasCode",
            "standardCode": code(f"Concept_{concept_index}", "Explicit author concept"),
        }
        for property_index, prop in enumerate(concept["properties"]):
            prop.update(
                name="Explicit author acquisition property", datatype="float",
                isRequired=True, isEnabled=True,
                code={"id": f"PropertyAlias_{concept_index}_{property_index}", "instanceType": "AliasCode",
                      "standardCode": code(f"Property_{concept_index}_{property_index}", "Explicit author property")},
            )
    # Explicit synthetic review binds the formerly unplaced cell decision.
    # It is a separate authored copy, not an inference made by the OSB mapper.
    # This assertion checks schema shape only, not executable/CORE acceptance.
    cell_reviews = [
        issue["executionReview"] for issue in report["mappingReport"]["issues"]
        if issue["code"] == "USDM_CELL_TRANSITION_EXECUTION_REVIEW_REQUIRED"
    ]
    main = next(timeline for timeline in mapped["scheduleTimelines"] if timeline.get("mainTimeline") is True)
    reviewed_target = main["entryId"]
    reviewed_default = main["instances"][-1]["id"]
    for review in cell_reviews:
        pending = next(timeline for timeline in mapped["scheduleTimelines"]
                       if timeline["id"] == review["draftTargets"]["timelineId"])
        decision = next(instance for instance in pending["instances"]
                        if instance["id"] == review["draftTargets"]["scheduledDecisionInstanceId"])
        assignment = next(item for item in decision["conditionAssignments"]
                          if item["id"] == review["draftTargets"]["conditionAssignmentId"])
        assignment["conditionTargetId"] = reviewed_target
        decision["defaultConditionId"] = reviewed_default
        main["instances"].append(decision)
        main["entryId"] = decision["id"]
        mapped["scheduleTimelines"].remove(pending)
    # Compact native unit/null-flavor/source-code records do not carry a
    # dictionary version. Resolve those omissions only in this test's reviewed
    # copy; they remain explicit issues in the actual producer artifact.
    for issue in report["mappingReport"]["issues"]:
        if issue["code"] != "USDM_CODE_AUTHORITY_REQUIRED":
            continue
        path = issue["targetPath"].split("/")[2:]
        assert path[-1] == "codeSystemVersion"
        target = reviewed
        for segment in path[:-1]:
            target = target[int(segment)] if isinstance(target, list) else target[segment]
        target[path[-1]] = "Explicit test author source terminology version"
    errors = list(validator.iter_errors(reviewed))
    assert not errors, [(list(error.absolute_path), error.message) for error in errors]
    mapper, _ = mapping(native_study_graph(), monkeypatch)
    with pytest.raises(USDMMappingAuthorityRequired):
        mapper.map(native_study_graph()["study"], VERSION)
