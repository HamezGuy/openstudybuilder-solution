"""Actual native review/association services with explicitly authored repositories."""
import copy
import time
from types import SimpleNamespace
from uuid import uuid4
import pytest
from authlib.jose import JWTClaims
from common.auth.models import Auth, AccessTokenClaims
from clinical_mdr_api.tests.unit.services.test_native_item_observation import fixture, TENANT, STUDY
from clinical_mdr_api.tests.unit.services.test_selected_activity_item_observation import SelectedMemoryRepository, selection_request
from clinical_mdr_api.tests.fixtures.osb_proposal_v21 import build_context, build_proposal, OPENAPI_HASH, FACT_HASH
from clinical_mdr_api.tests.unit.services.test_proposal_review import _set_authority_mode
from clinical_mdr_api.services.integrations.proposal_review import ProposalReviewService, ProposalReviewPrincipal, _context_candidate
from clinical_mdr_api.services.integrations.canonical_json import canonical_hash
from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json
from clinical_mdr_api.models.integrations.proposal_review import ProposalObjectDecisionInput, ProposalReviewIntake
from clinical_mdr_api.models.integrations.governed_item_association import GovernedItemAssociationReview, GovernedItemAssociationRead
from clinical_mdr_api.services.integrations.governed_item_association import GovernedItemAssociationService
from clinical_mdr_api.services.integrations.selected_activity_item_observation import SelectedActivityItemService
from clinical_mdr_api.services.integrations.native_item_observation import NativeItemObservationError


def association_fixture():
    def enrich(base):
        context = build_context()
        item = copy.deepcopy(context["candidates"]["activities"][0])
        item.update({"resource_family": "odm_items", "resource_type": "OdmItem", "uid": "OdmItem_1"})
        context["candidates"]["odm_items"] = [item]
        return {**base, **context}
    f = fixture(context_enricher=enrich, openapi_hash=OPENAPI_HASH)
    f.request = selection_request(f.request)
    f.now[0] = time.time()
    f.auth = Auth(JWTClaims({}, {}), AccessTokenClaims(iss="https://authored.invalid", sub="authored-human", oid="authored-human",
        aud=["osb"], exp=int(f.now[0]+300), iat=int(f.now[0]-1), sid="session-1", tenantId=TENANT,
        studyIds=[STUDY, f.request.nativeStudyId], roles={"Study.Read", "Study.Write", "Library.Read"},
        purpose="interactive-domain-access", capabilities=["candidate:read", "study:read", "study:write",
            "governed-item-association:review", "governed-item-association:read"]), authentication_verified=True)
    context = f.blobs[4]
    proposal = build_proposal(f.request.mappingContextHash)
    proposal.update({"tenantId": TENANT, "studyId": STUDY})
    item = proposal["sections"]["odm"][0]
    item["targetKey"] = "primary"
    item["conceptId"] = canonical_hash({"factId": "fact-1", "revision": 1, "factContentHash": FACT_HASH, "targetKey": "primary"})
    candidate = _context_candidate(context["candidates"]["odm_items"][0], f.request.mappingContextHash)
    item["mapping"].update({"candidates": [candidate], "disposition": "review", "matchMethod": "exact_text", "mappingConfidence": 0.8})
    proposal["reconciliation"].update({"review": 2, "unresolved": 0})
    proposal = _set_authority_mode(proposal, "enforced")
    item = proposal["sections"]["odm"][0]
    ProposalReviewIntake(proposal=proposal, worker_id="fixture")
    objects = ProposalReviewService(SimpleNamespace(get_context=lambda _: context))._validate_proposal(proposal, OPENAPI_HASH)
    f.proposal, f.item, f.candidate, f.objects = proposal, item, candidate, objects
    # The actual existing decide() generates its own session review record.
    captured = []
    legacy = SimpleNamespace(get_proposal=lambda _: {"proposal": proposal, "objects": objects},
                             append_decision=lambda ph, oid, decision: captured.append(decision))
    review_service = ProposalReviewService(legacy)
    review_service.get_status = lambda _: None  # Result listing is not an authority substitute.
    f.principal = ProposalReviewPrincipal(actor_id="authored-human", human_user_id="authored-human", token_id="session-1",
        tenant_id=TENANT, scoped_study_ids=frozenset({STUDY, f.request.nativeStudyId}), organization_ids=frozenset(),
        roles=frozenset({"Study.Read", "Study.Write"}), authentication_verified=True)
    review_service.decide(proposal["proposalHash"], item["proposalObjectId"],
        ProposalObjectDecisionInput(action="selected_candidate", candidate_key=candidate["candidateKey"], signature_id="session-1"), f.principal)
    f.original_decision = captured[0]
    f.repository = AssociationMemoryRepository(f)
    selected = SelectedActivityItemService(f.repository, lambda:f.auth, lambda:f.now[0], lambda:f.now[0],
        allowed_purposes=frozenset({"interactive-domain-access", "workflow-orchestration"})).observe(f.request)
    f.review = GovernedItemAssociationReview.model_validate({"contractVersion":"OsbGovernedItemAssociationReviewV1@1.0.0",
        "associationId":str(uuid4()), "selector":{"contractVersion":"OsbGovernedItemAssociationSelectorV1@1.0.0",
        "proposalHash":proposal["proposalHash"], "proposalObjectId":item["proposalObjectId"],
        "reviewDecisionId":f.original_decision["decision_id"], "reviewDecisionHash":f.original_decision["decision_content_hash"],
        "candidateKey":candidate["candidateKey"], "selected":f.request.model_dump(),
        "selectedPathHash":selected["selectionHash"]["value"], "libraryItemHash":selected["libraryObservation"]["itemHash"]["value"],
        "operationHash":selected["libraryObservation"]["operationHash"]["value"],
        "csl":{"nativeTenantId":str(uuid4()), "nativeStudyId":str(uuid4()), "canonicalRevisionId":str(uuid4()),
               "canonicalRevisionHash":"sha256:"+"1"*64, "planHash":"sha256:"+"2"*64, "reviewCommitmentHash":"sha256:"+"3"*64}},
        "signatureId":"session-1", "displayedStatement":"I reviewed this exact proposal decision, CSL plan and native selected Item association."})
    f.service = GovernedItemAssociationService(f.repository, lambda:f.auth, lambda:f.now[0], lambda:f.now[0], review=True)
    return f


