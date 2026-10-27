"""OSBI-5 (D-45, 2026-10-26), OSB_ISOLATION_DESIGN.md sections 2.1, 4.2 (cluster 1)
and 8: the body study uid of the study epoch services (C01, C02).

Under the delegated profile, create, preview, edit and the two batch services
refuse a body ``study_uid`` other than the path study with 404 before anything is
locked, looked up or saved: edit no longer loads, locks and rewrites the epoch of
the body study (C01), and create no longer answers the one-Supplemental-epoch rule
for the body study (C02). A batch is checked whole, before its loop and its lock.
Outside the profile the services make the same calls as before. Over HTTP through
the real study_epochs router (the shape of verifier probes p27 and p30), with only
the repository, CT maps and DB helpers faked; synthetic studies only.

OSBI-6 (D-45), sections 2.2, 4.2 (cluster 2) and 8: the disease milestones (C03).
Under the delegated profile the milestone service resolves a milestone only when
it belongs to the path study: find_by_uid, edit, reorder and delete refuse another
study's milestone with the 404 of an unknown uid before anything is locked, looked
up or saved, also when the route child check of OSBI-3 is not there, and create
refuses a body study other than the path study. Outside the profile a milestone
still resolves by uid alone. Over HTTP in the shape of verifier probe p28.
"""

import datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neomodel import db
from starlette.middleware import Middleware
from starlette_context.middleware import RawContextMiddleware

from clinical_mdr_api.domains.study_definition_aggregates.study_metadata import (
    StudyStatus,
)
from clinical_mdr_api.domains.study_selections import (
    study_disease_milestone as milestone_domain,
)
from clinical_mdr_api.domains.study_selections.study_epoch import StudyEpochVO
from clinical_mdr_api.models.controlled_terminologies.ct_term import (
    SimpleCTTermNameWithConflictFlag,
)
from clinical_mdr_api.models.study_selections import (
    study_disease_milestone as milestone_models,
)
from clinical_mdr_api.models.study_selections import study_epoch as epoch_models
from clinical_mdr_api.routers.studies import (  # pylint: disable=unused-import
    study_access,
    study_disease_milestones,
    study_epochs,
)
from clinical_mdr_api.services.studies import study as study_service
from clinical_mdr_api.services.studies import (
    study_disease_milestone as milestone_service,
)
from clinical_mdr_api.services.studies import study_epoch as epoch_service
from clinical_mdr_api.services.studies import study_visibility as vis
from clinical_mdr_api.services.user_info import UserInfoService
from common.auth import rbac
from common.auth.dependencies import security
from common.config import settings
from common.exception_handlers import register_exception_handlers
from common.exceptions import NotFoundException, ValidationException

_OWN = "Study_000999"
_FOREIGN = "Study_000998"
_UNKNOWN = "Study_000997"
_OWN_EPOCH = "StudyEpoch_000011"
_FOREIGN_EPOCH = "StudyEpoch_000077"
_FOREIGN_SIBLING = "StudyEpoch_000078"
_SECRET = "FOREIGN-TENANT-CONFIDENTIAL-EPOCH-DESCRIPTION"
_BASIC = "C_BASIC"
_SUPPLEMENTAL_RULE = "There can exist only one Supplemental Study Epoch."
_OWN_MILESTONE = "StudyDiseaseMilestone_000011"
_FOREIGN_MILESTONE = "StudyDiseaseMilestone_000777"
_FOREIGN_MILESTONE_SIBLING = "StudyDiseaseMilestone_000778"
_UNKNOWN_MILESTONE = "StudyDiseaseMilestone_000555"
_MILESTONE_SECRET = "FOREIGN-TENANT-CONFIDENTIAL-MILESTONE-DEFINITION"
_MILESTONE_SECRET_NAME = "FOREIGN-TENANT-MILESTONE-TYPE"

# The registry convention of section 8 (OSBI-27): each body field that names a
# study, with the entry-point cases of this module that send a foreign value and
# assert 404 and no repository save.
GUARDED_FIELDS = {
    ("StudyEpochCreateInput", "study_uid"): (
        "test_c02_create_with_a_foreign_body_study_answers_404",
        "test_c02_preview_with_a_foreign_body_study_answers_404",
        "test_c02_batch_post_with_a_foreign_body_study_answers_404",
        "test_c02_batch_preview_with_a_foreign_body_study_answers_404",
        "test_c02_body_naming_another_study_no_longer_skips_the_own_rule",
    ),
    ("StudyEpochEditInput", "study_uid"): (
        "test_c01_patch_with_a_foreign_body_study_answers_404",
        "test_c01_patch_of_a_foreign_epoch_answers_404",
        "test_c01_service_edit_with_a_foreign_body_study_raises_404",
    ),
    ("StudyEpochBatchUpdateInput", "study_uid"): (
        "test_c01_batch_patch_with_a_foreign_body_study_answers_404",
        "test_c01_batch_with_one_foreign_item_is_refused_whole",
    ),
    ("StudyDiseaseMilestoneCreateInput", "study_uid"): (
        "test_c03_create_with_a_foreign_body_study_answers_404",
    ),
}


