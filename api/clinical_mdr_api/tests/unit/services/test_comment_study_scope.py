"""OSBI-20 (D-45, 2026-10-26), OSB_ISOLATION_DESIGN.md sections 2.4, 5.3 and 8: the
comment topics, the comment lists and the thread create (C06).

Under the delegated profile every comment service entry first checks the
caller's study read authority, so a Library-only token gets 403 before any
lookup. A topic must name a study (``^/?studies/(?P<uid>[^/]+)(?:/|$)``), else
403; a study the caller cannot read answers the 404 of an unknown study, naming
the uid the caller typed, before the repository runs. A topic filter then
matches case-sensitively, with equality or STARTS WITH, and a bare
``studies/{uid}`` never matches another study whose uid it prefixes. A list
without a topic is restricted inside both queries to the caller's visible
studies, and an empty scope answers an empty page without a query. A thread
POST checks its topic's study, and a study-activity topic its child, before any
uid is generated or anything is saved. Outside the profile the repository
receives the same arguments as before. Over HTTP through the real comments
router and service bodies, in the shape of verifier probe test_zz_verify_C06;
only the repository, the user and neomodel's transaction are faked. Synthetic
studies only.
"""

import datetime
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware import Middleware
from starlette_context.middleware import RawContextMiddleware

from clinical_mdr_api.domain_repositories.comments import comments_repository
from clinical_mdr_api.domains.comments.comments import CommentThreadAR, CommentTopicAR
from clinical_mdr_api.routers.comments.comments import router as comments_router
from clinical_mdr_api.services.comments import comments as comments_service
from clinical_mdr_api.services.studies import study_visibility as vis
from common.auth import rbac
from common.auth.dependencies import security
from common.exception_handlers import register_exception_handlers

_OWN = "Study_000999"
_FOREIGN = "Study_000998"
_OWN_ACTIVITY = "StudyActivity_000011"
_FOREIGN_ACTIVITY = "StudyActivity_000777"
_OWN_PATH = f"studies/{_OWN}/study-activities/{_OWN_ACTIVITY}"
_FOREIGN_PATH = f"studies/{_FOREIGN}/study-activities/{_FOREIGN_ACTIVITY}"
_SECRET = "FOREIGN-TENANT-CONFIDENTIAL-COMMENT"
_NO_STUDY = "A comment topic must name a study in delegated mode."
# Fifty assigned studies: the own one and 49 bound to another tenant.
_FIFTY = {_OWN, *(f"Study_{n:06d}" for n in range(1000, 1049))}


class SyntheticUser:
    tenant_id = "tenant-synthetic"
    study_ids = {_OWN}
    purpose = "interactive-domain-access"
    capabilities = {"study:read", "study:write"}
    roles = {"Study.Read", "Study.Write"}


class LibraryOnlyUser(SyntheticUser):
    capabilities = {"library:read", "library:write"}
    roles = {"Library.Read", "Library.Write"}


def _scope(uid):
    return (
        (True, "tenant-synthetic", "active")
        if uid == _OWN
        else (True, "another-tenant", "active")
    )


def _owners(uids):
    owner_by_uid = {_OWN_ACTIVITY: {_OWN}, _FOREIGN_ACTIVITY: {_FOREIGN}}
    return {uid: frozenset(owner_by_uid.get(uid, ())) for uid in uids}


def _never(*_args, **_kwargs):
    raise AssertionError("no lookup may run here")


def _thread(uid, topic_path, text):
    return CommentThreadAR.from_repository_values(
        uid=uid,
        text=text,
        topic_path=topic_path,
        author_id="synthetic-author",
        author_display_name="Synthetic Author",
        status="ACTIVE",
        created_at=datetime.datetime(2026, 10, 1),
    )


def _repo():
    repo = MagicMock()
    repo.find_all_comment_topics.side_effect = lambda **kw: (
        [
            CommentTopicAR.from_repository_values(
                uid="CommentTopic_000001", topic_path=_OWN_PATH, threads_active_count=1
            )
        ],
        1,
    )
    repo.find_all_comment_threads.side_effect = lambda **kw: (
        [_thread("CommentThread_000001", _OWN_PATH, "own thread")],
        1,
    )
    repo.find_topic_by_path.return_value = None
    repo.generate_thread_uid.return_value = "CommentThread_000900"
    repo.generate_topic_uid.return_value = "CommentTopic_000900"
    saved = {}
    repo.save_comment_thread.side_effect = lambda ar: saved.__setitem__("thread", ar)
    repo.find_comment_thread_by_uid.side_effect = lambda uid: (
        saved["thread"] if "thread" in saved and saved["thread"].uid == uid else None
    )
    return repo


