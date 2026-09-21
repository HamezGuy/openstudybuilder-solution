"""Actual OSB producer -> actual CSL checkpoint -> OSB package, entirely in memory.

Run with --noconftest: no service runtime, database fixture or signature provider
is imported. Approval records below are synthetic prerequisites, not signatures.
"""

from __future__ import annotations

import ast
import base64
import copy
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest

API = Path(__file__).resolve().parents[3]
OSB = API.parents[1]
CSL = Path(os.environ.get("CSL_REPO_ROOT", str(OSB.parent / "ClinicalSemanticLayer")))
SERVICES = API / "services" / "integrations"


def definitions(path, names=None, dependencies=None):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes, found = [], set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [node.module]
                if isinstance(node, ast.ImportFrom)
                else [value.name for value in node.names]
            )
            if all(name.split(".")[0] in sys.stdlib_module_names for name in modules):
                nodes.append(node)
            continue
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            targets = {node.name}
        elif isinstance(node, ast.Assign):
            targets = {
                value.id for value in node.targets if isinstance(value, ast.Name)
            }
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = {node.target.id}
        else:
            continue
        if names is None or targets & names:
            nodes.append(node)
            found.update(targets)
    assert (
        names is None or not names - found
    ), f"Producer fixture dependencies changed: {names - found}"
    namespace = {"__name__": "native_package_contract_test", **(dependencies or {})}
    # Execute the actual definitions while replacing only their external I/O.
    # pylint: disable-next=exec-used
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace
    )  # pylint: disable=exec-used
    return namespace


HASHING = definitions(API / "generated" / "platform_contracts" / "hash_signing_v1.py")
FAMILIES = definitions(SERVICES / "osb_family_map.py")
REQUEST_VERSIONS = definitions(SERVICES / "osb_candidate_request_versions.py")
STUDY_HEADS = definitions(SERVICES / "native_study_head.py")
DB = SimpleNamespace()
CANDIDATE = definitions(
    SERVICES / "candidate_set.py",
    {
        "CANDIDATE_SET_MEDIA_TYPE",
        "OsbCandidateSetError",
        "_record",
        "_iso",
        "require_exactly_one_active_osb_binding",
        "active_osb_binding",
        "_assert_identity_binding",
        "_assert_candidate_native_checkpoint_current",
        "assert_candidate_set_current",
    },
    {**HASHING, **STUDY_HEADS, "db": DB},
)


class NativePreviewBusinessError(Exception):
    pass


METADATA = definitions(
    SERVICES / "study_metadata_mapping.py",
    dependencies={
        **HASHING,
        "db": DB,
        "BusinessLogicException": NativePreviewBusinessError,
        "NotFoundException": NativePreviewBusinessError,
        "ValidationException": NativePreviewBusinessError,
    },
)
CAPTURE_PROJECTION = definitions(
    SERVICES / "native_capture_projection.py", dependencies=HASHING
)
CAPTURE_MAPPING = definitions(
    SERVICES / "native_capture_mapping.py",
    dependencies={**HASHING, **CAPTURE_PROJECTION, "db": DB},
)
CAPTURE_FIXTURE = definitions(
    Path(__file__).with_name("test_native_capture_mapping.py"),
    {
        "TYPES",
        "source",
        "fixture",
        "NativePort",
        "apply",
        "selected_dependency_fixture",
        "selected_library_fixture",
    },
    {**HASHING, **CAPTURE_PROJECTION, **CAPTURE_MAPPING},
)
MAPPING = definitions(
    SERVICES / "mapping_decision_v1.py",
    dependencies={
        **HASHING,
        **FAMILIES,
        **CANDIDATE,
        **REQUEST_VERSIONS,
        "db": DB,
        "apply_metadata_selections": METADATA["apply_metadata_selections"],
        "apply_native_capture_selections": CAPTURE_MAPPING[
            "apply_native_capture_selections"
        ],
        "CAPTURE_READBACK_SCHEMA": CAPTURE_PROJECTION["CAPTURE_READBACK_SCHEMA"],
    },
)
SOURCE = definitions(
    Path(__file__).with_name("test_osb_mapping_decision_apply.py"),
    {
        "TENANT",
        "STUDY",
        "OPENAPI",
        "NATIVE",
        "FakeQuery",
        "_hash",
        "_decision_pair",
        "capture_decision_pair",
    },
    {**HASHING, **CANDIDATE, **MAPPING, **CAPTURE_PROJECTION},
)
STATE = definitions(
    SERVICES / "native_package_state_v2.py",
    dependencies={
        **HASHING,
        **FAMILIES,
        **CANDIDATE,
        **MAPPING,
        **CAPTURE_PROJECTION,
        **REQUEST_VERSIONS,
        **STUDY_HEADS,
        "read_capture_target": CAPTURE_MAPPING["read_capture_target"],
        "assert_selected_capture_identity": CAPTURE_MAPPING[
            "assert_selected_capture_identity"
        ],
        "db": DB,
        "METADATA_PATHS": METADATA["METADATA_PATHS"],
        "normalize_metadata_value": METADATA["_normalized"],
        "read_metadata_target": METADATA["read_metadata_target"],
        "verify_metadata_reference_bindings": METADATA[
            "verify_metadata_reference_bindings"
        ],
    },
)
PACKAGE = definitions(
    SERVICES / "native_package_v2.py",
    dependencies={**HASHING, **CANDIDATE, **STATE, "db": DB},
)
canonical = HASHING["canonical_json"]
hash_ref = HASHING["canonical_json_hash_ref"]
Error = CANDIDATE["OsbCandidateSetError"]
TENANT, STUDY = SOURCE["TENANT"], SOURCE["STUDY"]


def metadata_failure(code, message):
    raise Error(code, message, 409)


METADATA["_fail"] = metadata_failure
METADATA["_candidate_error_type"] = lambda: Error


def capture_failure(code, message):
    raise Error(code, message, 422)


CAPTURE_PROJECTION["fail"] = capture_failure
CAPTURE_MAPPING["fail"] = capture_failure
CAPTURE_FIXTURE["OsbCandidateSetError"] = Error


def h(value, schema, media="application/json"):
    return hash_ref(value, schema_version=schema, media_type=media)


def artifact(payload, kind, schema, media, id_field, version_field):
    return PACKAGE["_artifact_ref"](
        {
            "artifactId": payload[id_field],
            "artifactVersionId": payload[version_field],
            "kind": kind,
            "tenantId": TENANT,
            "payloadHash": h(payload, schema, media),
            "byteSize": len(canonical(payload).encode("utf-8")),
            "createdAt": "2026-09-10T12:00:00.000Z",
            "stableLocator": f"artifact://test/{kind}/{payload[version_field]}",
            **(
                {
                    "payloadContract": "accuratrials.cc.PreReleaseApprovalV1",
                    "payloadContractVersion": schema.rsplit("@", 1)[1],
                }
                if kind == "pre-release-approval-v1"
                else {}
            ),
        }
    )


