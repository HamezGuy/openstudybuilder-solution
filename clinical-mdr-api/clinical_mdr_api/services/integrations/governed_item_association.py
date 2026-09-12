"""Explicit native session review of exact cross-system association pins.

This does not verify the CSL plan's signature/custody or establish clinical
approval. CSL must independently establish those when consuming the result.
"""
import time
from datetime import datetime
from types import SimpleNamespace
from pydantic import ValidationError
from common.auth.user import auth
from clinical_mdr_api.domain_repositories.integrations.governed_item_association import GovernedItemAssociationRepository
from clinical_mdr_api.models.integrations.governed_item_association import GovernedItemAssociationReview, GovernedItemAssociationResponse, GovernedItemAssociationRecord
from clinical_mdr_api.models.integrations.proposal_review import ProposalReviewIntake
from clinical_mdr_api.services.integrations.proposal_review import ProposalReviewService, ProposalReviewPrincipal
from clinical_mdr_api.services.integrations.selected_activity_item_observation import SelectedActivityItemService, instant
from clinical_mdr_api.services.integrations.native_item_observation import NativeItemObservationError, NativeItemObservationService, _json
from clinical_mdr_api.services.integrations.canonical_json import canonical_hash
from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json, canonical_json_hash_ref


def require(value, code="OSB_ASSOCIATION_CUSTODY_INVALID", status=409):
    if not value:
        raise NativeItemObservationError(code, status)


def digest(value):
    return canonical_json_hash_ref(value, schema_version="OsbGovernedItemAssociationV1@1.0.0")["value"]


