# Pipeline consolidation, 2026-09-21

## Canonical implementations

- `common/exception_handlers.py` owns error logging, rejection identifiers,
  tracing, serialization, status codes and exception headers for all three API
  applications. Thirteen repeated handlers were replaced with registrations.
  The consumer API still does not translate ordinary `ValueError` into HTTP 400;
  the extensions API keeps FastAPI's existing HTTP-exception response.
- `clinical_mdr_api/services/integrations/canonical_json.py` delegates to the
  existing generated platform hash/signing contract. Its `canonical_hash`
  adapter still returns the bare hexadecimal digest used by persisted records.
  Invalid inputs expose the generated `PlatformHashError.code`; a second
  canonical-JSON algorithm is no longer maintained.
- Native-route contract tests exercise the real routers and shared exception
  registration. The null-adjudication HTTP fixture no longer extracts and
  recompiles a copy of a handler from the application source.
- Earlier main commits already consolidated the companion-family contract
  generators and frontend gateway identity decoder. This change preserves those
  implementations rather than adding alternatives.

## Authority and fixture repairs

The wider regression run exposed outdated test data, not a reason to relax the
production contracts:

- Native-package checkpoints and governance requests now carry the region
  returned by the actual applied artifact. No region is guessed or defaulted.
- The synthetic USDM arm explicitly authors its data origin and rationale.
  Missing and unknown origin authority remain rejected.
- The origin-query fixture recognizes the existing repository's governed
  terminology lookup, including catalog/package/date, identifiers, membership
  and approved-version predicates. Its C188727/C176263 subset matches the
  checked-in DDF terminology snapshot in the shared-types repository byte for
  byte at the term level. The source file's SHA-256 is
  `986d01ce424d40cbc69c2e8eba65e75e26b46fcb79cd22064a236b512ba0bf1f`.
- Neo4j fixtures create actual `LATEST_DRAFT` and version relationships with the
  existing `Draft` status. Regression cases reject missing, ended and incorrectly
  classified draft authority. Production authority checks were not weakened.

The configured Black/isort formatting expands some previously compact fixtures;
the semantic changes are limited to the authority and contract repairs above.

## Verification

Executed with the repository's Python 3.14 environment:

- Shared common unit tests plus native, canonical-JSON and null-adjudication
  service unit tests: **484 passed, five subtests passed**, no failures.
- Real Neo4j tests in `test_native_item_observation_neo4j.py` and
  `test_selected_activity_item_observation_neo4j.py`: **38 passed**.
- Black and isort checks cover all 15 changed Python files; `git diff --check`
  passes.
- Scoped Pylint and mypy checks pass for the shared exception-handler module and
  canonical-JSON adapter. After aligning registration with FastAPI's typed
  decorator API, the 25 handler, hashing, route and HTTP regression tests pass.
- The platform coordinator's cross-language hash/signing, signing-trust and
  observability/privacy checks passed against the participating source trees.

Neo4j verification used a uniquely named, isolated loopback container from the
installed image
`sha256:42fd5b9ead4dd4211f6f91bd831c358e4e2117367d04633fbf88682ca4792b30`.
It had no host-data mounts and was removed after the tests. No production graph,
clinical export or live deployment was exercised. Existing serializer and
deprecation warnings remain; these checks are not a claim that every OSB UI,
integration or deployment suite ran.

## Branch and recovery boundary

Work is committed only on `main` in this primary checkout. Shared local guards
and the owner origin's main-only rule prevent ordinary non-main branch work.
The repository guidance no longer describes a Gitflow workflow.

OSB already had only main branch references when this consolidation began.
Commit `f67a1fa985a3bb88e1a9db273c6dff0927f3e583` previously landed additions
from the pruned IGS runtime snapshots. Their original tips remain under
`archive/*` tags. They are recovery history, not active branches, and must not
be merged as whole trees because their snapshots omit other repository trees.
This consolidation does not claim an ancestry merge of those snapshots.
