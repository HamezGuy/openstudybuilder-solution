import datetime

from neomodel import One, RelationshipFrom, RelationshipTo, ZeroOrOne

from clinical_mdr_api.domain_repositories.models.generic import (
    ClinicalMdrNode,
    ClinicalMdrRel,
    ConjunctionRelation,
    ZonedDateTimeProperty,
)
from common.neomodel import StringProperty


class StudyAction(ClinicalMdrNode):
    audit_trail = RelationshipFrom(
        ".study.StudyRoot", "AUDIT_TRAIL", model=ClinicalMdrRel
    )
    date: datetime.datetime = ZonedDateTimeProperty()
    status = StringProperty()
    author_id = StringProperty()
    # Why the write was made, when the caller gave a reason (FX-OSB-09: study
    # arm Create, Edit and Delete actions).
    change_reason = StringProperty()
    has_before = RelationshipTo(
        ".study_selections.StudySelection",
        "BEFORE",
        model=ConjunctionRelation,
        cardinality=ZeroOrOne,
    )
    has_after = RelationshipTo(
        ".study_selections.StudySelection",
        "AFTER",
        model=ConjunctionRelation,
        cardinality=One,
    )
    study_value_has_before = RelationshipTo(
        ".study.StudyValue",
        "BEFORE",
        model=ConjunctionRelation,
        cardinality=ZeroOrOne,
    )
    study_value_node_has_after = RelationshipTo(
        ".study.StudyValue",
        "AFTER",
        model=ConjunctionRelation,
        cardinality=One,
    )
    study_field_has_before = RelationshipTo(
        ".study_field.StudyField",
        "BEFORE",
        model=ConjunctionRelation,
        cardinality=ZeroOrOne,
    )
    study_field_node_has_after = RelationshipTo(
        ".study_field.StudyField",
        "AFTER",
        model=ConjunctionRelation,
        cardinality=One,
    )


class Delete(StudyAction):
    pass


class Create(StudyAction):
    # Set only when a selection is copied from another study (FX-OSB-02):
    # the source study and the source selection the new one was copied from.
    source_study_uid = StringProperty()
    source_selection_uid = StringProperty()


class Edit(StudyAction):
    pass


class Archive(StudyAction):
    """A study archived: AFTER points to the study value current when it was archived, change_reason states why.
    The newest Archive or Unarchive on a study's audit trail is its archive state (StudyArchiveVO).
    """


class Unarchive(StudyAction):
    """An archived study restored: AFTER points to the study value current when it was restored."""


class UpdateSoASnapshot(StudyAction):
    object_type = StringProperty()
