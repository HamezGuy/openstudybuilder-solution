"""Bounded raw reads of one selected native StudyValue, without DTO enrichment."""

from dataclasses import dataclass, field
from typing import Any

from neomodel import db

from clinical_mdr_api.domain_repositories.study_selections.study_version_repository import (
    StudyVersionRepository,
)
from clinical_mdr_api.services._utils import ensure_transaction
from common.exceptions import ValidationException

MAX_SNAPSHOT_NODES = 10000
MAX_SNAPSHOT_EDGES = 50000
MAX_SNAPSHOT_DEPTH = 16
HELPER_LABELS = frozenset({"CTTermContext", "Conjunction", "StudyVisitGroup"})
PROVENANCE_RELATIONS = frozenset(
    {"BEFORE", "AFTER", "AUDIT_TRAIL", "REPLACED_BY", "HAS_VERSION"}
)
IMMUTABLE_STUDY_STATES = frozenset({"LOCKED", "RELEASED"})


@dataclass
class StudySnapshotGraph:
    study_uid: str
    version: str
    value_id: str
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[dict[str, Any]] = field(default_factory=list)
    version_states: list[dict[str, Any]] = field(default_factory=list)
    external_versions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    owners: dict[str, list[str]] = field(default_factory=dict)


def expandable(labels: list[str]) -> bool:
    """Only selected helpers and actual Value nodes have a dependency frontier."""
    return bool(HELPER_LABELS.intersection(labels)) or any(
        label.endswith("Value") and label != "StudyValue" for label in labels
    )


def immutable_version_cutoff(graph: StudySnapshotGraph):
    """Read only immutable versions; a mutable draft cannot give an exact snapshot."""
    states = [
        state
        for state in graph.version_states
        if str(state.get("version")) == graph.version
        and state.get("status") in IMMUTABLE_STUDY_STATES
    ]
    ValidationException.raise_if(
        not states,
        msg="STUDY_SNAPSHOT_IMMUTABLE_VERSION_REQUIRED: select a locked or released version",
    )
    ValidationException.raise_if(
        any(state.get("start_date") is None for state in states),
        msg="STUDY_SNAPSHOT_VERSION_TIMESTAMP_REQUIRED",
    )
    # Subsequent state labels must not move the original immutable snapshot time.
    return min(state["start_date"] for state in states)