class SyntheticUser:
    tenant_id = "tenant-synthetic"
    study_ids = {_OWN}
    purpose = "interactive-domain-access"
    capabilities = {"study:read", "study:write"}
    roles = {"Study.Read", "Study.Write"}

    def id(self):
        return "synthetic-user"


def _scope(uid):
    return (
        (True, "tenant-synthetic", "active")
        if uid == _OWN
        else (True, "another-tenant", "active")
    )


def _never(*_args, **_kwargs):
    raise AssertionError("no lookup may run here")


def _owners(uids):
    owner_by_uid = {
        _OWN_EPOCH: {_OWN},
        _FOREIGN_EPOCH: {_FOREIGN},
        _FOREIGN_SIBLING: {_FOREIGN},
        _OWN_MILESTONE: {_OWN},
        _FOREIGN_MILESTONE: {_FOREIGN},
        _FOREIGN_MILESTONE_SIBLING: {_FOREIGN},
    }
    return {uid: frozenset(owner_by_uid.get(uid, ())) for uid in uids}


def _term(uid, name):
    return SimpleCTTermNameWithConflictFlag(
        term_uid=uid,
        sponsor_preferred_name=name,
        queried_effective_date=None,
        date_conflict=False,
    )


def _subtype_name(subtype):
    return {
        "ST1": "Treatment",
        "ST2": "Follow-up",
        _BASIC: settings.basic_epoch_name,
    }[subtype]


def _vo(study_uid, uid, *, subtype="ST1", order=1, description=None):
    basic = subtype == _BASIC
    return StudyEpochVO(
        study_uid=study_uid,
        start_rule=f"{study_uid} start",
        end_rule=f"{study_uid} end",
        epoch=(
            _term("E_BASIC", settings.basic_epoch_name)
            if basic
            else _term("E1", "Treatment 1")
        ),
        subtype=_term(subtype, _subtype_name(subtype)),
        epoch_type=(
            _term("T_BASIC", settings.basic_epoch_name)
            if basic
            else _term("T1", "Treatment")
        ),
        description=description,
        order=order,
        status=StudyStatus.DRAFT,
        start_date=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        author_id="author",
        author_username="author@synthetic",
        uid=uid,
    )


class FakeRepo:
    """Holds one epoch of the own study and two of the foreign study, and records
    every study each lookup is asked about."""

    def __init__(self, own_has_basic=False):
        self.own_has_basic = own_has_basic
        self.find_calls = []
        self.all_epochs_calls = []
        self.basic_queries = []
        self.saved = []
        self.author_id = "synthetic-user"
        self.store = {
            (_OWN_EPOCH, _OWN): lambda: _vo(_OWN, _OWN_EPOCH),
            (_FOREIGN_EPOCH, _FOREIGN): lambda: _vo(
                _FOREIGN, _FOREIGN_EPOCH, description=_SECRET
            ),
            (_FOREIGN_SIBLING, _FOREIGN): lambda: _vo(
                _FOREIGN, _FOREIGN_SIBLING, subtype="ST2", description=_SECRET
            ),
        }

    def find_by_uid(self, uid, study_uid, study_value_version=None, for_update=False):
        assert study_value_version is None
        self.find_calls.append((uid, study_uid, for_update))
        if (uid, study_uid) in self.store:
            return self.store[(uid, study_uid)]()
        raise ValidationException(msg=f"StudyEpoch with UID '{uid}' doesn't exist.")

    def find_all_epochs_by_study(self, study_uid, study_value_version=None):
        assert study_value_version is None
        self.all_epochs_calls.append(study_uid)
        return [make() for (_, owner), make in self.store.items() if owner == study_uid]

    def get_basic_epoch(self, study_uid):
        self.basic_queries.append(study_uid)
        if study_uid == _FOREIGN or (study_uid == _OWN and self.own_has_basic):
            return f"StudyEpoch_BASIC_OF_{study_uid}"
        return None

    def save(self, epoch):
        epoch.uid = epoch.uid or f"StudyEpoch_NEW_{len(self.saved) + 1}"
        self.saved.append((epoch.uid, epoch.study_uid, epoch.start_rule))
        return epoch


class _Env:
    def __init__(self):
        self.repo = FakeRepo()
        self.write_locks = []
        self.lock_checks = []
        self.owner_queries = []
        self.service_class = None


