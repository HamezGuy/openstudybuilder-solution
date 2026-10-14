# Descriptive USDM observation runtime

The existing USDM mapper can return factual observations beside its normal
export. This opt-in path preserves the existing complete canonical definition,
including its mapping report and native records. It does not approve a source,
establish native-operation correspondence, or grant EDC execution coverage.

## Service boundary

`USDMService.get_by_uid_with_report(uid, study_value_version=version)` keeps its
historical behavior. Passing the keyword-only `observation_runtime` invokes the
existing `map_with_correspondence_report` on the same study read and exact native
version. The service validates current runtime authority before reading the study.

`read_pinned_canonical_definition(root)` also keeps its historical result.
`read_pinned_canonical_definition_with_observations(root,
observation_runtime=runtime)` returns three sibling values:

- `canonicalDefinition`: the unchanged historical definition and hash preimage.
- `correspondenceObservations`: the existing four authored-document registrations
  and their diagnostics; these remain partial descriptive observations.
- `observationRuntime`: the verified runtime manifest/hash, descriptive ruleset
  hash, reference-dictionary hash, complete factual censuses and kernel diagnostics.

The observed wrapper validates runtime authority before constructing native
services, because some constructors read terminology. Both exports use the same
definition-shaping and scope checks. Missing runtime, changed installation,
invalid source scope or failed inventory aborts the observed read; no partial
success replaces it. The ordinary export remains independently available.

## Runtime qualification

`services/ddf/usdm_observation_runtime.py` implements the closed
`OsbMapperObservationRuntimeV1@1.0.0` manifest described by
`schemas/platform/osb-mapper-observation-runtime-v1.schema.json`.

`ObservationRuntimePaths` identifies separate immutable API, Python and Node
roots, the retained API archive and the published shared-types archive. The
manifest records every regular file's exact bytes and hash in those roots,
the API archive, `Pipfile.lock`, runtime executables, image digest, and the complete
installed inventory kernel. Paths are sorted by UTF-16 code units. Missing or
extra files, unreadable directories, unsafe archive paths and links are refused.
No Git line-ending normalization is applied to this runtime seal.

`build_runtime_manifest` inventories already prepared roots and archives. It
does not build an image, install dependencies or qualify the supplied image
identity. `verify_runtime_files` proves file equality, not deployment authority.

`ObservationRuntime` requires an independently selected `expected_manifest_hash`
and an `assert_deployment_binding` callback. The callback receives copies of the
manifest/hash and the exact roots. It must independently verify the current
image identity, process and dependency search paths, immutable mount topology,
and absence of executable import shadows; it returns exactly `True` or refuses.
Repeating the image digest written in the manifest is insufficient. There is no
default callback or implicit qualification of a developer checkout/virtualenv.
File and deployment checks repeat around inventory execution.

The current kernel is the immutable shared-types archive whose SHA256 is
`114ce8e05c2f24ee1352ff61772d4e54a70990cc0920dc32e986fa2eecd04278`.
The Node bridge invokes its existing `inventoryUsdmV4Document` and reference
dictionary, rather than implementing a second reference resolver. It verifies
the actual Node version and confines package resolution to the sealed root.
The child receives fixed arguments, no shell or inherited Node hooks, a
256 MiB heap limit, a 15-second deadline, and bounded input/output/stderr.
Transport failures expose bounded errors without source values or child logs.

Objects, references and terminal values are retained in full, including ambiguous
or unresolved references, duplicate typed IDs, scoped resolution, null, false,
zero, empty strings and empty containers. Diagnostics are separate from census
hashes. All seven reference statuses reachable in the pinned dictionary have
real-kernel vectors; the declared `unsupported-metadata` status currently has no
registered attribute and has a separately labeled synthetic shape check.

## Qualification and remaining integration

The foundation gate exercised 95 cases with zero skips across the runtime,
inventory bridge, existing observations, service/export integration and existing
carrier-state behavior. Native repository responses and deployment authority in
these cases are synthetic; kernel execution and disposable file/archive checks
are real. A subsequent 13-case focused gate passed after replacing the bridge's
duplicate canonicalizer with the existing implementation from the pinned package;
the other 26 runtime cases were deselected in that follow-up. Formatting, scoped
lint, Node syntax and whitespace checks passed. The full installed reference
dictionary has canonical HashRef value
`sha256:09761aed4b54fb91822b893b2ce866e259eff62a4fe13563919b78e3bf52edcc`
under `UsdmReferenceDictionaryV4@4.0.0`.

Kernel-dependent tests use an already installed Node/kernel and retained archive.
`OSB_TEST_KERNEL_NODE_ROOT` and `OSB_TEST_KERNEL_ARCHIVE` select those test
prerequisites. Missing prerequisites explicitly skip their qualification tests;
ordinary Python test collection does not require Node or install dependencies.

No production runtime seal, new signed capture endpoint or execution family is
qualified by this change. A successor factual capture must pin the expected
runtime and ruleset hashes in its signed input, publish their full artifact
descriptors with the result, and preserve exact journal replay and transactional
native-state rechecks. Existing carrier V1/V2 inputs and complete-definition
equality remain unchanged.
