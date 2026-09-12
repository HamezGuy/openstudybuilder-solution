"""Authored synthetic native graph histories for the actual snapshot reader.

Only graph storage is replaced. Each value/relationship below is an explicit
fixture fact; LATEST access remains the failure sentinel from the compound graph.
"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from clinical_mdr_api.tests.fixtures.usdm_native_compound import NativeCompoundSource, Relations, Value
from clinical_mdr_api.tests.fixtures.usdm_native_study import AS_OF, STUDY_UID, VERSION
from clinical_mdr_api.services.studies.study_native_library_snapshot import StudyNativeLibrarySnapshot
from clinical_mdr_api.services.studies.study_activity_instance_snapshot import project_activity_definition


class NativeActivitySource:
    def __init__(self, *, as_of=AS_OF):
        self.library = NativeCompoundSource(STUDY_UID, VERSION, as_of)
        self.as_of = as_of
        self.terms, self.codelists = {}, {}
        self.datatype = self.context("FLOAT", "NativeDataTypes", "float")
        self.role = self.context("RESULT", "NativeRoles", "result")
        self.dimension = self.context("TIME", "NativeDimensions", "time")
        self.choice = self.context("CHOICE_A", "NativeChoices", "A")
        self.item_class = self.root(
            "activityItemClass", "ItemClass_1",
            {"name": "Result", "display_name": "Result label", "definition": "Exact class definition. ",
             "nci_concept_id": None, "order": 0},
            later_properties={"name": "UNSELECTED current Result", "definition": "UNSELECTED definition"},
            relations={"has_data_type": [self.datatype], "has_role": [self.role]},
        )
        self.instance_class = self.root(
            "activityInstanceClass", "InstanceClass_1",
            {"name": "Measurement", "definition": "", "is_domain_specific": False},
        )
        self.unit = self.root(
            "unitDefinition", "Unit_1",
            {"name": "synthetic unit", "definition": "", "conversion_factor_to_master": 1.0,
             "use_complex_unit_conversion": False, "use_molecular_weight": False},
            relations={"has_ct_dimension": [self.dimension], "has_ct_unit": [],
                       "has_unit_subset": [], "has_ucum_term": []},
        )
        self.item = Value("native-activity-item-1", text_value="", is_adam_param_specific=False,
                          is_activity_instance_id_specific=None)
        self.item.has_activity_item_class = Relations(self.item_class)
        self.item.has_ct_term = Relations(self.choice)
        # Native ActivityItemCreateInput permits a term selection OR a whole
        # codelist, not both on the same item.
        self.item.has_codelist = Relations()
        self.item.has_unit_definition = Relations(self.unit)
        self.day_unit = self.root(
            "unitDefinition", "Unit_days",
            {"name": "day", "definition": "Authored native day reference",
             "conversion_factor_to_master": 86400.0,
             "use_complex_unit_conversion": False, "use_molecular_weight": False},
            relations={"has_ct_dimension": [self.dimension], "has_ct_unit": [],
                       "has_unit_subset": [], "has_ucum_term": []},
        )
        self.instance = self.root(
            "activityInstance", "LibraryInstance_1",
            {"name": "Platelets at selected version", "definition": "Authored instance", "molecular_weight": 0,
             "nci_concept_id": None},
            relations={"activity_instance_class": [self.instance_class], "contains_activity_item": [self.item]},
        )
        grouping = self.root(
            "activityInstanceGrouping", "Grouping_1", {},
            relations={"has_activity": []},
        )
        self.instance.has_grouping_root = Relations(grouping)

    def root(self, kind, uid, properties, **kwargs):
        root = self.library.root(kind, uid, properties, **kwargs)
        root.element_id = "native-root:" + kind + ":" + uid
        return root

    def context(self, uid, codelist_uid, submission):
        name = self.root("ctTermName", uid, {"name": submission}, later_properties={"name": "UNSELECTED " + submission})
        attributes = self.root("ctTermAttributes", uid, {"concept_id": None, "preferred_term": submission, "definition": ""})
        term = SimpleNamespace(uid=uid, element_id="native-term:" + uid,
                               has_name_root=Relations(name), has_attributes_root=Relations(attributes))
        self.terms[uid] = term
        if codelist_uid not in self.codelists:
            list_name = self.root("ctCodelistName", codelist_uid, {"name": codelist_uid})
            list_attrs = self.root("ctCodelistAttributes", codelist_uid, {"name": codelist_uid, "submission_value": codelist_uid})
            membership = Relations()
            membership.rows = []
            membership.all_relationships = lambda node, membership=membership: [
                state for item, state in membership.rows if item is node
            ]
            self.codelists[codelist_uid] = SimpleNamespace(
                uid=codelist_uid, element_id="native-codelist:" + codelist_uid,
                has_name_root=Relations(list_name), has_attributes_root=Relations(list_attrs),
                has_term=membership,
            )
        codelist = self.codelists[codelist_uid]
        member = Value("native-membership:" + codelist_uid + ":" + uid, submission_value=submission)
        member.has_term_root = Relations(term)
        state = SimpleNamespace(start_date=self.as_of - timedelta(days=30), end_date=None,
                                __properties__={"order": len(codelist.has_term.nodes), "start_date": self.as_of - timedelta(days=30),
                                                "end_date": None, "author_id": "synthetic-author"})
        codelist.has_term.nodes.append(member)
        codelist.has_term.rows.append((member, state))
        return SimpleNamespace(element_id="native-context:" + codelist_uid + ":" + uid,
                               has_selected_term=Relations(term), has_selected_codelist=Relations(codelist))

    @contextmanager
    def isolated(self):
        with (
            patch("clinical_mdr_api.services.studies.study_native_library_snapshot.CTTermRoot",
                  SimpleNamespace(nodes=SimpleNamespace(get_or_none=lambda uid: self.terms.get(uid)))),
            patch("clinical_mdr_api.services.studies.study_native_library_snapshot.CTCodelistRoot",
                  SimpleNamespace(nodes=SimpleNamespace(get_or_none=lambda uid: self.codelists.get(uid)))),
        ):
            yield

    def snapshot(self):
        return StudyNativeLibrarySnapshot(STUDY_UID, VERSION, as_of=self.as_of)

    def definition(self, uid, version=None, **kwargs):
        assert (uid, version) == ("LibraryInstance_1", "1.0")
        assert kwargs.get("study_uid", STUDY_UID) == STUDY_UID
        assert kwargs.get("study_value_version", VERSION) == VERSION
        selection_uid = kwargs.get("study_activity_instance_uid", "InstanceSelection_1")
        with self.isolated():
            return project_activity_definition(
                self.snapshot(), self.instance,
                self.instance.has_version.rows[0][0].element_id, version,
                {"studyValueIdentity": "native-study-value-2", "selectionIdentity": "native-selection-1",
                 "properties": {"uid": selection_uid}},
            )

    def visit_units(self, visit, **scope):
        from clinical_mdr_api.services.integrations.edc_native_visit_projection import read_visit_units

        assert scope == {"study_uid": STUDY_UID, "study_value_version": VERSION, "as_of": self.as_of}
        roots = {"Unit_days": self.day_unit, "Unit_1": self.unit}

        def query(text, parameters):
            assert "value.name = $day_unit_name" in text and "HAS_VERSION" in text
            assert parameters == {"as_of": self.as_of, "day_unit_name": "day"}
            return [[self.day_unit.uid]], []

        with self.isolated(), patch(
            "clinical_mdr_api.services.integrations.edc_native_visit_projection.db.cypher_query", query,
        ), patch(
            "clinical_mdr_api.services.integrations.edc_native_visit_projection.UnitDefinitionRoot",
            SimpleNamespace(nodes=SimpleNamespace(get_or_none=lambda uid: roots.get(uid))),
        ):
            return read_visit_units(visit, **scope)

    def source_input(self):
        histories = {
            f"{kind}/{uid}": [
                {"valueIdentity": value.element_id, "properties": deepcopy(value.__properties__),
                 "version": deepcopy(vars(state))}
                for value, state in root.has_version.rows
            ] for (kind, uid), root in self.library.roots.items()
            if hasattr(root, "element_id")
        }
        return {
            "histories": histories,
            "item": {
                "nativeIdentity": self.item.element_id,
                "properties": deepcopy(self.item.__properties__),
                "activityItemClassUid": self.item_class.uid,
                "ctTermContexts": [{
                    "nativeIdentity": item.element_id,
                    "termUid": item.has_selected_term.get().uid,
                    "codelistUid": item.has_selected_codelist.get().uid,
                } for item in self.item.has_ct_term.all()],
                "codelistUids": [item.uid for item in self.item.has_codelist.all()],
                "unitDefinitionUids": [item.uid for item in self.item.has_unit_definition.all()],
            },
            "codelistMemberships": {
                uid: [{
                    "nativeIdentity": member.element_id,
                    "properties": deepcopy(member.__properties__),
                    "relationship": deepcopy(state.__properties__),
                    "termUid": member.has_term_root.get().uid,
                } for member, state in root.has_term.rows]
                for uid, root in self.codelists.items()
            },
        }