class GovernedItemAssociationService:
    def __init__(self, repository=None, auth_reader=auth, clock=time.time, monotonic=time.monotonic, *, review=False):
        self.repository = repository or GovernedItemAssociationRepository()
        self.auth_reader, self.clock, self.monotonic, self.review = auth_reader, clock, monotonic, review

    def request_budget(self):
        return NativeItemObservationService(auth_reader=self.auth_reader, clock=self.clock).request_budget()

    def observe(self, request, cancellation=None):
        require(isinstance(request, GovernedItemAssociationReview) == self.review, "OSB_ASSOCIATION_ACTION_INVALID", 422)
        budget, original = self.request_budget(), self.auth_reader()
        started, wall_started = self.monotonic(), self.clock()
        claims_pin = canonical_json(original.access_token_claims.model_dump(mode="json"))
        selector = request.selector
        role = "Study.Write" if self.review else "Study.Read"
        capability = "governed-item-association:review" if self.review else "governed-item-association:read"
        def authority():
            require(cancellation is None or not cancellation.is_set(), "OSB_ITEM_READ_CANCELLED", 408)
            require(self.auth_reader() is original and original.authentication_verified is True, "OSB_ASSOCIATION_AUTH_REQUIRED", 403)
            claims = original.access_token_claims
            require(canonical_json(claims.model_dump(mode="json")) == claims_pin, "OSB_ASSOCIATION_AUTH_CHANGED", 403)
            require(self.clock() >= wall_started and self.clock() < claims.exp
                    and self.monotonic() - started < budget, "OSB_ASSOCIATION_AUTH_EXPIRED", 403)
            caller = original.user
            require((caller.sub, caller.issuer, caller.tenant_id, caller.purpose, sorted(caller.roles),
                     sorted(caller.study_ids), sorted(caller.capabilities)) ==
                    (claims.sub, claims.iss, claims.tenant_id, claims.purpose, sorted(claims.roles or []),
                     sorted(claims.study_ids), sorted(claims.capabilities)), "OSB_ASSOCIATION_AUTH_CHANGED", 403)
            require(role in caller.roles and "Study.Read" in caller.roles and capability in caller.capabilities,
                    "OSB_ASSOCIATION_SCOPE_DENIED", 403)
            require(caller.purpose in {"interactive-domain-access", "workflow-orchestration"}, "OSB_ASSOCIATION_SCOPE_DENIED", 403)
            if self.review:
                require(bool(claims.oid) and caller.sub == claims.oid and caller.sub == claims.sub,
                        "OSB_ASSOCIATION_HUMAN_REQUIRED", 403)
                require(request.signatureId == (claims.sid or claims.jti or claims.uti)
                        and "study:write" in caller.capabilities, "OSB_ASSOCIATION_SESSION_REQUIRED", 403)
            return original
        def remaining():
            authority()
            return min(5, budget - (self.monotonic() - started), original.access_token_claims.exp - self.clock())
        authority()
        selected_service = SelectedActivityItemService(self.repository, authority, self.clock, self.monotonic,
            allowed_purposes=frozenset({"interactive-domain-access", "workflow-orchestration"}))
        p = {**selector.selected.model_dump(), "tenantId": original.user.tenant_id,
             "proposalHash": selector.proposalHash, "proposalObjectId": selector.proposalObjectId,
             "reviewDecisionId": selector.reviewDecisionId, "reviewDecisionHash": selector.reviewDecisionHash}
        p["targetKey"] = canonical_hash({"nativeStudyId": selector.selected.nativeStudyId,
            "cslTenantId": selector.csl.nativeTenantId, "cslStudyId": selector.csl.nativeStudyId,
            "canonicalRevisionId": selector.csl.canonicalRevisionId})

        def source():
            selected = selected_service.observe(selector.selected, cancellation)
            require(selected["selectionHash"]["value"] == selector.selectedPathHash
                    and selected["libraryObservation"]["itemHash"]["value"] == selector.libraryItemHash
                    and selected["libraryObservation"]["operationHash"]["value"] == selector.operationHash,
                    "OSB_ASSOCIATION_NATIVE_PINS_CHANGED")
            rows = self.repository.review_custody(p, remaining())
            authority()
            require(len(rows) == 1, "OSB_ASSOCIATION_CURRENT_REVIEW_AMBIGUOUS")
            row = rows[0]
            proposal, item, context = [_json(value) for value in row[:3]]
            claims = original.access_token_claims
            principal = ProposalReviewPrincipal(actor_id=original.user.sub, human_user_id=claims.oid or "",
                token_id=claims.sid or claims.jti or claims.uti or "", tenant_id=original.user.tenant_id,
                scoped_study_ids=frozenset(original.user.study_ids), organization_ids=frozenset(),
                roles=frozenset(original.user.roles), authentication_verified=True, purpose=original.user.purpose,
                capabilities=frozenset(original.user.capabilities), enforce_delegated_scope=True)
            try:
                principal.assert_proposal_access(proposal.get("tenantId", ""), proposal.get("studyId", ""), role)
                if self.review:
                    principal.assert_can_sign(request.signatureId)
                ProposalReviewIntake(proposal=proposal, worker_id="retained-custody-validation")
                require(canonical_hash(context) == row[12] == selector.selected.mappingContextHash
                        and proposal.get("osbMappingContextHash") == row[12] and proposal.get("authorityMode") == "enforced")
                objects = ProposalReviewService(SimpleNamespace(get_context=lambda key: context if key == row[12] else None))._validate_proposal(
                    proposal, context.get("osbOpenApiHash"))
            except (ValueError, TypeError, KeyError, ValidationError) as error:
                raise NativeItemObservationError("OSB_ASSOCIATION_PROPOSAL_INVALID") from error
            require(sum(value == item for value in objects) == 1 and item.get("proposalObjectId") == selector.proposalObjectId)
            decision = dict(zip(["decision_id", "action", "candidate_key", "note", "signature_id", "signature_verified",
                                 "decision_content_hash", "actor_id", "decided_at"], row[3:12]))
            require(decision["decision_id"] == selector.reviewDecisionId and decision["action"] == "selected_candidate"
                    and decision["candidate_key"] == selector.candidateKey and decision["signature_verified"] is True
                    and isinstance(decision["actor_id"], str) and bool(decision["actor_id"])
                    and isinstance(decision["signature_id"], str) and bool(decision["signature_id"]))
            require(canonical_hash({"proposalHash": selector.proposalHash, "proposalObjectId": selector.proposalObjectId,
                    **{k: v for k, v in decision.items() if k != "decision_content_hash"}})
                    == decision["decision_content_hash"] == selector.reviewDecisionHash)
            offered = [value for value in item["mapping"]["candidates"] if value["candidateKey"] == selector.candidateKey]
            require(len(offered) == 1 and all(offered[0].get(key) == value for key, value in {
                "uid": selector.selected.itemUid, "version": selector.selected.itemVersion,
                "resourceFamily": "odm_items", "resourceType": "OdmItem", "contextHash": row[12]}.items()))
            refs = [value for value in proposal["sourceFactRefs"] if value["factId"] == selector.selected.factId]
            require(item["mapping"]["factIds"] == [selector.selected.factId]
                    and item["targetKey"] == selector.selected.targetKey and len(refs) == 1
                    and refs[0]["revision"] == selector.selected.revision)
            authority()
            return {"proposalJson": row[0], "objectJson": row[1], "contextJson": row[2], "decision": decision}, selected

        def associations():
            rows = self.repository.associations(p, remaining())
            require(len(rows) <= 1, "OSB_ASSOCIATION_AMBIGUOUS")
            require(all(len(row) == 4 and row[3] is True for row in rows), "OSB_ASSOCIATION_ATTACHMENT_MISMATCH")
            return [row[:3] for row in rows]
        def validate_record(record):
            try:
                GovernedItemAssociationRecord.model_validate(record)
                review = record["review"]
                reviewed_at = datetime.fromisoformat(review["reviewedAt"]).timestamp()
                source_at = datetime.fromisoformat(record["sourceObservedAt"]).timestamp()
                require(instant(reviewed_at) == review["reviewedAt"] and instant(source_at) == record["sourceObservedAt"]
                    and review["credentialIssuedAt"] <= source_at <= reviewed_at < review["credentialExpiresAt"]
                    and reviewed_at <= self.clock(), "OSB_ASSOCIATION_REVIEW_TIME_INVALID")
                require(review["subject"] == review["humanSubject"] and review["tenantId"] == p["tenantId"]
                    and {"Study.Read", "Study.Write", "Library.Read"} <= set(review["roles"])
                    and {"candidate:read", "study:read", "study:write", "governed-item-association:review"} <= set(review["capabilities"])
                    and {selector.selected.platformStudyId, selector.selected.nativeStudyId,
                         _json(before["proposalJson"])["studyId"]} <= set(review["studyIds"]), "OSB_ASSOCIATION_REVIEW_AUTH_INVALID")
            except (ValueError, TypeError, KeyError, ValidationError) as error:
                raise NativeItemObservationError("OSB_ASSOCIATION_REVIEW_RECORD_INVALID") from error
        before, selected = source()
        existing = associations()
        require(len(existing) <= 1, "OSB_ASSOCIATION_AMBIGUOUS")
        if existing:
            require(existing[0][0] == request.associationId, "OSB_ASSOCIATION_CONFLICT")
            record = _json(existing[0][2])
            association_hash = digest(record)
            require(association_hash == existing[0][1] and record.get("selector") == selector.model_dump()
                    and record.get("associationId") == request.associationId and record.get("nativeTenantId") == p["tenantId"])
            validate_record(record)
            if self.review:
                require(record["review"]["issuer"] == original.access_token_claims.iss
                        and record["review"]["subject"] == original.user.sub
                        and record["review"]["sessionHash"] == canonical_hash({"session": request.signatureId}),
                        "OSB_ASSOCIATION_REPLAY_REVIEWER_MISMATCH")
            else:
                require(association_hash == request.associationHash, "OSB_ASSOCIATION_HASH_MISMATCH")
        else:
            require(self.review, "OSB_ASSOCIATION_UNAVAILABLE")
            claims = original.access_token_claims
            record = {"contractVersion": "OsbGovernedItemAssociationV1@1.0.0", "associationId": request.associationId,
                "nativeTenantId": p["tenantId"], "selector": selector.model_dump(),
                "review": {"assurance": "native-session-review", "action": "governed-item-association:review",
                    "issuer": claims.iss, "subject": claims.sub, "humanSubject": claims.oid, "tenantId": claims.tenant_id,
                    "roles": sorted(claims.roles or []), "studyIds": sorted(claims.study_ids),
                    "capabilities": sorted(claims.capabilities), "purpose": claims.purpose,
                    "sessionHash": canonical_hash({"session": request.signatureId}),
                    "credentialIssuedAt": claims.iat, "credentialExpiresAt": claims.exp,
                    "reviewedAt": instant(self.clock()), "displayedStatement": request.displayedStatement},
                "sourceObservedAt": selected["observedAt"], "cslPlanCustodyVerified": False,
                "clinicalApprovalVerified": False, "detachedSignatureVerified": False}
            association_hash = digest(record)
            validate_record(record)
            payload = canonical_json(record)
            # The retained reader projects at most 16384 characters. Bound
            # UTF-8 bytes before append so every new immutable row is readable.
            require(len(payload.encode()) <= 16384, "OSB_ASSOCIATION_RECORD_LIMIT")
            inserted = self.repository.append_association({**p, **before, "associationId": request.associationId,
                "associationHash": association_hash, "payloadJson": payload,
                "credentialExpiryMillis": int(min(claims.exp, wall_started + budget) * 1000)}, remaining())
            require(inserted == [[request.associationId, association_hash, payload]], "OSB_ASSOCIATION_APPEND_CONFLICT")
        # Re-read original source/authority after persistence or retained replay;
        # errors do not erase the immutable record or imply semantic approval.
        after, final_selected = source()
        require(before == after, "OSB_ASSOCIATION_SOURCE_CHANGED")
        final = associations()
        require(final == [[request.associationId, association_hash, canonical_json(record)]], "OSB_ASSOCIATION_CURRENT_CHANGED")
        require(self.repository.scope(p, remaining()) == [[selector.selected.bindingId,
                selector.selected.nativeStudyId, selector.selected.nativeStudyVersion]], "OSB_ASSOCIATION_SCOPE_WITHDRAWN", 403)
        authority()
        result = {"contractVersion": "OsbGovernedItemAssociationObservationV1@1.0.0",
            "associationId": request.associationId, "associationHash": association_hash, "nativeTenantId": p["tenantId"],
            "selector": selector.model_dump(), "assurance": "native-session-review",
            "associationReviewAction": "governed-item-association:review", "currentOriginalDecisionVerified": True,
            "selectedActivityReachabilityVerified": True, "cslPlanCustodyVerified": False,
            "clinicalApprovalVerified": False, "detachedSignatureVerified": False,
            "observedAt": final_selected["observedAt"], "authorityCheckedAt": instant(self.clock()),
            "expiresAt": instant(min(wall_started + budget, original.access_token_claims.exp))}
        result = GovernedItemAssociationResponse.model_validate(result).model_dump()
        authority()
        require(self.clock()*1000 < int(min(wall_started + budget, original.access_token_claims.exp)*1000),
                "OSB_ASSOCIATION_AUTH_EXPIRED", 403)
        return result
