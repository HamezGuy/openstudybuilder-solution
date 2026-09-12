"""Exact catalogue/date identity and real term-history selector regressions."""

from copy import deepcopy
from datetime import timedelta

import pytest

from clinical_mdr_api.domain_repositories.study_selections.study_epoch_repository import StudyEpochRepository
from clinical_mdr_api.domains.study_definition_aggregates.study_metadata import StudyStatus
from clinical_mdr_api.domains.study_selections.study_epoch import StudyEpochVO
from clinical_mdr_api.models.controlled_terminologies.ct_term import SimpleCTTermNameWithConflictFlag
from clinical_mdr_api.services.ddf.usdm_mapping_context import native_json
from clinical_mdr_api.services.studies.study_epoch import StudyEpochService
from clinical_mdr_api.services.studies.study_epoch_snapshot import resolve_epoch_term_history
from clinical_mdr_api.tests.fixtures.usdm_native_activity import NativeActivitySource
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource
from clinical_mdr_api.tests.fixtures.usdm_native_study import AS_OF, STUDY_UID, VERSION, native_study_graph


def fixture():
    source = NativeActivitySource()
    selection = deepcopy(next(item for item in native_study_graph()["standards"]
                              if item.ct_package.catalogue_name == "SDTM CT"))
    package = selection.ct_package
    package.uid = "opaque-selected-package"
    package.effective_date = AS_OF.date()
    package.extends_package = "opaque-published-package"
    stored = package.model_dump(mode="json")
    published = {**stored, "uid": "opaque-published-package", "extends_package": None}
    term = lambda: SimpleCTTermNameWithConflictFlag(
        term_uid="FLOAT", sponsor_preferred_name="UNSELECTED query fallback",
        date_conflict=True,
    )
    epoch = StudyEpochVO(
        uid="Epoch_1", study_uid=STUDY_UID,
        epoch=term(), subtype=term(), epoch_type=term(),
        start_rule=None, end_rule=None, description=None, order=1,
        status=StudyStatus.LOCKED, start_date=AS_OF,
        author_id="synthetic-author", author_username="Synthetic Author",
    )
    rows = [[stored, "SDTM CT", published, "SDTM CT", [stored, published]]]
    return source, selection, epoch, rows


def resolve_many(source, selection, epochs, rows, *, snapshot=None):
    calls = []
    def query(text, params):
        assert "CONTAINS_PACKAGE" in text and "EXTENDS_PACKAGE" in text
        assert "CONTAINS 'SDTM CT'" not in text and "LIMIT 1" not in text
        calls.append(params)
        return rows, []
    with source.isolated():
        resolve_epoch_term_history(
            epochs, STUDY_UID, VERSION, selections=[selection],
            snapshot=snapshot or source.snapshot(), query=query,
        )
    assert calls == [{"uid": "opaque-selected-package"}]
    return [epoch.terminology_source for epoch in epochs]


def resolve(source, selection, epoch, rows, *, snapshot=None):
    return resolve_many(source, selection, [epoch], rows, snapshot=snapshot)[0]


def test_sponsor_ancestry_and_opaque_uids_resolve_actual_dated_epoch_name():
    source, selection, epoch, rows = fixture()
    witness = resolve(source, selection, epoch, rows)
    assert epoch.epoch.sponsor_preferred_name == "float"
    assert epoch.epoch.date_conflict is False
    assert witness["package"]["selectedPackage"]["uid"] == "opaque-selected-package"
    assert witness["package"]["publishedPackage"]["uid"] == "opaque-published-package"
    assert witness["terms"]["epoch"]["name"]["version"] == "1.0"
    assert witness["cutoff"] == AS_OF.isoformat()
    assert not witness["issues"]


@pytest.mark.parametrize("status, actions", [
    (StudyStatus.DRAFT, ["edit", "delete", "lock", "reorder"]),
    (StudyStatus.LOCKED, []),
    (StudyStatus.RELEASED, []),
], ids=["DRAFT", "LOCKED", "RELEASED"])
def test_epoch_response_preserves_native_status_history_and_supported_actions(status, actions):
    source, selection, epoch, rows = fixture()
    epoch.status = status
    witness = deepcopy(resolve(source, selection, epoch, rows))
    response = StudyEpochService._transform_all_to_response_model(epoch, VERSION)
    assert epoch.status is status
    assert epoch.possible_actions == actions
    assert response.status == status.value
    assert response.possible_actions == actions
    assert response.study_version == VERSION
    assert response.epoch_name == "float"
    assert response.epoch_ctterm.date_conflict is False
    assert response.model_dump(mode="json")["terminology_source"] == witness
    assert epoch.terminology_source == witness


