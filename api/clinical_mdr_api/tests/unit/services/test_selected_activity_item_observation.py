"""Selected activity composition over actual library producer/observer fixtures."""
import copy

import pytest
from clinical_mdr_api.tests.unit.services.test_native_item_observation import fixture, MemoryRepository, STUDY
from clinical_mdr_api.models.integrations.selected_activity_item_observation import SelectedActivityItemRequest
from clinical_mdr_api.services.integrations.selected_activity_item_observation import SelectedActivityItemService
from clinical_mdr_api.services.integrations.native_item_observation import NativeItemObservationError


def selection_request(pins):
    return SelectedActivityItemRequest.model_validate({**pins.model_dump(),
        "contractVersion": "OsbSelectedActivityItemRequestV1@1.0.0", "scope": "selected-activity-item",
        "studyValueVersion": pins.nativeStudyVersion, "studyActivityInstanceUid": "SAI_1",
        "activityInstanceUid": "AI_1", "activityInstanceVersion": "1.0",
        "activityItemClassUid": "AIC_1", "activityItemClassVersion": "1.0"})


def projection(request):
    return {key: getattr(request, key) for key in (
        "nativeStudyId", "studyValueVersion", "studyActivityInstanceUid", "activityInstanceUid", "activityInstanceVersion",
        "activityItemClassUid", "activityItemClassVersion", "itemUid", "itemVersion")} | {
        "studyVersionStatus": "Draft", "activityInstanceStatus": "Final", "activityItemClassStatus": "Final", "itemStatus": "Final",
        "activityItem": {"textValue": "Native selected item", "isAdamParamSpecific": False, "isActivityInstanceIdSpecific": True},
        "itemLink": {"order": 1, "primary": True, "presetResponseValue": None, "valueCondition": None, "valueDependentMap": None}}


class SelectedMemoryRepository(MemoryRepository):
    def __init__(self, f, request):
        super().__init__(f.request, f.blobs, f.fields)
        self.path = projection(request)
        self.matches, self.path_related = 1, False
    def read(self, name, rows, timeout):
        if getattr(self, "checkpoint", None): timeout = min(timeout, self.checkpoint())
        result = super().read(name, rows, timeout)
        if getattr(self, "checkpoint", None): self.checkpoint()
        return result
    def path_count(self, p, timeout): return self.read("path_count", [[self.matches]], timeout)
    def path_projection(self, p, timeout): return self.read("path_projection", [[self.path, self.path_related]], timeout)


def selected_fixture():
    f = fixture()
    f.request = selection_request(f.request)
    f.repository = SelectedMemoryRepository(f, f.request)
    f.service = SelectedActivityItemService(f.repository, lambda: f.auth, lambda: f.now[0], lambda: f.now[0])
    return f


def test_selected_reachability_conserves_library_scope_and_actual_projection():
    f = selected_fixture()
    value = f.service.observe(f.request)
    assert value["selectedActivityReachabilityVerified"] is True
    assert value["semanticApprovalVerified"] is False
    assert value["formApplicabilityVerified"] is False and value["requirednessVerified"] is False
    assert value["libraryObservation"]["scope"] == "library-item"
    assert value["libraryObservation"]["studySelectionVerified"] is False
    assert value["selection"]["activityItem"]["textValue"] == "Native selected item"
    assert f.repository.calls.count("path_count") == 4
    assert f.repository.calls.count("path_projection") == 2


@pytest.mark.parametrize("matches", [0, 2])
def test_cardinality_checked_before_any_path_data_projection(matches):
    f = selected_fixture()
    f.repository.matches = matches
    with pytest.raises(NativeItemObservationError, match="PATH_NOT_UNIQUE"):
        f.service.observe(f.request)
    assert "path_projection" not in f.repository.calls


@pytest.mark.parametrize("key", ["studyValueVersion", "studyActivityInstanceUid", "activityInstanceUid", "activityInstanceVersion",
                                 "activityItemClassUid", "activityItemClassVersion", "itemUid", "itemVersion"])
def test_caller_pins_are_never_copied_as_observed_identity(key):
    f = selected_fixture()
    f.repository.path[key] = "unrelated"
    with pytest.raises(NativeItemObservationError): f.service.observe(f.request)


@pytest.mark.parametrize("change", ["native-permission", "platform-permission", "role", "capability", "unverified", "expired"])
def test_original_native_study_authority_is_required(change):
    f = selected_fixture()
    if change == "native-permission": f.auth.user.study_ids.remove(f.request.nativeStudyId)
    if change == "platform-permission": f.auth.user.study_ids.remove(STUDY)
    if change == "role": f.auth.user.roles.remove("Library.Read")
    if change == "capability": f.auth.user.capabilities.remove("study:read")
    if change == "unverified": f.auth.authentication_verified = False
    if change == "expired": f.now[0] += 301
    with pytest.raises(NativeItemObservationError): f.service.observe(f.request)
    assert "path_projection" not in f.repository.calls


@pytest.mark.parametrize("change", ["duplicate", "path-value", "role", "expiry", "deadline", "scope"])
def test_late_path_or_authority_changes_withhold_observation(change):
    f = selected_fixture()
    def hook(name):
        if name == "path_projection" and f.repository.calls.count(name) == 2:
            if change == "duplicate": f.repository.matches = 2
            if change == "path-value": f.repository.path["itemLink"]["order"] = 2
            if change == "role": f.auth.user.roles.clear()
            if change == "expiry": f.now[0] += 301
            if change == "deadline": f.now[0] += 16
            if change == "scope": f.repository.missing = "scope"
    f.repository.hook = hook
    with pytest.raises(NativeItemObservationError): f.service.observe(f.request)


def test_unproven_typed_relations_and_oversized_selected_data_refuse():
    f = selected_fixture()
    f.repository.path_related = True
    with pytest.raises(NativeItemObservationError, match="TYPED_RELATIONSHIPS_UNSUPPORTED"): f.service.observe(f.request)
    f = selected_fixture()
    f.repository.path["activityItem"]["textValue"] = "x" * 4097
    with pytest.raises(NativeItemObservationError, match="PROJECTION_INVALID"): f.service.observe(f.request)
