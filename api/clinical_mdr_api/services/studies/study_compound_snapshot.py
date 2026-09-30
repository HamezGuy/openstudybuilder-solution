"""Read study-selected library values without following mutable LATEST links."""

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi.encoders import jsonable_encoder
from clinical_mdr_api.domain_repositories.models.controlled_terminology import CTTermRoot
from clinical_mdr_api.domains.controlled_terminologies.utils import CtTermInfo
from clinical_mdr_api.models.concepts.compound import Compound
from clinical_mdr_api.models.concepts.compound_alias import CompoundAlias
from clinical_mdr_api.models.concepts.medicinal_product import MedicinalProduct
from clinical_mdr_api.models.concepts.pharmaceutical_product import PharmaceuticalProduct
from common.exceptions import ValidationException


class StudyCompoundSourceError(ValidationException):
    status_code = 422


def _utc(value):
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise StudyCompoundSourceError("STUDY_LIBRARY_SOURCE_DATE_REQUIRED")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class StudyCompoundSnapshotReader:
    """A single study scope and an explicit selection/as-of witness per reading."""

    REPOSITORIES = {
        "compound": "compound_repository",
        "compoundAlias": "compound_alias_repository",
        "medicinalProduct": "medicinal_product_repository",
        "pharmaceuticalProduct": "pharmaceutical_product_repository",
        "numericValueWithUnit": "numeric_value_with_unit_repository",
        "unitDefinition": "unit_definition_repository",
        "activeSubstance": "active_substance_repository",
        "lagTime": "lag_time_repository",
        "dictionaryTerm": "dictionary_term_generic_repository",
        "dictionarySubstance": "dictionary_term_substance_repository",
        "ctTermName": "ct_term_name_repository",
    }

    def __init__(
        self, repos, study_uid: str, study_value_version: str | None,
        *, as_of: datetime | None = None, terms_at_specific_datetime: datetime | None = None,
    ):
        if not isinstance(study_uid, str) or not study_uid.strip():
            raise StudyCompoundSourceError("STUDY_LIBRARY_STUDY_IDENTITY_REQUIRED")
        if study_value_version is not None and (
            not isinstance(study_value_version, str) or not study_value_version.strip()
        ):
            raise StudyCompoundSourceError("STUDY_LIBRARY_STUDY_VERSION_REQUIRED")
        requested_version = None
        if study_value_version is not None:
            try:
                requested_version = Decimal(study_value_version)
            except InvalidOperation as error:
                raise StudyCompoundSourceError("STUDY_LIBRARY_STUDY_VERSION_INVALID") from error
            if not requested_version.is_finite():
                raise StudyCompoundSourceError("STUDY_LIBRARY_STUDY_VERSION_INVALID")
        self.repos = repos
        self.study_uid = study_uid
        self.study_value_version = study_value_version
        if as_of is None and study_value_version is not None:
            from clinical_mdr_api.domains.study_definition_aggregates.study_metadata import StudyComponentEnum
            from clinical_mdr_api.services.studies.study import StudyService

            study = StudyService().get_by_uid(
                study_uid, include_sections=[StudyComponentEnum.VERSION_METADATA],
                study_value_version=study_value_version,
            )
            if study.uid != study_uid:
                raise StudyCompoundSourceError("STUDY_LIBRARY_STUDY_IDENTITY_MISMATCH")
            metadata = study.current_metadata.version_metadata
            # Native study versions are Decimal values; library item versions
            # use a separate major/minor string contract. Keep the original
            # requested string for every exact graph selection query.
            actual_version = getattr(metadata, "version_number", None)
            if (
                not isinstance(actual_version, Decimal)
                or not actual_version.is_finite()
                or actual_version != requested_version
            ):
                raise StudyCompoundSourceError("STUDY_LIBRARY_SOURCE_VERSION_MISMATCH")
            as_of = metadata.version_timestamp
            if as_of is None:
                raise StudyCompoundSourceError("STUDY_LIBRARY_SOURCE_DATE_REQUIRED")
        self.as_of = _utc(as_of or datetime.now(timezone.utc))
        self.terms_as_of = _utc(terms_at_specific_datetime) or self.as_of
        self.bindings: list[dict[str, Any]] = []
        self._cache = {}
        self._selected_values: dict[tuple[str, str], str] = {}
        self._selection_uid: str | None = None

    @staticmethod
    def _value_id(value) -> str:
        identifier = getattr(value, "element_id", None)
        if not isinstance(identifier, str) or not identifier:
            raise StudyCompoundSourceError("STUDY_LIBRARY_VALUE_IDENTITY_REQUIRED")
        return identifier

    def _select_version(self, repository, root, kind, uid, value_id, cutoff):
        history = repository._get_version_relation_keys(root)[0]
        candidates = []
        for value in history.all():
            if value_id is not None and self._value_id(value) != value_id:
                continue
            for relationship in history.all_relationships(value):
                start = _utc(relationship.start_date)
                end = _utc(relationship.end_date)
                if start is None or start > cutoff:
                    continue
                # Native study selections authorize approved library values.
                # Draft children cannot replace a selected approved reading.
                if relationship.status not in {"Final", "Retired"}:
                    continue
                if value_id is None and end is not None and cutoff >= end:
                    continue
                candidates.append((value, relationship))
        identities = {
            (self._value_id(value), relationship.version)
            for value, relationship in candidates
        }
        if len(identities) != 1:
            raise StudyCompoundSourceError(
                f"STUDY_LIBRARY_SOURCE_UNRESOLVED: {kind}/{uid}; "
                "No unique approved selected value/version exists in this native snapshot."
            )
        if not all(isinstance(version, str) and version for _, version in identities):
            raise StudyCompoundSourceError(f"STUDY_LIBRARY_VERSION_REQUIRED: {kind}/{uid}")
        # State transitions of the same immutable value/version are history,
        # not alternative clinical values. Select the state effective latest
        # before this exact snapshot and reject tied, conflicting state rows.
        latest_start = max(_utc(relationship.start_date) for _, relationship in candidates)
        states = [
            (value, relationship) for value, relationship in candidates
            if _utc(relationship.start_date) == latest_start
        ]
        signatures = {
            (relationship.version, relationship.status, _utc(relationship.end_date),
             relationship.change_description, relationship.author_id)
            for _, relationship in states
        }
        if len(signatures) != 1:
            raise StudyCompoundSourceError(f"STUDY_LIBRARY_STATE_AMBIGUOUS: {kind}/{uid}")
        return states[0]

    def read(self, kind: str, uid: str | None, *, value_id: str | None = None):
        if uid is None:
            return None
        if not isinstance(uid, str) or not uid:
            raise StudyCompoundSourceError(f"STUDY_LIBRARY_IDENTITY_REQUIRED: {kind}")
        selected = self._selected_values.get((kind, uid))
        if value_id is not None and selected is not None and value_id != selected:
            raise StudyCompoundSourceError(f"STUDY_LIBRARY_SELECTED_VALUE_CONFLICT: {kind}/{uid}")
        value_id = value_id or selected
        cutoff = self.terms_as_of if kind == "ctTermName" else self.as_of
        key = kind, uid, value_id, cutoff
        if key in self._cache:
            return self._cache[key]
        repository = getattr(self.repos, self.REPOSITORIES[kind])
        if kind == "ctTermName":
            # This repository's generic root getter takes the internal name-root
            # element ID. Native clinical relationships carry the public term UID.
            term_root = CTTermRoot.nodes.get_or_none(uid=uid)
            root = term_root.has_name_root.get_or_none() if term_root is not None else None
            library = term_root.has_library.get_or_none() if term_root is not None else None
        else:
            root, library = repository._get_root_and_library(uid)
        if root is None:
            raise StudyCompoundSourceError(f"STUDY_LIBRARY_SOURCE_MISSING: {kind}/{uid}")
        value, relationship = self._select_version(
            repository, root, kind, uid, value_id, cutoff
        )
        aggregate = repository._create_aggregate_root_instance_from_version_root_relationship_and_value(
            root=root, library=library, value=value, relationship=relationship,
            native_snapshot_reader=self,
        )
        if aggregate.uid != uid or aggregate.item_metadata.version != relationship.version:
            raise StudyCompoundSourceError(f"STUDY_LIBRARY_READBACK_MISMATCH: {kind}/{uid}")
        self._cache[key] = aggregate
        definition = next(
            (getattr(aggregate, field) for field in
             ("concept_vo", "ct_term_vo", "dictionary_term_vo")
             if hasattr(aggregate, field)),
            None,
        )
        if not is_dataclass(definition):
            raise StudyCompoundSourceError(f"STUDY_LIBRARY_DEFINITION_REQUIRED: {kind}/{uid}")
        self.bindings.append({
            "kind": kind, "uid": uid, "version": relationship.version,
            "libraryName": getattr(library, "name", None),
            "mode": "selected-value" if value_id is not None else "snapshot-as-of",
            "asOf": cutoff.isoformat(), "studyUid": self.study_uid,
            "studyValueVersion": self.study_value_version,
            "studyCompoundUid": self._selection_uid,
            "valueIdentity": self._value_id(value),
            "value": dict(value.__properties__),
            "definition": jsonable_encoder(definition),
            "versionState": {
                "status": relationship.status,
                "startDate": _utc(relationship.start_date).isoformat(),
                "endDate": _utc(relationship.end_date).isoformat() if relationship.end_date else None,
                "changeDescription": relationship.change_description,
                "authorId": relationship.author_id,
            },
        })
        return aggregate

    def callback(self, kind):
        return lambda uid: self.read(kind, uid)

    def term_info(self, context):
        if context is None:
            return None
        term = context.has_selected_term.get_or_none()
        if term is None:
            raise StudyCompoundSourceError("STUDY_LIBRARY_TERM_IDENTITY_REQUIRED")
        aggregate = self.read("ctTermName", term.uid)
        return CtTermInfo(term_uid=term.uid, name=aggregate.ct_term_vo.name)

    def codelist_term(
        self, term_uid, codelist_submission_value, at_specific_date_time=None,
    ):
        # The selected study standard's effective date is independent of the
        # clinical snapshot date; pass it explicitly at this native boundary.
        result = self.repos.ct_codelist_name_repository.get_codelist_term_by_uid_and_submval(
            term_uid, codelist_submission_value,
            at_specific_date_time=self.terms_as_of, strict_snapshot=True,
        )
        if result is None and term_uid is not None:
            raise StudyCompoundSourceError(f"STUDY_LIBRARY_CT_SOURCE_MISSING: {term_uid}")
        if result is not None and result.ct_simple_codelist_term_vo.date_conflict:
            raise StudyCompoundSourceError("STUDY_LIBRARY_CT_DATE_CONFLICT")
        if result is not None:
            definition = asdict(result.ct_simple_codelist_term_vo)
            binding = {
                "kind": "ctCodelistTerm",
                "uid": f"{definition['codelist_uid']}:{term_uid}",
                "mode": "snapshot-as-of", "asOf": self.terms_as_of.isoformat(),
                "studyUid": self.study_uid, "studyValueVersion": self.study_value_version,
                "studyCompoundUid": self._selection_uid, "definition": definition,
            }
            if binding not in self.bindings:
                self.bindings.append(binding)
        return result

    def pharmaceutical_product(self, uid, *, value_id=None):
        aggregate = self.read("pharmaceuticalProduct", uid, value_id=value_id)
        return PharmaceuticalProduct.from_pharmaceutical_product_ar(
            pharmaceutical_product_ar=aggregate,
            find_term_by_uid=self.callback("ctTermName"),
            find_numeric_value_by_uid=self.callback("numericValueWithUnit"),
            find_lag_time_by_uid=self.callback("lagTime"),
            find_unit_by_uid=self.callback("unitDefinition"),
            find_active_substance_by_uid=self.callback("activeSubstance"),
            find_dictionary_term_by_uid=self.callback("dictionaryTerm"),
            find_substance_term_by_uid=self.callback("dictionarySubstance"),
            find_codelist_term_by_uid_and_submission_value=self.codelist_term,
        )

    def selection_models(self, selection, *, history_dosing_uid=None, history_date=None):
        if selection.study_uid != self.study_uid:
            raise StudyCompoundSourceError("STUDY_LIBRARY_SELECTION_STUDY_MISMATCH")
        selection_date = _utc(selection.start_date)
        if selection_date is None:
            raise StudyCompoundSourceError("STUDY_LIBRARY_SELECTION_DATE_REQUIRED")
        if selection_date > self.as_of:
            raise StudyCompoundSourceError("STUDY_LIBRARY_SELECTION_AFTER_SNAPSHOT")
        self._selection_uid = selection.study_selection_uid
        references = self.repos.study_compound_repository.get_selected_library_references(
            self.study_uid, selection.study_selection_uid,
            study_value_version=self.study_value_version,
            history_dosing_uid=history_dosing_uid, history_date=history_date,
        )
        for binding in references:
            kind, uid, value_id = binding["kind"], binding["uid"], binding["valueIdentity"]
            if kind not in {"compoundAlias", "medicinalProduct", "pharmaceuticalProduct"}:
                raise StudyCompoundSourceError("STUDY_LIBRARY_SELECTED_KIND_INVALID")
            expected_uid = {
                "compoundAlias": selection.compound_alias_uid,
                "medicinalProduct": selection.medicinal_product_uid,
            }
            if kind in expected_uid and expected_uid[kind] != uid:
                raise StudyCompoundSourceError(f"STUDY_LIBRARY_SELECTED_IDENTITY_MISMATCH: {kind}/{uid}")
            key = kind, uid
            previous = self._selected_values.get(key)
            if previous is not None and previous != value_id:
                raise StudyCompoundSourceError(f"STUDY_LIBRARY_SELECTED_VALUE_AMBIGUOUS: {kind}/{uid}")
            self._selected_values[key] = value_id
        for kind, uid in (
            ("compoundAlias", selection.compound_alias_uid),
            ("medicinalProduct", selection.medicinal_product_uid),
        ):
            if uid is not None and (kind, uid) not in self._selected_values:
                raise StudyCompoundSourceError(f"STUDY_LIBRARY_SELECTED_VALUE_MISSING: {kind}/{uid}")
        compound = self.read("compound", selection.compound_uid)
        alias = self.read("compoundAlias", selection.compound_alias_uid)
        product = self.read("medicinalProduct", selection.medicinal_product_uid)
        for related in (alias, product):
            if related is not None and related.concept_vo.compound_uid != selection.compound_uid:
                raise StudyCompoundSourceError("STUDY_LIBRARY_COMPOUND_RELATIONSHIP_MISMATCH")
        products = [
            self.pharmaceutical_product(binding["uid"], value_id=binding["valueIdentity"])
            for binding in references if binding["kind"] == "pharmaceuticalProduct"
        ]
        selected_product_uids = {item.uid for item in products}
        if product is not None:
            products.extend(
                self.pharmaceutical_product(item.uid)
                for item in product.concept_vo.pharmaceutical_products
                if item.uid not in selected_product_uids
            )
        return (
            Compound.from_compound_ar(compound) if compound is not None else None,
            CompoundAlias.from_ar(alias, self.callback("compound")) if alias is not None else None,
            MedicinalProduct.from_medicinal_product_ar(product) if product is not None else None,
            products,
        )
