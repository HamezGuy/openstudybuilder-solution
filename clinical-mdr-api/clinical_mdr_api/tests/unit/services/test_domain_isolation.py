from types import SimpleNamespace

import pytest

from clinical_mdr_api.services.studies import study as study_service
from clinical_mdr_api.services.studies import study_visibility as vis
from clinical_mdr_api.services.integrations.proposal_review import (
    ProposalReviewPrincipal,
)
from common.exceptions import ForbiddenException, NotFoundException


class SyntheticUser:
    tenant_id = "tenant-synthetic"
    study_ids = {"Study_000999"}
    purpose = "interactive-domain-access"
    capabilities = {"study:read", "study:write"}
    roles = {"Study.Read", "Study.Write"}


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


def test_exact_assignment_and_native_tenant_binding_are_required():
    vis.assert_study_uid_visible("Study_000999", require_write=True)

    with pytest.raises(NotFoundException):
        vis.assert_study_uid_visible("Study_000998")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("purpose", "wrong-purpose"),
        ("capabilities", {"study:read"}),
        ("roles", {"Study.Read"}),
    ],
)
def test_write_scope_rejects_wrong_purpose_capability_or_role(monkeypatch, field, value):
    principal = SyntheticUser()
    monkeypatch.setattr(principal, field, value)
    monkeypatch.setattr(vis, "_request_user", lambda: principal)

    with pytest.raises(ForbiddenException):
        vis.assert_study_uid_visible("Study_000999", require_write=True)


def test_collection_scope_never_turns_missing_authority_into_wildcard(monkeypatch):
    principal = SyntheticUser()
    principal.study_ids = set()
    monkeypatch.setattr(vis, "_request_user", lambda: principal)

    vis.assert_collection_scope(require_write=False, route_path="/api/studies")
    assert vis.study_visible_to_user(principal, None, study_uid=None) is False


@pytest.mark.parametrize(
    ("route_path", "require_write"),
    [
        ("/api/studies/list", False),
        ("/api/studies/headers", False),
        ("/api/studies/template", False),
        ("/api/studies", True),
    ],
)
def test_legacy_unscoped_collection_routes_fail_closed(route_path, require_write):
    with pytest.raises(ForbiddenException):
        vis.assert_collection_scope(
            require_write=require_write,
            route_path=route_path,
        )


def test_assigned_collection_scope_rejects_any_invalid_binding(monkeypatch):
    principal = SyntheticUser()
    principal.study_ids = {"Study_000999", "Study_000998"}
    monkeypatch.setattr(vis, "_request_user", lambda: principal)

    with pytest.raises(ForbiddenException):
        vis.assigned_study_uids()


def test_mapping_context_requires_exact_native_study_in_delegated_mode():
    with pytest.raises(ForbiddenException):
        vis.assert_mapping_context_scope(None)

    vis.assert_mapping_context_scope("Study_000999", require_write=True)


def test_proposal_review_requires_purpose_and_capability():
    principal = ProposalReviewPrincipal(
        actor_id="synthetic-reviewer",
        human_user_id="synthetic-reviewer",
        token_id="synthetic-session",
        tenant_id="tenant-synthetic",
        scoped_study_ids=frozenset({"Study_000999"}),
        organization_ids=frozenset(),
        roles=frozenset({"Study.Write"}),
        authentication_verified=True,
        purpose="workflow-orchestration",
        capabilities=frozenset({"study:write"}),
        enforce_delegated_scope=True,
    )
    principal.assert_proposal_access(
        "tenant-synthetic", "Study_000999", "Study.Write"
    )

    invalid = ProposalReviewPrincipal(
        **{
            **principal.__dict__,
            "capabilities": frozenset({"study:read"}),
        }
    )
    with pytest.raises(ValueError, match="CAPABILITY_REQUIRED"):
        invalid.assert_proposal_access(
            "tenant-synthetic", "Study_000999", "Study.Write"
        )


def test_delegated_root_listing_never_calls_unscoped_repository(monkeypatch):
    requested = []

    class ExactRepository:
        @staticmethod
        def find_all(**_kwargs):
            raise AssertionError("delegated collection called unscoped find_all")

        @staticmethod
        def find_by_uid(uid):
            requested.append(uid)
            return {"uid": uid}

    repository = ExactRepository()
    repos = SimpleNamespace(
        study_definition_repository=repository,
        project_repository=SimpleNamespace(find_by_project_number=lambda _value: None),
        clinical_programme_repository=SimpleNamespace(find_by_uid=lambda _value: None),
        close=lambda: None,
    )
    service = object.__new__(study_service.StudyService)
    service._repos = repos

    monkeypatch.setattr(study_service, "delegated_study_scope_required", lambda: True)
    monkeypatch.setattr(
        study_service,
        "assigned_study_uids",
        lambda **_kwargs: ("Study_000999",),
    )
    monkeypatch.setattr(study_service, "_caller_may_see", lambda _item: True)
    monkeypatch.setattr(
        study_service.StudyService,
        "_models_compact_study_from_study_definition_ar",
        staticmethod(lambda study_definition_ar, **_kwargs: study_definition_ar),
    )
    monkeypatch.setattr(
        study_service,
        "service_level_generic_filtering",
        lambda items, **_kwargs: SimpleNamespace(items=items, total=len(items)),
    )

    result = service.get_all()

    assert requested == ["Study_000999"]
    assert result.items == [{"uid": "Study_000999"}]
