"""Exact native ODM versions reachable from selected activity definitions.

Reachability supplies draft form candidates. It never proves a selected form
version or an event-to-visit assignment for a study.
"""

from copy import deepcopy
from hashlib import sha256

from clinical_mdr_api.services.ddf.usdm_mapping_context import native_json
from clinical_mdr_api.services.integrations.canonical_json import canonical_json
from clinical_mdr_api.services.integrations.edc_source_snapshot import is_source_snapshot


class NativeOdmCandidateError(ValueError):
    pass


def candidate_identity(uid: str, version: str) -> str:
    return "OSB_FORM_" + sha256(canonical_json([uid, version]).encode()).hexdigest().upper()


def read_study_odm_candidates(
    study_uid, study_value_version, *, form_reader, group_reader, item_reader,
):
    from clinical_mdr_api.services.integrations.study_authority import _get_study_odm_metadata

    closure = _get_study_odm_metadata(study_uid, study_value_version)
    if closure.get("scope") != "study-reachable-native-odm":
        raise NativeOdmCandidateError("OSB_NATIVE_ODM_CANDIDATE_SCOPE")
    cache = {}

    def identity(kind, reference):
        uid, version = reference.get("uid"), reference.get("version")
        if not isinstance(uid, str) or not uid or not isinstance(version, str) or not version:
            raise NativeOdmCandidateError(f"OSB_NATIVE_ODM_EXACT_VERSION_REQUIRED:{kind}/{uid}")
        return uid, version

    def read(kind, reference, reader):
        uid, version = identity(kind, reference)
        key = (kind, uid, version)
        if key not in cache:
            record = native_json(reader(uid, version=version))
            if not isinstance(record, dict) or (record.get("uid"), record.get("version")) != (uid, version):
                raise NativeOdmCandidateError(f"OSB_NATIVE_ODM_VERSION_MISMATCH:{kind}/{uid}@{version}")
            cache[key] = record
        return deepcopy(cache[key])

    reached_groups = {identity("itemGroup", row): row for row in closure.get("itemGroups", [])}
    reached_items = {identity("item", row): row for row in closure.get("items", [])}
    candidates = []
    seen = set()
    for reference in closure.get("forms", []):
        form = read("form", reference, form_reader)
        if is_source_snapshot(form):
            continue
        key = (form["uid"], form["version"])
        if key in seen:
            continue
        seen.add(key)
        if form.get("oid") != reference.get("oid"):
            raise NativeOdmCandidateError(f"OSB_NATIVE_ODM_FORM_OID_MISMATCH:{key}")
        # The graph query supplies only linked descendants. A complete version
        # read must still contain every witnessed link before it can be offered
        # as a candidate. A changed read cannot retain an obsolete scope proof.
        witnessed_groups = {
            identity("itemGroup", row) for row in reference.get("itemGroupRefs", [])
        }
        form_groups = {identity("itemGroup", row) for row in form.get("item_groups") or []}
        if not witnessed_groups or not witnessed_groups <= form_groups:
            raise NativeOdmCandidateError(f"OSB_NATIVE_ODM_CLOSURE_MISMATCH:form/{key}")
        groups = []
        for group_ref in form.get("item_groups") or []:
            group = read("itemGroup", group_ref, group_reader)
            group_key = identity("itemGroup", group)
            proof = reached_groups.get(group_key) if group_key in witnessed_groups else None
            if group_key in witnessed_groups:
                actual_items = {identity("item", row) for row in group.get("items") or []}
                proof_items = {
                    identity("item", row) for row in (proof or {}).get("itemRefs", [])
                }
                if (
                    proof is None or group.get("oid") != proof.get("oid")
                    or not proof_items or not proof_items <= actual_items
                ):
                    raise NativeOdmCandidateError(f"OSB_NATIVE_ODM_CLOSURE_MISMATCH:itemGroup/{group_key}")
            items = []
            for item_ref in group.get("items") or []:
                item = read("item", item_ref, item_reader)
                item_proof = reached_items.get(identity("item", item))
                if item_proof is not None and item_proof.get("oid") != item.get("oid"):
                    raise NativeOdmCandidateError(
                        f"OSB_NATIVE_ODM_CLOSURE_MISMATCH:item/{item['uid']}@{item['version']}"
                    )
                items.append({"reference": deepcopy(item_ref), "record": item})
            groups.append({"reference": deepcopy(group_ref), "record": group, "items": items})
        source = {
            "form": form, "groups": groups,
            "reachableForm": deepcopy(reference),
        }
        candidates.append({
            "refKey": candidate_identity(*key), "nativeUid": key[0],
            "nativeVersion": key[1], "nativeOid": form.get("oid"),
            "studyUid": study_uid, "studyValueVersion": study_value_version,
            "status": "draft-candidate",
            "requiresFormVersionReview": True, "requiresVisitAssignmentReview": True,
            "sourceSha256": sha256(canonical_json(source).encode()).hexdigest(),
            **source,
        })
    return {
        "formatVersion": "osb-native-odm-candidates/1",
        "scope": {"studyUid": study_uid, "studyValueVersion": study_value_version},
        "authority": "selected-activity-definition-reachability-only",
        "closure": closure, "candidates": candidates,
    }