class NativeMetadataPortFixture:
    """Inject native I/O and DTO validation; this suite tests the wire boundary."""

    def __init__(self):
        self.study = {
            "uid": "Study_990001",
            "study_parent_part": None,
            "current_metadata": {
                "version_metadata": {
                    "study_status": "DRAFT",
                    "version_timestamp": "2026-09-10T12:00:00+00:00",
                }
            },
        }
        self.writes = []
        self.reference_version = "1.0"

    def read(self, uid):
        assert uid == self.study["uid"]
        return copy.deepcopy(self.study)

    def resolve(self, kind, reference, _context, plan):
        assert plan["metadataPath"] == "high_level_study_design.trial_type_codes"
        assert kind == "termRef" and reference["codelistName"] == "Trial Type"
        assert _context["schemaVersion"] == "osb-mapping-context/2.0"
        assert _context["selectedPackages"][0]["packageUid"] == "Synthetic_DDF_1"
        uid = {"Trial type 1": "Term_1", "Trial type 2": "Term_2"}[
            reference["termName"]
        ]
        return {
            "value": {"term_uid": uid},
            "identity": {
                "resourceType": "CTTerm",
                "uid": uid,
                "version": self.reference_version,
                "parentUid": "TrialType",
                "parentVersion": "1.0",
            },
        }

    @staticmethod
    def validate(path, value):
        # Native model validation belongs to the materializer's tests. Keep this
        # isolated fixture restricted to known values without importing OSB.
        if path == "study_population.number_of_expected_subjects":
            assert isinstance(value, int) and not isinstance(value, bool) and value >= 0
        elif path in {
            "study_description.study_title",
            "high_level_study_design.study_stop_rules",
        }:
            assert isinstance(value, str)
        elif path == "high_level_study_design.trial_type_codes":
            assert isinstance(value, list) and all(
                isinstance(item.get("term_uid"), str) for item in value
            )
        else:
            raise AssertionError(f"Unsupported metadata fixture path: {path}")

    def lock(self, uid):
        assert uid == self.study["uid"]
        self.writes.append("lock")

    @staticmethod
    def lock_references(bindings):
        assert all(
            binding["identity"]["uid"].startswith("Term_") for binding in bindings
        )

    def preview(self, uid, body):
        assert uid == self.study["uid"]
        preview = copy.deepcopy(self.study)
        for section, values in body["current_metadata"].items():
            preview["current_metadata"].setdefault(section, {}).update(
                copy.deepcopy(values)
            )
        return preview

    def patch(self, uid, body):
        assert uid == self.study["uid"]
        self.writes.append("patch")

        def merge(target, values):
            for key, value in values.items():
                if isinstance(value, dict):
                    merge(target.setdefault(key, {}), value)
                else:
                    target[key] = copy.deepcopy(value)

        merge(self.study["current_metadata"], body["current_metadata"])
        self.study["current_metadata"]["version_metadata"][
            "version_timestamp"
        ] = "2026-09-10T12:00:01+00:00"


