from clinical_mdr_api.domain_repositories.models.generic import ClinicalMdrNode
from common.neomodel import BooleanProperty, IntegerProperty, StringProperty


class FeatureFlag(ClinicalMdrNode):
    sn = IntegerProperty(unique_index=True)
    section = StringProperty()
    feature = StringProperty()
    name = StringProperty()
    enabled = BooleanProperty()
    description = StringProperty()