@pytest.mark.parametrize("change", ["missing-history", "ambiguous-history", "ambiguous-package", "wrong-catalogue"])
def test_unavailable_epoch_history_never_replays_query_fallback(change):
    source, selection, epoch, rows = fixture()
    history = source.library.roots["ctTermName", "FLOAT"].has_version
    if change == "missing-history":
        history.nodes, history.rows = [], []
    elif change == "ambiguous-history":
        history.rows[0][1].end_date = None
        history.rows[1][1].start_date = AS_OF - timedelta(hours=1)
    elif change == "ambiguous-package":
        rows.append(deepcopy(rows[0]))
    else:
        rows[0][3] = "Another Catalogue"
    witness = resolve(source, selection, epoch, rows)
    assert epoch.epoch.sponsor_preferred_name is None
    assert epoch.epoch.date_conflict is True
    assert witness["issues"]
    assert "UNSELECTED query fallback" not in str(witness)


def test_ambiguous_epoch_candidates_survive_response_and_mapper_without_selecting_a_name(monkeypatch):
    native, selection, epoch, rows = fixture()
    name_root = native.library.roots["ctTermName", "FLOAT"]
    attributes_root = native.library.roots["ctTermAttributes", "FLOAT"]
    # Actual ControlledTerminologyVersionRoot has an element ID, not a UID.
    del name_root.uid
    del attributes_root.uid
    history = name_root.has_version
    history.rows[0][1].end_date = None
    history.rows[1][1].start_date = AS_OF - timedelta(hours=1)
    snapshot = native.snapshot()
    with native.isolated():
        snapshot.term("FLOAT", cutoff=AS_OF)
    expected = [{**deepcopy(record), "termUid": "FLOAT"} for record in snapshot.records]
    witness = resolve(native, selection, epoch, rows, snapshot=snapshot)
    assert witness["records"] == expected
    unresolved = next(record for record in witness["records"] if record.get("state") == "unresolved")
    assert unresolved["kind"] == "ctTermName" and unresolved["uid"] == name_root.element_id
    assert unresolved["termUid"] == "FLOAT"
    assert unresolved["rootIdentity"] == name_root.element_id
    assert unresolved["asOf"] == AS_OF.isoformat()
    assert unresolved["studyUid"] == STUDY_UID and unresolved["studyValueVersion"] == VERSION
    assert unresolved["candidates"] == [{
        "valueIdentity": value.element_id,
        "properties": native_json(value.__properties__),
        "states": [{
            "version": state.version, "status": state.status,
            "startDate": state.start_date.isoformat(),
            "endDate": state.end_date.isoformat() if state.end_date else None,
            "authorId": state.author_id, "changeDescription": state.change_description,
        }],
    } for value, state in history.rows]
    assert witness["terms"]["epoch"]["name"] is None and witness["issues"]
    assert epoch.epoch.sponsor_preferred_name is None and epoch.epoch.date_conflict is True
    response = StudyEpochService._transform_all_to_response_model(epoch, VERSION)
    assert response.model_dump(mode="json")["terminology_source"] == witness
    source = NativeStudySource()
    source.graph["epochs"] = [response]
    monkeypatch.setattr("clinical_mdr_api.services.ddf.usdm_mapper.db.cypher_query", source.query)
    report = source.mapper().map_with_report(source.graph["study"], VERSION)
    retained = next(entry["record"] for entry in report["nativeRecords"] if entry["kind"] == "studyEpoch")
    assert retained["terminology_source"] == witness
    assert retained["epoch_name"] is None and retained["epoch_ctterm"]["date_conflict"] is True
    assert report["mappingReport"]["state"] == "incomplete"
    assert any(issue["code"] == "USDM_EPOCH_TERM_HISTORY_REQUIRED"
               for issue in report["mappingReport"]["issues"])
    mapped = report["document"]["study"]["versions"][0]["studyDesigns"][0]["epochs"][0]
    assert "name" not in mapped
    extension = next(entry for entry in mapped["extensionAttributes"] if entry["url"].endswith("/native/studyEpoch"))

    def extension_nodes(node):
        yield node
        for child in node.get("extensionAttributes", []):
            yield from extension_nodes(child)

    by_url = {node["url"]: node for node in extension_nodes(extension)}
    record_index = witness["records"].index(unresolved)
    for index, candidate in enumerate(unresolved["candidates"]):
        prefix = f'{extension["url"]}/terminology_source/records/{record_index}/candidates/{index}'
        assert by_url[prefix + "/valueIdentity"]["valueString"] == candidate["valueIdentity"]
        assert by_url[prefix + "/properties/name"]["valueString"] == candidate["properties"]["name"]
        for field in ("version", "status", "startDate", "authorId", "changeDescription"):
            assert by_url[prefix + "/states/0/" + field]["valueString"] == candidate["states"][0][field]


