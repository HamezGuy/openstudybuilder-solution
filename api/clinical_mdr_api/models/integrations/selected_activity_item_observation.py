"""Exact native selected-activity reachability; not semantic or form approval."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from clinical_mdr_api.models.integrations.native_item_observation import (
    NativeItemObservationRequest, NativeItemObservationResponse, Identity,
    ScalarText, ScalarInteger, ObservationHash, Instant,
)


class SelectedActivityItemRequest(NativeItemObservationRequest):
    contractVersion: Literal["OsbSelectedActivityItemRequestV1@1.0.0"]
    scope: Literal["selected-activity-item"]
    studyValueVersion: Identity
    studyActivityInstanceUid: Identity
    activityInstanceUid: Identity
    activityInstanceVersion: Identity
    activityItemClassUid: Identity
    activityItemClassVersion: Identity


class SelectedActivityItemFields(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    textValue: ScalarText | None
    isAdamParamSpecific: Annotated[bool, Field(strict=True)] | None
    isActivityInstanceIdSpecific: Annotated[bool, Field(strict=True)] | None


class SelectedActivityItemLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    order: ScalarInteger | None
    primary: Annotated[bool, Field(strict=True)] | None
    presetResponseValue: ScalarText | None
    valueCondition: ScalarText | None
    valueDependentMap: ScalarText | None


class SelectedActivityItemPath(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    nativeStudyId: Identity
    studyValueVersion: Identity
    studyVersionStatus: Identity
    studyActivityInstanceUid: Identity
    activityInstanceUid: Identity
    activityInstanceVersion: Identity
    activityInstanceStatus: Literal["Final"]
    activityItemClassUid: Identity
    activityItemClassVersion: Identity
    activityItemClassStatus: Literal["Final"]
    itemUid: Identity
    itemVersion: Identity
    itemStatus: Literal["Final"]
    activityItem: SelectedActivityItemFields
    itemLink: SelectedActivityItemLink


class SelectedActivityItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contractVersion: Literal["OsbSelectedActivityItemObservationV1@1.0.0"]
    scope: Literal["selected-activity-item"]
    pins: SelectedActivityItemRequest
    libraryObservation: NativeItemObservationResponse
    selection: SelectedActivityItemPath
    selectionHash: ObservationHash
    selectedActivityReachabilityVerified: Literal[True]
    semanticApprovalVerified: Literal[False]
    formApplicabilityVerified: Literal[False]
    requirednessVerified: Literal[False]
    observedAt: Instant
    authorityCheckedAt: Instant
    expiresAt: Instant
