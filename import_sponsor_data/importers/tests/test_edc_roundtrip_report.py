"""Exercise the comparison CLI through fake database and HTTP boundaries."""

import json
import sys
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from importers import verify_edc_roundtrip


@pytest.mark.parametrize("mutation", ["none", "definition", "execution", "null-extension", "payload"])
def test_roundtrip_report_checks_current_graph_execution_and_source_custody(monkeypatch, capsys, mutation):
    expected = {
        "formatVersion": "2.0",
        "definition": {"document": {"study": {"versions": [{"id": "v1", "unknown": [None, False, 0, "invalid-source-value"]}]}}},
        "execution": {"forms": {"forms": []}, "visits": [], "studyTasks": [{"title": "Retained task", "_obligationIds": []}]},
        "source": {
            "artifacts": [{"artifactId": "source-1", "encoding": "base64-reference", "payload": "source-hash"}],
            "payloads": [{"sha256": "source-hash", "payload": "e30=", "byteLength": 2}],
            "normalizations": [],
        },
        "extensions": {"unknownNull": None, "_osbExport": {"sourceUnknown": False}},
    }
    actual = deepcopy(expected)
    actual["extensions"]["_osbExport"] = {"previous": deepcopy(expected["extensions"]["_osbExport"]), "census": {"rows": [{"kind": "mapping_authority"}]}}
    if mutation == "definition":
        del actual["definition"]["document"]["study"]["versions"][0]["unknown"]
    elif mutation == "execution":
        del actual["execution"]["studyTasks"][0]["_obligationIds"]
    elif mutation == "null-extension":
        del actual["extensions"]["unknownNull"]
    elif mutation == "payload":
        actual["source"]["payloads"] = []
    before = deepcopy(actual)
    context = MagicMock()
    context.__enter__.return_value.read_latest_payload.return_value = {"payload": {"sourceBundle": expected}, "payload_hash": "source-wire-hash"}
    monkeypatch.setenv("MAPPING_AUTHORITY_MODE", "shadow")
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "development")
    monkeypatch.setattr(verify_edc_roundtrip, "EcrfPlatformDb", lambda: context)
    monkeypatch.setattr(verify_edc_roundtrip.requests, "get", lambda *args, **kwargs: SimpleNamespace(raise_for_status=lambda: None, json=lambda: actual))
    monkeypatch.setattr(sys, "argv", ["verify_edc_roundtrip", "--study", "study-1", "--osb-study", "Study_1", "--api-base-url", "https://osb.invalid/api"])

    with pytest.raises(SystemExit) as result:
        verify_edc_roundtrip.main()

    report = json.loads(capsys.readouterr().out)
    assert actual == before
    assert report["sourceCounts"]["tasks"] == report["exportCounts"]["tasks"] == 1
    assert report["exportCensus"] == [{"kind": "mapping_authority"}]
    # Comparison never turns the separate native-authority hold into approval.
    assert result.value.code == 1
    if mutation == "none":
        assert report["differenceCount"] == 0
    else:
        assert report["differenceCount"] > 0