class AssociationMemoryRepository(SelectedMemoryRepository):
    def __init__(self, f):
        super().__init__(f, f.request)
        self.f, self.saved, self.writes, self.review_count = f, [], 0, 1
    def review_custody(self, p, timeout):
        f, d = self.f, self.f.original_decision
        row = [canonical_json(f.proposal), canonical_json(f.item), canonical_json(f.blobs[4]),
            *[d.get(k) for k in ["decision_id", "action", "candidate_key", "note", "signature_id", "signature_verified",
                               "decision_content_hash", "actor_id", "decided_at"]], f.request.mappingContextHash]
        return self.read("review_custody", [row]*self.review_count, timeout)
    def associations(self, p, timeout): return self.read("associations", [row+[True] for row in self.saved], timeout)
    def append_association(self, p, timeout):
        self.writes += 1
        self.saved = [[p["associationId"], p["associationHash"], p["payloadJson"]]]
        return self.read("append_association", self.saved, timeout)


def current_request(f, result):
    return GovernedItemAssociationRead.model_validate({"contractVersion":"OsbGovernedItemAssociationReadV1@1.0.0",
        "associationId":result["associationId"], "associationHash":result["associationHash"], "selector":f.review.selector.model_dump()})


def test_new_human_association_preserves_original_review_and_limited_assurance():
    f = association_fixture()
    before = copy.deepcopy(f.original_decision)
    result = f.service.observe(f.review)
    assert f.original_decision == before and f.repository.writes == 1
    assert result["assurance"] == "native-session-review"
    assert not result["cslPlanCustodyVerified"] and not result["clinicalApprovalVerified"] and not result["detachedSignatureVerified"]
    assert "session-1" not in canonical_json(result) and "Authored" not in canonical_json(result)
    assert f.service.observe(f.review)["associationHash"] == result["associationHash"]
    assert f.repository.writes == 1
    f.service.review = False
    assert f.service.observe(current_request(f,result))["associationHash"] == result["associationHash"]


@pytest.mark.parametrize("change", ["human", "session", "write", "capability", "purpose", "native-study", "proposal-study", "unverified", "expiry"])
def test_original_auth_context_required_before_association_write(change):
    f = association_fixture()
    if change == "human": f.auth.access_token_claims.oid = "machine"
    if change == "session": f.review = f.review.model_copy(update={"signatureId":"other"})
    if change == "write": f.auth.user.roles.remove("Study.Write")
    if change == "capability": f.auth.user.capabilities.remove("governed-item-association:review")
    if change == "purpose": f.auth.user.purpose = "different"
    if change == "native-study": f.auth.user.study_ids.remove(f.request.nativeStudyId)
    if change == "proposal-study": f.auth.user.study_ids.remove(STUDY)
    if change == "unverified": f.auth.authentication_verified = False
    if change == "expiry": f.now[0] += 301
    with pytest.raises(NativeItemObservationError): f.service.observe(f.review)
    assert f.repository.writes == 0


@pytest.mark.parametrize("change", ["decision", "duplicate-head", "proposal", "candidate", "selection", "item", "operation", "csl-plan", "duplicate-association"])
def test_current_read_denies_changed_source_or_borrowed_association(change):
    f = association_fixture()
    result = f.service.observe(f.review)
    request = current_request(f, result)
    f.service.review = False
    if change == "decision": f.original_decision["decision_id"] = str(uuid4())
    if change == "duplicate-head": f.repository.review_count = 2
    if change == "proposal": f.proposal["authorityMode"] = "shadow"
    if change == "candidate": f.item["mapping"]["candidates"][0]["uid"] = "borrowed"
    if change == "selection": f.repository.path["activityItem"]["textValue"] = "changed"
    if change == "item": f.fields["datatype"] = "string"
    if change == "operation": request = request.model_copy(update={"selector":request.selector.model_copy(update={"operationHash":"sha256:"+"f"*64})})
    if change == "csl-plan": request = request.model_copy(update={"selector":request.selector.model_copy(update={"csl":request.selector.csl.model_copy(update={"planHash":"sha256:"+"f"*64})})})
    if change == "duplicate-association": f.repository.saved *= 2
    with pytest.raises(NativeItemObservationError): f.service.observe(request)
