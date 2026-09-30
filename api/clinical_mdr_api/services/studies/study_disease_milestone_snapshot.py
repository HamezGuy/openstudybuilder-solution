"""Resolve milestone text at an actual selected native study snapshot."""

from datetime import datetime
from typing import Any

from neo4j.time import DateTime

from clinical_mdr_api.domain_repositories.models.controlled_terminology import CTTermRoot
from clinical_mdr_api.models.study_selections.study_disease_milestone import (
    StudyDiseaseMilestone,
    StudyDiseaseMilestoneTermSource,
    StudyDiseaseMilestoneTermValue,
)
from clinical_mdr_api.services.studies.study_compound_snapshot import (
    StudyCompoundSnapshotReader,
    StudyCompoundSourceError,
)
from clinical_mdr_api.services.user_info import UserInfoService
from common.exceptions import ValidationException
from common.utils import convert_to_datetime


class StudyDiseaseMilestoneSourceError(ValidationException):
    status_code = 422


def _native(value: Any) -> Any:
    if isinstance(value, DateTime):
        return convert_to_datetime(value)
    if isinstance(value, dict):
        return {key: _native(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_native(item) for item in value]
    return value


def _required_identity(value, field):
    if not isinstance(value, str) or not value:
        raise StudyDiseaseMilestoneSourceError(
            f"STUDY_MILESTONE_SOURCE_IDENTITY_REQUIRED: {field}"
        )
    return value


def _term_value(reader, term, kind, repository, root_name, issues):
    root = getattr(term, root_name).get_or_none() if term is not None else None
    if root is None:
        issues.append(f"STUDY_MILESTONE_TERM_HISTORY_MISSING: {kind}")
        return None
    try:
        # Share the native approved-version/date and ambiguity rules already
        # used for study-selected compounds. No current aggregate/catalogue
        # lookup or mutable LATEST link supplies historical text.
        value, relationship = reader._select_version(
            repository, root, kind, term.uid, None, reader.as_of
        )
    except StudyCompoundSourceError as error:
        issues.append(f"STUDY_MILESTONE_TERM_HISTORY_UNRESOLVED: {kind}; {error.msg}")
        return None
    return StudyDiseaseMilestoneTermValue(
        value_identity=reader._value_id(value),
        version=relationship.version,
        status=relationship.status,
        start_date=relationship.start_date,
        end_date=relationship.end_date,
        author_id=relationship.author_id,
        change_description=relationship.change_description,
        value=_native(dict(value.__properties__)),
    )


def read_disease_milestone_snapshot(
    repos, study_uid: str, study_value_version: str
) -> list[StudyDiseaseMilestone]:
    """Read exact selections and separately disclose the dated CT resolution.

    A milestone's CTTermContext stores roots only. The historical name and
    definition are observations from approved HAS_VERSION history effective
    at the study's recorded snapshot time, not invented selected CT versions.
    """
    reader = StudyCompoundSnapshotReader(repos, study_uid, study_value_version)
    rows = _native(repos.study_disease_milestone_repository.find_snapshot_selections(
        study_uid, study_value_version
    ))
    study_values = {row["study_value_identity"] for row in rows}
    if len(study_values) != 1 or not next(iter(study_values), None):
        raise StudyDiseaseMilestoneSourceError("STUDY_MILESTONE_SNAPSHOT_AMBIGUOUS")
    # The native study reader prefers the locked state of the same immutable
    # value. Require its exact timestamp rather than guessing from row order.
    selected_status = (
        "LOCKED" if any(row["study_version"].get("status") == "LOCKED" for row in rows)
        else "RELEASED"
    )
    selected_rows = [
        row for row in rows
        if row["study_version"].get("status") == selected_status
        and row["study_version"].get("start_date") == reader.as_of
    ]
    version_states = [row["study_version"] for row in selected_rows]
    if not version_states or any(value != version_states[0] for value in version_states):
        raise StudyDiseaseMilestoneSourceError("STUDY_MILESTONE_SNAPSHOT_DATE_MISMATCH")
    items = []
    seen = {}
    for row in selected_rows:
        if row["selection_identity"] is None:
            continue
        selection, action = row["selection"], row["action"]
        uid = _required_identity(selection.get("uid"), "selection.uid")
        if uid in seen:
            if row != seen[uid]:
                raise StudyDiseaseMilestoneSourceError(
                    f"STUDY_MILESTONE_SELECTION_AMBIGUOUS: {uid}"
                )
            continue
        seen[uid] = row
        for field in ("selection_identity", "action_identity", "context_identity", "term_uid", "codelist_uid"):
            _required_identity(row[field], field)
        if not isinstance(action, dict) or not isinstance(action.get("date"), datetime):
            raise StudyDiseaseMilestoneSourceError(f"STUDY_MILESTONE_AUDIT_DATE_REQUIRED: {uid}")
        if action["date"] > reader.as_of:
            raise StudyDiseaseMilestoneSourceError(f"STUDY_MILESTONE_SELECTION_AFTER_SNAPSHOT: {uid}")
        term = CTTermRoot.nodes.get_or_none(uid=row["term_uid"])
        issues = []
        name = _term_value(
            reader, term, "ctTermName", repos.ct_term_name_repository,
            "has_name_root", issues,
        )
        attributes = _term_value(
            reader, term, "ctTermAttributes", repos.ct_term_attributes_repository,
            "has_attributes_root", issues,
        )
        name_text = name.value.get("name") if name is not None else None
        definition = attributes.value.get("definition") if attributes is not None else None
        for field, value in (("name", name_text), ("definition", definition)):
            if value is None:
                issues.append(f"STUDY_MILESTONE_TERM_TEXT_MISSING: {field}")
            elif not isinstance(value, str):
                raise StudyDiseaseMilestoneSourceError(f"STUDY_MILESTONE_TERM_TEXT_INVALID: {field}")
        source = StudyDiseaseMilestoneTermSource(
            state="unresolved" if issues else "resolved",
            mode="study-snapshot-as-of",
            study_uid=study_uid, study_value_version=study_value_version,
            study_value_identity=row["study_value_identity"], as_of=reader.as_of,
            selection_identity=row["selection_identity"],
            context_identity=row["context_identity"],
            term_uid=row["term_uid"], codelist_uid=row["codelist_uid"],
            name=name, attributes=attributes, issues=issues, native_selection=row,
        )
        items.append(StudyDiseaseMilestone(
            uid=uid, study_uid=study_uid, study_version=study_value_version,
            order=selection.get("order"), status=selection.get("status"),
            start_date=action["date"],
            author_username=UserInfoService.get_author_username_from_id(action.get("author_id")),
            disease_milestone_type=row["term_uid"],
            disease_milestone_type_name=name_text,
            disease_milestone_type_definition=definition,
            repetition_indicator=selection.get("repetition_indicator"),
            terminology_source=source,
        ))
    return items