class _Env:
    def __init__(self):
        self.repo = _repo()
        self.scope_queries = []
        self.owner_queries = []


@pytest.fixture(name="env")
def fixture_env(monkeypatch):
    env = _Env()
    meta = MagicMock()
    meta.comments_repository = env.repo
    monkeypatch.setattr(comments_service, "MetaRepository", lambda: meta)
    monkeypatch.setattr(
        comments_service,
        "user",
        lambda: SimpleNamespace(id=lambda: "synthetic-user", name="Synthetic User"),
    )
    # The bodies run as written; only the neomodel transaction is left out (probe C06).
    for name in (
        "create_comment_thread",
        "edit_comment_thread",
        "delete_comment_thread",
        "create_comment_reply",
        "edit_comment_thread_reply",
        "delete_comment_reply",
    ):
        method = getattr(comments_service.CommentsService, name)
        monkeypatch.setattr(
            comments_service.CommentsService,
            name,
            getattr(method, "__wrapped__", method),
        )
    return env


def _strict_profile(monkeypatch, env, caller):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", True)
    monkeypatch.setattr(vis, "_request_user", lambda: caller)
    monkeypatch.setattr(vis, "_study_scope", _scope)

    def study_scopes(uids):
        env.scope_queries.append(tuple(uids))
        return {uid: _scope(uid) for uid in uids}

    def study_child_owners(uids):
        env.owner_queries.append(tuple(uids))
        return _owners(uids)

    monkeypatch.setattr(vis, "_study_scopes", study_scopes)
    monkeypatch.setattr(vis, "_study_child_owners", study_child_owners)


@pytest.fixture(name="strict")
def fixture_strict(monkeypatch, env):
    _strict_profile(monkeypatch, env, SyntheticUser())
    return env


@pytest.fixture(name="non_strict")
def fixture_non_strict(monkeypatch, env):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", False)
    monkeypatch.setattr(vis, "_request_user", lambda: None)
    monkeypatch.setattr(vis, "_study_scope", _never)
    monkeypatch.setattr(vis, "_study_scopes", _never)
    monkeypatch.setattr(vis, "_study_child_owners", _never)
    return env


@pytest.fixture(name="client")
def fixture_client():
    # RawContextMiddleware as in main.py, so the request memo of study_visibility runs.
    app = FastAPI(middleware=[Middleware(RawContextMiddleware)])
    register_exception_handlers(app)
    app.include_router(comments_router)
    app.dependency_overrides[security.dependency] = lambda: None
    app.dependency_overrides[rbac.ANY.dependency] = lambda: None
    return TestClient(app, raise_server_exceptions=False)


def _assert_no_lookup(env):
    assert env.repo.mock_calls == []
    assert env.scope_queries == []
    assert env.owner_queries == []


def _assert_unknown_study(resp, uid):
    assert resp.status_code == 404, resp.text
    assert resp.json()["message"] == f"Study Definition with UID '{uid}' doesn't exist."
    assert _SECRET not in resp.text


# GET /comment-topics and GET /comment-threads with a topic


@pytest.mark.parametrize(
    "topic_path, partial",
    [
        (_FOREIGN_PATH, False),
        (f"studies/{_FOREIGN}/study-activities", True),
        (f"studies/{_FOREIGN}", True),
        (f"/studies/{_FOREIGN}/study-activities", True),
        (f"studies/{_FOREIGN.lower()}/study-activities", True),
    ],
)
@pytest.mark.parametrize("route", ["/comment-topics", "/comment-threads"])
def test_a_list_with_a_foreign_topic_answers_404_before_the_repository(
    strict, client, route, topic_path, partial
):
    resp = client.get(
        route,
        params={"topic_path": topic_path, "topic_path_partial_match": partial},
    )
    typed = topic_path.lstrip("/").split("/")[1]
    _assert_unknown_study(resp, typed)
    strict.repo.find_all_comment_topics.assert_not_called()
    strict.repo.find_all_comment_threads.assert_not_called()