class StudyVersionSnapshotRepository:
    """Bounded immutable-version reads retain raw temporal and numeric types."""

    @staticmethod
    def _query(query: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        rows, columns = db.cypher_query(query, parameters)
        ValidationException.raise_if(
            len(rows) > MAX_SNAPSHOT_EDGES,
            msg="STUDY_SNAPSHOT_BOUND_EXCEEDED: relationship read",
        )
        return [dict(zip(columns, row)) for row in rows]

    @staticmethod
    def _add_edges(graph: StudySnapshotGraph, rows, seen):
        for row in rows:
            for side in ("source", "target"):
                node = row[side]
                graph.nodes[node["id"]] = node
            edge = row["edge"]
            if edge["id"] not in seen:
                seen.add(edge["id"])
                graph.edges.append(edge)
        ValidationException.raise_if(
            len(graph.nodes) > MAX_SNAPSHOT_NODES
            or len(graph.edges) > MAX_SNAPSHOT_EDGES,
            msg="STUDY_SNAPSHOT_BOUND_EXCEEDED: graph size",
        )

    @ensure_transaction(db)
    def read(self, study_uid: str, version: str) -> StudySnapshotGraph:
        ValidationException.raise_if(
            not isinstance(version, str) or not version.strip(),
            msg="STUDY_SNAPSHOT_VERSION_REQUIRED",
        )
        root, value, _ = StudyVersionRepository().get_selected_version(
            study_uid, version
        )
        value_id, root_id = value.element_id, root.element_id
        if not isinstance(value_id, str) or not value_id:
            raise ValidationException(msg="STUDY_SNAPSHOT_VERSION_IDENTITY_INVALID")
        if not isinstance(root_id, str) or not root_id:
            raise ValidationException(msg="STUDY_SNAPSHOT_ROOT_IDENTITY_INVALID")
        graph = StudySnapshotGraph(study_uid, version, value_id)
        parameters = {
            "value_id": value_id,
            "root_id": root_id,
            "limit": MAX_SNAPSHOT_EDGES + 1,
        }
        states = self._query(
            """
            MATCH (root:StudyRoot)-[state:HAS_VERSION]->(value:StudyValue)
            WHERE elementId(root)=$root_id AND elementId(value)=$value_id
            RETURN {id:elementId(value), labels:labels(value),
                    properties:properties(value)} AS node, properties(state) AS state
            LIMIT $limit
            """,
            parameters,
        )
        ValidationException.raise_if(not states, msg="STUDY_SNAPSHOT_VERSION_MISSING")
        graph.nodes[value_id] = states[0]["node"]
        graph.version_states = [row["state"] for row in states]
        immutable_version_cutoff(graph)
        rows = self._query(
            """
            MATCH (value:StudyValue)-[rel]-(other)
            WHERE elementId(value)=$value_id
              AND (startNode(rel)=value OR type(rel)='HAS_STUDY_SUBPART')
            WITH rel, startNode(rel) AS source, endNode(rel) AS target
            RETURN {id:elementId(source), labels:labels(source), properties:properties(source)} AS source,
                   {id:elementId(target), labels:labels(target), properties:properties(target)} AS target,
                   {id:elementId(rel), source:elementId(source), target:elementId(target),
                    type:type(rel), properties:properties(rel)} AS edge
            LIMIT $limit
            """,
            parameters,
        )
        seen: set[str] = set()
        self._add_edges(graph, rows, seen)
        selected = {
            edge["target"]
            for edge in graph.edges
            if edge["source"] == graph.value_id
            and edge["type"] not in PROVENANCE_RELATIONS
            and edge["type"] not in {"HAS_STUDY_SUBPART", "HAS_STUDY_VERSION"}
        }
        visited = {graph.value_id}
        frontier = selected
        for _ in range(MAX_SNAPSHOT_DEPTH):
            if not frontier:
                break
            visited.update(frontier)
            rows = self._query(
                """
                MATCH (node)-[rel]-(other) WHERE elementId(node) IN $frontier
                  AND (startNode(rel)=node OR type(rel) IN ['HAS_VERSION','HAS_FIELD'])
                WITH rel, startNode(rel) AS source, endNode(rel) AS target
                RETURN {id:elementId(source), labels:labels(source), properties:properties(source)} AS source,
                       {id:elementId(target), labels:labels(target), properties:properties(target)} AS target,
                       {id:elementId(rel), source:elementId(source), target:elementId(target),
                        type:type(rel), properties:properties(rel)} AS edge
                LIMIT $limit
                """,
                {"frontier": sorted(frontier), "limit": MAX_SNAPSHOT_EDGES + 1},
            )
            self._add_edges(graph, rows, seen)
            frontier = {
                edge["target"]
                for edge in graph.edges
                if edge["source"] in visited
                and edge["type"] not in PROVENANCE_RELATIONS
                and not edge["type"].startswith("LATEST")
                and edge["target"] not in visited
                and expandable(graph.nodes[edge["target"]]["labels"])
            }
        ValidationException.raise_if(
            bool(frontier), msg="STUDY_SNAPSHOT_BOUND_EXCEEDED: dependency depth"
        )
        external = [
            identity
            for identity, node in graph.nodes.items()
            if "StudyValue" in node["labels"] and identity != graph.value_id
        ]
        if external:
            for row in self._query(
                """
                MATCH (root:StudyRoot)-[state:HAS_VERSION]->(value:StudyValue)
                WHERE elementId(value) IN $identities
                RETURN elementId(value) AS identity, root.uid AS study_uid,
                       properties(state) AS state LIMIT $limit
                """,
                {"identities": external, "limit": MAX_SNAPSHOT_EDGES + 1},
            ):
                graph.external_versions.setdefault(row["identity"], []).append(
                    {"study_uid": row["study_uid"], "state": row["state"]}
                )
        owned = [
            identity
            for identity, node in graph.nodes.items()
            if "StudySelection" in node["labels"] or "StudyField" in node["labels"]
        ]
        if owned:
            for row in self._query(
                """
                MATCH (root:StudyRoot)-[:HAS_VERSION]->(:StudyValue)-[membership]->(node)
                WHERE elementId(node) IN $identities
                RETURN elementId(node) AS identity, collect(DISTINCT root.uid) AS owners
                LIMIT $limit
                """,
                {"identities": owned, "limit": MAX_SNAPSHOT_EDGES + 1},
            ):
                graph.owners[row["identity"]] = row["owners"]
        return graph
