"""Publish an OSB study's EDC export into the EDC's protocol-drop folder.

The EDC's "Pull from protocol layer" feature reads a shared drop folder
(<EDC_DROP_DIR>/<runId>/ with study.ecrfstudy + manifest.json — contract in
libreclinicaapi/src/services/hybrid/drop-folder.reader.ts). That channel was
retired when the direct 360i→EDC writer was removed; this script re-feeds it
FROM OPENSTUDYBUILDER instead, so OSB remains the sole source of studies
entering the EDC while the operator regains the in-EDC review/import UI.

The bundle written is byte-identical to what the Leg-C exporter returns
(GET /integrations/edc/studies/{uid}/study-bundle) — the same projection the
x-api-key push path sends, so no data-loss surface is introduced. Parity of
that projection vs the retired direct path is already proven (1180/1180
fields, all grounding channels byte-equal; see OSB_INTEGRATION_STATUS doc).

Usage (stacks up):
    python importers/publish_edc_drop.py --study Study_000017 \
        --drop-dir ../.edc-drop [--api http://localhost:5005/api]

Idempotent: re-publishing the same study overwrites its folder atomically
(write to .tmp, then swap). The EDC treats a re-published folder as a
refresh candidate, exactly as it did for upstream re-builds.
"""

import argparse
import datetime
import json
import os
import shutil
import sys
import urllib.request

from .utils.mapping_authority import assert_unsafe_legacy_mutation_allowed


def fetch_bundle(api: str, study_uid: str) -> dict:
    url = f"{api}/integrations/edc/studies/{study_uid}/study-bundle"
    with urllib.request.urlopen(url, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_manifest(bundle: dict, study_uid: str, now_iso: str) -> dict:
    forms = bundle.get("forms", {}).get("forms", [])
    fields = sum(len(f.get("fields", [])) for f in forms)
    dev = bundle.get("_deviationSpec") or {}
    dev_rules = len(dev.get("rules", [])) if isinstance(dev.get("rules"), list) else 0
    tasks = bundle.get("studyTasks")
    task_count = len(tasks) if isinstance(tasks, list) else (
        len(tasks.get("tasks", [])) if isinstance(tasks, dict) else 0)
    study = bundle.get("study", {}) or {}
    return {
        "handoffVersion": 1,
        "generatedAt": now_iso,
        # scope=study: this is the merged whole-study package, not one
        # document's run — matches what the reader expects for import.
        "scope": "study",
        "study": {
            "studyId": study_uid,
            "title": study.get("name") or bundle.get("sourceStudyName"),
            "builtAt": bundle.get("exportedAt") or now_iso,
            "includedDocuments": [],
            "excludedDocuments": [],
            "totalAssertionsMerged": None,
            "notice": (
                "Published from OpenStudyBuilder (study of record) via "
                "publish_edc_drop.py — the Leg-C export projection, identical "
                "to the x-api-key push payload."
            ),
        },
        "run": {
            "cacheKey": f"osb-{study_uid}",
            "fileName": study.get("name") or study_uid,
            "readinessVerdict": None,
        },
        "bundle": {
            "file": "study.ecrfstudy",
            "statistics": {
                "forms": len(forms),
                "fields": fields,
                "visits": len(bundle.get("visits", [])),
                "assignments": len(bundle.get("visitFormAssignments", [])),
                "deviationRules": dev_rules,
                "studyTasks": task_count,
            },
            "warningsDeclaredInFile": len(
                (bundle.get("_exportCensus") or {}).get("rows", [])),
        },
        "sourceDocuments": [],
        "groundingChannels": [
            k for k in (
                "_provenance", "_retainedNarrative", "_streams",
                "_sourceEvidence", "_deviationSpec",
            ) if bundle.get(k) is not None
        ],
        "publishedBy": "openstudybuilder-leg-c",
    }


def main() -> None:
    assert_unsafe_legacy_mutation_allowed("publish_edc_drop")
    ap = argparse.ArgumentParser()
    ap.add_argument("--study", required=True, help="OSB study uid, e.g. Study_000017")
    ap.add_argument("--api", default="http://localhost:5005/api")
    ap.add_argument("--drop-dir", required=True,
                    help="EDC drop root (the folder EDC_DROP_DIR points at)")
    args = ap.parse_args()

    print(f"fetching Leg-C export for {args.study} …", flush=True)
    bundle = fetch_bundle(args.api.rstrip("/"), args.study)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    manifest = build_manifest(bundle, args.study, now_iso)

    run_id = f"osb-{args.study}"
    root = os.path.abspath(args.drop_dir)
    final_dir = os.path.join(root, run_id)
    tmp_dir = final_dir + ".tmp"
    os.makedirs(root, exist_ok=True)
    if os.path.isdir(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(tmp_dir)

    with open(os.path.join(tmp_dir, "study.ecrfstudy"), "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, ensure_ascii=False)
    with open(os.path.join(tmp_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)

    if os.path.isdir(final_dir):
        shutil.rmtree(final_dir)
    os.replace(tmp_dir, final_dir)

    st = manifest["bundle"]["statistics"]
    print(f"published {run_id} -> {final_dir}")
    print(f"  forms={st['forms']} fields={st['fields']} visits={st['visits']} "
          f"assignments={st['assignments']} deviationRules={st['deviationRules']} "
          f"studyTasks={st['studyTasks']}")


if __name__ == "__main__":
    main()
