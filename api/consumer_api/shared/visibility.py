"""Study visibility for the consumer API (plan W2.3, 2026-09-21).

The consumer API served every study to any caller holding ``Study.Read``: its routes
never consulted the delegated study scope the study routers enforce through
``enforce_visible_study``. Two dependencies and one filter close that gap with the
same rules (``clinical_mdr_api.services.studies.study_visibility``), so a caller in
delegated mode sees exactly the studies assigned to it and a study outside the
assignment answers 404, as it does everywhere else. The rules are read through the
module at call time, never bound at import, so the one implementation stays the
one that is configured (and monkeypatched) at runtime.

OP-OSB1 (2026-10-25) closed the three study routes that still had no scope: the
schedule replacement (POST ``/studies/{uid}/soa``) needs write on that study, study
creation (POST ``/studies``) has no delegated study contract and fails closed as the
main API's POST ``/studies`` does, and the study-metadata read resolves its studies
by project and number and then needs read on each of them.
"""

from fastapi import Request
from neomodel import db

from clinical_mdr_api.services.studies import study_visibility as visibility
from common.auth.dependencies import security
from common.exceptions import NotFoundException


def enforce_visible_consumer_study(request: Request, _auth=security) -> None:
    """A ``/studies/{uid}/...`` route must name a study the caller may read."""
    uid = str(request.path_params.get("uid", "") or "").strip()
    if uid:
        visibility.assert_study_uid_visible(uid, require_write=False)


def enforce_writable_consumer_study(request: Request, _auth=security) -> None:
    """A writing ``/studies/{uid}/...`` route must name a study the caller may write."""
    uid = str(request.path_params.get("uid", "") or "").strip()
    if uid:
        visibility.assert_study_uid_visible(uid, require_write=True)


def _study_roots_by_number(
    project_id: str, study_number: str, subpart_acronym: str | None
) -> list[str]:
    """Every study root with any value carrying this project and study number.

    The study-metadata listing matches released values by project and number (and
    subpart when given) through several queries, and an older value of a root can
    carry a number the root no longer has. Every root any of them could read is
    returned, so the scope check below covers each one, not only the root whose
    latest value matches."""
    subpart_filter = (
        "AND value.study_subpart_acronym = $subpart" if subpart_acronym else ""
    )
    rows, _ = db.cypher_query(
        f"""
        MATCH (root:StudyRoot)-->(value:StudyValue)
        WHERE value.study_id_prefix = $project
          AND value.study_number = $study_number
          {subpart_filter}
        RETURN DISTINCT root.uid
        ORDER BY root.uid
        """,
        {
            "project": project_id,
            "study_number": study_number,
            "subpart": subpart_acronym,
        },
    )
    return [str(row[0]) for row in rows if row and row[0]]


def enforce_visible_consumer_study_by_number(request: Request, _auth=security) -> None:
    """A route that selects its study by ``project_id`` and ``study_number`` must
    resolve only to studies the caller may read. A study outside the assignment
    answers the same 404 as a study that does not exist, so the read does not
    disclose which project and number pairs exist in other tenants. A request
    without both selectors is left to the route's own validation, which refuses it.
    The values are used exactly as the route receives them."""
    project_id = request.query_params.get("project_id")
    study_number = request.query_params.get("study_number")
    if project_id is None or study_number is None:
        return
    subpart_acronym = request.query_params.get("subpart_acronym")
    # The caller's own authority is checked first, so a caller without read
    # authority learns nothing about which studies exist.
    visibility._assert_operation_scope(  # pylint: disable=protected-access
        visibility._request_user(),  # pylint: disable=protected-access
        False,
    )
    not_found = (
        f"Study id {project_id}-{study_number}{subpart_acronym or ''} not found."
    )
    study_uids = _study_roots_by_number(project_id, study_number, subpart_acronym)
    NotFoundException.raise_if(not study_uids, msg=not_found)
    try:
        for study_uid in study_uids:
            visibility.assert_study_uid_visible(study_uid, require_write=False)
    except NotFoundException as exc:
        raise NotFoundException(msg=not_found) from exc


def enforce_consumer_collection_write_scope(request: Request, _auth=security) -> None:
    """A writing collection route (POST ``/studies``) has no exact delegated study
    boundary: in delegated mode it fails closed, exactly as the main API's study
    create does."""
    visibility.assert_collection_scope(require_write=True, route_path=request.url.path)


def enforce_consumer_collection_scope(request: Request, _auth=security) -> None:
    """A collection route with no study in its path is admitted only when it
    has a proven per-assignment read (the ``/studies`` root); the audit-trail and
    papillon SoA surfaces are legacy cross-study reads and fail closed in
    delegated mode, exactly as the study routers' collection routes do."""
    visibility.assert_collection_scope(require_write=False, route_path=request.url.path)


def _study_uid(study) -> str:
    value = study.get("uid") if isinstance(study, dict) else getattr(study, "uid", "")
    return str(value or "").strip()


def visible_consumer_studies(studies):
    """Keep the rows of ``/v1/studies`` the caller may read. Rows are the consumer
    database's dicts (``uid``, ``version_author`` when present)."""
    caller = visibility._request_user()  # pylint: disable=protected-access
    return [
        study
        for study in studies
        if visibility.study_visible_to_user(
            caller,
            visibility.catalog_item_author(study) if isinstance(study, dict) else None,
            study_uid=_study_uid(study),
        )
    ]
