"""Compare exact native structure without inventing unpinned library meaning."""

import math
from copy import deepcopy
from datetime import date, datetime, time
from typing import Any
from urllib.parse import quote

from neo4j.time import Date as Neo4jDate
from neo4j.time import DateTime as Neo4jDateTime
from neo4j.time import Duration as Neo4jDuration
from neo4j.time import Time as Neo4jTime
from neomodel import db

from clinical_mdr_api.domain_repositories.models.study import StudyValue
from clinical_mdr_api.domain_repositories.models.study_selections import (
    STUDY_SELECTION_CONCRETE_LABELS,
)
from clinical_mdr_api.domain_repositories.study_selections.study_version_snapshot_repository import (
    HELPER_LABELS,
    IMMUTABLE_STUDY_STATES,
    PROVENANCE_RELATIONS,
    StudySnapshotGraph,
    StudyVersionSnapshotRepository,
    expandable,
    immutable_version_cutoff,
)
from clinical_mdr_api.models.study_selections.study_version_snapshot import (
    StudySnapshotCoverageIssue,
    StudyVersionChange,
    StudyVersionDiff,
    StudyVersionSnapshot,
)
from clinical_mdr_api.services._utils import ensure_transaction
from clinical_mdr_api.services.ddf.usdm_ct_package_mapping import _source_json
from clinical_mdr_api.services.integrations.canonical_json import (
    canonical_hash,
    canonical_json,
)
from common.exceptions import ValidationException

LAYOUT_RELATIONS = frozenset({"HAS_PROTOCOL_SOA_CELL", "HAS_PROTOCOL_SOA_FOOTNOTE"})
ATTACHMENT_RELATIONS = frozenset(
    relationship.definition["relation_type"]
    for _, relationship in StudyValue.__all_relationships__
    if relationship.definition["direction"] == 1
)


def typed_value(value: Any) -> dict[str, Any]:
    """Preserve driver types and exact integer/float text before shared hashing."""
    kind: str
    payload: Any
    if value is None:
        kind, payload = "null", None
    elif isinstance(value, bool):
        kind, payload = "boolean", value
    elif isinstance(value, int):
        kind, payload = "integer", str(value)
    elif isinstance(value, float):
        ValidationException.raise_if(
            not math.isfinite(value),
            msg="STUDY_SNAPSHOT_SERIALIZATION_UNSUPPORTED: nonfinite float",
        )
        kind, payload = "float", repr(value)
    elif isinstance(value, str):
        kind, payload = "string", value
    elif isinstance(value, (Neo4jDateTime, Neo4jDate)):
        kind = "datetime" if isinstance(value, Neo4jDateTime) else "date"
        payload = _source_json(value)
    elif isinstance(value, (datetime, date, time)):
        kind, payload = type(value).__name__, (
            _source_json(value) if not isinstance(value, time) else value.isoformat()
        )
    elif isinstance(value, (Neo4jTime, Neo4jDuration)):
        kind = "time" if isinstance(value, Neo4jTime) else "duration"
        payload = str(value)
    elif isinstance(value, (bytes, bytearray)):
        kind, payload = "bytes", bytes(value).hex()
    elif isinstance(value, (list, tuple)):
        kind, payload = "array", [typed_value(item) for item in value]
    elif isinstance(value, dict) and all(isinstance(key, str) for key in value):
        kind, payload = "map", {key: typed_value(item) for key, item in value.items()}
    else:
        raise ValidationException(
            msg=f"STUDY_SNAPSHOT_SERIALIZATION_UNSUPPORTED: {type(value).__name__}"
        )
    return {"type": kind, "value": payload}


def typed_properties(properties: dict[str, Any]) -> dict[str, Any]:
    return {name: typed_value(value) for name, value in properties.items()}


def _selection_identity(node):
    labels = STUDY_SELECTION_CONCRETE_LABELS.intersection(node["labels"])
    uid = node["properties"].get("uid")
    ValidationException.raise_if(
        len(labels) != 1 or not isinstance(uid, str) or not uid.strip(),
        msg="STUDY_SNAPSHOT_SELECTION_IDENTITY_INVALID",
    )
    return next(iter(labels)), uid


