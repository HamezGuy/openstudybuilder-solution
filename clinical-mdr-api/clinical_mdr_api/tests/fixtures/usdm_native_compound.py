"""Offline native graph boundaries; real repository factories and response DTOs.

Every LATEST read raises. Selected alias/product values coexist with later
approved values, and independently versioned children change after the selected
study timestamp. No USDM document or expected mapper output enters this fixture.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from clinical_mdr_api.domain_repositories.concepts.active_substance_repository import ActiveSubstanceRepository
from clinical_mdr_api.domain_repositories.concepts.compound_alias_repository import CompoundAliasRepository
from clinical_mdr_api.domain_repositories.concepts.compound_repository import CompoundRepository
from clinical_mdr_api.domain_repositories.concepts.medicinal_product_repository import MedicinalProductRepository
from clinical_mdr_api.domain_repositories.concepts.pharmaceutical_product_repository import PharmaceuticalProductRepository
from clinical_mdr_api.domain_repositories.concepts.simple_concepts.lag_time_repository import LagTimeRepository
from clinical_mdr_api.domain_repositories.concepts.simple_concepts.numeric_value_with_unit_repository import NumericValueWithUnitRepository
from clinical_mdr_api.domain_repositories.concepts.unit_definitions.unit_definition_repository import UnitDefinitionRepository
from clinical_mdr_api.domain_repositories.controlled_terminologies.ct_term_name_repository import CTTermNameRepository
from clinical_mdr_api.domain_repositories.dictionaries.dictionary_term_repository import DictionaryTermGenericRepository
from clinical_mdr_api.domain_repositories.dictionaries.dictionary_term_substance_repository import DictionaryTermSubstanceRepository
from clinical_mdr_api.domains.study_selections.study_compound_dosing import StudyCompoundDosingVO
from clinical_mdr_api.domains.study_selections.study_selection_compound import StudySelectionCompoundVO
from clinical_mdr_api.services.studies.study_compound_dosing_selection import StudyCompoundDosingSelectionService
from clinical_mdr_api.services.studies.study_compound_selection import StudyCompoundSelectionService
from clinical_mdr_api.services.studies.study_compound_snapshot import StudyCompoundSnapshotReader


def forbidden_latest(*args, **kwargs):
    raise AssertionError("A current/latest library lookup cannot authorize this selected snapshot.")


class Relations:
    def __init__(self, *nodes):
        self.nodes = list(nodes)

    def all(self):
        return list(self.nodes)

    def get_or_none(self):
        assert len(self.nodes) <= 1
        return self.nodes[0] if self.nodes else None

    def get(self):
        result = self.get_or_none()
        assert result is not None
        return result

    single = get_or_none


class Value(SimpleNamespace):
    def __init__(self, identity, **properties):
        super().__init__(**properties)
        self.element_id = identity
        self.__properties__ = dict(properties)

    @staticmethod
    def labels():
        return ["SyntheticNativeValue"]


class History(Relations):
    def __init__(self, rows):
        super().__init__(*(value for value, _ in rows))
        self.rows = list(rows)

    def all_relationships(self, value):
        return [relationship for candidate, relationship in self.rows if candidate is value]


class NativeCompoundSource:
    def __init__(self, study_uid, version, as_of):
        self.study_uid, self.version, self.as_of = study_uid, version, as_of
        self.calls = []
        self.roots = {}
        self.repos = SimpleNamespace()
        self.library = SimpleNamespace(name="Synthetic native product library", is_editable=True)
        self.dictionary_library = SimpleNamespace(name="Synthetic substance dictionary", is_editable=True)
        self.selection_date = as_of - timedelta(days=30)
        latest = SimpleNamespace(get=forbidden_latest, get_or_none=forbidden_latest,
                                 single=forbidden_latest, all=forbidden_latest)
        self._latest = latest
        self._seed()
        self._repositories()
        self.selection = StudySelectionCompoundVO(
            study_uid=study_uid, study_selection_uid="CompoundSelection_1",
            compound_uid="Compound_1", compound_alias_uid="Alias_1",
            medicinal_product_uid="MedicinalProduct_1", type_of_treatment_uid=None,
            reason_for_missing_value_uid=None, dispenser_uid=None, dispenser=None,
            dose_frequency_uid=None, dose_frequency=None,
            delivery_device_uid=None, delivery_device=None, other_info="",
            study_compound_dosing_count=1, start_date=self.selection_date,
            author_id="synthetic-author", author_username="Synthetic Author",
        )
        self.references = [
            {"kind": kind, "uid": uid, "valueIdentity": self.roots[kind, uid].has_version.rows[0][0].element_id}
            for kind, uid in (
                ("compoundAlias", "Alias_1"), ("medicinalProduct", "MedicinalProduct_1"),
                ("pharmaceuticalProduct", "PharmaceuticalProduct_1"),
            )
        ]
        self.repos.study_compound_repository = SimpleNamespace(
            get_selected_library_references=self.selected_references,
            find_by_uid=self.find_selection,
            find_by_uid_and_dosing_uid=self.find_history_selection,
        )
        self.repos.ct_codelist_name_repository = SimpleNamespace(
            get_codelist_term_by_uid_and_submval=self.codelist_term,
        )
        self.repos.project_repository = SimpleNamespace(
            find_by_study_uid=lambda uid: SimpleNamespace(name="Synthetic project", project_number="SYN")
            if uid == self.study_uid else None,
        )

    def root(self, kind, uid, properties, *, relations=None, later_properties=None, selected=False):
        upgrade = self.as_of + timedelta(days=1) if not selected else self.as_of - timedelta(days=1)
        first = Value(uid + ":value:1", **properties)
        second = Value(uid + ":value:2", **{**properties, **(later_properties or {})})
        for value in (first, second):
            for name, targets in (relations or {}).items():
                setattr(value, name, Relations(*targets))
        rows = [
            (first, SimpleNamespace(
                version="1.0", status="Final", start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
                end_date=upgrade, change_description="Explicit synthetic original value", author_id="synthetic-author",
            )),
            (second, SimpleNamespace(
                version="2.0", status="Final", start_date=upgrade, end_date=None,
                change_description="Adversarial unselected update", author_id="synthetic-author",
            )),
        ]
        root = SimpleNamespace(
            uid=uid, has_version=History(rows), has_latest_value=self._latest,
            latest_draft=self._latest, latest_final=self._latest, latest_retired=self._latest,
        )
        self.roots[kind, uid] = root
        return root

    def _seed(self):
        compound = self.root("compound", "Compound_1", dict(
            name="Synthetic compound", name_sentence_case="Synthetic compound",
            definition="", abbreviation=None, is_sponsor_compound=False, external_id="SYN-C",
        ), later_properties={"name": "UNSELECTED compound"})
        self.root("compoundAlias", "Alias_1", dict(
            name="Selected alias", name_sentence_case="Selected alias", definition=None,
            abbreviation="", is_preferred_synonym=False,
        ), relations={"is_compound": [compound]}, later_properties={"name": "UNSELECTED alias"}, selected=True)
        unit_properties = dict(
            name="mg", definition="Explicit synthetic milligram unit", si_unit=False,
            display_unit=True, master_unit=False, convertible_unit=False,
            us_conventional_unit=False, use_complex_unit_conversion=False, use_molecular_weight=False,
            legacy_code=None, conversion_factor_to_master=None, order=0, comment="",
        )
        unit_relations = {name: [] for name in ("has_ct_unit", "has_unit_subset", "has_ct_dimension", "has_ucum_term")}
        unit = self.root("unitDefinition", "Unit_mg", unit_properties, relations=unit_relations,
                         later_properties={"name": "UNSELECTED different unit", "conversion_factor_to_master": 1000})
        hours = self.root("unitDefinition", "Unit_hours", {**unit_properties, "name": "hours"}, relations=unit_relations)
        numbers = {}
        for uid, amount, unit_root in (("Dose_1", 5.5, unit), ("Strength_1", 0, unit), ("HalfLife_1", 8, hours)):
            numbers[uid] = self.root("numericValueWithUnit", uid,
                {"value": amount, "definition": "", "abbreviation": None},
                relations={"has_unit_definition": [unit_root]},
                later_properties={"value": amount + 1000})
        term = self.root("ctTermName", "Domain_1",
                         {"name": "Synthetic exposure domain", "name_sentence_case": "Synthetic exposure domain"},
                         later_properties={"name": "UNSELECTED domain"})
        public_term = SimpleNamespace(uid="Domain_1", has_library=Relations(self.library),
                                      has_name_root=Relations(term), has_term_root=Relations())
        term.has_root = Relations(public_term)
        self.public_terms = {"Domain_1": public_term}
        domain_context = SimpleNamespace(has_selected_term=Relations(public_term))
        lag = self.root("lagTime", "Lag_1", {"value": 0, "definition": None, "abbreviation": ""},
                       relations={"has_unit_definition": [hours], "has_sdtm_domain": [domain_context]},
                       later_properties={"value": 99})
        dictionary_codelist = SimpleNamespace(uid="DictionaryCodelist_1", has_library=Relations(self.dictionary_library))
        pclass = self.root("dictionaryTerm", "PClass_1", dict(
            name="Synthetic pharmacologic class", name_sentence_case="Synthetic pharmacologic class",
            dictionary_id="SYN-PC", definition="", abbreviation=None,
        ), later_properties={"dictionary_id": "UNSELECTED-PC"})
        pclass.has_term = Relations(dictionary_codelist)
        unii = self.root("dictionarySubstance", "UNII_1", dict(
            name="Synthetic active substance", name_sentence_case="Synthetic active substance",
            dictionary_id="SYN-UNII", definition=None, abbreviation="",
        ), relations={"has_pclass": [pclass]}, later_properties={"dictionary_id": "UNSELECTED-UNII"})
        unii.has_term = Relations(dictionary_codelist)
        active = self.root("activeSubstance", "ActiveSubstance_1", dict(
            analyte_number="SYN-AN", short_number="SYN-S", long_number="SYN-L",
            inn="Synthetic active substance", external_id=None,
        ), relations={"has_unii_value": [unii]}, later_properties={"inn": "UNSELECTED substance"})
        ingredient = SimpleNamespace(
            external_id="SYN-ING", formulation_name="", has_substance=Relations(active),
            has_strength_value=Relations(numbers["Strength_1"]),
            has_half_life=Relations(numbers["HalfLife_1"]), has_lag_time=Relations(lag),
        )
        formulation = SimpleNamespace(external_id="SYN-FORMULATION", has_ingredient=Relations(ingredient))
        pharmaceutical = self.root("pharmaceuticalProduct", "PharmaceuticalProduct_1",
            {"external_id": "SYN-PHARM"}, relations={
                "has_formulation": [formulation], "has_dosage_form": [], "has_route_of_administration": [],
            }, later_properties={"external_id": "UNSELECTED-PHARM"}, selected=True)
        self.root("medicinalProduct", "MedicinalProduct_1", dict(
            name="Selected medicinal product", name_sentence_case="Selected medicinal product",
            external_id="SYN-MED",
        ), relations={
            "is_compound": [compound], "has_pharmaceutical_product": [pharmaceutical],
            "has_dose_value": [numbers["Dose_1"]], "has_dose_frequency": [],
            "has_delivery_device": [], "has_dispenser": [],
        }, later_properties={"name": "UNSELECTED medicinal product"}, selected=True)

    def _repositories(self):
        classes = {
            "compound": CompoundRepository, "compoundAlias": CompoundAliasRepository,
            "medicinalProduct": MedicinalProductRepository, "pharmaceuticalProduct": PharmaceuticalProductRepository,
            "activeSubstance": ActiveSubstanceRepository, "unitDefinition": UnitDefinitionRepository,
            "numericValueWithUnit": NumericValueWithUnitRepository, "lagTime": LagTimeRepository,
            "dictionaryTerm": DictionaryTermGenericRepository, "dictionarySubstance": DictionaryTermSubstanceRepository,
            "ctTermName": CTTermNameRepository,
        }
        for kind, repository_class in classes.items():
            repository = object.__new__(repository_class)
            repository._get_root_and_library = lambda uid, kind=kind: (
                self.roots.get((kind, uid)),
                self.dictionary_library if kind.startswith("dictionary") else self.library,
            )
            repository.find_by_uid_2 = forbidden_latest
            setattr(self.repos, StudyCompoundSnapshotReader.REPOSITORIES[kind], repository)

    def selected_references(self, study_uid, selection_uid, study_value_version=None,
                            *, history_dosing_uid=None, history_date=None):
        assert (study_uid, selection_uid) == (self.study_uid, "CompoundSelection_1")
        assert (study_value_version == self.version and history_dosing_uid is history_date is None) or (
            study_value_version is None and history_dosing_uid == "DosingSelection_1" and history_date == self.selection_date
        )
        self.calls.append(("selection", study_uid, study_value_version, history_dosing_uid, history_date))
        return [dict(row) for row in self.references]

    def find_selection(self, study_uid, study_compound_uid, study_value_version=None):
        assert (study_uid, study_compound_uid, study_value_version) == (
            self.study_uid, "CompoundSelection_1", self.version)
        return self.selection, 1

    def find_history_selection(self, study_uid, study_compound_uid, study_compound_dosing_uid, history_date):
        assert (study_uid, study_compound_uid, study_compound_dosing_uid, history_date) == (
            self.study_uid, "CompoundSelection_1", "DosingSelection_1", self.selection_date)
        return self.selection, 1

    @staticmethod
    def codelist_term(term_uid, codelist_submission_value, at_specific_date_time=None, *, strict_snapshot=False):
        assert strict_snapshot and at_specific_date_time is not None
        assert term_uid is None, "This fixture declares no selected codelist term."
        return None

    @contextmanager
    def isolated(self, study):
        def read_study(service, uid, **kwargs):
            assert uid == self.study_uid and kwargs["study_value_version"] == self.version
            return study

        with patch(
            "clinical_mdr_api.services.user_info.UserInfoService.get_author_username_from_id",
            return_value="Synthetic Author",
        ), patch(
            "clinical_mdr_api.services.studies.study.StudyService.get_by_uid", read_study,
        ), patch(
            "clinical_mdr_api.services.studies.study_compound_snapshot.CTTermRoot",
            SimpleNamespace(nodes=SimpleNamespace(get_or_none=lambda uid: self.public_terms.get(uid))),
        ):
            yield

    def response_models(self, study, element):
        with self.isolated(study):
            compound_service = object.__new__(StudyCompoundSelectionService)
            compound_service._repos = self.repos
            compound_service._extract_study_standards_effective_date = lambda **_: self.as_of
            compounds = compound_service._transform_all_to_response_model(
                SimpleNamespace(study_uid=self.study_uid, study_compounds_selection=[self.selection]), self.version,
            )
            dosing_service = object.__new__(StudyCompoundDosingSelectionService)
            dosing_service._repos = self.repos
            dosing_service._transform_study_element_model = lambda *args, **kwargs: element
            dosing = dosing_service._transform_to_response_model(
                self.study_uid,
                StudyCompoundDosingVO(
                    study_uid=self.study_uid, study_selection_uid="DosingSelection_1",
                    study_compound_uid=self.selection.study_selection_uid, study_element_uid=element.element_uid,
                    compound_uid=self.selection.compound_uid, compound_alias_uid=self.selection.compound_alias_uid,
                    medicinal_product_uid=self.selection.medicinal_product_uid,
                    dose_value_uid="Dose_1", start_date=self.selection_date, author_id="synthetic-author",
                ), 1, self.as_of, study_value_version=self.version,
            )
        return compounds, [dosing]