@pytest.fixture(name="env")
def fixture_env(monkeypatch):
    env = _Env()
    monkeypatch.setattr(db, "_active_transaction", object(), raising=False)
    monkeypatch.setattr(
        study_service.StudyService, "__init__", lambda self, *a, **k: None
    )
    monkeypatch.setattr(
        study_service.StudyService,
        "check_if_study_is_locked",
        lambda self, study_uid: (env.lock_checks.append(study_uid), False)[1],
    )

    def acquire_write_lock_study_value(uid):
        env.write_locks.append(uid)

    monkeypatch.setattr(
        epoch_service, "acquire_write_lock_study_value", acquire_write_lock_study_value
    )
    monkeypatch.setattr(
        epoch_service.StudyVisitRepository,
        "find_all_visits_by_study_uid",
        staticmethod(lambda study_uid: []),
    )

    class _Service(epoch_service.StudyEpochService):
        def __init__(self, study_uid=None, **_kwargs):
            # pylint: disable=super-init-not-called
            self.repo = env.repo
            self.author = "synthetic-user"
            self._repos = MagicMock()
            self.study_epoch_subtypes_by_uid = {
                uid: _term(uid, _subtype_name(uid)) for uid in ("ST1", "ST2", _BASIC)
            }
            self.study_epoch_epochs_by_uid = {
                "E1": _term("E1", "Treatment 1"),
                "E_BASIC": _term("E_BASIC", settings.basic_epoch_name),
            }
            self.study_epoch_types_by_uid = {
                "T1": _term("T1", "Treatment"),
                "T_BASIC": _term("T_BASIC", settings.basic_epoch_name),
            }

        def _validate_lag_time_units(self, *_args, **_kwargs):
            return None

        def _get_epoch_type_object(self, subtype):
            return _term("T1", "Treatment")

        def _from_input_values(self, study_uid, study_epoch_create_input, *_a, **_k):
            # The real builder writes the path study; only its CT reads are skipped.
            created = _vo(
                study_uid, None, subtype=study_epoch_create_input.epoch_subtype
            )
            created.order = None
            created.start_rule = study_epoch_create_input.start_rule
            return created

        def _snapshot_existing_epoch_state(self, study_uid):
            return {}

        def _collect_affected_existing_after_creates(self, before, study_uid):
            return []

        def _compute_affected_existing_for_preview(self, study_uid, specs):
            return []

    monkeypatch.setattr(study_epochs, "StudyEpochService", _Service)
    env.service_class = _Service
    return env


@pytest.fixture(name="strict")
def fixture_strict(monkeypatch, env):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", True)
    monkeypatch.setattr(vis, "_request_user", SyntheticUser)
    monkeypatch.setattr(vis, "_study_scope", _scope)
    monkeypatch.setattr(
        vis, "_study_scopes", lambda uids: {uid: _scope(uid) for uid in uids}
    )

    def study_child_owners(uids):
        env.owner_queries.append(tuple(uids))
        return _owners(uids)

    monkeypatch.setattr(vis, "_study_child_owners", study_child_owners)
    return env


@pytest.fixture(name="non_strict")
def fixture_non_strict(monkeypatch, env):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", False)
    monkeypatch.setattr(vis, "_request_user", lambda: None)
    monkeypatch.setattr(vis, "_study_scope", _never)
    monkeypatch.setattr(vis, "_study_scopes", _never)
    monkeypatch.setattr(vis, "_study_child_owners", _never)
    return env


def _test_client(*middleware):
    # study_router carries the epoch routes and, through the import of
    # study_disease_milestones above, the milestone routes.
    app = FastAPI(middleware=list(middleware))
    register_exception_handlers(app)
    app.include_router(study_epochs.router)
    app.dependency_overrides[security.dependency] = lambda: None
    app.dependency_overrides[rbac.STUDY_READ.dependency] = lambda: None
    app.dependency_overrides[rbac.STUDY_WRITE.dependency] = lambda: None
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(name="client")
def fixture_client():
    return _test_client()


def _assert_body_study_refused(response, env, body_study=_FOREIGN):
    assert response.status_code == 404, response.text
    assert "Study Definition" in response.text
    assert body_study in response.text
    assert _SECRET not in response.text
    assert f"{_FOREIGN} end" not in response.text
    assert not env.repo.find_calls
    assert not env.repo.all_epochs_calls
    assert not env.repo.basic_queries
    assert not env.repo.saved
    assert not env.write_locks


def _create_body(study_uid, subtype=_BASIC):
    return {"study_uid": study_uid, "epoch_subtype": subtype}