def _key(*parts):
    return "/".join(quote(str(part), safe="") for part in parts)


def _is_provenance(edge):
    return (
        edge["type"] in PROVENANCE_RELATIONS
        or edge["type"].startswith("LATEST")
        or edge["type"] == "HAS_STUDY_VERSION"
    )


class _SnapshotBuilder:
    def __init__(self, graph: StudySnapshotGraph):
        self.graph = graph
        self.keys = {graph.value_id: "study_value"}
        self.selected = {graph.value_id}
        self.logical: dict[tuple[str, str], str] = {}
        self.edges: list[dict[str, Any]] = []
        self.issues: dict[tuple[str, str], StudySnapshotCoverageIssue] = {}
        self.observations: dict[str, dict[str, Any]] = {}
        self.provenance: dict[str, Any] = {
            "study_value_element_id": graph.value_id,
            "nodes": {},
            "excluded_edges": [],
        }
        self.exact_properties: dict[str, dict[str, Any]] = {}
        self.external_pins: dict[str, Any] = {}
        self.visiting: set[str] = set()
        self.cutoff = immutable_version_cutoff(graph)

    def issue(self, code, path, detail):
        self.issues[(code, path)] = StudySnapshotCoverageIssue(
            code=code, path=path, detail=detail
        )

    def _memberships(self):
        memberships = set()
        for edge in self.graph.edges:
            if edge["source"] != self.graph.value_id or _is_provenance(edge):
                continue
            if edge["type"] in LAYOUT_RELATIONS or edge["type"] == "HAS_STUDY_SUBPART":
                continue
            if edge["type"] not in ATTACHMENT_RELATIONS:
                self.issue(
                    "attachment_not_supported",
                    edge["type"],
                    "The native attachment has no declared StudyValue model relationship.",
                )
                continue
            target = edge["target"]
            membership = (edge["type"], target)
            ValidationException.raise_if(
                membership in memberships, msg="STUDY_SNAPSHOT_DUPLICATE_MEMBERSHIP"
            )
            memberships.add(membership)
            node = self.graph.nodes[target]
            if "StudySelection" in node["labels"]:
                logical = _selection_identity(node)
                key = _key("selection", *logical)
                self.logical.setdefault(logical, target)
                ValidationException.raise_if(
                    self.logical[logical] != target,
                    msg="STUDY_SNAPSHOT_DUPLICATE_IDENTITY",
                )
            elif "StudyField" in node["labels"]:
                name = node["properties"].get("field_name")
                ValidationException.raise_if(
                    edge["type"] != "HAS_PROJECT"
                    and (not isinstance(name, str) or not name),
                    msg="STUDY_SNAPSHOT_FIELD_IDENTITY_INVALID",
                )
                key = _key("field", edge["type"], name or "project")
            else:
                raise ValidationException(
                    msg="STUDY_SNAPSHOT_ATTACHMENT_TARGET_INVALID"
                )
            ValidationException.raise_if(
                key in self.keys.values() and self.keys.get(target) != key,
                msg="STUDY_SNAPSHOT_DUPLICATE_IDENTITY",
            )
            ValidationException.raise_if(
                self.graph.owners.get(target) != [self.graph.study_uid],
                msg="STUDY_SNAPSHOT_FOREIGN_MEMBERSHIP",
            )
            self.keys[target] = key
            self.selected.add(target)
        for edge in self.graph.edges:
            if (
                edge["source"] == self.graph.value_id
                and edge["type"] in LAYOUT_RELATIONS
            ):
                ValidationException.raise_if(
                    edge["target"] not in self.selected,
                    msg="STUDY_SNAPSHOT_LAYOUT_TARGET_NOT_SELECTED",
                )

    def _semantic_edges(self):
        active = set(self.selected)
        changed = True
        included: set[str] = set()
        while changed:
            changed = False
            for edge in self.graph.edges:
                source, target = edge["source"], edge["target"]
                if _is_provenance(edge):
                    continue
                if source not in active and not (
                    edge["type"] in {"HAS_FIELD", "HAS_STUDY_SUBPART"}
                    and target in self.selected
                ):
                    continue
                node = self.graph.nodes[target]
                if "StudySelection" in node["labels"] and target not in self.selected:
                    # Unchanged selections are shared by several StudyValues, so
                    # their copied edges also reach revisions that belong only to
                    # another version of this study. Such an edge is not part of
                    # this version: both endpoints must be selected here. A target
                    # with no owner or a foreign owner is corruption.
                    _selection_identity(node)
                    ValidationException.raise_if(
                        self.graph.owners.get(target) != [self.graph.study_uid],
                        msg="STUDY_SNAPSHOT_TARGET_NOT_SELECTED",
                    )
                    self.provenance["excluded_edges"].append(
                        {
                            "element_id": edge["id"],
                            "reason": "other_study_version_revision",
                        }
                    )
                    continue
                if edge["id"] not in included:
                    included.add(edge["id"])
                    self.edges.append(edge)
                if target not in active and expandable(node["labels"]):
                    active.add(target)
                    changed = True
        for edge in self.graph.edges:
            if _is_provenance(edge):
                self.provenance["excluded_edges"].append(
                    {
                        "element_id": edge["id"],
                        "type": edge["type"],
                        "properties": typed_properties(edge["properties"]),
                    }
                )

    def _reference(self, identity):
        node = self.graph.nodes[identity]
        labels, props = node["labels"], node["properties"]
        if "StudyValue" in labels:
            states = self.graph.external_versions.get(identity, [])
            owners = {row["study_uid"] for row in states}
            ValidationException.raise_if(
                len(owners) != 1 or any(not owner for owner in owners),
                msg="STUDY_SNAPSHOT_EXTERNAL_OWNER_AMBIGUOUS",
            )
            study_uid = next(iter(owners))
            pins = {
                str(row["state"]["version"])
                for row in states
                if row["state"].get("status") in IMMUTABLE_STUDY_STATES
                and row["state"].get("start_date") is not None
                and row["state"]["start_date"] <= self.cutoff
                and row["state"].get("version") not in {None, ""}
            }
            identity_props = {"study_uid": study_uid}
            if len(pins) == 1:
                version = next(iter(pins))
                identity_props["version"] = version
                key = _key("study_version", study_uid, version)
            else:
                key = _key("study_reference", study_uid)
                self.issue(
                    "external_study_version_not_pinned",
                    key,
                    "The referenced native StudyValue has no unique immutable version at the selected study timestamp. Candidate state evidence is provenance only.",
                )
            self.exact_properties[identity] = typed_properties(identity_props)
            self.external_pins[identity] = sorted(
                states, key=lambda row: canonical_json(typed_value(row))
            )
            return key
        uid = props.get("uid")
        identity_props = {"uid": uid} if isinstance(uid, str) and uid else {}
        if "MetaStudyField" in labels:
            identity_props = {
                name: props[name]
                for name in ("osb_field_name", "study_field_selection_type")
                if name in props
            }
        elif "Library" in labels and isinstance(props.get("name"), str):
            identity_props = {"name": props["name"]}
        ValidationException.raise_if(
            not identity_props, msg="STUDY_SNAPSHOT_REFERENCE_IDENTITY_REQUIRED"
        )
        key = _key(
            "reference",
            "+".join(sorted(labels)),
            canonical_hash(typed_properties(identity_props)),
        )
        self.exact_properties[identity] = typed_properties(identity_props)
        self.observations[key] = {
            "reference": key,
            "labels": sorted(labels),
            "properties": typed_properties(props),
        }
        self.issue(
            "library_value_not_pinned",
            key,
            "The native edge selects an identity, not an immutable library value. Observed attributes are excluded from the structural hash.",
        )
        return key

    def _value_pin(self, identity):
        pins = []
        for edge in self.graph.edges:
            if edge["type"] != "HAS_VERSION" or edge["target"] != identity:
                continue
            state = edge["properties"]
            start = state.get("start_date")
            if self.cutoff is None or start is None:
                continue
            if start > self.cutoff or state.get("status") not in {"Final", "Retired"}:
                continue
            root = self.graph.nodes[edge["source"]]
            uid, version = root["properties"].get("uid"), state.get("version")
            if isinstance(uid, str) and uid and isinstance(version, str) and version:
                pins.append(
                    {"labels": sorted(root["labels"]), "uid": uid, "version": version}
                )
        unique = {canonical_json(pin): pin for pin in pins}
        if len(unique) != 1:
            # The actual Value is still selected. Its version name is not invented.
            self.issue(
                "selected_value_version_not_pinned",
                "+".join(sorted(self.graph.nodes[identity]["labels"])),
                "The selected Value has no unique approved root/version at the selected study timestamp.",
            )
            return None
        return next(iter(unique.values()))

    def _node_key(self, identity):
        if identity in self.keys:
            return self.keys[identity]
        ValidationException.raise_if(
            identity in self.visiting, msg="STUDY_SNAPSHOT_DEPENDENCY_CYCLE"
        )
        node = self.graph.nodes[identity]
        if not expandable(node["labels"]):
            key = self._reference(identity)
            self.keys[identity] = key
            return key
        self.visiting.add(identity)
        children = [
            {
                "type": edge["type"],
                "target": self._node_key(edge["target"]),
                "properties": typed_properties(edge["properties"]),
            }
            for edge in self.edges
            if edge["source"] == identity
        ]
        if "CTTermContext" in node["labels"]:
            for kind, label in (
                ("HAS_SELECTED_TERM", "CTTermRoot"),
                ("HAS_SELECTED_CODELIST", "CTCodelistRoot"),
            ):
                targets = [
                    edge["target"]
                    for edge in self.edges
                    if edge["source"] == identity and edge["type"] == kind
                ]
                ValidationException.raise_if(
                    len(targets) != 1
                    or label not in self.graph.nodes[targets[0]]["labels"],
                    msg="STUDY_SNAPSHOT_CT_CONTEXT_INVALID",
                )
        signature: dict[str, Any] = {
            "labels": sorted(node["labels"]),
            "properties": typed_properties(node["properties"]),
            "relationships": sorted(children, key=canonical_json),
        }
        if not HELPER_LABELS.intersection(node["labels"]):
            signature["library_pin"] = self._value_pin(identity)
        key = _key("dependency", canonical_hash(signature))
        self.visiting.remove(identity)
        self.keys[identity] = key
        self.exact_properties[identity] = signature["properties"]
        if "library_pin" in signature:
            self.external_pins[identity] = signature["library_pin"]
        return key

    def build(self):
        self._memberships()
        self._semantic_edges()
        self.provenance["study_version_records"] = [
            {
                "element_id": edge["target"],
                "properties": typed_properties(
                    self.graph.nodes[edge["target"]]["properties"]
                ),
            }
            for edge in self.graph.edges
            if edge["source"] == self.graph.value_id
            and edge["type"] == "HAS_STUDY_VERSION"
        ]
        for edge in self.edges:
            self._node_key(edge["source"])
            self._node_key(edge["target"])
        nodes: dict[str, dict[str, Any]] = {}
        for identity, key in self.keys.items():
            node = self.graph.nodes[identity]
            self.provenance["nodes"].setdefault(key, []).append(identity)
            if identity == self.graph.value_id:
                continue
            record: dict[str, Any] = {
                "labels": sorted(node["labels"]),
                "properties": self.exact_properties.get(
                    identity, typed_properties(node["properties"])
                ),
            }
            if identity in self.external_pins and "StudyValue" not in node["labels"]:
                record["library_pin"] = self.external_pins[identity]
            if key in nodes:
                ValidationException.raise_if(
                    nodes[key]["properties"] != record["properties"],
                    msg="STUDY_SNAPSHOT_DUPLICATE_IDENTITY",
                )
                nodes[key]["occurrences"] += 1
            else:
                nodes[key] = {**record, "occurrences": 1}
        relationships = [
            {
                "source": self.keys[edge["source"]],
                "type": edge["type"],
                "target": self.keys[edge["target"]],
                "properties": typed_properties(edge["properties"]),
            }
            for edge in self.edges
        ]
        content = {
            "study_value": typed_properties(
                self.graph.nodes[self.graph.value_id]["properties"]
            ),
            "nodes": nodes,
            "relationships": sorted(relationships, key=canonical_json),
        }
        self.provenance["version_states"] = sorted(
            [typed_properties(state) for state in self.graph.version_states],
            key=canonical_json,
        )
        self.provenance["excluded_edges"] = sorted(
            {
                canonical_json(row): row for row in self.provenance["excluded_edges"]
            }.values(),
            key=canonical_json,
        )
        self.provenance["nodes"] = {
            key: sorted(values) for key, values in self.provenance["nodes"].items()
        }
        self.provenance["external_versions"] = {
            self.keys[key]: typed_value(value)
            for key, value in self.external_pins.items()
            if "StudyValue" in self.graph.nodes[key]["labels"]
        }
        return StudyVersionSnapshot(
            study_uid=self.graph.study_uid,
            version=self.graph.version,
            content=content,
            structural_hash="sha256:" + canonical_hash(content),
            provenance=self.provenance,
            observations=sorted(self.observations.values(), key=canonical_json),
            coverage_issues=[self.issues[key] for key in sorted(self.issues)],
        )


