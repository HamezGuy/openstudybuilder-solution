"""Epoch terminology from an exact selected catalogue and dated native history."""

from copy import deepcopy
from datetime import date, datetime, time, timezone

from neomodel import db

from clinical_mdr_api.models.controlled_terminologies.ct_term import SimpleCTTermNameWithConflictFlag
from clinical_mdr_api.services.ddf.usdm_ct_package_mapping import _source_json
from clinical_mdr_api.services.studies.study_native_library_snapshot import StudyNativeLibrarySnapshot


PACKAGE_SCOPE_QUERY = """
    MATCH (selected:CTPackage {uid:$uid})
    MATCH ancestry=(selected)-[:EXTENDS_PACKAGE*0..]->(published:CTPackage)
    WHERE NOT EXISTS { MATCH (published)-[:EXTENDS_PACKAGE]->() }
    MATCH (catalogue:CTCatalogue)-[:CONTAINS_PACKAGE]->(selected)
    MATCH (published_catalogue:CTCatalogue)-[:CONTAINS_PACKAGE]->(published)
    RETURN properties(selected), catalogue.name, properties(published),
           published_catalogue.name, [p IN nodes(ancestry) | properties(p)]
"""


def resolve_epoch_term_history(
    epochs, study_uid, study_value_version, *, selections=None, snapshot=None,
    query=None,
):
    if not epochs:
        return
    if selections is None:
        from clinical_mdr_api.services.studies.study_standard_version_selection import StudyStandardVersionService

        selections = StudyStandardVersionService().get_standard_versions_in_study(
            study_uid, study_value_version=study_value_version, page_size=0,
        )
    snapshot = snapshot or StudyNativeLibrarySnapshot(study_uid, study_value_version)
    packages = {}
    issues = []
    for selection in selections:
        package = selection.ct_package
        if package.catalogue_name == "SDTM CT":
            record = _source_json(package.model_dump(mode="json"))
            key = package.uid, str(package.effective_date)
            if key in packages and packages[key] != record:
                issues.append("STUDY_EPOCH_CT_PACKAGE_SELECTION_CONFLICT")
            packages[key] = record
    package_scope, cutoff = None, None
    package_observations = []
    if len(packages) != 1:
        issues.append("STUDY_EPOCH_SELECTED_CT_PACKAGE_NOT_UNIQUE")
    elif not issues:
        package = next(iter(packages.values()))
        rows, _ = (query or db.cypher_query)(PACKAGE_SCOPE_QUERY, {"uid": package["uid"]})
        package_observations = _source_json(rows)
        if len(rows) != 1 or len(rows[0]) != 5:
            issues.append("STUDY_EPOCH_CT_PACKAGE_ANCESTRY_NOT_UNIQUE")
        else:
            selected, catalogue, published, published_catalogue, ancestry = _source_json(rows[0])
            if (
                selected.get("uid") != package["uid"] or catalogue != "SDTM CT"
                or published_catalogue != catalogue or not ancestry
                or ancestry[0] != selected or ancestry[-1] != published
                or any(not row.get("uid") for row in ancestry)
                or len({row.get("uid") for row in ancestry}) != len(ancestry)
                or package.get("extends_package") != (ancestry[1]["uid"] if len(ancestry) > 1 else None)
                or str(selected.get("effective_date")) != str(package["effective_date"])
            ):
                issues.append("STUDY_EPOCH_CT_PACKAGE_IDENTITY_MISMATCH")
            else:
                try:
                    dates = [date.fromisoformat(row["effective_date"]) for row in ancestry]
                    if any(parent > child for child, parent in zip(dates, dates[1:])):
                        raise ValueError("A selected package cannot precede its ancestor")
                except (KeyError, TypeError, ValueError):
                    issues.append("STUDY_EPOCH_CT_PACKAGE_DATE_INVALID")
                else:
                    cutoff = min(
                        datetime.combine(dates[0], time.max, timezone.utc), snapshot.as_of,
                    )
                    package_scope = {
                        "selectedPackage": selected, "selectedCatalogue": catalogue,
                        "publishedPackage": published, "publishedCatalogue": published_catalogue,
                        "ancestry": ancestry, "selection": package,
                    }
    for epoch in epochs:
        term_observations = {}
        witness = {
            "mode": "selected-standard-date-observation",
            "studyUid": study_uid, "studyValueVersion": study_value_version,
            "studyEpochUid": epoch.uid, "nativeStudyAsOf": snapshot.as_of.isoformat(),
            "package": package_scope, "cutoff": cutoff.isoformat() if cutoff else None,
            "selectedPackages": list(packages.values()),
            "packageObservations": package_observations,
            "terms": {}, "records": [], "issues": list(issues),
        }
        for field in ("epoch", "subtype", "epoch_type"):
            term = getattr(epoch, field)
            if term is None:
                witness["issues"].append(f"STUDY_EPOCH_TERM_IDENTITY_MISSING:{field}")
                continue
            if cutoff is not None and term.term_uid not in term_observations:
                term_snapshot = snapshot.new_evidence_scope()
                term_observations[term.term_uid] = term_snapshot.term(term.term_uid, cutoff=cutoff)
                # Native name/attribute roots have no separate term UID. Bind
                # their evidence to the exact term requested, not a label or
                # an assumption that a component's element ID is the term UID.
                witness["records"].extend(
                    {**deepcopy(record), "termUid": term.term_uid}
                    for record in term_snapshot.records
                )
                witness["issues"].extend(deepcopy(term_snapshot.issues))
            observation = deepcopy(term_observations.get(term.term_uid))
            name_source = observation.get("name") if observation else None
            name = name_source["properties"].get("name") if name_source else None
            witness["terms"][field] = observation
            if name is None:
                witness["issues"].append(f"STUDY_EPOCH_TERM_HISTORY_MISSING:{field}")
            elif not isinstance(name, str):
                raise ValueError("STUDY_EPOCH_TERM_TEXT_INVALID")
            setattr(epoch, field, SimpleCTTermNameWithConflictFlag(
                term_uid=term.term_uid, sponsor_preferred_name=name,
                queried_effective_date=cutoff if name_source else None,
                date_conflict=name_source is None,
            ))
        epoch.terminology_source = witness
