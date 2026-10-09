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

## Native section projection

The same selected native mapping report supplies these additional sections:

- Section 4.1 shows native high-level design and intervention metadata, including
  null reasons, durations with units, stop rules and separate root-arm/branch
  counts. Branches are not silently counted as additional native root arms.
- Section 5.1 shows all native population fields and the separately scoped cohort
  counts and explicit arm/branch associations. Counts are never summed.
- Sections 5.2 and 5.3 retain ordered inclusion and exclusion criteria, repeated
  text, source definition identifiers/versions and key-criterion indicators.
  Classification uses CDISC C25532/C25370, not sponsor display labels. Unknown
  categories remain unclassified; template-only selections remain uninstantiated.
  Native study-level criteria do not establish arm-specific eligibility.
- Section 6 shows intervention roles/types, source library versions, selected
  dispense/device facts, administrations, dose/frequency/route and composition.
  Arm, epoch and element applicability must agree with both canonical references
  and the retained native dosing and design-cell relationships. Missing,
  duplicate or conflicting identities cannot authorize an assignment.
- Product definitions and their ingredients/strengths remain visible separately
  from administration assignment. Assignment requires one exact native selected
  product UID/version and study/compound scope. Catalog order, matching names,
  allowed product routes and another compound's product cannot supply that
  authority. Strength denominators, when present, retain their value and unit;
  the current native mapper emits scalar strengths with numerator quantities.

Zero, false, repeated values, interval text, native missing-value reasons and
unit/version identifiers are preserved. Source descriptions are sanitized with
the existing sanitizer and other source text is HTML-escaped. Projection leaves
the retained mapping evidence unchanged. Foreign selected-record scope returns
HTTP 422 before the visual readers run.

The native protocol-header source has a version and lock flag but no authored
protocol-section bodies. Structured facts therefore do not become clinical
rationale, benefit-risk assessments, safety plans or approved narrative content.
The preview explicitly identifies those unavailable bodies. This is a software
qualification boundary, not a prerequisite to testing the mapped source path
with synthetic multi-arm data, and not a claim of achieved SURPASS time, cost,
labor or clinical metrics.

## Focused verification

From `api`, using the configured Python environment:

```text
python -m pytest clinical_mdr_api/tests/unit/services/test_m11_preview_http.py -q --no-cov -p no:cacheprovider
python -m pytest clinical_mdr_api/tests/unit/services/test_m11_source_sections.py -q --no-cov -p no:cacheprovider
```

These public HTTP tests render the real Jinja template using the real native
mapper and isolated source fixtures. They check selected-version propagation,
non-first version selection, provenance rejection, unavailable and ambiguous
inputs, native identifiers, HTML escaping, visibility and query validation.
They do not use a Neo4j clear-database fixture or a live clinical source.
The section tests include three root arms, one branch, multiple compounds and
products, wrong-but-existing references, ambiguous identities, zero/null values,
concentration denominators, repeated criteria and source HTML sanitation.

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

### Native section verification recorded on 2026-10-09

- The expanded run passed 62 cases: 18 new section HTTP cases, the 18 existing
  preview HTTP cases and all 26 unchanged native-domain cases. The final review
  tightened assigned-product verification to require the exact native dosing
  relationship as well as product-selection authority.
- After that review and nullable-reference typing fixes, the final 36 HTTP cases
  passed again. Scoped mypy passed for the helper and route; Pylint passed at
  10/10, and Black/isort checks passed for all four affected Python files.
- Another 88 existing isolated tests passed across compound snapshot, native
  corrections, native export, native library semantics and mapper semantics.
  None of these scoped runs skipped a test or contacted a clinical database.
- The literal-byte schema integrity test now passes with its expected SHA-256
  unchanged. An exact-path `.gitattributes` LF policy restores the canonical Git
  bytes on Windows; the schema content and full schema-drift assertion are
  unchanged. The previously documented CRLF failure is resolved.
- The actual HTTP-rendered synthetic preview was exported, and the old committed
  helper/template reproduced the missing native-section defect when loaded only
  in memory. The new preview's DOM is verified by public HTTP tests. A new visual
  browser inspection was not completed: the browser tool rejected the local
  file URL under its URL policy. No alternate access was attempted. The earlier
  B.1 Vue/browser qualification remains separate evidence.

There are no new routes, API schema changes, frontend source changes or external
authority contracts in this slice. Existing dependency and partial-draft model
serializer warnings remain; the native source is still deliberately incomplete
where clinical authoring facts do not exist.
