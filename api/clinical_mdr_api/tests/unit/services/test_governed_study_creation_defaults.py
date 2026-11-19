from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from clinical_mdr_api.domain_repositories.study_definitions.study_definition_repository import (
    StudyDefinitionRepository,
)
from clinical_mdr_api.models.study_selections.study import StudyCreateInput
from clinical_mdr_api.routers.integrations import native_identity as route
from clinical_mdr_api.services.integrations import candidate_set
from clinical_mdr_api.services.integrations import native_identity
from clinical_mdr_api.services.studies import study
from common.exceptions import BusinessLogicException


def test_numeric_candidate_wraps_within_native_four_digit_limit(monkeypatch):
    # Published sponsor seed for this identifier is 1252.
    occupied = [[str(number)] for number in range(1252, 10000)]
    monkeypatch.setattr(native_identity.db, "cypher_query", lambda query: (occupied, []))
    assert StudyDefinitionRepository.available_platform_study_number("349be3f2-9c96-440d-9a05-8fcf507007c8") == "1000"
    occupied.extend([str(number)] for number in range(1000, 1252))
    with pytest.raises(BusinessLogicException, match="No four-digit study number"):
        StudyDefinitionRepository.available_platform_study_number("349be3f2-9c96-440d-9a05-8fcf507007c8")


@pytest.mark.parametrize(
    ("supplied", "platform_id", "expected", "locks", "allocates"),
    [
        (None, "platform-id", "1252", 1, 1),
        ("0042", "platform-id", "0042", 1, 0),
        ("0042", None, "0042", 1, 0),
        (None, None, None, 0, 0),
    ],
)
def test_native_create_preserves_explicit_numbers_and_optional_native_drafts(monkeypatch, supplied, platform_id, expected, locks, allocates):
    service = study.StudyService.__new__(study.StudyService)
    service._repos = MagicMock()
    repository = service._repos.study_definition_repository
    repository.available_platform_study_number.return_value = "1252"
    metadata = MagicMock(side_effect=LookupError("metadata inspected"))
    monkeypatch.setattr(study.StudyIdentificationMetadataVO, "from_input_values", metadata)
    request = StudyCreateInput(project_number="P1", study_number=supplied)
    with pytest.raises(LookupError, match="metadata inspected"):
        study.StudyService.create.__wrapped__(service, request, platform_study_id=platform_id)
    assert metadata.call_args.kwargs["study_number"] == expected
    assert repository.lock_study_numbers.call_count == locks
    assert repository.available_platform_study_number.call_count == allocates
    if allocates:
        assert [call[0] for call in repository.mock_calls[:2]] == ["lock_study_numbers", "available_platform_study_number"]


def test_governed_ct_default_keeps_missing_catalogue_visible(monkeypatch):
    repo = MagicMock()
    repo.latest_standard_uid.side_effect = ["base-ddf", None, "base-cdash"]
    monkeypatch.setattr(native_identity, "CTPackageRepository", lambda: repo)
    monkeypatch.setattr(native_identity, "StudyStandardVersionService", MagicMock())
    select = MagicMock()
    select.return_value.uid = "version"
    monkeypatch.setattr(native_identity, "call_in_ambient_transaction", select)
    transaction = native_identity.Neo4jOsbNativeIdentityTransactionV1("tenant", "platform")
    references = transaction._select_required_standards("Study_fixture")
    assert [call.args for call in repo.latest_standard_uid.call_args_list] == [
        (catalogue, native_identity.settings.cdisc_library_name) for catalogue in native_identity.PLATFORM_REQUIRED_CT_CATALOGUES
    ]
    assert references == [
        "osb-standard-version:version:base-ddf",
        "osb-standard-version-absent:SDTM CT",
        "osb-standard-version:version:base-cdash",
    ]


def test_scoped_intent_recovery_verifies_the_original_signed_receipt(monkeypatch):
    principal = SimpleNamespace(
        sub="service:worker",
        human_subject=None,
        actor_chain=(),
        roles={"service", "Study.Read"},
        purpose="workflow-orchestration",
        capabilities={"native-identity:bind"},
        tenant_id="tenant",
        study_ids={"study"},
    )
    monkeypatch.setattr(route, "user", lambda: principal)
    monkeypatch.setattr(route.settings, "governed_study_creation_enabled", True)
    publications = [["accuratrials-osb", "study-draft-root", "created", "digest", "receipt", principal.sub]]
    read_publications = MagicMock(return_value=(publications, None))
    monkeypatch.setattr(candidate_set.db, "cypher_query", read_publications)
    receipt = {"intentId": "intent", "effectType": "created", "createIntentHash": {"value": "digest"}}
    envelope = {"signature": "original"}
    proof = MagicMock(
        return_value={
            "receipt": receipt,
            "signedReceiptEnvelope": envelope,
            "targetState": {"domainBindingId": "binding", "nativeIdentity": "Study_1", "nativeVersion": "v1"},
        }
    )
    monkeypatch.setattr(candidate_set, "read_native_binding_proof", proof)
    verify = MagicMock()
    monkeypatch.setattr(route, "_verify_candidate_request_signature", verify)
    request = SimpleNamespace(headers={"x-platform-study-id": "study"})
    data = route.get_created_root_receipt("intent", request)["data"]
    assert data == {"receipt": receipt, "signedReceiptEnvelope": envelope, "currentBinding": {"domainBindingId": "binding", "status": "active", "nativeIdentity": "Study_1", "nativeVersion": "v1"}}
    assert read_publications.call_args.args[1] == {"tenant": "tenant", "study": "study", "intent": "intent"}
    proof.assert_called_once_with(tenant_id="tenant", platform_study_id="study", receipt_id="receipt")
    verify.assert_called_once_with(receipt, envelope)
    verify.reset_mock()

    def denied(status, code=None):
        with pytest.raises(HTTPException) as fault:
            route.get_created_root_receipt("intent", request)
        assert fault.value.status_code == status
        if code:
            assert fault.value.detail["code"] == code
        verify.assert_not_called()

    principal.study_ids.clear()
    denied(403)
    principal.study_ids.add("study")
    principal.roles.remove("service")
    denied(403)
    principal.roles.add("service")
    principal.sub = "service:another"
    denied(403, "OSB_IDENTITY_INTENT_ACTOR_DENIED")
    principal.sub = "service:worker"
    receipt["createIntentHash"]["value"] = "different"
    denied(409, "OSB_IDENTITY_INTENT_RECEIPT_INVALID")
    publications.clear()
    denied(404, "OSB_IDENTITY_INTENT_RECEIPT_NOT_FOUND")