@lru_cache
def wire(
    spec=(("create", "compound_product_relationships"),),
    request_version="1.2.0",
    metadata_mode="distinct",
    capture_graph=None,
):
    request, candidate, decision, decision_artifact = SOURCE["_decision_pair"](*spec[0])
    intents, candidates, selections = [], [], []
    for index, (action, family) in enumerate(spec):
        req, cand, dec, _ = SOURCE["_decision_pair"](action, family)
        for values, entry in (
            (intents, req["typedSourceIntents"][0]),
            (candidates, cand["candidateRecords"][0]),
            (selections, dec["statement"]["selections"][0]),
        ):
            values.append({**entry, "factId": f"fact-{index + 1}"})
        intents[-1]["source"]["values"] = [
            {"sourcePath": "/test/name", "value": " e\u0301 ≥ 🧪\r\n"},
            {"sourcePath": "/test/boolean", "value": False},
            {"sourcePath": "/test/null", "value": None},
        ]
        if family == "study_metadata":
            path, value, options = (
                (
                    "high_level_study_design.study_stop_rules",
                    f"Rule {index + 1}",
                    {"metadataJoinedText": True},
                )
                if metadata_mode == "joined"
                else (
                    (
                        "high_level_study_design.trial_type_codes",
                        [{"termRef": "trial_type"}],
                        {
                            "metadataMultiValued": True,
                            "termRefs": {
                                "trial_type": {
                                    "termName": f"Trial type {index + 1}",
                                    "codelistName": "Trial Type",
                                    "basis": "Synthetic source term",
                                }
                            },
                        },
                    )
                    if metadata_mode == "multi"
                    else (
                        ("study_description.study_title", " e\u0301 ≥ 🧪\r\n", {})
                        if index
                        else ("study_population.number_of_expected_subjects", 342, {})
                    )
                )
            )
            intents[-1]["nativeStudyOperation"] = {
                "contractVersion": "OsbStudyMetadataPlanV1@1.0.0",
                "kind": "native-metadata",
                "osbResourceType": "StudyMetadata",
                "method": "PATCH",
                "route": "/studies/{study_uid}",
                "metadataPath": path,
                "metadataValue": value,
                **options,
            }
    capture_port = None
    if capture_graph is not None:
        capture_items, capture_port = capture_graph
        spec = tuple(
            (item["selection"]["action"], item["intent"]["resourceFamily"])
            for item in capture_items
        )
        request, candidate, decision, decision_artifact = SOURCE[
            "capture_decision_pair"
        ](capture_items, request_version)
        intents = request["typedSourceIntents"]
        candidates = candidate["candidateRecords"]
        selections = decision["statement"]["selections"]
        CAPTURE_MAPPING["NativeCapturePort"] = lambda: capture_port
    native = {
        **candidate["osbStudyIdentity"],
        "contractVersion": "1.0.0",
        "tenantId": TENANT,
        "platformStudyId": STUDY,
        "system": "osb",
    }
    snapshot = {
        "payloadHash": h(
            {"synthetic": [list(item) for item in spec]}, "SemanticSnapshotV1@1.0.0"
        )
    }
    request.update(
        {
            "contractVersion": f"OsbCandidateRequestV1@{request_version}",
            "tenantId": TENANT,
            "platformStudyId": STUDY,
            "requestId": "66666666-6666-4666-8666-666666666666",
            "requestVersionId": "77777777-7777-4777-8777-777777777777",
            "osbStudyIdentity": native,
            "semanticSnapshot": snapshot,
            "typedSourceIntents": intents,
            "sourceFactPackage": {
                "payloadHash": h({"testSource": True}, "SourceFactPackageV1@1.0.0")
            },
        }
    )
    metadata_port = NativeMetadataPortFixture()
    METADATA["NativeStudyMetadataPort"] = lambda: metadata_port
    METADATA["_validate_metadata_value"] = metadata_port.validate
    context = {
        "schemaVersion": "osb-mapping-context/2.0",
        "mappingAuthority": "OpenStudyBuilder",
        "contextHash": "context-1",
        "studyUid": native["nativeIdentity"],
        "selectedPackages": [
            {
                "catalogueName": "DDF CT",
                "packageUid": "Synthetic_DDF_1",
                "effectiveDate": "2026-09-01",
            }
        ],
    }
    offers = METADATA["prepare_metadata_offers"](
        intents, native["nativeIdentity"], context, port=metadata_port
    )
    for item in candidates:
        if item["resourceFamily"] == "study_metadata":
            item.update(
                offers[f'{item["factId"]}@{item["revision"]}:{item["targetKey"]}']
            )
    request_hash = h(request, request["contractVersion"], STATE["REQUEST_MEDIA"])
    candidate.update(
        {
            "candidateRecords": candidates,
            "osbStudyIdentity": native,
            "semanticSnapshot": snapshot,
            "mappingContext": context,
            "request": {
                "requestVersionId": request["requestVersionId"],
                "payloadHash": request_hash,
            },
        }
    )
    statement = decision["statement"]
    statement.update(
        {
            "selections": selections,
            "osbStudyIdentity": native,
            "candidateSetHash": h(
                candidate, "OsbCandidateSetV1@1.0.0", STATE["CANDIDATE_MEDIA"]
            ),
            "candidateRequestHash": request_hash,
            "semanticSnapshotHash": snapshot["payloadHash"],
            "decisionSetHash": h(selections, "StudyMappingSelectionSetV1@1.0.0"),
        }
    )
    # Rebind OSB's existing synthetic test records. No signing API is called.
    decision["humanSignature"]["recordHash"] = h(
        statement, "StudyMappingDecisionStatementV1@1.0.0"
    )
    decision["serviceAttestation"]["compositeHash"] = h(
        {key: value for key, value in decision.items() if key != "serviceAttestation"},
        "StudyMappingDecisionCompositeV1@1.0.0",
    )
    decision_artifact = MAPPING["_artifact_ref"](
        {
            **{
                key: value
                for key, value in decision_artifact.items()
                if key not in {"contractVersion", "descriptorHash"}
            },
            "payloadHash": h(decision, "StudyMappingDecisionV1@1.0.0"),
            "byteSize": len(canonical(decision).encode("utf-8")),
        }
    )
    store = SOURCE["FakeQuery"]()
    store.request_json, store.candidate_json, store.decision_json = map(
        canonical, [request, candidate, decision]
    )
    DB.cypher_query = store.cypher_query
    applied = MAPPING["apply_mapping_decision"](
        tenant_id=TENANT,
        platform_study_id=STUDY,
        decision_artifact=decision_artifact,
        actor="synthetic-test",
        osb_openapi_hash=SOURCE["OPENAPI"],
    )
    checkpoint_input = {
        "evidenceSet": applied["payload"],
        "evidenceArtifact": applied["artifactRef"],
        "decision": decision,
        "candidateRequest": request,
        "semanticSnapshotHash": snapshot["payloadHash"],
        "actor": "synthetic-test",
        "createdAt": "2026-09-10T12:00:00.000Z",
        "region": applied["artifactRef"]["region"],
        "producerEnvironment": "test",
        "producerVersion": "package-contract-test",
    }
    module_uri = (
        CSL / "packages/semantic-core/src/transformation-checkpoint-v1.ts"
    ).as_uri()
    script = (
        f"import {{buildTransformationCheckpointV1}} from {json.dumps(module_uri)};"
        "let text='';for await(const chunk of process.stdin)text+=chunk;"
        "console.log(JSON.stringify(buildTransformationCheckpointV1(JSON.parse(text))));"
    )
    built = subprocess.run(
        [
            os.environ.get("NODE", "node"),
            "--import",
            "tsx",
            "--input-type=module",
            "-e",
            script,
        ],
        input=json.dumps(checkpoint_input),
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=CSL,
        timeout=30,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    return {
        "producer": store,
        "metadata": metadata_port,
        "capture": capture_port,
        "applied": applied,
        "built": json.loads(built.stdout),
        "request": request,
        "candidate": candidate,
        "decision": decision,
    }


class MemoryStore:
    def __init__(self, value):
        self.__dict__.update(copy.deepcopy(value))
        self.checkpoint = self.built["checkpoint"]
        self.checkpoint_artifact = self.built["artifactRef"]
        self.inbound = {}
        self.reviews, self.packages, self.writes = {}, {}, []
        # Real OSB keeps an ended LATEST_DRAFT on lock. LATEST itself has no
        # version/status; the query only uses it to identify the current value.
        self.heads = [
            [
                "LATEST_DRAFT",
                "0.1",
                "DRAFT",
                "2026-09-10T12:00:00Z",
                "2026-09-10T12:00:01Z",
                True,
            ],
            ["LATEST_LOCKED", "0.1", "LOCKED", "2026-09-10T12:00:01Z", None, True],
        ]
        self.title = None
        self.label_override = None
        self.hook = None
        self.set_checkpoint(self.checkpoint)

    def set_checkpoint(self, payload):
        self.checkpoint = copy.deepcopy(payload)
        self.checkpoint_artifact = artifact(
            self.checkpoint,
            "transformation-checkpoint",
            "TransformationCheckpointV1@1.0.0",
            "application/json",
            "checkpointId",
            "checkpointVersionId",
        )
        self.inbound[
            (
                "transformation-checkpoint",
                self.checkpoint_artifact["payloadHash"]["value"],
            )
        ] = canonical(self.checkpoint)

    # Each return represents a distinct real repository query in this I/O fixture.
    def cypher_query(
        self, query, params=None
    ):  # pylint: disable=too-many-return-statements
        p = params or {}
        if self.hook:
            self.hook(query, p)
        if "tenant_id" in p:
            assert p["tenant_id"] == TENANT
        if "platform_study_id" in p:
            assert p["platform_study_id"] == STUDY
        if "study_uid" in p:
            assert p["study_uid"] == "Study_990001"
        if "EXECUTED_DECISION" in query:
            if p["payload_hash"] != self.applied["payloadHash"]["value"]:
                return ([], None)
            return (
                [
                    [
                        canonical(self.applied["payload"]),
                        canonical(self.applied["artifactRef"]),
                        canonical(self.decision),
                        self.applied["payload"]["decisionHash"]["value"],
                        h(self.decision, "StudyMappingDecisionV1@1.0.0")["value"],
                        self.applied["evidenceSetVersionId"],
                    ]
                ],
                None,
            )
        if "GENERATED_FROM" in query:
            if (
                p["payload_hash"]
                != self.applied["payload"]["candidateSetHash"]["value"]
            ):
                return ([], None)
            return (
                [
                    [
                        canonical(self.candidate),
                        canonical(self.request),
                        self.candidate["candidateSetVersionId"],
                    ]
                ],
                None,
            )
        if "RETURN binding.binding_id,study.uid,type(head)" in query:
            assert "head.end_date" in query and "value=latest" in query
            return (
                [
                    [
                        self.producer.binding[0],
                        "Study_990001",
                        *head[:3],
                        self.title,
                        None,
                        None,
                        None,
                        *head[3:],
                    ]
                    for head in self.heads
                ],
                None,
            )
        if "RETURN concept.managed_key" in query:
            rows = []
            for key, (payload, content_hash, version) in sorted(
                self.producer.managed.items()
            ):
                value = json.loads(payload)
                rows.append(
                    [
                        key,
                        value["resourceFamily"],
                        payload,
                        content_hash,
                        version,
                        value["factId"],
                        value["revision"],
                        value["targetKey"],
                    ]
                )
            return (rows, None)
        if "OsbInboundArtifact" in query:
            stored = self.inbound.get((p["kind"], p["payload_hash"]))
            return ([[stored]], None) if stored else ([], None)
        if "CREATE (review:OsbSpecialistReviewEvidenceV1" in query:
            self.writes.append("review")
            self.reviews[p["review_version_id"]] = [
                p["payload_json"],
                p["artifact_ref_json"],
            ]
            return ([], None)
        if "OsbSpecialistReviewEvidenceV1" in query:
            if "version_id" in p:
                row = self.reviews.get(p["version_id"])
                return ([copy.deepcopy(row)], None) if row else ([], None)
            rows = [
                [row[0]]
                for row in self.reviews.values()
                if h(
                    json.loads(row[0]),
                    "OsbSpecialistReviewEvidenceV1@1.0.0",
                    PACKAGE["SPECIALIST_REVIEW_MEDIA_TYPE"],
                )["value"]
                == p["payload_hash"]
            ]
            return (rows, None)
        if "CREATE (package:OsbNativePackageV2" in query:
            self.writes.append("package")
            self.packages[p["package_version_id"]] = [
                p["payload_json"],
                p["artifact_ref_json"],
                p["package_version_id"],
                p["payload_hash"],
                p["byte_size"],
            ]
            return ([], None)
        if "OsbNativePackageV2" in query:
            row = self.packages.get(p["version_id"])
            return ([copy.deepcopy(row)], None) if row else ([], None)
        result, columns = self.producer.cypher_query(query, p)
        if self.label_override is not None and "MATCH (root:" in query and result:
            result[0][2] = self.label_override
        return result, columns


def setup(
    spec=(("create", "compound_product_relationships"),),
    request_version="1.2.0",
    metadata_mode="distinct",
    capture_graph=None,
):
    producer = wire.__wrapped__ if capture_graph is not None else wire
    store = MemoryStore(producer(spec, request_version, metadata_mode, capture_graph))
    DB.cypher_query = store.cypher_query
    METADATA["NativeStudyMetadataPort"] = lambda: store.metadata
    if store.capture is not None:
        CAPTURE_MAPPING["NativeCapturePort"] = lambda: store.capture
    return store


def state(store):
    return STATE["load_checkpoint_native_state"](
        tenant_id=TENANT, platform_study_id=STUDY, checkpoint=store.checkpoint
    )


def review(store):
    return PACKAGE["record_specialist_review"](
        tenant_id=TENANT,
        platform_study_id=STUDY,
        input_payload={"transformationCheckpointArtifact": store.checkpoint_artifact},
        actor="synthetic-reviewer",
    )


def package_inputs(store, reviewed, approval_version="1.0.0"):
    manifest = {
        "contractVersion": "PlatformManifestV1@1.0.0",
        "tenantId": TENANT,
        "platformStudyId": STUDY,
        "manifestId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "manifestVersionId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "productionEligible": False,
    }
    manifest_artifact = artifact(
        manifest,
        "platform-manifest-v1",
        "PlatformManifestV1@1.0.0",
        PACKAGE["PLATFORM_MANIFEST_MEDIA_TYPE"],
        "manifestId",
        "manifestVersionId",
    )
    approval = {
        "approval_version": "PreReleaseApprovalV1",
        "approval_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "human_signature": {"tenant_id": TENANT, "platform_study_id": STUDY},
        "transformation_checkpoint_hash": store.checkpoint_artifact["payloadHash"][
            "value"
        ],
        "platform_manifest_hash": manifest_artifact["payloadHash"]["value"],
        "osb_authority_hash": store.checkpoint["osbAuthority"][
            "managedTargetCheckpointHash"
        ]["value"],
        "specialist_review_evidence_hash": reviewed["payloadHash"]["value"],
    }
    approval_artifact = artifact(
        approval,
        "pre-release-approval-v1",
        "PreReleaseApprovalV1@1.0.0",
        PACKAGE["PRE_RELEASE_APPROVAL_MEDIA_TYPE"],
        "approval_id",
        "approval_id",
    )
    # CommandCenter stores a separate, content-derived artifact version.
    approval_artifact = PACKAGE["_artifact_ref"](
        {
            **{
                key: value
                for key, value in approval_artifact.items()
                if key not in {"contractVersion", "descriptorHash"}
            },
            "artifactVersionId": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        }
    )
    if approval_version == "1.1.0":
        # Run CC's actual approval producer against an in-memory SQL fixture.
        # This creates no database record or real human approval.
        fixture = OSB.parent / "CommandCenter/test/fixtures/pre-release-approval.js"
        produced = subprocess.run(
            [os.environ.get("NODE", "node"), str(fixture)],
            input=json.dumps(
                {
                    "tenantId": TENANT,
                    "platformStudyId": STUDY,
                    "manifestId": manifest["manifestId"],
                    "manifestHash": manifest_artifact["payloadHash"]["value"],
                    "checkpointHash": store.checkpoint_artifact["payloadHash"]["value"],
                    "authorityHash": store.checkpoint["osbAuthority"][
                        "managedTargetCheckpointHash"
                    ]["value"],
                    "reviewHash": reviewed["payloadHash"]["value"],
                }
            ),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert produced.returncode == 0, produced.stderr
        actual = json.loads(produced.stdout)
        approval, approval_artifact = actual["approval"], actual["artifactRef"]
    store.inbound[
        ("platform-manifest-v1", manifest_artifact["payloadHash"]["value"])
    ] = canonical(manifest)
    store.inbound[
        ("pre-release-approval-v1", approval_artifact["payloadHash"]["value"])
    ] = canonical(approval)
    return {
        "transformationCheckpointArtifact": store.checkpoint_artifact,
        "platformManifestArtifact": manifest_artifact,
        "preReleaseApprovalArtifact": approval_artifact,
        "specialistReviewArtifact": reviewed["artifactRef"],
    }


def generate(inputs):
    return PACKAGE["generate_native_package_v2"](
        tenant_id=TENANT,
        platform_study_id=STUDY,
        input_payload=inputs,
        actor="synthetic-publisher",
    )


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.2.0", "1.3.0"])
def test_actual_producer_and_checkpoint_request_versions(version):
    store = setup(request_version=version)
    observed = state(store)
    assert observed["request"] == store.request
    assert (
        observed["candidateSetHash"] == store.decision["statement"]["candidateSetHash"]
    )
    assert (
        observed["requestHash"] == store.decision["statement"]["candidateRequestHash"]
    )


def test_selected_native_and_created_managed_objects_retain_exact_source_evidence():
    store = setup(
        (
            ("select", "criteria_templates"),
            ("select", "odm_items"),
            ("create", "compound_product_relationships"),
        )
    )
    before = canonical(store.applied)
    observed = state(store)
    content = PACKAGE["_package_content"](observed)
    assert (
        len(content["studyDesign"]["nativeRecords"])
        == len(content["captureDesign"]["nativeRecords"])
        == 1
    )
    assert len(content["studyDesign"]["managedConcepts"]) == 1
    assert len(content["contentIndex"]) == 3
    for entry, indexed in zip(
        observed["records"], content["contentIndex"], strict=True
    ):
        operation = entry["nativeOperationEvidence"]["evidence"]
        assert entry["payload"] == operation["normalizedReadBack"]
        assert entry["readBackHash"] == h(
            entry["payload"], operation["normalizedReadBackHash"]["schemaVersion"]
        )
        assert (
            h(entry["sourceIntent"], "OsbTypedSourceIntentV1@1.0.0")
            == operation["sourceInputHash"]
        )
        assert indexed["recordHash"] == h(entry, "OsbPackageNativeRecordV1@1.0.0")
        assert (
            entry["nativeOperationEvidence"]
            in store.applied["payload"]["evidenceRecords"]
        )
    assert content["contentIndexHash"] == h(
        content["contentIndex"], "OsbPackageContentIndexV1@1.0.0"
    )
    assert canonical(store.applied) == before
    assert not store.writes
    with pytest.raises(Error, match="Checkpoint is not zero-loss"):
        review(store)
    assert not store.writes


def test_selected_capture_producer_csl_checkpoint_and_package_preserve_full_native_evidence():
    items, port = CAPTURE_FIXTURE["selected_library_fixture"]()
    items[0]["intent"]["source"]["context"] = {
        "encounters": [],
        "relationships": [{"source": "synthetic", "condition": None}],
        "semanticAssociations": None,
        "unresolvedRelationships": [],
        "encounterProjection": {
            "contract": "study-build-encounter-projection/1",
            "buildHash": "a" * 64,
            "encounters": [
                {
                    "visitId": "visit-monthly",
                    "visitLabel": "Monthly follow-up",
                    "timepoint": None,
                    "required": False,
                    "derivedFrom": "soa_matrix",
                    "evidenceRef": "source-table:row-4:col-2",
                    "formObjectId": "form-a",
                    "properties": {
                        "condition": None,
                        "window": {"unit": "day", "value": 0},
                    },
                    "occurrenceEvidence": [{"page": 14}, {"page": 15}],
                }
            ],
        },
    }
    store = setup(request_version="1.3.0", capture_graph=(items, port))
    observed = state(store)
    assert len(observed["records"]) == len(items) == 6
    assert all(entry["sourceProjectionVerified"] for entry in observed["records"])
    assert all(
        entry["selection"]["action"] == "select" for entry in observed["records"]
    )
    assert store.checkpoint["blockers"] == []
    assert store.capture.creates == store.capture.associations == []
    intents = {
        STATE["_key"](intent): intent for intent in store.request["typedSourceIntents"]
    }
    assert {
        STATE["_key"](entry["sourceIntent"]) for entry in observed["records"]
    } == set(intents)
    for entry in observed["records"]:
        intent = intents[STATE["_key"](entry["sourceIntent"])]
        assert entry["payload"]["sourceBinding"]["source"] == intent["source"]
        assert entry["sourceIntent"] == intent
        assert entry["payload"]["native"] == store.capture.read(
            entry["resourceFamily"], entry["payload"]["uid"]
        )
    codelist = next(
        entry["payload"]
        for entry in observed["records"]
        if entry["resourceFamily"] == "controlled_terminology_codelists"
    )
    term = next(
        entry["payload"]
        for entry in observed["records"]
        if entry["resourceFamily"] == "controlled_terminology"
    )
    assert (codelist["native"]["name"]["version"], codelist["version"]) == (
        "2.0",
        "5.0",
    )
    assert (term["native"]["name"]["version"], term["version"]) == ("3.0", "7.0")
    package = generate(package_inputs(store, review(store)))
    assert len(package["payload"]["contentIndex"]) == 6
    assert canonical(package["payload"]["captureDesign"]["nativeRecords"]) == canonical(
        [
            entry
            for entry in observed["records"]
            if entry["resourceFamily"].startswith("odm_")
        ]
    )


@pytest.mark.parametrize("select_group", [False, True])
def test_selected_parent_created_child_graph_reaches_checkpoint_with_only_native_review_debt(
    select_group,
):
    graph = CAPTURE_FIXTURE["selected_dependency_fixture"](select_group=select_group)
    store = setup(request_version="1.3.0", capture_graph=graph)
    observed = state(store)
    assert len(observed["records"]) == 6
    assert all(
        entry["readBackHash"]["schemaVersion"]
        == CAPTURE_PROJECTION["CAPTURE_READBACK_SCHEMA"]
        for entry in observed["records"]
    )
    assert all(
        blocker["code"] == "NATIVE_CAPTURE_LIBRARY_REVIEW_REQUIRED"
        for blocker in store.checkpoint["blockers"]
    )
    assert store.checkpoint["blockers"]
    with pytest.raises(Error, match="Checkpoint is not zero-loss"):
        review(store)
    assert not store.writes


@pytest.mark.parametrize(
    "family,clock",
    [
        ("odm_forms", "version"),
        ("controlled_terminology_codelists", "name"),
        ("controlled_terminology_codelists", "attributes"),
        ("controlled_terminology", "name"),
        ("controlled_terminology", "attributes"),
    ],
)
def test_selected_capture_native_clock_changes_block_package_reread(family, clock):
    store = setup(
        request_version="1.3.0",
        capture_graph=CAPTURE_FIXTURE["selected_library_fixture"](),
    )
    entry = next(
        record
        for record in state(store)["records"]
        if record["resourceFamily"] == family
    )
    native = store.capture.values[entry["payload"]["uid"]]
    if clock == "version":
        native["version"] = "9.0"
    else:
        native[clock]["version"] = "9.0"
        if clock == "attributes":
            native["version"] = "9.0"
    with pytest.raises(Error, match="version"):
        state(store)
    assert not store.writes


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.2.0"])
def test_selected_full_capture_profile_is_not_accepted_for_historical_request_versions(
    version,
):
    store = setup(
        request_version="1.3.0",
        capture_graph=CAPTURE_FIXTURE["selected_library_fixture"](),
    )
    operation = store.applied["payload"]["evidenceRecords"][0]["evidence"]
    with pytest.raises(Error):
        STATE["_verify_native_projection"](
            operation,
            store.decision["statement"]["selections"][0],
            store.request["typedSourceIntents"][0],
            store.candidate["candidateRecords"][0],
            store.request["osbStudyIdentity"],
            request_contract_version=f"OsbCandidateRequestV1@{version}",
        )


def test_removing_native_checkpoint_blockers_does_not_manufacture_projection_evidence():
    store = setup((("select", "criteria_templates"),))
    store.checkpoint["blockers"] = []
    store.set_checkpoint(store.checkpoint)
    with pytest.raises(Error) as error:
        review(store)
    assert error.value.code == "OSB_NATIVE_SOURCE_PROJECTION_UNAVAILABLE"
    assert not store.writes


@pytest.mark.parametrize("edit", ["payload", "hash", "version", "membership"])
def test_managed_edits_after_checkpoint_fail_even_with_same_key(edit):
    store = setup()
    key = next(iter(store.producer.managed))
    stored = store.producer.managed[key]
    if edit == "payload":
        value = json.loads(stored[0])
        value["source"]["values"][1][
            "value"
        ] = 0  # False and 0 are not canonical-equal.
        stored[0] = canonical(value)
    elif edit == "hash":
        stored[1] = "sha256:" + "a" * 64
    elif edit == "version":
        stored[2] += 1
    else:
        store.producer.managed.pop(key)
    with pytest.raises(Error) as error:
        review(store)
    assert error.value.code == "OSB_POST_CHECKPOINT_NATIVE_EDIT"
    assert not store.writes


def test_native_readback_checks_content_not_only_uid_and_version():
    store = setup((("select", "criteria_templates"),))
    store.label_override = "edited name with the same UID/version"
    with pytest.raises(Error) as error:
        state(store)
    assert error.value.code == "OSB_POST_CHECKPOINT_NATIVE_EDIT"


@pytest.mark.parametrize("same_value", [True, False])
def test_historical_lock_cannot_hide_the_current_draft(same_value):
    store = setup()
    store.heads[0][4] = None
    store.heads[1][5] = same_value
    with pytest.raises(Error) as error:
        review(store)
    assert error.value.code == "OSB_NATIVE_STUDY_LOCK_REQUIRED"
    assert not store.writes


@pytest.mark.parametrize(
    "edit", ["missing-draft", "missing-lock", "not-current", "ended-lock"]
)
def test_a_native_lock_requires_closed_draft_and_current_open_lock(edit):
    store = setup()
    if edit == "missing-draft":
        store.heads.pop(0)
    elif edit == "missing-lock":
        store.heads.pop(1)
    elif edit == "not-current":
        store.heads[1][5] = False
    else:
        store.heads[1][4] = "2026-09-10T13:00:00Z"
    with pytest.raises(Error):
        review(store)
    assert not store.writes


def test_review_and_package_replay_preserve_exact_retained_bytes(monkeypatch):
    store = setup()
    first_review = review(store)
    inputs = package_inputs(store, first_review)
    first_package = generate(inputs)

    class Later:
        @staticmethod
        def now(_zone):
            return datetime.now(UTC) + timedelta(hours=1)

    monkeypatch.setitem(PACKAGE, "datetime", Later)
    replay_review = review(store)
    replay_package = generate(inputs)
    assert replay_review["replay"] is replay_package["replay"] is True
    assert replay_review["payload"] == first_review["payload"]
    assert replay_review["payloadHash"] == first_review["payloadHash"]
    assert replay_package["bytes"] == first_package["bytes"]
    assert replay_package["payloadHash"] == first_package["payloadHash"]
    assert store.writes == ["review", "package"]
    assert first_review["payload"]["nativeLockEvidence"]["nativeStatus"] == "LOCKED"
    assert (
        first_review["payload"]["nativeStateHash"]
        == first_package["payload"]["provenancePins"]["nativeStateHash"]
    )
    assert (
        first_package["payload"]["sourceFactPackage"]
        == store.request["sourceFactPackage"]
    )


@pytest.mark.parametrize("approval_version", ["1.0.0", "1.1.0"])
def test_actual_package_bytes_validate_schema_and_csl_approval_consumer(
    approval_version,
):
    store = setup()
    reviewed = review(store)
    inputs = package_inputs(store, reviewed, approval_version)
    package = generate(inputs)
    wire_input = {
        "packageBytes": base64.b64encode(package["bytes"]).decode("ascii"),
        "packageArtifact": package["artifactRef"],
        "transformationCheckpoint": store.checkpoint,
        "transformationCheckpointHash": store.checkpoint_artifact["payloadHash"],
        "platformManifest": json.loads(
            store.inbound[
                (
                    "platform-manifest-v1",
                    inputs["platformManifestArtifact"]["payloadHash"]["value"],
                )
            ]
        ),
        "platformManifestArtifact": inputs["platformManifestArtifact"],
        "preReleaseApproval": json.loads(
            store.inbound[
                (
                    "pre-release-approval-v1",
                    inputs["preReleaseApprovalArtifact"]["payloadHash"]["value"],
                )
            ]
        ),
        "preReleaseApprovalArtifact": inputs["preReleaseApprovalArtifact"],
        "specialistReview": reviewed["payload"],
        "specialistReviewArtifact": reviewed["artifactRef"],
        "tenantId": TENANT,
        "platformStudyId": STUDY,
        "actor": "synthetic-consumer",
        "createdAt": "2026-09-10T12:00:00.000Z",
        "region": package["artifactRef"]["region"],
        "producerEnvironment": "test",
        "producerVersion": "contract-test",
    }
    consumer = (
        CSL / "packages/semantic-core/src/governance-attestation-v1.ts"
    ).as_uri()
    schema = str(API / "generated/platform_contracts/osb-native-package-v2.schema.json")
    script = (
        "import assert from 'node:assert/strict';import fs from 'node:fs';"
        "import {Ajv2020} from 'ajv/dist/2020.js';import addFormats from 'ajv-formats';"
        f"import {{buildGovernanceAttestationV1,verifyGovernancePrerequisite,preReleaseApprovalSchemaVersion}} from {json.dumps(consumer)};"
        f"import {{platformCanonicalJson}} from {json.dumps((CSL / 'packages/semantic-core/src/platform-hash-signing-v1.ts').as_uri())};"
        "let text='';for await(const chunk of process.stdin)text+=chunk;"
        "const input=JSON.parse(text);input.packageBytes=Buffer.from(input.packageBytes,'base64');"
        "const payload=JSON.parse(input.packageBytes.toString('utf8'));"
        "const ajv=new Ajv2020({allErrors:true,strict:false});addFormats(ajv);"
        f"const valid=ajv.compile(JSON.parse(fs.readFileSync({json.dumps(schema)},'utf8')));"
        "assert.ok(valid(payload),JSON.stringify(valid.errors));"
        "assert.deepEqual(verifyGovernancePrerequisite({bytes:Buffer.from(platformCanonicalJson(input.preReleaseApproval)),"
        "artifact:input.preReleaseApprovalArtifact,kind:'pre-release-approval-v1',tenantId:input.tenantId,"
        "platformStudyId:input.platformStudyId,contractVersion:'PreReleaseApprovalV1',"
        "schemaVersion:preReleaseApprovalSchemaVersion(input.preReleaseApprovalArtifact),"
        "mediaType:'application/vnd.accuratrials.pre-release-approval-v1+json'}),input.preReleaseApproval);"
        "const accepted=buildGovernanceAttestationV1(input);"
        "assert.deepEqual(accepted.packageHash,input.packageArtifact.payloadHash);"
        "assert.deepEqual(accepted.packagePayload,payload);"
    )
    accepted = subprocess.run(
        [
            os.environ.get("NODE", "node"),
            "--import",
            "tsx",
            "--input-type=module",
            "-e",
            script,
        ],
        input=json.dumps(wire_input),
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=CSL,
        timeout=30,
        check=False,
    )
    assert accepted.returncode == 0, accepted.stderr


@pytest.mark.parametrize(
    "change",
    [
        "mismatched-version",
        "unsupported-version",
        "missing-basis",
        "not-ready",
        "unaccounted-claim",
    ],
)
def test_invalid_approval_versions_and_readiness_fail_before_package_write(change):
    store = setup()
    inputs = package_inputs(store, review(store), "1.1.0")
    ref = inputs["preReleaseApprovalArtifact"]
    approval = json.loads(
        store.inbound[("pre-release-approval-v1", ref["payloadHash"]["value"])]
    )
    fields = {
        key: value
        for key, value in ref.items()
        if key not in {"contractVersion", "descriptorHash"}
    }
    if change == "mismatched-version":
        fields["payloadContractVersion"] = "1.0.0"
    elif change == "unsupported-version":
        fields["payloadContractVersion"] = "1.2.0"
        fields["payloadHash"]["schemaVersion"] = "PreReleaseApprovalV1@1.2.0"
    else:
        if change == "missing-basis":
            del approval["readiness_basis"]
        elif change == "not-ready":
            approval["readiness_basis"]["ready"] = False
        else:
            approval["readiness_basis"]["counts"]["unaccountedClaims"] = 1
        fields["payloadHash"] = h(
            approval,
            "PreReleaseApprovalV1@1.1.0",
            PACKAGE["PRE_RELEASE_APPROVAL_MEDIA_TYPE"],
        )
        fields["byteSize"] = len(canonical(approval).encode("utf-8"))
        store.inbound[("pre-release-approval-v1", fields["payloadHash"]["value"])] = (
            canonical(approval)
        )
    inputs["preReleaseApprovalArtifact"] = PACKAGE["_artifact_ref"](fields)
    with pytest.raises(Error) as error:
        generate(inputs)
    assert error.value.code == (
        "OSB_PRE_RELEASE_APPROVAL_CONTRACT_VERSION_INVALID"
        if "version" in change
        else "OSB_PRE_RELEASE_APPROVAL_READINESS_INVALID"
    )
    assert store.writes == ["review"]


@pytest.mark.parametrize("edit", ["root", "managed", "unlock"])
def test_package_revalidates_current_content_and_lock_on_replay(edit):
    store = setup()
    inputs = package_inputs(store, review(store))
    generate(inputs)
    if edit == "root":
        store.title = "post-review change"
    elif edit == "managed":
        next(iter(store.producer.managed.values()))[2] += 1
    else:
        store.heads[0][4] = None
    with pytest.raises(Error):
        generate(inputs)
    assert store.writes == ["review", "package"]


@pytest.mark.parametrize(
    "layer", ["checkpoint", "candidate", "request", "evidence", "binding"]
)
def test_study_mismatch_is_rejected(layer):
    store = setup()
    if layer == "checkpoint":
        store.checkpoint["platformStudyId"] = "other-study"
    elif layer == "evidence":
        store.applied["payload"]["platformStudyId"] = "other-study"
    elif layer == "binding":
        store.producer.binding = ("other-binding", "Study_990001", "0.1")
    else:
        getattr(store, layer)["platformStudyId"] = "other-study"
    with pytest.raises(Error):
        state(store)
    assert not store.writes


@pytest.mark.parametrize(
    "field",
    [
        "receiptSetHash",
        "nativeEvidenceSetHash",
        "semanticSnapshotHash",
        "decisionSetHash",
    ],
)
def test_checkpoint_hash_tampering_is_rejected(field):
    store = setup()
    store.checkpoint[field]["value"] = "sha256:" + "a" * 64
    with pytest.raises(Error):
        state(store)


def test_census_and_authority_content_are_recomputed():
    for layer in ("census", "authority"):
        store = setup()
        if layer == "census":
            store.checkpoint["conservation"]["rows"][0]["sourceValueHash"]["value"] = (
                "sha256:" + "a" * 64
            )
        else:
            store.checkpoint["osbAuthority"]["managedTargetCheckpoint"][
                "operationCount"
            ] = 12
        with pytest.raises(Error):
            state(store)


def test_boolean_false_cannot_masquerade_as_zero_dropped_count():
    store = setup()
    store.checkpoint["conservation"]["counts"]["dropped"] = False
    store.set_checkpoint(store.checkpoint)
    with pytest.raises(Error):
        review(store)
    assert not store.writes


@pytest.mark.parametrize("kind", ["review", "package"])
def test_tampered_retained_replay_is_rejected(kind):
    store = setup()
    reviewed = review(store)
    inputs = package_inputs(store, reviewed)
    generated = generate(inputs)
    if kind == "review":
        row = store.reviews[reviewed["payload"]["reviewVersionId"]]
    else:
        row = store.packages[generated["packageVersionId"]]
    payload = json.loads(row[0])
    payload["platformStudyId"] = "other-study"
    row[0] = canonical(payload)
    with pytest.raises(Error):
        if kind == "review":
            review(store)
        else:
            generate(inputs)
    assert store.writes == ["review", "package"]


def test_edit_during_package_preparation_is_rejected_before_write():
    store = setup()
    inputs = package_inputs(store, review(store))

    def edit_after_replay_lookup(query, _params):
        if "MATCH (package:OsbNativePackageV2" in query:
            store.title = "concurrent edit"

    store.hook = edit_after_replay_lookup
    with pytest.raises(Error) as error:
        generate(inputs)
    assert error.value.code == "OSB_POST_REVIEW_NATIVE_EDIT"
    assert store.writes == ["review"]


def test_unknown_metadata_profile_cannot_select_its_own_hash_schema():
    store = setup((("select", "criteria_templates"),))
    operation = copy.deepcopy(
        store.applied["payload"]["evidenceRecords"][0]["evidence"]
    )
    operation["normalizedReadBackHash"][
        "schemaVersion"
    ] = "OsbStudyMetadataReadBackV1@99.0.0"
    with pytest.raises(Error) as error:
        STATE["_verify_native_projection"](
            operation,
            store.decision["statement"]["selections"][0],
            store.request["typedSourceIntents"][0],
            store.candidate["candidateRecords"][0],
            store.checkpoint["osbStudyIdentity"],
        )
    assert error.value.code == "OSB_PACKAGE_READBACK_PROFILE_UNSUPPORTED"


@pytest.mark.parametrize("version", ["1.2.0", "1.3.0"])
@pytest.mark.parametrize("mode", ["distinct", "joined", "multi"])
def test_actual_metadata_batch_readback_is_retained_and_matches_reviewed_values(
    mode, version
):
    store = setup(
        (("create", "study_metadata"), ("create", "study_metadata")),
        metadata_mode=mode,
        request_version=version,
    )
    assert store.metadata.writes == [
        "lock",
        "patch",
    ]  # The in-memory producer port only.
    observed = state(store)
    assert len(observed["records"]) == 2
    assert (
        len(PACKAGE["_package_content"](observed)["studyDesign"]["nativeRecords"]) == 2
    )
    for entry in observed["records"]:
        assert entry["scope"] == "study-metadata"
        assert (
            entry["targetIdentity"]["uid"]
            == store.checkpoint["osbStudyIdentity"]["nativeIdentity"]
        )
        assert (
            entry["targetIdentity"]["metadataPath"] == entry["payload"]["metadataPath"]
        )
        assert entry["targetIdentity"]["version"] == "2026-09-10T12:00:01+00:00"
        assert entry["readBackHash"] == h(entry["payload"], STATE["METADATA_SCHEMA"])
        assert (
            entry["payload"]
            == entry["nativeOperationEvidence"]["evidence"]["normalizedReadBack"]
        )
        assert entry["candidateRecord"] in store.candidate["candidateRecords"]
        assert entry["sourceProjectionVerified"] is False
    paths = {entry["targetIdentity"]["metadataPath"] for entry in observed["records"]}
    if mode == "distinct":
        assert len(paths) == 2
    else:
        assert len(paths) == 1
        expected = (
            "Rule 1\nRule 2"
            if mode == "joined"
            else [{"term_uid": "Term_1"}, {"term_uid": "Term_2"}]
        )
        assert all(
            entry["payload"]["metadataValue"] == expected
            for entry in observed["records"]
        )
    assert store.metadata.writes == ["lock", "patch"] and not store.writes


@pytest.mark.parametrize("edit", ["value", "timestamp"])
def test_metadata_edit_after_checkpoint_fails_with_the_same_study_uid(edit):
    store = setup((("create", "study_metadata"),))
    state(store)
    metadata = store.metadata.study["current_metadata"]
    if edit == "value":
        metadata["study_population"]["number_of_expected_subjects"] = 343
    else:
        metadata["version_metadata"]["version_timestamp"] = "2026-09-10T13:00:01+00:00"
    with pytest.raises(Error) as error:
        state(store)
    assert error.value.code == "OSB_POST_CHECKPOINT_NATIVE_EDIT"
    assert not store.writes


def test_metadata_reference_edit_after_checkpoint_fails_with_the_same_term_uid():
    store = setup((("create", "study_metadata"),), metadata_mode="multi")
    state(store)
    store.metadata.reference_version = "2.0"
    with pytest.raises(Error) as error:
        state(store)
    assert error.value.code == "OSB_STUDY_METADATA_REFERENCE_CHANGED"
    assert not store.writes


@pytest.mark.parametrize(
    "field",
    [
        "sourcePlanHash",
        "nativePreconditionHash",
        "nativeStudyId",
        "metadataPath",
        "referenceBindings",
    ],
)
def test_metadata_offer_cannot_lose_its_reviewed_lineage(field):
    store = setup((("create", "study_metadata"),))
    candidate = copy.deepcopy(store.candidate["candidateRecords"][0])
    del candidate["createOption"]["nativeStudyOperation"][field]
    operation = store.applied["payload"]["evidenceRecords"][0]["evidence"]
    with pytest.raises(Error):
        STATE["_verify_native_projection"](
            operation,
            store.decision["statement"]["selections"][0],
            store.request["typedSourceIntents"][0],
            candidate,
            store.checkpoint["osbStudyIdentity"],
        )


def test_metadata_cannot_hide_unprojected_source_fields_by_clearing_checkpoint_blockers():
    store = setup((("create", "study_metadata"),))
    store.checkpoint["blockers"] = []
    store.set_checkpoint(store.checkpoint)
    with pytest.raises(Error) as error:
        review(store)
    assert error.value.code == "OSB_NATIVE_SOURCE_PROJECTION_UNAVAILABLE"
    assert not store.writes


def test_metadata_reader_dispatch_retains_actual_metadata_helper_values(monkeypatch):
    path = "study_population.number_of_expected_subjects"
    selected = {"uid": "Study_990001", "metadataPath": path}
    study = {
        "uid": "Study_990001",
        "current_metadata": {
            "version_metadata": {"version_timestamp": "2026-09-10T12:00:01+00:00"},
            "study_population": {"number_of_expected_subjects": 342},
        },
    }
    reads = []

    def read(uid):
        reads.append(uid)
        return copy.deepcopy(study)

    def forbidden_library_read(*_args):
        pytest.fail("Study metadata must use the native StudyService reader.")

    monkeypatch.setitem(STATE, "_read_native_target", forbidden_library_read)
    monkeypatch.setitem(
        STATE,
        "read_metadata_target",
        lambda target: METADATA["read_metadata_target"](
            target, port=SimpleNamespace(read=read)
        ),
    )
    observed = STATE["_read_checkpoint_target"]("study_metadata", selected)
    assert observed == {
        "uid": "Study_990001",
        "version": "2026-09-10T12:00:01+00:00",
        "label": path,
        "resourceType": "StudyMetadata",
        "resourceFamily": "study_metadata",
        "metadataPath": path,
        "metadataValue": 342,
    }
    before_hash = h(observed, "OsbStudyMetadataReadBackV1@1.0.0")
    study["current_metadata"]["study_population"]["number_of_expected_subjects"] = 343
    edited = STATE["_read_checkpoint_target"]("study_metadata", selected)
    assert observed["uid"] == edited["uid"] and observed["version"] == edited["version"]
    assert before_hash != h(edited, "OsbStudyMetadataReadBackV1@1.0.0")
    assert reads == ["Study_990001", "Study_990001"]