def build_snapshot(graph: StudySnapshotGraph) -> StudyVersionSnapshot:
    """Normalize a bounded native graph. No database or current-library reads."""
    return _SnapshotBuilder(graph).build()


def compare_snapshots(
    before: StudyVersionSnapshot, after: StudyVersionSnapshot
) -> StudyVersionDiff:
    ValidationException.raise_if(
        before.study_uid != after.study_uid, msg="STUDY_SNAPSHOT_STUDY_MISMATCH"
    )
    changes = []

    def visit(left, right, path, left_present=True, right_present=True):
        both_present = left_present and right_present
        if both_present and left == right:
            return
        if (
            both_present
            and isinstance(left, dict)
            and isinstance(right, dict)
            and set(left) != {"type", "value"}
            and set(right) != {"type", "value"}
        ):
            for name in sorted(set(left) | set(right)):
                segment = name.replace("~", "~0").replace("/", "~1")
                visit(
                    left.get(name),
                    right.get(name),
                    path + "/" + segment,
                    name in left,
                    name in right,
                )
            return
        changes.append(
            StudyVersionChange(
                path=path or "/",
                kind=(
                    "added"
                    if not left_present
                    else "removed" if not right_present else "changed"
                ),
                before_present=left_present,
                after_present=right_present,
                before=deepcopy(left),
                after=deepcopy(right),
            )
        )

    visit(before.content, after.content, "")
    return StudyVersionDiff(
        study_uid=before.study_uid,
        from_version=before.version,
        to_version=after.version,
        before=before,
        after=after,
        changes=changes,
        structurally_equal=not changes,
        semantic_comparison_complete=not before.coverage_issues
        and not after.coverage_issues,
    )


class StudyVersionSnapshotService:
    """Compare locked or released native versions, never mutable draft reads."""

    def __init__(self):
        self.repository = StudyVersionSnapshotRepository()

    @ensure_transaction(db)
    def get_snapshot(self, study_uid: str, version: str) -> StudyVersionSnapshot:
        return build_snapshot(self.repository.read(study_uid, version))

    @ensure_transaction(db)
    def get_diff(
        self, study_uid: str, from_version: str, to_version: str
    ) -> StudyVersionDiff:
        return compare_snapshots(
            self.get_snapshot(study_uid, from_version),
            self.get_snapshot(study_uid, to_version),
        )
