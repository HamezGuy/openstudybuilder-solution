"""Read complete native library values at one explicit study timestamp.

Root references are dated observations, not invented version selections.
The approved-history selection and ambiguity policy is shared with compounds.
"""

from copy import deepcopy

from clinical_mdr_api.domain_repositories.models.controlled_terminology import (
    CTCodelistRoot,
    CTTermRoot,
)
from clinical_mdr_api.services.ddf.usdm_ct_package_mapping import _source_json
from clinical_mdr_api.services.studies.study_compound_snapshot import (
    StudyCompoundSnapshotReader,
    StudyCompoundSourceError,
    _utc,
)


class NativeHistory:
    @staticmethod
    def _get_version_relation_keys(root):
        return (root.has_version,)


def related(node, name):
    relation = getattr(node, name, None)
    return list(relation.all()) if relation is not None else []


def one(node, name):
    values = related(node, name)
    if len(values) > 1:
        raise StudyCompoundSourceError(f"STUDY_LIBRARY_RELATION_AMBIGUOUS: {name}")
    return values[0] if values else None


class StudyNativeLibrarySnapshot:
    def __init__(self, study_uid, study_value_version, *, as_of=None):
        self.reader = StudyCompoundSnapshotReader(
            None, study_uid, study_value_version, as_of=as_of,
        )
        self.issues = []
        self.records = []
        self._cache = {}

    @property
    def as_of(self):
        return self.reader.as_of

    def new_evidence_scope(self):
        """Read at the same fixed boundary with an independent evidence cache.

        Slicing shared records is insufficient: a cached term may add no new
        records, and previous reads can contain unrelated conflicts.
        """
        return StudyNativeLibrarySnapshot(
            self.reader.study_uid, self.reader.study_value_version, as_of=self.as_of,
        )

    def missing(self, kind, uid, reason):
        issue = {"kind": kind, "uid": uid, "reason": reason}
        if issue not in self.issues:
            self.issues.append(issue)

    def value(self, root, kind, *, value_identity=None, cutoff=None):
        uid = getattr(root, "uid", None) or getattr(root, "element_id", None)
        if root is None:
            self.missing(kind, uid, "STUDY_LIBRARY_SOURCE_MISSING")
            return None, None
        cutoff = _utc(cutoff) or self.as_of
        key = kind, self.reader._value_id(root), value_identity, cutoff
        if key in self._cache:
            return self._cache[key]
        try:
            value, state = self.reader._select_version(
                NativeHistory(), root, kind, uid, value_identity, cutoff,
            )
        except StudyCompoundSourceError as error:
            self.missing(kind, uid, error.msg)
            candidates = []
            for candidate in root.has_version.all():
                states = [
                    state for state in root.has_version.all_relationships(candidate)
                    if _utc(state.start_date) is not None and _utc(state.start_date) <= cutoff
                    and state.status in {"Final", "Retired"}
                    and (value_identity is None or self.reader._value_id(candidate) == value_identity)
                ]
                if states:
                    candidates.append({
                        "valueIdentity": self.reader._value_id(candidate),
                        "properties": _source_json(dict(candidate.__properties__)),
                        "states": [{
                            "version": state.version, "status": state.status,
                            "startDate": _utc(state.start_date).isoformat(),
                            "endDate": _utc(state.end_date).isoformat() if state.end_date else None,
                            "authorId": state.author_id, "changeDescription": state.change_description,
                        } for state in states],
                    })
            self.records.append({
                "kind": kind, "uid": uid, "state": "unresolved",
                "rootIdentity": self.reader._value_id(root), "asOf": cutoff.isoformat(),
                "studyUid": self.reader.study_uid, "studyValueVersion": self.reader.study_value_version,
                "candidates": candidates,
            })
            self._cache[key] = (None, None)
            return None, None
        evidence = {
            "kind": kind, "uid": uid,
            "rootIdentity": self.reader._value_id(root),
            "valueIdentity": self.reader._value_id(value),
            "version": state.version,
            "mode": "selected-value" if value_identity is not None else "snapshot-as-of",
            "asOf": cutoff.isoformat(),
            "studyUid": self.reader.study_uid,
            "studyValueVersion": self.reader.study_value_version,
            "properties": _source_json(dict(value.__properties__)),
            "versionState": {
                "version": state.version, "status": state.status,
                "startDate": _utc(state.start_date).isoformat(),
                "endDate": _utc(state.end_date).isoformat() if state.end_date else None,
                "authorId": state.author_id, "changeDescription": state.change_description,
            },
        }
        self.records.append(evidence)
        self._cache[key] = (value, evidence)
        return value, evidence

    def term(self, uid, *, cutoff=None):
        root = CTTermRoot.nodes.get_or_none(uid=uid)
        if root is None:
            self.missing("ctTerm", uid, "STUDY_LIBRARY_SOURCE_MISSING")
            return {"uid": uid, "name": None, "attributes": None}
        _, name = self.value(one(root, "has_name_root"), "ctTermName", cutoff=cutoff)
        _, attributes = self.value(
            one(root, "has_attributes_root"), "ctTermAttributes", cutoff=cutoff,
        )
        return {
            "uid": uid, "rootIdentity": self.reader._value_id(root),
            "name": deepcopy(name), "attributes": deepcopy(attributes),
        }

    def codelist(self, uid, *, include_terms=True, cutoff=None):
        root = CTCodelistRoot.nodes.get_or_none(uid=uid)
        if root is None:
            self.missing("ctCodelist", uid, "STUDY_LIBRARY_SOURCE_MISSING")
            return {"uid": uid, "name": None, "attributes": None, "terms": []}
        cutoff = _utc(cutoff) or self.as_of
        _, name = self.value(one(root, "has_name_root"), "ctCodelistName", cutoff=cutoff)
        _, attributes = self.value(
            one(root, "has_attributes_root"), "ctCodelistAttributes", cutoff=cutoff,
        )
        terms, unresolved_memberships = [], []
        if include_terms:
            for member in related(root, "has_term"):
                histories = root.has_term.all_relationships(member)
                states = [
                    state for state in histories
                    if _utc(state.start_date) is not None and _utc(state.start_date) <= cutoff
                    and (state.end_date is None or cutoff < _utc(state.end_date))
                ]
                if not states:
                    continue
                if len(states) != 1:
                    self.missing("ctCodelistMembership", uid, "STUDY_LIBRARY_MEMBERSHIP_AMBIGUOUS")
                    unresolved_memberships.append({
                        "memberIdentity": self.reader._value_id(member),
                        "properties": _source_json(dict(member.__properties__)),
                        "relationships": [_source_json(dict(state.__properties__)) for state in states],
                    })
                    continue
                term_root = one(member, "has_term_root")
                if term_root is None:
                    self.missing("ctCodelistMembership", uid, "STUDY_LIBRARY_TERM_IDENTITY_REQUIRED")
                    unresolved_memberships.append({
                        "memberIdentity": self.reader._value_id(member),
                        "properties": _source_json(dict(member.__properties__)),
                        "relationships": [_source_json(dict(states[0].__properties__))],
                    })
                    continue
                terms.append({
                    "memberIdentity": self.reader._value_id(member),
                    "properties": _source_json(dict(member.__properties__)),
                    "relationship": _source_json(dict(states[0].__properties__)),
                    "term": self.term(term_root.uid, cutoff=cutoff),
                })
            # Preserve the actual ordinal/order. An identity tie breaker is
            # stable source ordering and is not an invented response order.
            terms.sort(key=lambda entry: (
                entry["relationship"].get("order") is None,
                entry["relationship"].get("order") or 0,
                entry["memberIdentity"],
            ))
        return {
            "uid": uid, "rootIdentity": self.reader._value_id(root),
            "name": deepcopy(name), "attributes": deepcopy(attributes), "terms": terms,
            "unresolvedMemberships": unresolved_memberships,
        }

    def context(self, context, *, cutoff=None):
        if context is None:
            return None
        term_root, codelist_root = one(context, "has_selected_term"), one(context, "has_selected_codelist")
        if term_root is None or codelist_root is None:
            self.missing("ctTermContext", self.reader._value_id(context), "STUDY_LIBRARY_TERM_IDENTITY_REQUIRED")
        return {
            "contextIdentity": self.reader._value_id(context),
            "term": self.term(term_root.uid, cutoff=cutoff) if term_root is not None else None,
            "codelist": self.codelist(codelist_root.uid, cutoff=cutoff) if codelist_root is not None else None,
        }

    def unit(self, root):
        value, evidence = self.value(root, "unitDefinition")
        if value is None:
            return {"uid": getattr(root, "uid", None), "source": None}
        ucum_root = one(value, "has_ucum_term")
        _, ucum = self.value(ucum_root, "ucumTerm") if ucum_root is not None else (None, None)
        return {
            "uid": root.uid, "source": deepcopy(evidence),
            "ctUnits": [self.context(item) for item in related(value, "has_ct_unit")],
            "ctDimension": self.context(one(value, "has_ct_dimension")),
            "subsets": [self.context(item) for item in related(value, "has_unit_subset")],
            "ucum": deepcopy(ucum),
        }
