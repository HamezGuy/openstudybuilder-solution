"""Import exact reviewed owner schemas; never derive runtime authority from them.

The carrier proposal and successor records are immutable review inputs. Their
full input hashes are retained beside the selected schemas for reproducibility.
"""

# Executable script names follow the repository's kebab-case CLI convention.
# pylint: disable=invalid-name

import argparse
import copy
import hashlib
import json
from pathlib import Path

DIRECTORY = (
    Path(__file__).resolve().parents[1]
    / "clinical_mdr_api/generated/platform_contracts"
)


def review_interface_schemas(carrier, successor):
    """Close the bounded read and signed release envelopes over owner schemas."""
    definitions = {**carrier["$defs"], **successor["commandInput"]["$defs"]}
    specialist = successor["specialist"]["properties"]

    def closed(properties):
        return {
            "type": "object",
            "additionalProperties": False,
            "required": list(properties),
            "properties": properties,
        }

    def document(properties):
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": definitions,
            **closed(properties),
        }

    ref = {"$ref": "#/$defs/ArtifactRef"}
    number = {"type": "integer", "minimum": 0, "maximum": 9007199254740991}
    sections = [
        "crosswalkRows",
        "terminalAtoms",
        "nativeRecords",
        "canonicalDefinition",
    ]
    request = {
        "transformationCheckpointArtifact": specialist[
            "transformationCheckpointArtifact"
        ],
        "osbStudyIdentity": successor["commandInput"]["properties"]["osbStudyIdentity"],
    }
    context = {
        "contractVersion": {"const": "OsbSpecialistReviewContextV2@2.0.0"},
        **{
            key: specialist[key]
            for key in (
                "tenantId",
                "platformStudyId",
                "transformationCheckpointArtifact",
                "nativeStateHash",
                "nativeLockEvidence",
                "canonicalDefinitionHash",
                "sourceBasisHash",
                "authoredExecution",
                "conservationHash",
                "reviewScope",
                "productionEligible",
                "clinicalApproval",
            )
        },
        "nativeStateArtifact": ref,
        "canonicalDefinitionArtifact": ref,
        "conservation": {"$ref": "#/$defs/Conservation"},
        "contentCounts": closed(
            {
                key: number
                for key in (
                    "managedConcepts",
                    "nativeObjects",
                    "crosswalkRows",
                    "terminalAtoms",
                )
            }
        ),
        "inspection": closed(
            {
                "counts": closed({key: number for key in sections}),
                "pageSizeLimit": {"const": 100},
                "pageByteLimit": {"const": 262144},
            }
        ),
    }
    page = {
        **context,
        "contractVersion": {"const": "OsbSpecialistReviewPageV2@2.0.0"},
        "section": {"enum": sections},
        "offset": number,
        "total": number,
        "items": {
            "type": "array",
            "maxItems": 100,
            "items": closed({"position": number, "value": {}}),
        },
        "nextOffset": {"anyOf": [number, {"type": "null"}]},
        "pageSizeLimit": {"const": 100},
        "pageByteLimit": {"const": 262144},
    }
    release = {
        "contractVersion": {"const": "OsbNativePackageReleaseInputV2@2.0.0"},
        **request,
        **{
            key: ref
            for key in (
                "platformManifestArtifact",
                "preReleaseApprovalArtifact",
                "specialistReviewArtifact",
            )
        },
    }
    pointer = {"type": "string"}
    artifact_pointer = closed({"artifact": ref, "pointer": pointer})
    native_source = {
        "stateArtifact": ref,
        "contentIndexPointer": pointer,
        "recordHash": {"$ref": "#/$defs/HashRef"},
    }
    terminal_source = {"crosswalk": artifact_pointer, "bundle": artifact_pointer}
    source = {
        "oneOf": [
            artifact_pointer,
            closed(native_source),
            closed(terminal_source),
            closed(
                {
                    **terminal_source,
                    "nativeRecord": closed({**native_source, "nativePointer": pointer}),
                }
            ),
        ]
    }
    proof = {
        "section": {"enum": sections},
        "position": number,
        "valueHash": {"$ref": "#/$defs/HashRef"},
        "source": source,
    }
    return {
        "contextRequest": document(request),
        "context": document(context),
        "pageRequest": document(
            {
                **request,
                "section": {"enum": sections},
                "offset": number,
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            }
        ),
        "page": document(page),
        "entryRequest": document(
            {**request, "section": {"enum": sections}, "position": number}
        ),
        "entryProof": document(proof),
        "releaseInput": document(release),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--carrier-proposal", type=Path, required=True)
    parser.add_argument("--successor-contracts", type=Path, required=True)
    parser.add_argument("--governance-schema", type=Path, required=True)
    parser.add_argument("--platform-schemas", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    carrier_bytes = args.carrier_proposal.read_bytes()
    successor_bytes = args.successor_contracts.read_bytes()
    governance_bytes = args.governance_schema.read_bytes()
    platform_bytes = {
        name: (args.platform_schemas / name).read_bytes()
        for name in ("artifact-ref-v1.schema.json", "hash-ref-v1.schema.json")
    }
    carrier = json.loads(carrier_bytes)
    successor = json.loads(successor_bytes)
    carrier_hash = hashlib.sha256(carrier_bytes).hexdigest()
    if successor["sourceProposalSha256"] != carrier_hash:
        raise ValueError("Successor schema names a different carrier source.")
    package_schema_bytes = (
        DIRECTORY / "osb-native-package-v2.schema.json"
    ).read_bytes()
    package_schema = json.loads(package_schema_bytes)
    governance_schema = json.loads(governance_bytes)
    resolved = copy.deepcopy(carrier["wireSchema"])
    resolved["$defs"]["OsbCanonicalStudyDefinition"] = package_schema["$defs"][
        "CanonicalStudyDefinition"
    ]
    resolved["$defs"]["StudyStandardsReferenceV1"] = governance_schema["$defs"][
        "StudyStandardsReferenceV1"
    ]
    outputs = {
        "authored-study-execution-v1.schema.json": carrier["wireSchema"],
        "authored-study-execution-v1-resolved.schema.json": resolved,
        "osb-native-package-v2-family.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://schemas.accuratrials.com/osb/OsbNativePackageV2/2.3",
            "$defs": {"Legacy": package_schema, "Carrier": resolved},
            "oneOf": [
                {"$ref": "#/$defs/Legacy"},
                {"$ref": resolved["$id"] + "#/$defs/Package23"},
            ],
        },
        "authored-review-successor-contracts.json": successor["schemas"],
        "authored-review-interface-contracts.json": review_interface_schemas(
            carrier["wireSchema"], successor["schemas"]
        ),
        "governance-attestation-v1.schema.json": json.loads(governance_bytes),
        **{name: json.loads(value) for name, value in platform_bytes.items()},
        "authored-execution-contract-pins.json": {
            "carrierProposalSha256": carrier_hash,
            "successorContractsSha256": hashlib.sha256(successor_bytes).hexdigest(),
            "governanceSchemaSha256": hashlib.sha256(governance_bytes).hexdigest(),
            "osbPackage22SchemaSha256": hashlib.sha256(
                package_schema_bytes
            ).hexdigest(),
            "platformSchemaSha256": {
                name: hashlib.sha256(value).hexdigest()
                for name, value in platform_bytes.items()
            },
            "artifactProfiles": carrier["artifactProfiles"] + successor["profiles"],
            "agreedProfileCorrections": {
                "accepted-canonical-study-exchange@2.0.0": {
                    "mediaType": "application/vnd.accuratrial.studybundle+json",
                    "reason": "Owner agreement preserves existing HashRefV1 media grammar; bundle payload remains formatVersion 2.0.",
                },
                "accepted-snapshot-projection-package": {
                    "descriptorVersions": ["1.2.0", "1.3.0", "1.4.0"],
                    "payloadVersions": ["1.2", "1.3", "1.4"],
                    "transportSchemaVersions": [
                        "SnapshotProjectionPackage@1.2.0",
                        "SnapshotProjectionPackage@1.3.0",
                        "SnapshotProjectionPackage@1.4.0",
                    ],
                    "reason": "New transport profile uses registered three-part semver; payload contractVersion and legacy CSL packageHash remain unchanged.",
                },
            },
        },
    }
    stale = []
    for name, value in outputs.items():
        text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        path = DIRECTORY / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(name)
        else:
            path.write_text(text, encoding="utf-8", newline="\n")
    if stale:
        print("Stale authored execution schemas: " + ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