@pytest.mark.parametrize("reverse", [False, True])
def test_two_epochs_keep_only_their_own_history_including_shared_cached_terms(reverse):
    native, selection, epoch, rows = fixture()
    history = native.library.roots["ctTermName", "FLOAT"].has_version
    history.rows[0][1].end_date = None
    history.rows[1][1].start_date = AS_OF - timedelta(hours=1)
    epoch.subtype = SimpleCTTermNameWithConflictFlag(term_uid="RESULT", sponsor_preferred_name=None)
    epoch.epoch_type = SimpleCTTermNameWithConflictFlag(term_uid="TIME", sponsor_preferred_name=None)
    other = deepcopy(epoch)
    other.uid, other.order = "Epoch_2", 2
    other.epoch = SimpleCTTermNameWithConflictFlag(term_uid="CHOICE_A", sponsor_preferred_name=None)
    native.context("CHOICE_B", "NativeChoices", "B")
    snapshot = native.snapshot()
    with native.isolated():
        snapshot.term("CHOICE_B", cutoff=AS_OF - timedelta(days=1))
        snapshot.term("RESULT", cutoff=AS_OF)
        snapshot.term("FLOAT", cutoff=AS_OF)
    resolve_many(native, selection, [other, epoch] if reverse else [epoch, other], rows, snapshot=snapshot)
    for current, expected_uids in ((epoch, {"FLOAT", "RESULT", "TIME"}), (other, {"CHOICE_A", "RESULT", "TIME"})):
        witness = current.terminology_source
        assert witness["studyEpochUid"] == current.uid
        assert witness["nativeStudyAsOf"] == AS_OF.isoformat()
        assert {(record["kind"], record["termUid"]) for record in witness["records"]} == {
            (kind, uid) for uid in expected_uids for kind in ("ctTermName", "ctTermAttributes")
        }
        assert len(witness["records"]) == 6
        assert all(record["asOf"] == witness["cutoff"] == AS_OF.isoformat() for record in witness["records"])
        assert "CHOICE_B" not in str(witness)
        response = StudyEpochService._transform_all_to_response_model(current, VERSION)
        assert response.model_dump(mode="json")["terminology_source"] == witness
    assert epoch.epoch.sponsor_preferred_name is None and epoch.epoch.date_conflict is True
    assert epoch.terminology_source["issues"]
    assert other.epoch.sponsor_preferred_name == "A" and other.epoch.date_conflict is False
    assert other.terminology_source["issues"] == []
    assert "FLOAT" not in str(other.terminology_source)
    first_shared = next(record for record in epoch.terminology_source["records"]
                        if record["kind"] == "ctTermName" and record["uid"] == "RESULT")
    second_shared = next(record for record in other.terminology_source["records"]
                         if record["kind"] == "ctTermName" and record["uid"] == "RESULT")
    assert first_shared == second_shared
    first_shared["properties"]["name"] = "Consumer-local change"
    assert second_shared["properties"]["name"] == "result"


def test_same_day_name_change_after_native_snapshot_is_not_selected():
    source, selection, epoch, rows = fixture()
    history = source.library.roots["ctTermName", "FLOAT"].has_version
    history.rows[0][1].end_date = AS_OF + timedelta(hours=1)
    history.rows[1][1].start_date = AS_OF + timedelta(hours=1)
    resolve(source, selection, epoch, rows)
    assert epoch.epoch.sponsor_preferred_name == "float"
    assert epoch.epoch.queried_effective_date == AS_OF


def test_conflicting_selected_package_metadata_cannot_be_resolved_by_last_row():
    source, selection, epoch, _ = fixture()
    conflicting = deepcopy(selection)
    conflicting.ct_package.extends_package = "different-published-package"
    with source.isolated():
        resolve_epoch_term_history(
            [epoch], STUDY_UID, VERSION, selections=[selection, conflicting],
            snapshot=source.snapshot(),
            query=lambda *_args: pytest.fail("Conflicting selections cannot authorize a package query"),
        )
    assert epoch.epoch.sponsor_preferred_name is None
    assert "STUDY_EPOCH_CT_PACKAGE_SELECTION_CONFLICT" in epoch.terminology_source["issues"]


def test_selected_package_cannot_precede_its_published_ancestor():
    source, selection, epoch, rows = fixture()
    rows[0][2]["effective_date"] = (AS_OF + timedelta(days=1)).date().isoformat()
    witness = resolve(source, selection, epoch, rows)
    assert epoch.epoch.sponsor_preferred_name is None
    assert "STUDY_EPOCH_CT_PACKAGE_DATE_INVALID" in witness["issues"]


def test_historical_repository_query_uses_real_catalogue_and_no_latest_name_fallback():
    query, params = StudyEpochRepository.find_all_epochs_query(STUDY_UID, VERSION)
    assert "package.uid CONTAINS" not in query
    assert "LIMIT 1" not in query
    assert "ORDER BY dates_match" not in query
    assert "CTCatalogue {name:'SDTM CT'}" in query
    assert "['Final','Retired']" in query
    assert "native_study_as_of < package_date" in query
    assert params["study_status"] == ["LOCKED", "RELEASED"]
