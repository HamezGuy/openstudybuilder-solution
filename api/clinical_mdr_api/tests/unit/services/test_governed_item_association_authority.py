"""Independent boundary challenges layered after the initial owner fixture."""
import pytest
from clinical_mdr_api.tests.unit.services.test_governed_item_association import association_fixture, current_request
from clinical_mdr_api.services.integrations.native_item_observation import NativeItemObservationError


def test_final_association_io_native_scope_withdrawal_withholds_response():
    f = association_fixture()
    def withdraw(name):
        if name == "associations" and f.repository.calls.count(name) == 2:
            f.repository.missing = "scope"
    f.repository.hook = withdraw
    with pytest.raises(NativeItemObservationError): f.service.observe(f.review)
    assert f.repository.writes == 1  # Immutable review survives a withheld response.


def test_final_current_read_native_scope_withdrawal_withholds_response():
    f = association_fixture()
    value = f.service.observe(f.review)
    f.service.review = False
    seen = f.repository.calls.count("associations")
    def withdraw(name):
        if name == "associations" and f.repository.calls.count(name) == seen+2:
            f.repository.missing = "scope"
    f.repository.hook = withdraw
    with pytest.raises(NativeItemObservationError): f.service.observe(current_request(f,value))


@pytest.mark.parametrize("change", ["human", "roles", "tenant", "purpose", "expired-at-review", "future-review"])
def test_resealed_native_record_cannot_discard_original_human_authority(change):
    import json
    from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json
    from clinical_mdr_api.services.integrations.governed_item_association import digest
    f = association_fixture()
    result = f.service.observe(f.review)
    f.service.review = False
    record = json.loads(f.repository.saved[0][2])
    review = record["review"]
    if change == "human": review["humanSubject"] = "another-human"
    if change == "roles": review["roles"] = ["Study.Read", "Library.Read"]
    if change == "tenant": review["tenantId"] = "another-native-tenant"
    if change == "purpose": review["purpose"] = "unapproved-purpose"
    if change == "expired-at-review": review["credentialExpiresAt"] = review["credentialIssuedAt"]
    if change == "future-review": review["reviewedAt"] = "2099-01-01T00:00:00.000Z"
    current_hash = digest(record)
    f.repository.saved[0] = [result["associationId"], current_hash, canonical_json(record)]
    request = current_request(f, {**result,"associationHash":current_hash})
    with pytest.raises(NativeItemObservationError, match="REVIEW_RECORD_INVALID"): f.service.observe(request)
