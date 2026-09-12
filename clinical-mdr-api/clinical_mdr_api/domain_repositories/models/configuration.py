from neomodel import RelationshipTo

from clinical_mdr_api.domain_repositories.models.controlled_terminology import (
    CTCodelistRoot,
    CTTermRoot,
)
from clinical_mdr_api.domain_repositories.models.generic import (
    ClinicalMdrRel,
    VersionRelationship,
    VersionRoot,
    VersionValue,
)
from common.neomodel import BooleanProperty, StringProperty


class CTConfigValue(VersionValue):
    study_field_name = StringProperty()
    study_field_data_type = StringProperty()
    study_field_null_value_code = StringProperty()

    study_field_grouping = StringProperty()
    study_field_name_api = StringProperty()

    has_configured_codelist = RelationshipTo(
        CTCodelistRoot, "HAS_CONFIGURED_CODELIST", model=ClinicalMdrRel
    )
    # TODO update to CTTermContext
    has_configured_term = RelationshipTo(
        CTTermRoot, "HAS_CONFIGURED_TERM", model=ClinicalMdrRel
    )
    is_dictionary_term = BooleanProperty()


class CTConfigRoot(VersionRoot):
    has_version = RelationshipTo(
        CTConfigValue, "HAS_VERSION", model=VersionRelationship
    )
    has_latest_value = RelationshipTo(CTConfigValue, "LATEST", model=ClinicalMdrRel)
    latest_draft = RelationshipTo(CTConfigValue, "LATEST_DRAFT", model=ClinicalMdrRel)
    latest_final = RelationshipTo(CTConfigValue, "LATEST_FINAL", model=ClinicalMdrRel)
    latest_retired = RelationshipTo(
        CTConfigValue, "LATEST_RETIRED", model=ClinicalMdrRel
    )