def _patch_item(study_uid, epoch_uid, start_rule="batch rule"):
    return {
        "method": "PATCH",
        "content": {
            "study_uid": study_uid,
            "study_epoch_uid": epoch_uid,
            "start_rule": start_rule,
            "change_description": "synthetic",
        },
    }


# C01: edit and batch PATCH


def test_c01_patch_with_a_foreign_body_study_answers_404(strict, client):
    """The path child is the caller's own epoch, so the route admits it; only the
    body names the other study. Before OSBI-5 edit locked the body study and
    looked the epoch up there."""
    response = client.patch(
        f"/studies/{_OWN}/study-epochs/{_OWN_EPOCH}",
        json={
            "study_uid": _FOREIGN,
            "start_rule": "rule",
            "change_description": "synthetic",
        },
    )

    _assert_body_study_refused(response, strict)
    assert strict.lock_checks == [_OWN]


def test_c01_patch_of_a_foreign_epoch_answers_404(strict, client):
    """The p27 request: the foreign epoch under the own path, with the foreign
    study in the body. The route child check of OSBI-3 refuses the path child
    first; the epoch's text never reaches the caller."""
    response = client.patch(
        f"/studies/{_OWN}/study-epochs/{_FOREIGN_EPOCH}",
        json={
            "study_uid": _FOREIGN,
            "start_rule": "rule",
            "epoch_subtype": "ST2",
            "change_description": "synthetic",
        },
    )

    assert response.status_code == 404, response.text
    assert _SECRET not in response.text
    assert not strict.repo.find_calls
    assert not strict.repo.saved
    assert not strict.write_locks


def test_c01_service_edit_with_a_foreign_body_study_raises_404(strict):
    """A caller that reaches the service without the route (section 3.5) gets the
    same refusal, before any lookup."""
    service = strict.service_class(study_uid=_OWN)

    with pytest.raises(NotFoundException) as refused:
        service.edit(
            study_uid=_OWN,
            study_epoch_uid=_FOREIGN_EPOCH,
            study_epoch_input=epoch_models.StudyEpochEditInput(
                study_uid=_FOREIGN,
                start_rule="rule",
                epoch_subtype="ST2",
                change_description="synthetic",
            ),
        )

    assert _FOREIGN in str(refused.value)
    assert _SECRET not in str(refused.value)
    assert not strict.repo.find_calls
    assert not strict.repo.all_epochs_calls
    assert not strict.repo.saved
    assert not strict.write_locks


def test_c01_batch_patch_with_a_foreign_body_study_answers_404(strict, client):
    response = client.post(
        f"/studies/{_OWN}/study-epochs/batch",
        json=[_patch_item(_FOREIGN, _FOREIGN_EPOCH)],
    )

    _assert_body_study_refused(response, strict)
    assert not strict.owner_queries


def test_c01_batch_with_one_foreign_item_is_refused_whole(strict, client):
    """The own items come first; the batch is refused before its lock and before
    the first item is written."""
    response = client.post(
        f"/studies/{_OWN}/study-epochs/batch",
        json=[
            {"method": "POST", "content": _create_body(_OWN, "ST1")},
            _patch_item(_OWN, _OWN_EPOCH),
            _patch_item(_FOREIGN, _FOREIGN_EPOCH),
        ],
    )

    _assert_body_study_refused(response, strict)


def test_c01_batch_patch_of_a_foreign_epoch_stays_in_the_path_study(strict, client):
    """With the body equal to the path, the epoch uid of a batch item is looked up
    in the path study only, so another study's epoch is not found there."""
    response = client.post(
        f"/studies/{_OWN}/study-epochs/batch",
        json=[_patch_item(_OWN, _FOREIGN_EPOCH)],
    )

    assert response.status_code == 400, response.text
    assert _SECRET not in response.text
    assert strict.repo.find_calls == [(_FOREIGN_EPOCH, _OWN, True)]
    assert not strict.repo.saved
    assert strict.write_locks == [_OWN]


# C02: create, preview, batch POST and batch preview


@pytest.mark.parametrize("body_study", [_FOREIGN, _UNKNOWN, ""])
def test_c02_create_with_a_foreign_body_study_answers_404(strict, client, body_study):
    """The same 404 whether the named study has a Supplemental epoch, has none,
    or is blank. A padded own uid never reaches the service: InputModel strips
    it, so the service sees the own study."""
    response = client.post(
        f"/studies/{_OWN}/study-epochs", json=_create_body(body_study)
    )

    _assert_body_study_refused(response, strict, body_study)
    assert _SUPPLEMENTAL_RULE not in response.text


def test_c02_preview_with_a_foreign_body_study_answers_404(strict, client):
    response = client.post(
        f"/studies/{_OWN}/study-epochs/preview", json=_create_body(_FOREIGN)
    )

    _assert_body_study_refused(response, strict)
    assert _SUPPLEMENTAL_RULE not in response.text


