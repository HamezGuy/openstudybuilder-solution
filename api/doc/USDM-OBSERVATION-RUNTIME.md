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

### Deployed layout preflight, 15 October 2026

A read-only inspection of the existing `runtime-20261015-full1` API image
(`sha256:7020e49a9963e2e931b9f4a9da62c8eae0074b202087b0ae26946eb898f37236`)
confirmed that its current layout cannot supply this runtime seal:

- Node is absent from the image's executable search path, and none of the
  observation/kernel installation roots are present.
- Python 3.14.8's standard library lives under `/usr/local/lib/python3.14`,
  separately from Pipenv's installed application dependencies. The virtualenv
  contains three interpreter symlinks, which the complete-file verifier refuses.
- Installed `/app` contains 2,199 regular files, including 1,035 bytecode files;
  API tests have been removed. It is not byte-identical to the repository source
  archive. The root filesystem is writable.

The inspection used isolated Python with application imports and bytecode writes
disabled. It read filesystem metadata and container/image configuration without
reading secret values, contacting the database, changing configuration, or
restarting services. The full receipt is retained at
`D:/codex-task-evidence/01a118b3-5265-70a2-ba67-15b5b7bacfa8/osb-runtime-topology-preflight-20261015.json`,
SHA256 `356ec53e6998d1f8f533afa40e1730e8d0d2f7ae540151deacdf55d27afc25d1`.
It is layout evidence only, not runtime or deployment qualification.

The next packaging step must provide complete regular-file Python and Node/kernel
roots, retain an archive of the exact installed API bytes outside that API root,
and keep those roots immutable during execution. The existing release verifier
must independently bind the actual image identity, process, mounts and import
paths before an `ObservationRuntime` is constructed. Supplying an image digest
from the manifest back to its own callback cannot establish that binding.

This packaging work is separate from the existing EDC package 2.3 authored
roundtrip. That path retains the complete accepted bundle and portable IDs and
requires its existing native-root/checkpoint and authorization checks. It does
not gain descriptive correspondence authority from this preflight.

### Deferred packaging design

An opt-in `TARGET=observation` can preserve the current development and production
targets. Its Python prefix must include the interpreter, standard library and
locked dependencies as regular files; its separate Node root must include a
compatible Linux executable, the pinned kernel and locked runtime dependencies.
An exact compatible Node archive/image pin remains an unresolved build input.
Qualify isolated interpreter startup and the explicit API import path before
selecting this target for any of the three applications.

Generate the final manifest after the image is built and retain its expected
hash externally: embedding a manifest containing the final image digest into
that same image creates a digest cycle. Neither the manifest nor the retained
API archive may be placed inside a root it inventories.

The current Command Center OSB release verification checks an image tag. Its
`doVerify` / `emitVerifyScript` path also does not receive the release lock.
Independent qualification therefore requires carrying the retained image and
manifest pins through that existing verification path, checking the actual
container image identity and immutable mounts, and qualifying the observation
process's own import state. A new `docker exec python` process cannot prove the
running API worker's import paths or hooks. No production caller currently
constructs the observation runtime; packaging must not silently opt existing
M11, EDC export or carrier readers into it.
