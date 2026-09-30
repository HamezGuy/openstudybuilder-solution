"""Study visibility for the consumer API (plan W2.3, 2026-09-21).

The consumer API served every study to any caller holding ``Study.Read``: its routes
never consulted the delegated study scope the study routers enforce through
``enforce_visible_study``. Two dependencies and one filter close that gap with the
same rules (``clinical_mdr_api.services.studies.study_visibility``), so a caller in
delegated mode sees exactly the studies assigned to it and a study outside the
assignment answers 404, as it does everywhere else. The rules are read through the
module at call time, never bound at import, so the one implementation stays the
one that is configured (and monkeypatched) at runtime.
"""

from fastapi import Request

from clinical_mdr_api.services.studies import study_visibility as visibility
from common.auth.dependencies import security


def enforce_visible_consumer_study(request: Request, _auth=security) -> None:
    """A ``/studies/{uid}/...`` route must name a study the caller may read."""
    uid = str(request.path_params.get("uid", "") or "").strip()
    if uid:
        visibility.assert_study_uid_visible(uid, require_write=False)


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
