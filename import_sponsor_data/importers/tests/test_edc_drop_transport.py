"""Isolated publisher checks: exact bytes, current execution scope and hashes."""

import hashlib
import io
import json
import sys

import pytest
from importers import publish_edc_drop


def bundle_bytes():
    bundle = {
        "formatVersion": "2.0",
        "exportedAt": "2026-09-09T10:11:12.000Z",
        "definition": {"document": {"study": {"name": "Exact μg study"}}},
        "execution": {
            "forms": {"formatVersion": "1.0", "forms": [{"fields": [{"unit": " μg / m² ", "validationPattern": "^exact\\s+$"}]}]},
            "visits": [{"refKey": "V1"}],
            "visitFormAssignments": [{"visitRef": "V1", "formRef": "F1"}],
            "deviationSpec": {"rules": [{"id": "R1"}]},
            "studyTasks": [{"title": "Task"}],
        },
        "extensions": {"unknown": [None, False, 0, ""], "_osbExport": {"census": {"rows": [{"kind": "mapping_authority"}]}}},
    }
    return (json.dumps(bundle, ensure_ascii=False, indent=3) + "\n").encode("utf-8")


def test_publisher_writes_exact_downloaded_bytes_and_hashes_current_execution(tmp_path, monkeypatch):
    raw = bundle_bytes()
    calls = []

    def download(url, *, timeout):
        calls.append((url, timeout))
        return io.BytesIO(raw)

    monkeypatch.setenv("MAPPING_AUTHORITY_MODE", "legacy")
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "development")
    monkeypatch.setenv("ALLOW_UNSAFE_LEGACY_EDC_HELPERS", "1")
    monkeypatch.setattr(publish_edc_drop.urllib.request, "urlopen", download)
    drop = tmp_path / "drop"
    monkeypatch.setattr(sys, "argv", ["publish_edc_drop", "--study", "Study_1", "--api", "https://osb.invalid/api/", "--drop-dir", str(drop)])

    publish_edc_drop.main()

    assert calls == [("https://osb.invalid/api/integrations/edc/studies/Study_1/study-bundle", 300)]
    written = (drop / "osb-Study_1" / "study.ecrfstudy").read_bytes()
    manifest = json.loads((drop / "osb-Study_1" / "manifest.json").read_text(encoding="utf-8"))
    assert written == raw
    assert manifest["bundle"]["contentHash"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    assert manifest["bundle"]["byteLength"] == len(raw)
    assert manifest["bundle"]["statistics"] == {"forms": 1, "fields": 1, "visits": 1, "assignments": 1, "deviationRules": 1, "studyTasks": 1}
    assert manifest["bundle"]["warningsDeclaredInFile"] == 1
    assert manifest["study"]["title"] == "Exact μg study"


def test_current_publisher_rejects_historical_flat_writer(monkeypatch):
    old = {"formatVersion": "1.0", "study": {"name": "Historical"}, "forms": {"forms": []}}
    monkeypatch.setattr(publish_edc_drop.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(old).encode("utf-8")))
    with pytest.raises(ValueError, match="EDC_CURRENT_EXCHANGE_REQUIRED"):
        publish_edc_drop.fetch_bundle("https://osb.invalid", "Study_1")
    with pytest.raises(ValueError, match="EDC_CURRENT_EXCHANGE_REQUIRED"):
        publish_edc_drop.build_manifest(old, "Study_1", "2026-09-09T10:11:12.000Z")
