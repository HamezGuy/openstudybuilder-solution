"""Project an exact native CDISC package reading into published USDM code semantics."""

from datetime import date, datetime
import json
import re

from neo4j.time import Date as Neo4jDate, DateTime as Neo4jDateTime
from usdm_model.code import Code

from clinical_mdr_api.services.ddf.usdm_mapping_context import (
    USDMMappingAuthorityRequired,
    native_json,
)


# CDISC DDF-RA v4.0.0's published Pilot uses this URI and a terminology release
# date. Native package UIDs and sponsor package dates are separate source facts.
CDISC_CODE_SYSTEM = "http://www.cdisc.org"
CDISC_CATALOGUES = ("DDF CT", "SDTM CT", "CDASH CT", "PROTOCOL CT", "Protocol CT")

SELECTED_TERM_QUERY = """
    MATCH (package:CTPackage {uid: $package_uid})
    MATCH package_path=(package)-[:EXTENDS_PACKAGE*0..]->(published:CTPackage)
    WHERE NOT EXISTS { MATCH (published)-[:EXTENDS_PACKAGE]->() }
    MATCH (published)-[:CONTAINS_CODELIST]->(:CTPackageCodelist)
          -[:CONTAINS_TERM]->(:CTPackageTerm)
          -[:CONTAINS_ATTRIBUTES]->(attributes:CTTermAttributesValue)
          <-[version:HAS_VERSION]-(:CTTermAttributesRoot)
          <-[:HAS_ATTRIBUTES_ROOT]-(root:CTTermRoot)
    WHERE (root.uid = $concept_id OR attributes.concept_id = $concept_id)
      AND version.status IN ['Final', 'Retired']
      AND version.start_date <= $source_datetime
    MATCH (library:Library)-[:CONTAINS_TERM]->(root)
    MATCH (catalogue:CTCatalogue)-[:CONTAINS_PACKAGE]->(package)
    MATCH (published_catalogue:CTCatalogue)-[:CONTAINS_PACKAGE]->(published)
    RETURN properties(library), root.uid, elementId(attributes), properties(attributes),
           properties(package), catalogue.name, properties(published), published_catalogue.name,
           collect(DISTINCT properties(version)),
           [source_package IN nodes(package_path) | properties(source_package)]
"""


def _source_json(value):
    # Neo4j temporal values can contain nanoseconds. Their native ISO string
    # preserves that precision instead of rounding through datetime.to_native().
    if isinstance(value, (Neo4jDate, Neo4jDateTime)):
        return str(value)
    if isinstance(value, dict):
        return {key: _source_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_source_json(item) for item in value]
    return native_json(value)


def _release_date(value):
    if isinstance(value, datetime):
        raise ValueError("A published terminology release is a date, not a timestamp.")
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)).isoformat()


def selected_cdisc_code(mapper, concept_id, query):
    if concept_id is None:
        return mapper.get_void_usdm_code()
    for catalogue_name in CDISC_CATALOGUES:
        selected = mapper._ct_packages.get(catalogue_name)
        if selected is None:
            continue
        cutoff = mapper._study_as_of or mapper._effective_date_to_datetime(selected["effective_date"])
        rows, _ = query(SELECTED_TERM_QUERY, {
            "concept_id": concept_id,
            "package_uid": selected["uid"],
            "source_datetime": cutoff,
        })
        if not rows:
            continue
        if len(rows) != 1 or len(rows[0]) != 10:
            raise USDMMappingAuthorityRequired(
                f"USDM_CT_PACKAGE_TERM_AMBIGUOUS: {selected['uid']}/{concept_id}"
            )
        library, term_uid, value_id, attributes, package, catalogue, published, published_catalogue, versions, package_path = (
            _source_json(list(rows[0]))
        )
        source_path = f"study-standard-versions/{selected['uid']}/terms/{concept_id}"
        if not all(isinstance(item, dict) for item in (library, attributes, package)):
            raise USDMMappingAuthorityRequired("USDM_CT_PACKAGE_READBACK_INVALID")
        if (
            package.get("uid") != selected["uid"] or catalogue != catalogue_name
            or not isinstance(term_uid, str) or not term_uid
            or not isinstance(value_id, str) or not value_id
            or not isinstance(versions, list) or not versions
            or any(not isinstance(version, dict) for version in versions)
            or (attributes.get("concept_id") not in (None, "")
                and re.fullmatch(r"C[0-9]+", str(concept_id))
                and attributes.get("concept_id") != concept_id)
        ):
            raise USDMMappingAuthorityRequired("USDM_CT_PACKAGE_SOURCE_IDENTITY_MISMATCH")
        evidence = {
            "selectedPackage": package,
            "selectedCatalogue": catalogue,
            "selectionMetadata": selected["source"],
            "publishedPackage": published,
            "publishedCatalogue": published_catalogue,
            "publishedPackagePath": package_path,
            "library": library,
            "termUid": term_uid,
            "termValueIdentity": value_id,
            "attributes": attributes,
            "approvedNativeVersions": sorted(versions, key=lambda item: json.dumps(item, sort_keys=True)),
            "nativeStudyAsOf": _source_json(mapper._study_as_of),
            "libraryStateCutoff": _source_json(cutoff),
        }
        evidence_uid = json.dumps([selected["uid"], term_uid], separators=(",", ":"), ensure_ascii=False)
        mapper._context.retain(
            "ctPackageTermDefinition", evidence_uid, evidence,
            scope={"studyUid": getattr(mapper, "_study_uid", None),
                   "studyValueVersion": mapper._study_value_version},
        )
        try:
            selected_date = _release_date(package.get("effective_date"))
            published_date = _release_date(published.get("effective_date")) if isinstance(published, dict) else None
        except (ValueError, TypeError):
            selected_date = published_date = None
        if (
            library.get("name") != "CDISC"
            or not isinstance(published, dict) or not published.get("uid")
            or published_catalogue != catalogue_name
            or selected_date != selected["effective_date"]
            or published_date is None or published_date > selected_date
        ):
            mapper._context.unresolved(
                "USDM_CT_PUBLISHED_PACKAGE_AUTHORITY_REQUIRED", source_path,
                "Code/codeSystemVersion",
                "The selected native value has no exact CDISC published-package and release-date witness.",
                "Resolve its native CDISC package or unchanged published ancestor. A sponsor package date or library label is not a published terminology release.",
            )
            return mapper.get_void_usdm_code()
        # Native sponsor packages contain an ancestry link, not duplicated
        # published term nodes. Keep that actual path alongside both dates.
        if (
            not isinstance(package_path, list) or not package_path
            or any(not isinstance(node, dict) or not isinstance(node.get("uid"), str)
                   or not node["uid"] for node in package_path)
            or package_path[0] != package or package_path[-1] != published
            or len({node["uid"] for node in package_path}) != len(package_path)
            or selected["source"].get("extends_package") != (
                package_path[1]["uid"] if len(package_path) > 1 else None
            )
        ):
            raise USDMMappingAuthorityRequired("USDM_CT_PACKAGE_SOURCE_IDENTITY_MISMATCH")
        # The package's immutable attributes contain the published concept and
        # preferred term. A sponsor's independent name history is report data.
        return mapper._context.build(
            Code, source_path,
            id=mapper._id_manager.get_id(Code.__name__),
            code=attributes.get("concept_id"), decode=attributes.get("preferred_term"),
            codeSystem=CDISC_CODE_SYSTEM, codeSystemVersion=published_date,
            extensionAttributes=[
                mapper._native_extension("ct-package-term", evidence_uid, evidence)
            ],
            instanceType="Code",
        )
    return mapper.get_void_usdm_code()