def test_c02_batch_post_with_a_foreign_body_study_answers_404(strict, client):
    response = client.post(
        f"/studies/{_OWN}/study-epochs/batch",
        json=[
            {"method": "POST", "content": _create_body(_OWN, "ST1")},
            {"method": "POST", "content": _create_body(_FOREIGN)},
        ],
    )

    _assert_body_study_refused(response, strict)
    assert _SUPPLEMENTAL_RULE not in response.text


def test_c02_batch_preview_with_a_foreign_body_study_answers_404(strict, client):
    """Refused whole, not as one failed item among previews."""
    response = client.post(
        f"/studies/{_OWN}/study-epochs/batch/preview",
        json=[_create_body(_OWN, "ST1"), _create_body(_FOREIGN)],
    )

    _assert_body_study_refused(response, strict)
    assert _SUPPLEMENTAL_RULE not in response.text


def test_c02_body_naming_another_study_no_longer_skips_the_own_rule(strict, client):
    """The own study has a Supplemental epoch. Before OSBI-5 a body naming any
    other uid skipped the rule and wrote a second one into the own study."""
    strict.repo.own_has_basic = True

    response = client.post(f"/studies/{_OWN}/study-epochs", json=_create_body(_UNKNOWN))

    _assert_body_study_refused(response, strict, _UNKNOWN)


# The SPA request shape: the body study equals the path study


