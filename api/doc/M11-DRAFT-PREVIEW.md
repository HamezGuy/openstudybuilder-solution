# ICH M11 draft preview

The study M11 page requests `GET /usdm/v4/studyDefinitions/{study_uid}/m11`
with the selected `study_value_version`. The native mapping report, schedule of
activities, and study-design figure receive the same query value. Omitting it
remains supported for a current-source draft; the preview explicitly warns that
separate current reads may change during rendering. Selecting a metadata version
does not assert that the source is immutable or that a protocol was approved.

The renderer uses the existing `USDMService.get_by_uid_with_report` dictionary
contract. It displays unresolved mapping issues rather than treating a partially
mapped draft as strict USDM. The strict JSON USDM endpoint remains unchanged.
Report study/version, retained native study/version, and mapped version must
agree; ambiguous or mismatched provenance fails with HTTP 422 before visual
readers run. Existing authentication and study-visibility dependencies remain.

Header titles, acronym, sponsor protocol identifier and registry identifiers
come from the retained native study record. The protocol version comes from the
selected study version's referenced protocol document, without a first-item or
latest-version fallback. Missing or ambiguous design fields remain unavailable.
Missing quantities remain unknown, while zero and fractional source values are
preserved. Including healthy subjects is not evidence that the entire population
is healthy, and is not used to erase source indications.

The generated schedule retains each table's following footnote definitions in
their original order. Objective and endpoint rich text passes through the
existing approved HTML sanitizer before rendering, retaining supported formatting
while removing script tags and event handlers without changing native evidence.

The native mapper currently provides registry organizations but no authoritative
sponsor role and legal address. The preview therefore reports the sponsor as
unavailable; it cannot substitute the software vendor, a registry authority, or
an identifier scope. Approval dates, original-protocol status and amendment
identity also remain unavailable. The page is a source-derived draft containing
the M11 template's instructions and unfilled fields. It is not a complete M11
authoring workflow, an approved protocol, or evidence of TA3 clinical metrics.

The Vue page clears the previous preview during selection changes and errors,
cancels obsolete requests, and ignores late transport completion. A component-owned
`srcdoc` iframe permits the template's specification dialogs while omitting
`allow-same-origin`; it cannot access the parent application's document.

## Focused verification

From `api`, using the configured Python environment:

```text
python -m pytest clinical_mdr_api/tests/unit/services/test_m11_preview_http.py -q --no-cov -p no:cacheprovider
```

These public HTTP tests render the real Jinja template using the real native
mapper and isolated source fixtures. They check selected-version propagation,
non-first version selection, provenance rejection, unavailable and ambiguous
inputs, native identifiers, HTML escaping, visibility and query validation.
They do not use a Neo4j clear-database fixture or a live clinical source.

From `frontend`, with the installed Playwright Chromium or an explicit
`M11_BROWSER_CHANNEL` such as `msedge`:

```text
node --test tests/ich-m11-page.test.mjs
npm run build
```

The browser tests compile the actual Vue component and API method. They check
version changes, cancellation, out-of-order responses, error/retry/empty states,
unmount cleanup, and script-capable iframe isolation. Shared-machine runs use
the cooperative heavy-command wrapper described in workspace guidance.

### Verification recorded on 2026-10-09

- All 18 new public M11 route cases passed. The final combined run with existing
  mapper semantics, native corrections and native export regressions passed
  71 cases with no skips. Footnote preservation and rich-text sanitation tests
  first reproduced their respective defects before the corrections.
- Five actual Vue component/browser cases and the production frontend build
  passed. Independent browser review of the actual HTTP-rendered fixture
  confirmed native version 2.0 versus protocol version 1.1, draft/incomplete
  provenance, retained SoA footnote, readable layout and a working Full Title
  specification dialog, with no browser errors.
- Scoped mypy and Pylint passed for both changed Python runtime files;
  Black/isort passed for those files and the new route tests. The generated
  OpenAPI document and `apiVersion` agree at 3.0.700.
- An earlier adjacent native-domain run passed 25 cases but hit one existing
  byte-checksum assertion: Windows CRLF conversion of the pinned schema fixture.
  Its LF-normalized bytes exactly match Git HEAD and the expected checksum.
  The fixture and expected hash were left unchanged. Existing serializer and
  dependency deprecation warnings remain.

These checks use synthetic isolated data and do not establish production
readiness, complete regulatory authoring, or achieved SURPASS clinical metrics.
