"""Plan W2.3 (2026-09-21): the consumer API and the study-metadata listing honour the
delegated study scope like every study router. Unit-level, monkeypatched strict mode,
the same fixture shape as test_domain_isolation.py."""

from types import SimpleNamespace

import pytest

from clinical_mdr_api.services.studies import study_visibility as vis
from common.exceptions import ForbiddenException, NotFoundException
from consumer_api.shared import visibility as consumer_visibility


class SyntheticUser:
    tenant_id = "tenant-synthetic"
    study_ids = {"Study_000999"}
    purpose = "interactive-domain-access"
    capabilities = {"study:read"}
    roles = {"Study.Read"}


@pytest.fixture(autouse=True)
def strict_domain_scope(monkeypatch):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", True)
    monkeypatch.setattr(vis, "_request_user", lambda: SyntheticUser())
    monkeypatch.setattr(
        vis,
        "_study_scope",
        lambda uid: (True, "tenant-synthetic", "active")
        if uid == "Study_000999"
        else (True, "another-tenant", "active"),
    )


def _request(path: str, **path_params):
    return SimpleNamespace(path_params=path_params, url=SimpleNamespace(path=path))


def test_a_study_route_outside_the_assignment_answers_not_found():
    consumer_visibility.enforce_visible_consumer_study(
        _request("/v1/studies/Study_000999/study-visits", uid="Study_000999"), _auth=None
    )
    with pytest.raises(NotFoundException):
        consumer_visibility.enforce_visible_consumer_study(
            _request("/v1/studies/Study_000998/study-visits", uid="Study_000998"), _auth=None
        )


def test_the_study_list_keeps_only_assigned_studies():
    rows = [{"uid": "Study_000999", "id": "A"}, {"uid": "Study_000998", "id": "B"}, {"uid": "", "id": "C"}]
    assert [row["id"] for row in consumer_visibility.visible_consumer_studies(rows)] == ["A"]


def test_cross_study_collection_surfaces_fail_closed_in_delegated_mode():
    consumer_visibility.enforce_consumer_collection_scope(_request("/v1/studies"), _auth=None)
    for path in ("/v1/studies/audit-trail", "/v1/papillons/soa"):
        with pytest.raises(ForbiddenException):
            consumer_visibility.enforce_consumer_collection_scope(_request(path), _auth=None)


def test_study_metadata_listing_asserts_visibility_after_resolving_the_uid(monkeypatch):
    from clinical_mdr_api.domain_repositories.study_definitions import (
        study_definition_repository as repository_module,
    )
    from clinical_mdr_api.services.listings import listings_study as listings

    monkeypatch.setattr(
        repository_module.StudyDefinitionRepository,
        "find_uid_by_study_number",
        staticmethod(lambda **_kwargs: "Study_000998"),
    )
    service = listings.StudyMetadataListingService()
    with pytest.raises(NotFoundException):
        service.get_study_metadata("P1", "0998", version="1")
