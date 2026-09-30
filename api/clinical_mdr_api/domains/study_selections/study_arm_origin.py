"""Explicit native study-arm data origin; absence never implies an origin."""

from common.exceptions import ValidationException

STUDY_ARM_ORIGIN_CATALOGUE = "DDF CT"
STUDY_ARM_ORIGIN_CODELIST = "Study Arm Data Origin Type Value Set Terminology"
STUDY_ARM_ORIGIN_CODELIST_UID = "C188727"


def validate_study_arm_origin(
    term_uid: str | None, description: str | None
) -> None:
    if term_uid is None and description is None:
        return
    if (
        not isinstance(term_uid, str)
        or not term_uid.strip()
        or not isinstance(description, str)
        or not description.strip()
    ):
        raise ValidationException(
            msg="STUDY_ARM_DATA_ORIGIN_PAIR_REQUIRED: provide both an explicit "
            "data_origin_type_uid and a non-empty data_origin_description, "
            "or clear both fields."
        )
