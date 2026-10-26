"""Shared FastAPI dependency: nested study routes must match catalog visibility.

`security` is a sub-dependency so the JWT/user is in context before we decide.
"""

from fastapi import Request

from clinical_mdr_api.models.utils import CustomPage
from clinical_mdr_api.services.studies.study_visibility import (
    assert_study_authority,
    assert_study_uid_visible,
    assert_tied_to_study,
    caller_is_catalog_admin,
    delegated_study_scope_required,
)
from common.auth.dependencies import security
from common.exceptions import NotFoundException

# These are the study-root parameters used by the registered study routers.
# Child IDs such as study_standard_version_uid and study_visit_uid belong to
# objects inside that study and are never looked up as StudyRoot identities.
# Under the delegated profile the route checks that the path study owns every
# child path uid (D-45, CHILD_PATH_PARAMETERS below); each child service still
# resolves its object inside the authorized parent.
STUDY_ROOT_PATH_PARAMETERS = frozenset({"study_uid", "target_study_uid"})
# Copy routes name a second study that is only read: study_source_uid in the
# path (copy-from-study) and reference_study_uid in the query (copy-component).
# The caller must be able to read it, whatever the method of the route.
SOURCE_STUDY_PATH_PARAMETERS = frozenset({"study_source_uid"})
SOURCE_STUDY_QUERY_PARAMETERS = frozenset({"reference_study_uid"})
# Path parameters that name a child of the path study (a StudySelection), with
# the resource name a refusal uses. The check matches owners, not labels, so a
# misleading name can never refuse a uid of the caller's own study.
CHILD_PATH_PARAMETERS = {
    "study_epoch_uid": "Study Epoch",
    "study_visit_uid": "Study Visit",
    "study_arm_uid": "Study Arm",
    "arm_uid": "Study Arm",
    "study_branch_arm_uid": "Study Branch Arm",
    "branch_arm_uid": "Study Branch Arm",
    "study_cohort_uid": "Study Cohort",
    "study_element_uid": "Study Element",
    "study_design_cell_uid": "Study Design Cell",
    "study_objective_uid": "Study Objective",
    "study_endpoint_uid": "Study Endpoint",
    "study_criteria_uid": "Study Criteria",
    "study_activity_uid": "Study Activity",
    # The activity request route patches a StudyActivity.
    "study_activity_request_uid": "Study Activity",
    "study_activity_instance_uid": "Study Activity Instance",
    "study_activity_schedule_uid": "Study Activity Schedule",
    "study_activity_instruction_uid": "Study Activity Instruction",
    "study_activity_group_uid": "Study Activity Group",
    "study_activity_subgroup_uid": "Study Activity Subgroup",
    "study_soa_group_uid": "Study SoA Group",
    "study_soa_footnote_uid": "Study SoA Footnote",
    "study_compound_uid": "Study Compound",
    "study_compound_dosing_uid": "Study Compound Dosing",
    "study_disease_milestone_uid": "Study Disease Milestone",
    "study_data_supplier_uid": "Study Data Supplier",
    "study_standard_version_uid": "Study Standard Version",
    "control_pool_uid": "Study Control Pool",
    "adaptation_reference_uid": "Study Adaptation Reference",
}
# Path parameters that are not a study child: a visit group resolved inside the
# study's visits, a CT term, a library tag, an ADaM report name, an order and
# two version numbers.
NON_CHILD_PATH_PARAMETERS = frozenset(
    {
        "consecutive_visit_group_uid",
        "ts_parameter_term_uid",
        "uid",
        "adam_report",
        "new_order",
        "from_version",
        "to_version",
    }
)


def _refuse_blank_or_padded_study_path(request: Request, require_write: bool) -> None:
    """Delegated profile only: a blank path study uid, or one that stripping would
    change, answers the 404 of a study that does not exist, so the checks and the
    handler see one value (the 9dd1f75d rule of the consumer API). The caller's own
    authority is checked first, so a caller without it gets its 403 whatever the
    uid."""
    if not delegated_study_scope_required():
        return
    for key in sorted(STUDY_ROOT_PATH_PARAMETERS):
        if key not in request.path_params:
            continue
        received = str(request.path_params[key])
        if not received.strip() or received != received.strip():
            assert_study_authority(require_write=require_write)
            raise NotFoundException("Study Definition", received)


def enforce_visible_study(request: Request, _auth=security) -> None:
    require_write = request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
    _refuse_blank_or_padded_study_path(request, require_write)
    study_path_values = {
        str(value).strip()
        for key, value in request.path_params.items()
        if key in STUDY_ROOT_PATH_PARAMETERS and str(value).strip()
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

        # Included routers may expose the local route template ("/list") in
        # scope["route"], without the "/studies" mount prefix. Authorize the
        # concrete path that FastAPI matched, including its application prefix.
        route_path = request.url.path
        assert_collection_scope(
            require_write=require_write,
            route_path=route_path,
            path_parameters=request.path_params,
        )
    source_study_values = {
        str(value).strip()
        for key, value in request.path_params.items()
        if key in SOURCE_STUDY_PATH_PARAMETERS and str(value).strip()
    }
    # Every repeated query value is checked, not only the one FastAPI binds.
    source_study_values.update(
        value.strip()
        for key in SOURCE_STUDY_QUERY_PARAMETERS
        for value in request.query_params.getlist(key)
        if value.strip()
    )
    for study_uid in sorted(source_study_values - study_path_values):
        assert_study_uid_visible(study_uid, require_write=False)
    # The children of the path study, all in one owner query (ownership only;
    # nothing outside the delegated profile).
    children: dict[str, list[str]] = {}
    for name, value in request.path_params.items():
        if name in CHILD_PATH_PARAMETERS:
            children.setdefault(CHILD_PATH_PARAMETERS[name], []).append(str(value))
    if children and "study_uid" in request.path_params:
        assert_tied_to_study(str(request.path_params["study_uid"]), children=children)


def empty_cross_study_page(page_number: int, page_size: int):
    """Non-admins must not receive mixed-study listings from other accounts."""
    if caller_is_catalog_admin():
        return None
    return CustomPage(items=[], total=0, page=page_number, size=page_size)
