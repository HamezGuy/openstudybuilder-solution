"""Shared FastAPI dependency: nested study routes must match catalog visibility.

`security` is a sub-dependency so the JWT/user is in context before we decide.
"""

from fastapi import Request

from clinical_mdr_api.models.utils import CustomPage
from clinical_mdr_api.services.studies.study_visibility import (
    assert_study_uid_visible,
    caller_is_catalog_admin,
)
from common.auth.dependencies import security


def enforce_visible_study(request: Request, _auth=security) -> None:
    require_write = request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
    study_path_values = {
        str(value).strip()
        for key, value in request.path_params.items()
        if "study" in key.lower()
        and (key.lower().endswith("uid") or key.lower().endswith("id"))
        and str(value).strip()
    }
    if study_path_values:
        for study_uid in sorted(study_path_values):
            assert_study_uid_visible(
                study_uid,
                require_write=require_write,
            )
    else:
        # Only explicitly classified collection routes may run without a path
        # study. The root list performs exact per-assignment reads; legacy
        # cross-study surfaces fail closed in delegated mode.
        from clinical_mdr_api.services.studies.study_visibility import (
            assert_collection_scope,
        )

        route = request.scope.get("route")
        route_path = getattr(route, "path", None) or request.url.path
        assert_collection_scope(
            require_write=require_write,
            route_path=route_path,
        )


def empty_cross_study_page(page_number: int, page_size: int):
    """Non-admins must not receive mixed-study listings from other accounts."""
    if caller_is_catalog_admin():
        return None
    return CustomPage(items=[], total=0, page=page_number, size=page_size)
