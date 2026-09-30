"""Hold native reference and vocabulary membership locks for one metadata PATCH."""

from typing import Any

from neomodel import db


def lock_study_metadata_references(bindings: list[dict[str, Any]]) -> None:
    if not bindings:
        return
    from clinical_mdr_api.services.integrations.candidate_set import (
        OsbCandidateSetError,
    )

    identities = [binding.get("identity") or {} for binding in bindings]
    identities.extend(
        identity["sourceUnitIdentity"]
        for identity in list(identities)
        if "sourceUnitIdentity" in identity
    )
    uids = sorted(
        {
            identity.get("uid")
            for identity in identities
            if isinstance(identity.get("uid"), str)
        }
    )
    if not uids or any(not identity.get("uid") for identity in identities):
        raise OsbCandidateSetError(
            "OSB_STUDY_METADATA_REFERENCE_INVALID",
            "Every native reference must name its recorded identity.",
            409,
        )

    # Native CT has separate name and attribute roots; locking CTTermRoot alone
    # does not serialize a new name version. Unit definitions also depend on
    # UCUM, dimensions and subsets. The owning codelists/membership and their
    # current members join the lock set so an existing alias cannot change
    # while the source names are re-resolved. No repository cache is used.
    rows, _ = db.cypher_query(
        """
        MATCH (root)
        WHERE root.uid IN $uids AND
              (root:CTTermRoot OR root:DictionaryTermRoot OR root:UnitDefinitionRoot)
        WITH collect(DISTINCT root) AS roots, collect(DISTINCT root.uid) AS found
        UNWIND roots AS root
        MATCH path=(root)-[:HAS_NAME_ROOT|HAS_ATTRIBUTES_ROOT|LATEST|LATEST_FINAL|
            HAS_CT_UNIT|HAS_UNIT_SUBSET|HAS_CT_DIMENSION|HAS_UCUM_TERM|
            HAS_SELECTED_TERM|HAS_SELECTED_CODELIST*0..6]->(dependency)
        WITH found, collect(DISTINCT dependency) AS dependencies
        UNWIND dependencies AS dependency
        OPTIONAL MATCH (ct_parent:CTCodelistRoot)-[:HAS_TERM]->
            (membership:CTCodelistTerm)-[:HAS_TERM_ROOT]->(dependency)
        OPTIONAL MATCH (dictionary_parent:DictionaryCodelistRoot)-[:HAS_TERM]->(dependency)
        WITH found, dependencies, collect(DISTINCT ct_parent) +
             collect(DISTINCT dictionary_parent) AS parents,
             collect(DISTINCT membership) AS memberships
        UNWIND dependencies + parents AS owner
        MATCH (owner)-[:HAS_NAME_ROOT|HAS_ATTRIBUTES_ROOT|LATEST|LATEST_FINAL|
            HAS_TERM|HAS_TERM_ROOT*0..4]->(member)
        WITH found, dependencies, parents, memberships, collect(DISTINCT member) AS members
        WITH found, dependencies + parents + memberships + members AS candidates
        UNWIND candidates AS candidate
        OPTIONAL MATCH (library:Library)-[:CONTAINS_CONCEPT|CONTAINS_TERM|
            CONTAINS_CODELIST|CONTAINS_DICTIONARY_TERM|CONTAINS_DICTIONARY_CODELIST]->(candidate)
        WITH found, collect(DISTINCT candidate) + collect(DISTINCT library) AS candidates
        UNWIND candidates AS node
        WITH DISTINCT found, node WHERE node IS NOT NULL
        ORDER BY elementId(node)
        WITH found, collect(node) AS nodes
        CALL apoc.lock.nodes(nodes)
        RETURN found, size(nodes)
        """,
        {"uids": uids},
    )
    if len(rows) != 1 or sorted(rows[0][0]) != uids or rows[0][1] < len(uids):
        raise OsbCandidateSetError(
            "OSB_STUDY_METADATA_REFERENCE_CHANGED",
            "A native reference disappeared before it could be locked.",
            409,
        )
