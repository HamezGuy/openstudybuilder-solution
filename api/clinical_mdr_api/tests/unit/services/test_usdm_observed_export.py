"""One native version/read feeds unchanged definition and opt-in observations.

Native repositories and the deployment verifier are synthetic here. The actual
mapper, service, definition shaping and scope validation run unchanged.
"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json_hash_ref,
)
from clinical_mdr_api.services.ddf.usdm_service import USDMService
from clinical_mdr_api.services.integrations import native_package_state_v2 as state
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource
from clinical_mdr_api.tests.fixtures.usdm_native_study import STUDY_UID, VERSION
from clinical_mdr_api.tests.unit.services.test_authored_protocol_documents import (
    authored_source,
)


@pytest.fixture(name="source_service")
def fixture_source_service(monkeypatch):
    graph, content = authored_source()
    graph["header"].authored_documents = content.model_dump(mode="json")
    graph["header"].authored_documents_hash = content.content_hash()
    source = NativeStudySource(graph)
    service = object.__new__(USDMService)
    service._usdm_mapper = source.mapper()
    monkeypatch.setattr(state, "USDMService", lambda: service)
    with source.isolated():
        yield source, service


def test_opt_in_keeps_complete_definition_and_uses_one_source_read(source_service):
    source, _ = source_service
    root = {"nativeStudyId": STUDY_UID, "nativeVersion": VERSION}
    plain = state.read_pinned_canonical_definition(root)
    original_hash = canonical_json_hash_ref(
        plain, schema_version=state.DEFINITION_SCHEMA
    )
    source.calls.clear()
    checks, documents = [], []

    def observe(document):
        documents.append(deepcopy(document))
        return {"syntheticRuntimeEvidence": True, "correspondenceAuthority": False}

    runtime = SimpleNamespace(
        assert_current=lambda: checks.append(len(source.calls)), observe=observe
    )
    result = state.read_pinned_canonical_definition_with_observations(
        root, observation_runtime=runtime
    )
    assert checks == [0, 0]
    assert len([call for call in source.calls if call[0] == "study"]) == 1
    assert documents == [plain["document"]]
    assert result["canonicalDefinition"] == plain
    assert (
        canonical_json_hash_ref(
            result["canonicalDefinition"], schema_version=state.DEFINITION_SCHEMA
        )
        == original_hash
    )
    assert result["correspondenceObservations"]["correspondenceAuthority"] is False
    assert result["correspondenceObservations"]["registrations"]
    assert result["observationRuntime"]["syntheticRuntimeEvidence"] is True
    assert "observationRuntime" not in result["canonicalDefinition"]
    assert "correspondenceObservations" not in result["canonicalDefinition"]
    assert state.read_pinned_canonical_definition(root) == plain


def test_unbound_runtime_refuses_before_native_reads(source_service):
    source, service = source_service

    def unavailable():
        raise RuntimeError("Synthetic deployment binding revoked")

    runtime = SimpleNamespace(assert_current=unavailable)
    with pytest.raises(RuntimeError, match="binding revoked"):
        service.get_by_uid_with_report(STUDY_UID, VERSION, observation_runtime=runtime)
    assert source.calls == []


def test_runtime_failure_cannot_return_partial_observation_and_default_recovers(
    source_service,
):
    source, service = source_service
    plain = service.get_by_uid_with_report(STUDY_UID, VERSION)

    def refuse(_document):
        raise RuntimeError("Synthetic kernel failure")

    runtime = SimpleNamespace(assert_current=lambda: None, observe=refuse)
    with pytest.raises(RuntimeError, match="kernel failure"):
        state.read_pinned_canonical_definition_with_observations(
            {"nativeStudyId": STUDY_UID, "nativeVersion": VERSION},
            observation_runtime=runtime,
        )
    assert service.get_by_uid_with_report(STUDY_UID, VERSION) == plain
    assert source.calls


def test_observed_definition_requires_explicit_runtime_before_service_call(monkeypatch):
    def unexpected():
        pytest.fail("A missing runtime must not read native state")

    monkeypatch.setattr(state, "USDMService", unexpected)
    with pytest.raises(state.OsbCandidateSetError) as error:
        state.read_pinned_canonical_definition_with_observations(
            {"nativeStudyId": STUDY_UID, "nativeVersion": VERSION},
            observation_runtime=None,
        )
    assert error.value.code == "OSB_OBSERVATION_RUNTIME_REQUIRED"


def test_revoked_runtime_refuses_before_native_service_construction(monkeypatch):
    def unexpected():
        pytest.fail("Native service constructors must not run before authority checks")

    def revoked():
        raise RuntimeError("Synthetic deployment binding revoked")

    monkeypatch.setattr(state, "USDMService", unexpected)
    with pytest.raises(RuntimeError, match="binding revoked"):
        state.read_pinned_canonical_definition_with_observations(
            {"nativeStudyId": STUDY_UID, "nativeVersion": VERSION},
            observation_runtime=SimpleNamespace(assert_current=revoked),
        )


@pytest.mark.parametrize("field", ["studyUid", "studyValueVersion", "usdmVersion"])
@pytest.mark.parametrize("observed", [False, True])
def test_both_export_paths_require_exact_native_scope(
    source_service, monkeypatch, field, observed
):
    _, service = source_service
    exported = service.get_by_uid_with_report(STUDY_UID, VERSION)
    if field == "usdmVersion":
        exported["document"][field] = "incorrect"
    else:
        exported["mappingReport"][field] = "incorrect"
    exported.update(correspondenceObservations={}, observationRuntime={})
    monkeypatch.setattr(service, "get_by_uid_with_report", lambda *_a, **_k: exported)
    root = {"nativeStudyId": STUDY_UID, "nativeVersion": VERSION}
    with pytest.raises(state.OsbCandidateSetError) as error:
        if observed:
            state.read_pinned_canonical_definition_with_observations(
                root, observation_runtime=SimpleNamespace(assert_current=lambda: None)
            )
        else:
            state.read_pinned_canonical_definition(root)
    assert error.value.code == "OSB_CANONICAL_DEFINITION_SCOPE_MISMATCH"
