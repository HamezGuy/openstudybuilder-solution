"""Compose conserved library proof and exact native selected-activity reachability."""
import time
from datetime import UTC, datetime

from pydantic import ValidationError
from clinical_mdr_api.domain_repositories.integrations.selected_activity_item_observation import SelectedActivityItemRepository
from clinical_mdr_api.models.integrations.native_item_observation import NativeItemObservationRequest
from clinical_mdr_api.models.integrations.selected_activity_item_observation import SelectedActivityItemRequest, SelectedActivityItemPath, SelectedActivityItemResponse
from clinical_mdr_api.services.integrations.native_item_observation import NativeItemObservationService, NativeItemObservationError
from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json, canonical_json_hash_ref
from common.auth.user import auth


def require(value, code="OSB_SELECTED_ITEM_UNAVAILABLE"):
    if not value:
        raise NativeItemObservationError(code)


def instant(value):
    return datetime.fromtimestamp(value, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SelectedActivityItemService:
    def __init__(self, repository=None, auth_reader=auth, clock=time.time, monotonic=time.monotonic,
                 allowed_purposes=frozenset({"workflow-orchestration"})):
        self.repository = repository or SelectedActivityItemRepository()
        self.auth_reader, self.clock, self.monotonic = auth_reader, clock, monotonic
        self.allowed_purposes = frozenset(allowed_purposes)

    def request_budget(self):
        return NativeItemObservationService(auth_reader=self.auth_reader, clock=self.clock,
                                             monotonic=self.monotonic).request_budget()

    def observe(self, request: SelectedActivityItemRequest, cancellation=None):
        original = self.auth_reader()
        budget = self.request_budget()
        started, wall_started = self.monotonic(), self.clock()
        original_claims = canonical_json(original.access_token_claims.model_dump(mode="json"))
        def caller_pin():
            caller = original.user
            return (caller.sub, caller.issuer, caller.tenant_id, caller.purpose,
                    tuple(sorted(caller.study_ids)), tuple(sorted(caller.capabilities)), tuple(sorted(caller.roles)))
        original_caller = caller_pin()
        def checkpoint():
            require(cancellation is None or not cancellation.is_set(), "OSB_ITEM_READ_CANCELLED")
            current = self.auth_reader()
            require(current is original and current.authentication_verified is True,
                    "OSB_ITEM_AUTH_REQUIRED")
            require(canonical_json(current.access_token_claims.model_dump(mode="json")) == original_claims,
                    "OSB_ITEM_SCOPE_DENIED")
            require(caller_pin() == original_caller, "OSB_ITEM_SCOPE_DENIED")
            remaining = min(budget - (self.monotonic() - started), original.access_token_claims.exp - self.clock())
            require(remaining > 0 and self.clock() >= wall_started, "OSB_ITEM_AUTH_EXPIRED")
            return remaining
        def checked_auth():
            checkpoint()
            return original
        # Shared repository's query deadline includes this outer ceiling even
        # during both complete library/custody observations.
        self.repository.checkpoint = checkpoint
        library_request = NativeItemObservationRequest.model_validate({
            **{key: value for key, value in request.model_dump().items()
               if key in NativeItemObservationRequest.model_fields},
            "contractVersion": "OsbNativeItemObservationRequestV1@1.0.0", "scope": "library-item"})
        require(request.studyValueVersion == request.nativeStudyVersion, "OSB_SELECTED_STUDY_VERSION_MISMATCH")
        library = NativeItemObservationService(self.repository, checked_auth, self.clock, self.monotonic,
                                                allowed_purposes=self.allowed_purposes)
        first_library = library.observe(library_request, cancellation)
        p = {**request.model_dump(), "tenantId": original.user.tenant_id}

        def selection_observation():
            require(self.repository.path_count(p, min(5, checkpoint())) == [[1]], "OSB_SELECTED_PATH_NOT_UNIQUE")
            rows = self.repository.path_projection(p, min(5, checkpoint()))
            observed_at = self.clock()
            checkpoint()
            require(len(rows) == 1 and isinstance(rows[0][0], dict), "OSB_SELECTED_PATH_PROJECTION_UNAVAILABLE")
            require(rows[0][1] is False, "OSB_SELECTED_ITEM_TYPED_RELATIONSHIPS_UNSUPPORTED")
            try:
                selection = SelectedActivityItemPath.model_validate(rows[0][0]).model_dump()
            except ValidationError as error:
                raise NativeItemObservationError("OSB_SELECTED_PATH_PROJECTION_INVALID") from error
            for key in ("nativeStudyId", "studyValueVersion", "studyActivityInstanceUid", "activityInstanceUid",
                        "activityInstanceVersion", "activityItemClassUid", "activityItemClassVersion", "itemUid", "itemVersion"):
                require(selection[key] == p[key], "OSB_SELECTED_PATH_IDENTITY_MISMATCH")
            require(self.repository.path_count(p, min(5, checkpoint())) == [[1]], "OSB_SELECTED_PATH_NOT_UNIQUE")
            return selection, observed_at

        first_selection, _ = selection_observation()
        final_library = library.observe(library_request, cancellation)
        stable_library = lambda value: {k: v for k, v in value.items()
                                        if k not in {"observedAt", "authorityCheckedAt", "expiresAt"}}
        require(stable_library(first_library) == stable_library(final_library), "OSB_SELECTED_LIBRARY_CHANGED")
        final_selection, observed_at = selection_observation()
        require(first_selection == final_selection, "OSB_SELECTED_PATH_CHANGED")
        require(self.repository.scope(p, min(5, checkpoint())) == [[request.bindingId, request.nativeStudyId, request.nativeStudyVersion]],
                "OSB_ITEM_SCOPE_UNAVAILABLE")
        checkpoint()
        result = {"contractVersion": "OsbSelectedActivityItemObservationV1@1.0.0",
                  "scope": "selected-activity-item", "pins": request.model_dump(),
                  "libraryObservation": final_library, "selection": final_selection,
                  "selectionHash": canonical_json_hash_ref(final_selection, schema_version="OsbSelectedActivityItemPathV1@1.0.0"),
                  "selectedActivityReachabilityVerified": True, "semanticApprovalVerified": False,
                  "formApplicabilityVerified": False, "requirednessVerified": False,
                  "observedAt": instant(observed_at), "authorityCheckedAt": instant(self.clock()),
                  "expiresAt": instant(min(wall_started + budget, original.access_token_claims.exp))}
        result = SelectedActivityItemResponse.model_validate(result).model_dump()
        require(len(canonical_json(result).encode("utf-8")) <= 98_304, "OSB_SELECTED_RESPONSE_LIMIT")
        checkpoint()
        require(self.clock() * 1000 < int(min(wall_started + budget, original.access_token_claims.exp) * 1000),
                "OSB_ITEM_AUTH_EXPIRED")
        return result
