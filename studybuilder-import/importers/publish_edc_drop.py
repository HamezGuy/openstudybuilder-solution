"""Publish exact OSB V2 export bytes to a configured EDC protocol-drop folder.

The manifest summarizes canonical study identity and portable execution. It
records the downloaded byte hash; neither publication nor the preview census
establishes native release or EDC activation authority. Existing explicit
migration-environment gates remain required.
"""

import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys
import urllib.request

from .utils.mapping_authority import assert_unsafe_legacy_mutation_allowed


def fetch_bundle(api: str, study_uid: str) -> tuple[dict, bytes]:
    url = f"{api}/integrations/edc/studies/{study_uid}/study-bundle"
    with urllib.request.urlopen(url, timeout=300) as resp:
        raw = resp.read(384 * 1024 * 1024 + 1)
        if len(raw) > 384 * 1024 * 1024:
            raise ValueError("EDC_EXCHANGE_TRANSPORT_LIMIT")
        bundle = json.loads(raw.decode("utf-8"))
        if bundle.get("formatVersion") != "2.0":
            raise ValueError("EDC_CURRENT_EXCHANGE_REQUIRED")
        return bundle, raw


def build_manifest(bundle: dict, study_uid: str, now_iso: str, bundle_bytes: bytes | None = None) -> dict:
    if bundle.get("formatVersion") != "2.0":
        raise ValueError("EDC_CURRENT_EXCHANGE_REQUIRED")
    execution = bundle["execution"]
    forms = execution["forms"]["forms"]
    fields = sum(len(f.get("fields", [])) for f in forms)
    dev = execution.get("deviationSpec")
    dev = dev if isinstance(dev, dict) else {}
    dev_rules = len(dev.get("rules", [])) if isinstance(dev.get("rules"), list) else 0
    tasks = execution.get("studyTasks")
    task_count = len(tasks) if isinstance(tasks, list) else (
        len(tasks.get("tasks", [])) if isinstance(tasks, dict) else 0)
    study = bundle["definition"]["document"].get("study", {})
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
                "Published from the OpenStudyBuilder V2 draft preview. "
                "The downloaded bytes are retained exactly; native "
                "reconciliation and release require separate decisions."
            ),
        },
        "run": {
            "cacheKey": f"osb-{study_uid}",
            "fileName": study.get("name") or study_uid,
            "readinessVerdict": None,
        },
        "bundle": {
            "file": "study.ecrfstudy",
            **({"contentHash": "sha256:" + hashlib.sha256(bundle_bytes).hexdigest(), "byteLength": len(bundle_bytes)} if bundle_bytes is not None else {}),
            "statistics": {
                "forms": len(forms),
                "fields": fields,
                "visits": len(execution.get("visits", [])),
                "assignments": len(execution.get("visitFormAssignments", [])),
                "deviationRules": dev_rules,
                "studyTasks": task_count,
            },
            "warningsDeclaredInFile": len(
                bundle.get("extensions", {}).get("_osbExport", {}).get("census", {}).get("rows", [])),
        },
        "sourceDocuments": [],
        "groundingChannels": [
            k for k in (
                "_provenance", "_retainedNarrative", "_streams",
                "_sourceEvidence", "_deviationSpec",
            ) if bundle.get("extensions", {}).get(k) is not None
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

    if not args.study or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in args.study):
        raise ValueError("OSB_STUDY_UID_PATH_INVALID")
    print(f"fetching Leg-C export for {args.study} …", flush=True)
    bundle, bundle_bytes = fetch_bundle(args.api.rstrip("/"), args.study)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    manifest = build_manifest(bundle, args.study, now_iso, bundle_bytes)

    run_id = f"osb-{args.study}"
    root = os.path.abspath(args.drop_dir)
    final_dir = os.path.join(root, run_id)
    tmp_dir = final_dir + ".tmp"
    os.makedirs(root, exist_ok=True)
    if os.path.isdir(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(tmp_dir)

    with open(os.path.join(tmp_dir, "study.ecrfstudy"), "wb") as fh:
        fh.write(bundle_bytes)
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
