"""OP-OSB1 (2026-10-25), OSB_PERMISSIONS.md OP-08: the template and instance
parameter routes accept ``?study_uid=`` and then return that study's visit names,
study days, timepoints and endpoint texts. The two generic ``get_parameters``
methods behind all nine routes now need read on the named study. Unit-level,
monkeypatched strict mode, the same fixture shape as test_consumer_visibility.py.
A call with no study stays library-only."""

from types import SimpleNamespace

import pytest

from clinical_mdr_api.services.studies import study_visibility as vis
from clinical_mdr_api.services.syntax_instances.generic_syntax_instance_service import (
    GenericSyntaxInstanceService,
)
from clinical_mdr_api.services.syntax_templates.generic_syntax_template_service import (
    GenericSyntaxTemplateService,
)
from common.exceptions import ForbiddenException, NotFoundException


class SyntheticUser:
    tenant_id = "tenant-synthetic"
    study_ids = {"Study_000999"}
    purpose = "interactive-domain-access"
    capabilities = {"study:read"}
    roles = {"Study.Read", "Library.Read"}


class SyntheticLibraryReader(SyntheticUser):
    capabilities: set[str] = set()
    roles = {"Library.Read"}


@pytest.fixture(autouse=True)
def strict_domain_scope(monkeypatch):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", True)
    monkeypatch.setattr(vis, "_request_user", SyntheticUser)
    monkeypatch.setattr(
        vis,
        "_study_scope",
        lambda uid: (
            (True, "tenant-synthetic", "active")
            if uid == "Study_000999"
            else (True, "another-tenant", "active")
        ),
    )


class _RecordingRepository:
    def __init__(self):
        self.calls = []

    def get_parameters_including_terms(self, template_uid, **kwargs):
        self.calls.append((template_uid, kwargs.get("study_uid")))
        return []

    def find_by_uid(self, uid):
        return SimpleNamespace(uid=uid, template_uid="Template_000001")


def _template_parameters(study_uid):
    repository = _RecordingRepository()
    service = SimpleNamespace(repository=repository)
    # The undecorated method: @db.transaction would open a database session.
    result = GenericSyntaxTemplateService.get_parameters.__wrapped__(  # type: ignore[attr-defined]
        service, "Template_000001", study_uid=study_uid
    )
    return result, repository.calls


def _instance_parameters(study_uid):
    repository = _RecordingRepository()
    service = SimpleNamespace(repository=repository, template_repository=repository)
    result = GenericSyntaxInstanceService.get_parameters.__wrapped__(  # type: ignore[attr-defined]
        service, "Instance_000001", study_uid=study_uid
    )
    return result, repository.calls


@pytest.mark.parametrize(
    "parameters",
    [_template_parameters, _instance_parameters],
    ids=["template", "instance"],
)
def test_parameters_for_an_assigned_study_are_returned(parameters):
    result, calls = parameters("Study_000999")
    assert result == []
    assert calls == [("Template_000001", "Study_000999")]


@pytest.mark.parametrize(
    "parameters",
    [_template_parameters, _instance_parameters],
    ids=["template", "instance"],
)
def test_parameters_for_an_unassigned_study_answer_not_found(parameters):
    with pytest.raises(NotFoundException):
        parameters("Study_000998")


@pytest.mark.parametrize(
    "parameters",
    [_template_parameters, _instance_parameters],
    ids=["template", "instance"],
)
def test_parameters_for_a_study_need_study_read(monkeypatch, parameters):
    monkeypatch.setattr(vis, "_request_user", SyntheticLibraryReader)
    with pytest.raises(ForbiddenException, match="study:read"):
        parameters("Study_000999")


@pytest.mark.parametrize(
    "parameters",
    [_template_parameters, _instance_parameters],
    ids=["template", "instance"],
)
@pytest.mark.parametrize("study_uid", [None, ""], ids=["none", "empty"])
def test_parameters_without_a_study_stay_library_only(
    monkeypatch, parameters, study_uid
):
    monkeypatch.setattr(vis, "_request_user", SyntheticLibraryReader)
    result, calls = parameters(study_uid)
    assert result == []
    assert calls == [("Template_000001", study_uid)]