@pytest.mark.parametrize(
    "topic_path", ["studies/", "studies", "/studies/", "/Topic/0", "library/x"]
)
@pytest.mark.parametrize("route", ["/comment-topics", "/comment-threads"])
def test_a_list_topic_that_names_no_study_answers_403(
    strict, client, route, topic_path
):
    resp = client.get(
        route, params={"topic_path": topic_path, "topic_path_partial_match": True}
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["message"] == _NO_STUDY
    _assert_no_lookup(strict)


def test_topics_of_the_own_study_match_case_sensitively(strict, client):
    """The SPA's study-activities prefix (StudyActivityTable.vue:437-439)."""
    prefix = f"studies/{_OWN}/study-activities"
    resp = client.get(
        "/comment-topics",
        params={"topic_path": prefix, "topic_path_partial_match": True},
    )
    assert resp.status_code == 200, resp.text
    assert [item["topic_path"] for item in resp.json()["items"]] == [_OWN_PATH]
    kwargs = strict.repo.find_all_comment_topics.call_args.kwargs
    assert kwargs == {
        "topic_path": prefix,
        "topic_path_partial_match": True,
        "page_number": 1,
        "page_size": 10,
        "exact_case": True,
    }


def test_threads_of_the_own_topic_match_case_sensitively(strict, client):
    """The SPA's thread dialog asks for one exact topic (api/comments.js:7-11)."""
    resp = client.get("/comment-threads", params={"topic_path": _OWN_PATH})
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"][0]["text"] == "own thread"
    kwargs = strict.repo.find_all_comment_threads.call_args.kwargs
    assert kwargs == {
        "topic_path": _OWN_PATH,
        "topic_path_partial_match": False,
        "status": None,
        "page_number": 1,
        "page_size": 10,
        "exact_case": True,
    }


# Lists without a topic


@pytest.mark.parametrize(
    "route, method",
    [
        ("/comment-topics", "find_all_comment_topics"),
        ("/comment-threads", "find_all_comment_threads"),
    ],
)
def test_a_list_without_a_topic_sends_only_the_visible_study_prefixes(
    monkeypatch, env, client, route, method
):
    caller = SyntheticUser()
    caller.study_ids = set(_FIFTY)
    _strict_profile(monkeypatch, env, caller)

    resp = client.get(route)

    assert resp.status_code == 200, resp.text
    kwargs = getattr(env.repo, method).call_args.kwargs
    assert kwargs["topic_path"] is None
    assert kwargs["exact_case"] is True
    assert kwargs["topic_prefixes"] == [f"studies/{_OWN}", f"/studies/{_OWN}"]
    # One batched scope query for the whole 50-study token.
    assert len(env.scope_queries) == 1
    assert set(env.scope_queries[0]) == _FIFTY


@pytest.mark.parametrize(
    "route, method",
    [
        ("/comment-topics", "find_all_comment_topics"),
        ("/comment-threads", "find_all_comment_threads"),
    ],
)
def test_a_list_without_a_topic_and_no_visible_study_is_empty(
    monkeypatch, env, client, route, method
):
    caller = SyntheticUser()
    caller.study_ids = {_FOREIGN}
    _strict_profile(monkeypatch, env, caller)

    resp = client.get(route)

    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == [] and resp.json()["total"] == 0
    getattr(env.repo, method).assert_not_called()


# POST /comment-threads


def _assert_nothing_created(env):
    for name in (
        "find_topic_by_path",
        "generate_thread_uid",
        "generate_topic_uid",
        "save_comment_topic",
        "save_comment_thread",
    ):
        getattr(env.repo, name).assert_not_called()


@pytest.mark.parametrize(
    "topic_path",
    [
        _FOREIGN_PATH,
        f"/studies/{_FOREIGN}/study-activities/{_FOREIGN_ACTIVITY}",
        f"studies/{_FOREIGN}",
        f"studies/{_FOREIGN.lower()}/anything",
    ],
)
def test_a_thread_post_on_a_foreign_topic_answers_404_with_no_save(
    strict, client, topic_path
):
    resp = client.post(
        "/comment-threads", json={"text": "planted", "topic_path": topic_path}
    )
    _assert_unknown_study(resp, topic_path.lstrip("/").split("/")[1])
    _assert_nothing_created(strict)


def test_a_thread_post_on_another_study_activity_answers_404_with_no_save(
    strict, client
):
    topic_path = f"studies/{_OWN}/study-activities/{_FOREIGN_ACTIVITY}"
    resp = client.post(
        "/comment-threads", json={"text": "planted", "topic_path": topic_path}
    )
    assert resp.status_code == 404, resp.text
    assert (
        resp.json()["message"]
        == f"Study Activity with UID '{_FOREIGN_ACTIVITY}' doesn't exist."
    )
    assert strict.owner_queries == [(_FOREIGN_ACTIVITY,)]
    _assert_nothing_created(strict)


@pytest.mark.parametrize("topic_path", ["studies/", "/Topic/0"])
def test_a_thread_post_on_a_topic_without_a_study_answers_403(
    strict, client, topic_path
):
    resp = client.post(
        "/comment-threads", json={"text": "planted", "topic_path": topic_path}
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["message"] == _NO_STUDY
    _assert_no_lookup(strict)


def test_a_thread_post_on_an_own_study_activity_is_created(strict, client):
    """The SPA's comment dialog (StudyActivityTable.vue:229, CommentAdd.vue:57)."""
    resp = client.post(
        "/comment-threads", json={"text": "own comment", "topic_path": _OWN_PATH}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["topic_path"] == _OWN_PATH
    strict.repo.save_comment_topic.assert_called_once()
    strict.repo.save_comment_thread.assert_called_once()
    assert strict.owner_queries == [(_OWN_ACTIVITY,)]


# Authority first


def _every_route(client):
    thread = "CommentThread_000001"
    reply = "CommentReply_000001"
    return [
        client.get("/comment-topics"),
        client.get("/comment-topics", params={"topic_path": _OWN_PATH}),
        client.get("/comment-threads"),
        client.get("/comment-threads", params={"topic_path": _FOREIGN_PATH}),
        client.post("/comment-threads", json={"text": "x", "topic_path": _OWN_PATH}),
        client.get(f"/comment-threads/{thread}"),
        client.patch(f"/comment-threads/{thread}", json={"status": "RESOLVED"}),
        client.delete(f"/comment-threads/{thread}"),
        client.get(f"/comment-threads/{thread}/replies"),
        client.post(
            f"/comment-threads/{thread}/replies",
            json={"text": "x", "thread_status": "RESOLVED"},
        ),
        client.get(f"/comment-threads/{thread}/replies/{reply}"),
        client.patch(f"/comment-threads/{thread}/replies/{reply}", json={"text": "y"}),
        client.delete(f"/comment-threads/{thread}/replies/{reply}"),
    ]


def test_a_library_only_token_gets_403_on_every_comment_route_before_any_lookup(
    monkeypatch, env, client
):
    _strict_profile(monkeypatch, env, LibraryOnlyUser())
    for resp in _every_route(client):
        assert resp.status_code == 403, (
            resp.request.method,
            resp.request.url,
            resp.text,
        )
        assert resp.json()["message"] == "study:read capability is required."
    _assert_no_lookup(env)


# Outside the delegated profile


def test_outside_the_profile_the_repository_arguments_are_identical(non_strict, client):
    resp = client.get(
        "/comment-topics",
        params={"topic_path": "studies/", "topic_path_partial_match": True},
    )
    assert resp.status_code == 200, resp.text
    assert non_strict.repo.find_all_comment_topics.call_args.kwargs == {
        "topic_path": "studies/",
        "topic_path_partial_match": True,
        "page_number": 1,
        "page_size": 10,
    }
    resp = client.get(
        "/comment-threads",
        params={"topic_path": _FOREIGN, "topic_path_partial_match": True},
    )
    assert resp.status_code == 200, resp.text
    assert non_strict.repo.find_all_comment_threads.call_args.kwargs == {
        "topic_path": _FOREIGN,
        "topic_path_partial_match": True,
        "status": None,
        "page_number": 1,
        "page_size": 10,
    }
    resp = client.get("/comment-threads")
    assert resp.status_code == 200, resp.text
    assert non_strict.repo.find_all_comment_threads.call_args.kwargs == {
        "topic_path": None,
        "topic_path_partial_match": False,
        "status": None,
        "page_number": 1,
        "page_size": 10,
    }


def test_outside_the_profile_a_thread_post_is_created_as_today(non_strict, client):
    resp = client.post(
        "/comment-threads", json={"text": "as today", "topic_path": _FOREIGN_PATH}
    )
    assert resp.status_code == 201, resp.text
    non_strict.repo.find_topic_by_path.assert_called_once_with(_FOREIGN_PATH)
    non_strict.repo.save_comment_thread.assert_called_once()


# The repository clauses


@pytest.fixture(name="cypher")
def fixture_cypher(monkeypatch):
    calls = []

    def cypher_query(query, params):
        calls.append((query, dict(params)))
        return ([[0]], ["total"]) if "RETURN COUNT" in query else ([], [])

    monkeypatch.setattr(comments_repository.db, "cypher_query", cypher_query)
    return calls


_FINDERS = ["find_all_comment_threads", "find_all_comment_topics"]


def _where(query):
    """The first WHERE of a query, up to the next line that starts WITH or RETURN."""
    return re.split(r"\n\s*(?:WITH|RETURN)\b", query.split("WHERE", 1)[1])[0]


def _find(method, **kwargs):
    return getattr(comments_repository.CommentsRepository(), method)(**kwargs)


@pytest.mark.parametrize("method", _FINDERS)
def test_repository_defaults_keep_todays_clauses(cypher, method):
    _find(method, topic_path="studies/X", topic_path_partial_match=True)
    _find(method, topic_path="studies/X")
    _find(method)
    partial, exact, unfiltered = cypher[0:2], cypher[2:4], cypher[4:6]
    for query, params in partial:
        assert "AND toLower(topic.topic_path) CONTAINS toLower($topic_path)" in query
        assert set(params) == {"topic_path", "skip", "limit"}
    for query, _ in exact:
        assert "AND toLower(topic.topic_path) = toLower($topic_path)" in query
    for query, params in unfiltered:
        assert "topic.topic_path" not in _where(query)
        assert params["topic_path"] is None
    for query, _ in cypher:
        assert "STARTS WITH" not in query and "topic_prefixes" not in query


@pytest.mark.parametrize("method", _FINDERS)
def test_repository_exact_case_matches_with_equality_or_starts_with(cypher, method):
    _find(method, topic_path=_OWN_PATH, exact_case=True)
    _find(
        method,
        topic_path=f"studies/{_OWN}/study-activities",
        topic_path_partial_match=True,
        exact_case=True,
    )
    _find(
        method,
        topic_path=f"studies/{_OWN}",
        topic_path_partial_match=True,
        exact_case=True,
    )
    _find(
        method,
        topic_path=f"/studies/{_OWN}/",
        topic_path_partial_match=True,
        exact_case=True,
    )
    clauses = [
        "AND topic.topic_path = $topic_path ",
        "AND topic.topic_path STARTS WITH $topic_path ",
        "AND (topic.topic_path = $topic_path "
        "OR topic.topic_path STARTS WITH $topic_path + '/') ",
        "AND topic.topic_path STARTS WITH $topic_path ",
    ]
    for index, clause in enumerate(clauses):
        page, count = cypher[2 * index], cypher[2 * index + 1]
        for query, _ in (page, count):
            assert clause in query
            assert "toLower" not in _where(query)


@pytest.mark.parametrize("method", _FINDERS)
def test_repository_topic_prefixes_restrict_page_and_count(cypher, method):
    prefixes = [f"studies/{_OWN}", f"/studies/{_OWN}"]
    _find(method, topic_prefixes=prefixes, exact_case=True)
    clause = (
        "AND any(prefix IN $topic_prefixes WHERE topic.topic_path = prefix "
        "OR topic.topic_path STARTS WITH prefix + '/') "
    )
    assert len(cypher) == 2
    for query, params in cypher:
        assert clause in query
        assert params["topic_prefixes"] == prefixes