def test_spa_patch_reaches_the_own_epoch(strict, client):
    response = client.patch(
        f"/studies/{_OWN}/study-epochs/{_OWN_EPOCH}",
        json={
            "study_uid": _OWN,
            "start_rule": "edited rule",
            "change_description": "synthetic",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["uid"] == _OWN_EPOCH
    assert strict.repo.find_calls == [(_OWN_EPOCH, _OWN, True)]
    assert strict.repo.saved == [(_OWN_EPOCH, _OWN, "edited rule")]
    assert strict.owner_queries == [(_OWN_EPOCH,)]


def test_spa_create_and_preview_pass(strict, client):
    created = client.post(
        f"/studies/{_OWN}/study-epochs", json=_create_body(_OWN, "ST1")
    )
    previewed = client.post(
        f"/studies/{_OWN}/study-epochs/preview", json=_create_body(_OWN, "ST1")
    )

    assert created.status_code == 201, created.text
    assert created.json()["study_uid"] == _OWN
    assert previewed.status_code == 200, previewed.text
    assert previewed.json()["uid"] == "preview"
    assert strict.repo.saved == [("StudyEpoch_NEW_1", _OWN, None)]
    assert strict.write_locks == [_OWN]


def test_spa_supplemental_rule_still_applies_to_the_own_study(strict, client):
    strict.repo.own_has_basic = True

    response = client.post(f"/studies/{_OWN}/study-epochs", json=_create_body(_OWN))

    assert response.status_code == 400, response.text
    assert _SUPPLEMENTAL_RULE in response.text
    assert strict.repo.basic_queries == [_OWN]
    assert not strict.repo.saved


def test_spa_batch_and_batch_preview_pass(strict, client):
    batch = client.post(
        f"/studies/{_OWN}/study-epochs/batch",
        json=[
            {"method": "POST", "content": _create_body(_OWN, "ST1")},
            _patch_item(_OWN, _OWN_EPOCH, "edited in batch"),
        ],
    )
    preview = client.post(
        f"/studies/{_OWN}/study-epochs/batch/preview",
        json=[_create_body(_OWN, "ST1"), _create_body(_OWN, "ST2")],
    )

    assert batch.status_code == 207, batch.text
    assert [item["response_code"] for item in batch.json()["items"]] == [201, 200]
    assert strict.repo.find_calls == [(_OWN_EPOCH, _OWN, True)]
    assert {study for _, study, _ in strict.repo.saved} == {_OWN}
    assert strict.write_locks[0] == _OWN
    assert preview.status_code == 200, preview.text
    assert [item["response_code"] for item in preview.json()["items"]] == [200, 200]


# Outside the delegated profile: the same calls as before OSBI-5


def test_outside_the_profile_edit_still_looks_up_the_body_study(non_strict, client):
    response = client.patch(
        f"/studies/{_OWN}/study-epochs/{_FOREIGN_EPOCH}",
        json={
            "study_uid": _FOREIGN,
            "start_rule": "rule",
            "change_description": "synthetic",
        },
    )

    assert response.status_code == 200, response.text
    assert non_strict.repo.find_calls == [(_FOREIGN_EPOCH, _FOREIGN, True)]
    assert non_strict.repo.saved == [(_FOREIGN_EPOCH, _FOREIGN, "rule")]


def test_outside_the_profile_batch_patch_still_looks_up_the_body_study(
    non_strict, client
):
    response = client.post(
        f"/studies/{_OWN}/study-epochs/batch",
        json=[_patch_item(_FOREIGN, _FOREIGN_EPOCH)],
    )

    assert response.status_code == 207, response.text
    assert non_strict.write_locks == [_OWN]
    assert non_strict.repo.find_calls == [(_FOREIGN_EPOCH, _FOREIGN, True)]


def test_outside_the_profile_create_still_checks_the_body_study(non_strict, client):
    refused = client.post(f"/studies/{_OWN}/study-epochs", json=_create_body(_FOREIGN))
    created = client.post(f"/studies/{_OWN}/study-epochs", json=_create_body(_UNKNOWN))
    previewed = client.post(
        f"/studies/{_OWN}/study-epochs/batch/preview", json=[_create_body(_FOREIGN)]
    )

    assert refused.status_code == 400 and _SUPPLEMENTAL_RULE in refused.text
    assert created.status_code == 201, created.text
    assert previewed.status_code == 200, previewed.text
    assert previewed.json()["items"][0]["response_code"] == 400
    assert non_strict.repo.basic_queries == [_FOREIGN, _UNKNOWN, _FOREIGN]
    assert non_strict.repo.saved == [("StudyEpoch_NEW_1", _OWN, None)]


# C03: disease milestones (OSBI-6)


def _milestone_vo(study_uid, uid, order, milestone_type="CTTerm_DM2"):
    foreign = study_uid == _FOREIGN
    return milestone_domain.StudyDiseaseMilestoneVO(
        study_uid=study_uid,
        repetition_indicator=False,
        order=order,
        status=StudyStatus.DRAFT,
        start_date=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        author_id="author",
        author_username="author@synthetic",
        disease_milestone_type=milestone_type,
        disease_milestone_type_definition=(
            _MILESTONE_SECRET if foreign else "Own milestone definition"
        ),
        disease_milestone_type_name=(
            _MILESTONE_SECRET_NAME if foreign else "Own milestone type"
        ),
        uid=uid,
    )


class FakeMilestoneRepo:
    """One milestone of the own study and two of the foreign study, found by uid
    alone as the real repository finds them; records every lookup and save."""

    def __init__(self):
        self.find_calls = []
        self.all_calls = []
        self.saved = []
        self.store = {
            _OWN_MILESTONE: lambda: _milestone_vo(_OWN, _OWN_MILESTONE, 1),
            _FOREIGN_MILESTONE: lambda: _milestone_vo(
                _FOREIGN, _FOREIGN_MILESTONE, 1, "CTTerm_DM1"
            ),
            _FOREIGN_MILESTONE_SIBLING: lambda: _milestone_vo(
                _FOREIGN, _FOREIGN_MILESTONE_SIBLING, 2
            ),
        }

    def create_ctlist_definition(self, _codelist_name):
        return {
            "CTTerm_DM1": {"name": "Diagnosis", "definition": "Synthetic"},
            "CTTerm_DM2": {"name": "Relapse", "definition": "Synthetic"},
        }

    def find_by_uid(self, uid):
        self.find_calls.append(uid)
        if uid in self.store:
            return self.store[uid]()
        raise ValidationException(
            msg=f"Study Disease Milestone with UID '{uid}' doesn't exist."
        )

    def find_all_disease_milestones_by_study(self, study_uid):
        self.all_calls.append(study_uid)
        items = [make() for make in self.store.values()]
        return sorted(
            (item for item in items if item.study_uid == study_uid),
            key=lambda item: item.order,
        )

    def save(self, item, delete_flag=False):
        item.uid = item.uid or f"StudyDiseaseMilestone_NEW_{len(self.saved) + 1}"
        self.saved.append((item.uid, item.study_uid, item.order, delete_flag))
        if delete_flag:
            self.store.pop(item.uid, None)
        return item


@pytest.fixture(name="milestones")
def fixture_milestones(monkeypatch, env):
    repo = FakeMilestoneRepo()
    meta = MagicMock()
    meta.study_disease_milestone_repository = repo
    monkeypatch.setattr(milestone_service, "MetaRepository", lambda: meta)
    monkeypatch.setattr(
        milestone_service, "user", lambda: MagicMock(id=lambda: "synthetic-user")
    )

    def acquire_write_lock_study_value(uid):
        env.write_locks.append(uid)

    monkeypatch.setattr(
        milestone_service,
        "acquire_write_lock_study_value",
        acquire_write_lock_study_value,
    )
    monkeypatch.setattr(
        UserInfoService,
        "get_author_username_from_id",
        classmethod(lambda cls, user_id: user_id),
    )
    # The bodies run as written; only the neomodel transaction is left out (p28).
    for name in ("find_by_uid", "create", "edit", "reorder", "delete"):
        monkeypatch.setattr(
            milestone_service.StudyDiseaseMilestoneService,
            name,
            getattr(milestone_service.StudyDiseaseMilestoneService, name).__wrapped__,
        )
    # The real _create_ctlist_map refills this module-level map; restore it after.
    types_before = dict(milestone_domain.StudyDiseaseMilestoneType)
    yield repo
    milestone_domain.StudyDiseaseMilestoneType.clear()
    milestone_domain.StudyDiseaseMilestoneType.update(types_before)


@pytest.fixture(name="route_check", params=["route check", "service check only"])
def fixture_route_check(request, monkeypatch):
    """Each C03 refusal is asserted twice: with the route child check of OSBI-3,
    and without it, where only the service checks of OSBI-6 stand."""
    if request.param == "service check only":
        monkeypatch.delitem(
            study_access.CHILD_PATH_PARAMETERS, "study_disease_milestone_uid"
        )
    return request.param


def _milestone_url(milestone_uid, study_uid=_OWN):
    return f"/studies/{study_uid}/study-disease-milestones/{milestone_uid}"


def _milestone_create_body(study_uid, milestone_type="CTTerm_DM1"):
    return {
        "study_uid": study_uid,
        "disease_milestone_type": milestone_type,
        "repetition_indicator": False,
    }


def _assert_no_milestone_reached(env, repo):
    assert not repo.find_calls
    assert not repo.all_calls
    assert not repo.saved
    assert not env.write_locks


_MILESTONE_REQUESTS = [
    ("GET", "", None),
    ("PATCH", "", {"repetition_indicator": True}),
    ("DELETE", "", None),
    ("PATCH", "/order", {"new_order": 2}),
]


@pytest.mark.parametrize("method,suffix,body", _MILESTONE_REQUESTS)
def test_c03_foreign_milestone_answers_the_404_of_an_unknown_uid(
    strict, milestones, client, route_check, method, suffix, body
):
    """The p28 requests: Study_000999 in the path and Study_000998's milestone
    StudyDiseaseMilestone_000777. Before OSBI-6, without the route check, GET
    returned that milestone, PATCH and /order rewrote it, and DELETE removed it
    and renumbered Study_000998's milestones."""
    foreign = client.request(
        method, _milestone_url(_FOREIGN_MILESTONE) + suffix, json=body
    )
    unknown = client.request(
        method, _milestone_url(_UNKNOWN_MILESTONE) + suffix, json=body
    )

    assert foreign.status_code == 404, (route_check, foreign.text)
    assert unknown.status_code == 404, (route_check, unknown.text)
    assert foreign.json()["type"] == unknown.json()["type"]
    assert foreign.json()["message"] == unknown.json()["message"].replace(
        _UNKNOWN_MILESTONE, _FOREIGN_MILESTONE
    )
    assert _MILESTONE_SECRET not in foreign.text
    assert _MILESTONE_SECRET_NAME not in foreign.text
    assert _FOREIGN not in foreign.text
    _assert_no_milestone_reached(strict, milestones)


def _service_arguments(operation, milestone_uid):
    return {
        "find_by_uid": {"uid": milestone_uid, "study_uid": _OWN},
        "edit": {
            "study_uid": _OWN,
            "study_disease_milestone_uid": milestone_uid,
            "study_disease_milestone_input": (
                milestone_models.StudyDiseaseMilestoneEditInput(
                    repetition_indicator=True
                )
            ),
        },
        "reorder": {
            "study_uid": _OWN,
            "study_disease_milestone_uid": milestone_uid,
            "new_order": 2,
        },
        "delete": {"study_uid": _OWN, "study_disease_milestone_uid": milestone_uid},
    }[operation]


@pytest.mark.parametrize("operation", ["find_by_uid", "edit", "reorder", "delete"])
def test_c03_service_refuses_a_foreign_milestone_before_any_lookup(
    strict, milestones, operation
):
    """A caller that reaches the service without the route (section 3.5) gets the
    refusal of an unknown uid, before the lock and before the repository."""
    service = milestone_service.StudyDiseaseMilestoneService()

    messages = []
    for milestone_uid in (_FOREIGN_MILESTONE, _UNKNOWN_MILESTONE):
        with pytest.raises(NotFoundException) as refused:
            getattr(service, operation)(**_service_arguments(operation, milestone_uid))
        messages.append(refused.value.msg)

    assert messages[0] == messages[1].replace(_UNKNOWN_MILESTONE, _FOREIGN_MILESTONE)
    assert _FOREIGN not in messages[0]
    _assert_no_milestone_reached(strict, milestones)


def test_c03_find_by_uid_without_a_study_is_refused_in_the_profile(strict, milestones):
    """A caller that names no study cannot resolve a milestone by uid alone."""
    service = milestone_service.StudyDiseaseMilestoneService()

    with pytest.raises(NotFoundException):
        service.find_by_uid(_OWN_MILESTONE)

    assert strict.owner_queries == [(_OWN_MILESTONE,)]
    assert not milestones.find_calls


@pytest.mark.parametrize("body_study", [_FOREIGN, _UNKNOWN, ""])
def test_c03_create_with_a_foreign_body_study_answers_404(
    strict, milestones, client, body_study
):
    """The create body's study_uid has to echo the path study, as the SPA sends
    it (DiseaseMilestoneForm.vue)."""
    response = client.post(
        f"/studies/{_OWN}/study-disease-milestones",
        json=_milestone_create_body(body_study),
    )

    assert response.status_code == 404, response.text
    assert "Study Definition" in response.text
    assert body_study in response.text
    _assert_no_milestone_reached(strict, milestones)


def test_c03_spa_requests_on_the_own_milestone_pass(monkeypatch, strict, milestones):
    """The SPA shape under the profile. With the request context of main.py the
    route check and the service check share one owner query per request."""
    caller = SyntheticUser()
    monkeypatch.setattr(vis, "_request_user", lambda: caller)
    client = _test_client(Middleware(RawContextMiddleware))

    read = client.get(_milestone_url(_OWN_MILESTONE))
    edited = client.patch(
        _milestone_url(_OWN_MILESTONE), json={"repetition_indicator": True}
    )
    reordered = client.patch(
        _milestone_url(_OWN_MILESTONE) + "/order", json={"new_order": 1}
    )
    deleted = client.delete(_milestone_url(_OWN_MILESTONE))
    created = client.post(
        f"/studies/{_OWN}/study-disease-milestones",
        json=_milestone_create_body(_OWN),
    )

    assert read.status_code == 200, read.text
    assert read.json()["study_uid"] == _OWN
    assert edited.status_code == 200, edited.text
    assert edited.json()["repetition_indicator"] is True
    assert reordered.status_code == 200, reordered.text
    assert deleted.status_code == 204, deleted.text
    assert created.status_code == 201, created.text
    assert created.json()["study_uid"] == _OWN
    assert strict.owner_queries == [(_OWN_MILESTONE,)] * 4
    assert milestones.find_calls == [_OWN_MILESTONE] * 4
    assert set(milestones.all_calls) == {_OWN}
    assert {study for _, study, _, _ in milestones.saved} == {_OWN}
    assert strict.write_locks == [_OWN] * 4


def test_c03_outside_the_profile_a_milestone_resolves_by_uid_alone(
    non_strict, milestones, client
):
    """The same calls as before OSBI-6: no owner query, and the repository is
    asked for the uid alone, also through the service without a study."""
    read = client.get(_milestone_url(_FOREIGN_MILESTONE))
    found = milestone_service.StudyDiseaseMilestoneService().find_by_uid(
        _FOREIGN_MILESTONE
    )

    assert read.status_code == 200, read.text
    assert read.json()["study_uid"] == _FOREIGN
    assert found.study_uid == _FOREIGN
    assert milestones.find_calls == [_FOREIGN_MILESTONE, _FOREIGN_MILESTONE]
    assert not non_strict.owner_queries


def test_c03_outside_the_profile_writes_are_unchanged(non_strict, milestones, client):
    deleted = client.delete(_milestone_url(_FOREIGN_MILESTONE))
    created = client.post(
        f"/studies/{_OWN}/study-disease-milestones",
        json=_milestone_create_body(_FOREIGN),
    )

    assert deleted.status_code == 204, deleted.text
    assert created.status_code == 201, created.text
    assert created.json()["study_uid"] == _OWN
    assert milestones.find_calls == [_FOREIGN_MILESTONE]
    assert milestones.all_calls == [_FOREIGN, _OWN]
    assert milestones.saved == [
        (_FOREIGN_MILESTONE, _FOREIGN, 1, True),
        (_FOREIGN_MILESTONE_SIBLING, _FOREIGN, 1, False),
        ("StudyDiseaseMilestone_NEW_3", _OWN, 2, False),
    ]
    assert non_strict.write_locks == [_OWN, _OWN]


# The registry declaration


def test_guarded_fields_name_real_fields_and_cases():
    cases = globals()
    for (model_name, field), names in GUARDED_FIELDS.items():
        model = getattr(epoch_models, model_name, None) or getattr(
            milestone_models, model_name
        )
        assert field in set(model.model_fields)
        assert names
        for name in names:
            assert callable(cases.get(name)), name
