# OpenStudyBuilder Data Inventory (AccuraTrial fork)

| | |
|---|---|
| **Prepared** | 2026-09-15, revision 2 (adds related studies §2.8, objective/endpoint levels §8.4, Library and Admin page columns §16.7, codelist values Appendix C) |
| **Codebase** | `C:\Projects\OpenSourceBuilder` at commit `29ae0c0c` plus the uncommitted working-tree changes present on that date |
| **Base product** | OpenStudyBuilder v2.9.0 (upstream commit `5ba8e431`, Novo Nordisk open source) |
| **Fork** | AccuraTrial Command Center integration: tenant-scoped study access, EDC export/import, 360i importer, governed mapping contracts |
| **Components covered** | `clinical-mdr-api` (FastAPI backend + consumer API), `studybuilder` (Vue frontend), `studybuilder-import` (importers), `neo4j-mdr-db` (graph schema) |

## Contents

- [0. How to read this document](#0-how-to-read-this-document)
- [1. How OpenStudyBuilder organises data](#1-how-openstudybuilder-organises-data)
- [2. Study identity, status and versioning](#2-study-identity-status-and-versioning)
- [3. Registry identifiers](#3-registry-identifiers)
- [4. Study title](#4-study-title)
- [5. Study properties (Study Type and Study Attributes)](#5-study-properties-study-type-and-study-attributes)
- [6. Study population](#6-study-population)
- [7. Study structure](#7-study-structure)
- [8. Study purpose: objectives, endpoints, eligibility criteria](#8-study-purpose-objectives-endpoints-eligibility-criteria)
- [9. Study activities and the Schedule of Activities](#9-study-activities-and-the-schedule-of-activities)
- [10. Study data specifications (activity instances and operational SoA)](#10-study-data-specifications-activity-instances-and-operational-soa)
- [11. Study interventions: compounds and compound dosing](#11-study-interventions-compounds-and-compound-dosing)
- [12. Study data suppliers](#12-study-data-suppliers)
- [13. Study data standard versions, tags and scores](#13-study-data-standard-versions-tags-and-scores)
- [14. Derived and exported views of a study](#14-derived-and-exported-views-of-a-study)
- [15. AccuraTrial fork: additional data collected, stored and exchanged](#15-accuratrial-fork-additional-data-collected-stored-and-exchanged)
- [16. Library data (shared standards that studies select from)](#16-library-data-shared-standards-that-studies-select-from)
- [17. User, audit and system data](#17-user-audit-and-system-data)
- [18. Summary: what a study record consists of](#18-summary-what-a-study-record-consists-of)
- [Appendix A. Configurable study metadata fields (study field configuration)](#appendix-a-configurable-study-metadata-fields-study-field-configuration)
- [Appendix B. Where to look in the code](#appendix-b-where-to-look-in-the-code)
- [Appendix C. Sponsor codelist values used by study fields](#appendix-c-sponsor-codelist-values-used-by-study-fields)

## 0. How to read this document

This inventory lists every category of data that OpenStudyBuilder **collects** (entered by users or imported), **derives** (computed by the system) and **displays** (tables, overviews, protocol views, exports). It is organised the way the application is: the study definition first (identity, registry identifiers, title, properties, population, structure, purpose, activities, data specifications, interventions, suppliers, standard versions), then the derived and exported views of a study, then the fork-specific integration data, then the shared Library standards that study definitions reference, and finally user, audit and system data.

Each field table uses the same columns:

| Column | Meaning |
|---|---|
| **UI label** | The label shown in the frontend (English locale, `studybuilder/src/locales/en/app.json`) |
| **API field** | The property name in the REST API and in the Neo4j graph (`clinical-mdr-api`) |
| **Type** | text, number, boolean, date, duration (value + unit), select (single controlled term), multi-select, reference (link to another object), rich text |
| **Values / source** | Where the allowed values come from: a CDISC or sponsor codelist (`Cnnnnn` is the CDISC codelist concept code), a dictionary (SNOMED CT, UNII, MED-RT, UCUM), a Library entity, or free text |
| **Req.** | Whether the field is mandatory on the form or in the API |
| **Meaning** | The definition shown to users as field help, or the model description in the API |

"Null flavour" means the *Reason for missing* companion that most study metadata fields have: instead of a value the user may pick a reason code such as *Not Applicable (NA)*, *Positive Infinity (PINF)* or *Unknown*. The system rejects a field that carries both a value and a null flavour.

## 1. How OpenStudyBuilder organises data

**Two halves.** The *Library* holds shared, versioned standards (controlled terminology, dictionaries, activities and other concepts, syntax templates, CRF/ODM definitions, CDISC data models, projects, data suppliers). A *Study* is a study definition that mostly *selects* from the Library and adds study-specific values. Almost every study field is therefore either free text/number entered by the user, or a reference to a Library item.

**Storage.** Everything is stored in one Neo4j graph. A study is a `StudyRoot` node with one `StudyValue` per version. Study metadata fields hang off the `StudyValue` as typed `StudyField` nodes (`StudyTextField`, `StudyIntField`, `StudyBooleanField`, `StudyTimeField`, `StudyArrayField`, `StudyProjectField`), each carrying `field_name` and `value`; codelist-backed fields additionally link to the selected `CTTermRoot`, dictionary-backed fields to `DictionaryTermRoot`, and null flavours to the *Null Flavor* codelist term. Study structure and study selections are `StudySelection` nodes (`StudyArm`, `StudyBranchArm`, `StudyCohort`, `StudyElement`, `StudyEpoch`, `StudyVisit`, `StudyDesignCell`, `StudyObjective`, `StudyEndpoint`, `StudyCriteria`, `StudyActivity`, `StudyActivityInstance`, `StudyActivitySchedule`, `StudyActivityInstruction`, `StudySoAFootnote`, `StudySoAGroup`, `StudyActivityGroup`, `StudyActivitySubGroup`, `StudyCompound`, `StudyCompoundDosing`, `StudyDiseaseMilestone`, `StudyDataSupplier`, `StudyDesignClass`, `StudySourceVariable`, `StudyVersion`, `StudyDefinitionDocument`) that each carry `uid`, `order` and `accepted_version` plus audit links. Source: `clinical-mdr-api/clinical_mdr_api/domain_repositories/models/`.

**Versioning.** A study is *Draft*, *Released* (minor versions 0.1, 0.2, …), *Locked* (major versions 1.0, 2.0, …) or *Deleted* (soft delete; still listed on the *Deleted studies* tab). Every release or lock freezes a persistent `StudyValue` snapshot. Library items are versioned separately (*Draft* / *Final* / *Retired*, `major.minor`), and a study selection points at a specific library version; the study can be told a newer version exists and accept or keep the old one.

**Audit trail.** Every create, edit and delete on a study or library item records who and when (`author_id`, resolved to a username for display, plus timestamps on the version relationships and `StudyAction` nodes of type `Create`, `Edit`, `Delete`, `UpdateSoASnapshot`). Study field changes are also exposed as a per-field audit trail (section, field name, before value, after value, action, author, date).

**Configurable metadata.** The list of study metadata fields is data-driven. `CTConfig` nodes seeded from `studybuilder-import/datafiles/configuration/study_fields_configuration.csv` (102 rows, identical to upstream v2.9.0) define each field's name, data type, null-flavour companion, backing codelist or term, grouping and API name. Appendix A reproduces the full list. The fork adds two API-only fields programmatically (observational study model, time perspective).

### 1.1 Study navigation map

The *Studies* sidebar (`studybuilder/src/stores/app.js`) exposes these pages; the section of this document that covers each is given in brackets. Entries commented out in the menu definition are listed as *disabled*.

| Menu | Page | Route | Covered in |
|---|---|---|---|
| About Studies | Study Summary | `StudySummary` | §2, §14.1 |
| Study List | Find, edit and add studies | `SelectOrAddStudy` | §2.1, §2.2 |
| Manage Study | Study (tabs Study Core Attributes, Study Status, Study Subparts, Protocol Versions) | `StudyStatus` | §2.3 to §2.6 |
| Manage Study | Data Standard Versions | `StudyDataStandardVersions` | §13.1 |
| Manage Study | Data Suppliers | `StudyDataSuppliers` | §12 |
| Define Study | Study Title | `StudyTitle` | §4 |
| Define Study | Registry Identifiers | `StudyRegistryIdentifiers` | §3 |
| Define Study | Study Properties | `StudyProperties` | §5 |
| Define Study | Study Structure | `StudyStructure` | §7 |
| Define Study | Study Population | `StudyPopulation` | §6 |
| Define Study | Study Criteria | `StudySelectionCriteria` | §8.3 |
| Define Study | Study Interventions | `StudyInterventions` | §11 |
| Define Study | Study Purpose | `StudyPurpose` | §8.1, §8.2 |
| Define Study | Study Activities | `StudyActivities` | §9 |
| Define Study | Data Specifications | `StudyDataSpecifications` | §10 |
| Define Study | *disabled:* Specification Overview, Terminology | | |
| View Specifications | Protocol Elements | `ProtocolElements` | §14.1 |
| View Specifications | EDC Export *(fork)* | `EdcExport` | §15.4 |
| View Specifications | SDTM Study Design Datasets | `SdtmStudyDesignDatasets` | §14.3 |
| View Specifications | USDM | `Usdm` | §14.4 |
| View Specifications | ICH M11 | `IchM11` | §14.5 |
| View Specifications | Clinical Transparency (study disclosure) | `StudyDisclosure` | §14.2 |
| View Specifications | *disabled:* Standardisation Plan, CRF Specifications, Trial Supplies Specifications, ODM Specification, CTR ODM XML, SDTM Specifications | | |
| View Listings | Analysis Study Metadata (New) | `AnalysisStudyMetadataNew` | §14.3 |
| View Listings | *disabled:* MMA Trial Metadata, SDTM Define (P21 / CST), DMW Additional Metadata, SDTM Additional Metadata, ADaM Define (P21 / CST) | | |
| Admin (top-level) | Global Preferences, Feature Flags, System Announcement, Data Completeness Tags, Complexity Burdens, ODM Vendor Extensions | | §16.6, §17 |

## 2. Study identity, status and versioning

Pages: *Studies › Study List* (`/studies/select_or_add_study`, view `SelectOrAddStudy.vue`), *Studies › Manage Study › Study* (`StudyStatus.vue` with tabs Study Core Attributes, Study Status, Study Subparts, Protocol Versions), *Studies › Manage Study › Study Versions* (`VersionHistory.vue`). API: `GET/POST /studies`, `GET/PATCH/DELETE /studies/{uid}`, `POST /studies/{uid}/release|lock|unlock`, `GET /studies/{uid}/snapshot-history`, `GET /studies/{uid}/audit-trail`, `GET /studies/{uid}/fields-audit-trail`. Backend models: `clinical-mdr-api/clinical_mdr_api/models/study_selections/study.py`, domain rules in `domains/study_definition_aggregates/study_metadata.py`.

### 2.1 Creating a study (Study List › Add study, `StudyCreationForm.vue`)

The form offers *Create from scratch* or *Create from an existing study* (clone).

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| Project ID | `project_number` | reference | Library › Projects (`GET /projects`) | yes | The project the study belongs to. The project in turn belongs to a Clinical Programme and optionally a Brand. |
| Project name | (derived) `project_name` | text | from project | – | Auto-populated from the selected project. |
| Brand name | (derived) `brand_name` | text | from project | – | Auto-populated from the project, only if a brand exists. |
| Clinical programme | (derived) `clinical_programme_name` | text | from project | – | Auto-populated from the project. |
| Study number | `study_number` | text | 1–4 digits (regex `\d{1,4}`; the UI checks the configured study-number length) | yes unless an acronym is given | Becomes part of the Study ID. Must be unique among non-deleted studies. |
| Study acronym | `study_acronym` | text | free text | yes unless a number is given | Optional free text, used instead of a study number in early planning. Must be unique. |
| Study ID | `study_id` | derived | `<study_id_prefix>-<study_number>` where the prefix is the project number | – | ID generated for the study, e.g. `CDISC DEV-1234`. |
| Description | `description` | text | free text | no | Free-text description (mainly used for subparts). |

Clone options (`StudyCloneInput`, form `StudyStructureCopyForm.vue`): boolean switches `copy_study_arm`, `copy_study_branch_arm`, `copy_study_cohort`, `copy_study_element`, `copy_study_visit`, `copy_study_visits_study_footnote`, `copy_study_epoch`, `copy_study_epochs_study_footnote`, `copy_study_design_matrix`, `copy_study_soa_group`, `copy_study_activity`, `copy_study_activity_instance`, `copy_study_activity_group`, `copy_study_activity_subgroup`, `copy_study_activity_schedule`, plus `validation_mode` (strict or lenient). Separately, the *Copy from study* action on Study Properties can copy the three metadata components `high_level_study_design`, `study_intervention` and `study_population` from another study.

### 2.2 Study List table (`StudyTable.vue`)

Columns displayed: Clinical programme, Project ID, Project name, Study number, Study ID, Main study ID, Subpart ID, Study acronym, Subpart acronym, Study title, Latest version, Latest locked version, Latest released version, Data completeness (tags), Status, Modified, Modified by. A second tab lists *Deleted studies* with the same columns. The table can be exported (CSV/JSON/XML/Excel) like every table in the application.

### 2.3 Study Core Attributes (`StudyForm.vue`, `PATCH /studies/{uid}`)

Same fields as 2.1 (Project ID, Project name, Brand name, Study number, Study acronym, Study ID). From here a study that has never been locked can be *deleted* (soft delete). The *History* shown on this page is the full study definition audit trail.

### 2.4 Study status, release, lock and unlock (`StudyStatusForm.vue`, `StudyStatusTable.vue`)

Release/lock form (`POST /studies/{uid}/release` or `/lock`, model `LockReleaseInput`):

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| Specify reason for releasing/locking the study | `reason_for_change_uid` | select | sponsor codelist of lock/release reasons (e.g. *Final Protocol*, *Other*) | yes | Why a snapshot is taken. *Final Protocol* requires a protocol major version. |
| Other reason for locking/releasing | `other_reason_for_locking_releasing` | text | free text | when reason is *Other* | Free-text reason. |
| Protocol header major version | `protocol_header_major_version` | number | integer | when reason is *Final Protocol* | Protocol document version this study version corresponds to. |
| Protocol header minor version | `protocol_header_minor_version` | number | integer | no | Set to 0 for a final protocol. |
| Change description | `change_description` | text | free text | no | Summary of what changed compared with the previous version (stored as `version_description`). |

Unlock form (`POST /studies/{uid}/unlock`, `UnlockInput`): *Specify reason for unlocking* (`reason_for_change_uid`, sponsor codelist of unlock reasons) and *Other reason for unlocking* (`other_reason_for_unlocking`).

Status table columns: Study status, Reason for unlocking study, Other reason for unlocking, Reason for locking or releasing study, Change description, Protocol Version, Metadata version, Modified, Modified by. The Study Core Attributes tab shows Study status, Clinical programme, Project number, Project name, Study number and Study acronym.

Version metadata stored per study version (`ver_metadata`, `StudyVersionMetadataJsonModel`):

| API field | Type | Meaning |
|---|---|---|
| `study_status` | enum | `DRAFT`, `RELEASED`, `LOCKED`, `DELETED` |
| `version_number` | decimal | Minor number for releases (0.1, 0.2 …), major for locks (1.0, 2.0 …); empty on drafts |
| `version_timestamp` | datetime | When the version was created |
| `version_author` | text | Username of the person who created the version |
| `version_description` | text | Change description entered on release/lock |

Rules: a study can only be locked when a study number and a study title exist; a locked study cannot be edited until unlocked; locking also creates a released instance so the latest released is never older than the latest locked. Persisted on `StudyVersion` (`other_reason_for_locking`, `other_reason_for_unlocking`, links to the reason terms) and `StudyDefinitionDocument` (`protocol_header_major_version`, `protocol_header_minor_version`).

### 2.5 Study subparts (`StudySubpartForm.vue`, `StudySubpartEditForm.vue`, `StudySubpartsTable.vue`)

A subpart is a separate study definition under a parent study (one protocol with several sub-studies). Titles and registry identifiers are inherited from the parent and read-only on the subpart; subparts are released and locked together with the parent.

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| Study subpart acronym | `study_subpart_acronym` | text | 1–10 characters, letters and digits only, stored upper-case | yes | Describes the subpart, e.g. `SINGLEDOSE`, `PART2`. |
| Study acronym | `study_acronym` | text | free text | no | Acronym of the whole study. |
| Description | `description` | text | free text | no | Free text. |
| Subpart ID | `subpart_id` | derived | letter `a`, `b`, `c` … assigned by the system | – | Unique ID of the subpart; the Study ID becomes `<project>-<number>-<subpart acronym>`. |
| Study parent part | `study_parent_part_uid` / `study_parent_part` | reference | an existing study in the same project | yes | Parent study. Two ways to create: *Create new study to be study subpart* or *Add existing study as study subpart* (the existing study's number is overwritten with the parent's). |

Table columns: Study ID, Study acronym, Subpart ID, Subpart acronym, Description. Subparts can be reordered (`PATCH /studies/{uid}/order`), which reassigns the letter IDs; a subpart audit trail records the changes.

### 2.6 Protocol versions tab (`ProtocolVersionsTable.vue`)

Read-only list of the distinct protocol header versions a study version has been linked to on release/lock. Columns: Study status, Reason for unlocking study, Other reason for unlocking, Reason for locking or releasing study, Other reason for locking or releasing, Change description, Protocol Version (`major.minor`), Latest metadata version, Original metadata version. `StudyProtocolHeaderVersion` also exposes whether the study contains a locked version with *Final Protocol* as the lock reason.

### 2.7 Audit trail views

* *Study Versions* page (`VersionHistory.vue`, `GET /studies/{uid}/snapshot-history`): one row per version with status, version number, timestamp, author and description.
* Per-field audit trail (`GET /studies/{uid}/fields-audit-trail`, `StudyFieldAuditTrailEntry`): `study_uid`, `author_username`, `date`, and a list of actions each with `section` (identification, registry identifiers, study design, population, intervention, description), `field_name`, `before_value`, `after_value`, `action` (Create/Edit/Delete).
* Every table in the study module has a *History* action returning the version history of that row (same versioning fields).

### 2.8 Related studies: parts, extensions and reuse ("secondary studies")

OpenStudyBuilder has no entity called a secondary, child or companion study. The relationships between studies that do exist, and the data they carry, are:

| Concept | What is stored | Where |
|---|---|---|
| Study subparts (parent study with sub-studies under one protocol) | `study_parent_part` link, `subpart_id` (a, b, c …), `study_subpart_acronym`; titles and registry identifiers are inherited from the parent; subparts release and lock with the parent | §2.5, Study List columns *Main study ID*, *Subpart ID*, *Study subpart acronym* |
| Extension study | the boolean study field *Extension study* (`is_extension_trial`, TS parameter EXTTIND) on the Study Type tab; no link to the parent study is stored | §5.1 |
| Clone / create from an existing study | `POST /studies/{uid}/clone` with the copy switches of §2.1 (arms, branches, cohorts, elements, epochs, visits, design matrix, SoA groups, activities, instances, groupings, schedules, footnotes) | §2.1 |
| Copy metadata component from another study | `GET /studies/{uid}/copy-component` with `reference_study_uid`, `component_to_copy` (study design, intervention or population) and `overwrite` | Study Properties and Population pages, *Copy from study* action |
| Select from studies | Objectives, endpoints, criteria, activities, footnotes, activity instructions and titles can be copied from one or more other studies in their add forms; the copied selection references the same library item, no cross-study link is kept | §4, §8, §9 |
| Template study | An administrator-chosen study (`/studies/template`: `study_uid`, `study_value_version`) offered as the default source for cloning | §13.4, Library › Admin Definitions › Template Study |
| Study Structures overview | Cross-study report grouping studies with identical structure (study IDs, arm / epoch / element counts, cohorts flag) | §7.10, Library › Overview Pages › Study Structures |
| Selection containment | `GET /studies/{uid}/study-selection-containment/{target}` comparing which selection labels of one study are contained in another | §13.4 |
| Secondary IDs | On the Clinical Transparency page every non-empty registry identifier is shown as a *Secondary ID* with an ID type; these are the identifiers of §3, not other studies | §14.2 |

## 3. Registry identifiers

Page: *Studies › Define Study › Registry Identifiers* (`RegistryIdentifiers.vue`, table `RegistryIdentifiersSummary.vue`, form `RegistryIdentifiersForm.vue`). API: part of `GET/PATCH /studies/{uid}` under `current_metadata.identification_metadata.registry_identifiers` (`RegistryIdentifiersJsonModel`); domain rules in `domains/study_definition_aggregates/registry_identifiers.py`. Each identifier is free text with a *Reason for missing* null-flavour companion; the form shows an *NA* tick box per identifier. Values are inherited by study subparts.

| UI label | API field | Null-flavour companion | Format / meaning |
|---|---|---|---|
| ClinicalTrials.gov ID | `ct_gov_id` | `ct_gov_id_null_value_code` | `NCT` followed by 8 digits (e.g. `NCT00000419`). |
| EUDRACT ID | `eudract_id` | `eudract_id_null_value_code` | European Union Drug Regulating Authorities Clinical Trials number, `YYYY-NNNNNN-CC` (e.g. `2007-002422-29`). |
| Universal Trial Number (UTN) | `universal_trial_number_utn` | `universal_trial_number_utn_null_value_code` | WHO International Clinical Trials Registry number, `Uxxxx-xxxx-xxxx` (e.g. `U1111-1168-4339`). |
| Japanese Trial Registry ID (JAPIC) | `japanese_trial_registry_id_japic` | `japanese_trial_registry_id_japic_null_value_code` | Japan Pharmaceutical Information Center number, `Japic CTI-xxxxxx`. |
| Investigational New Drug Application (IND) Number | `investigational_new_drug_application_number_ind` | `investigational_new_drug_application_number_ind_null_value_code` | FDA IND number issued when human trials are permitted (e.g. `IND Number: 132371`). |
| EU Trial Number | `eu_trial_number` | `eu_trial_number_null_value_code` | EU CT number issued under the EU Clinical Trials Regulation (CTIS). |
| CIV-ID / SIN Number | `civ_id_sin_number` | `civ_id_sin_number_null_value_code` | EU medical-device clinical investigation ID (CIV-ID) or Single Identification Number. |
| National Clinical Trial Number | `national_clinical_trial_number` | `national_clinical_trial_number_null_value_code` | National registry number. |
| Japanese Trial Registry Number (jRCT) | `japanese_trial_registry_number_jrct` | `japanese_trial_registry_number_jrct_null_value_code` | Japan Registry of Clinical Trials number. |
| NMPA Number | `national_medical_products_administration_nmpa_number` | `national_medical_products_administration_nmpa_number_null_value_code` | China National Medical Products Administration number. |
| EUDAMED SRN Number | `eudamed_srn_number` | `eudamed_srn_number_null_value_code` | European Database on Medical Devices Single Registration Number. |
| Investigational Device Exemption (IDE) Number | `investigational_device_exemption_ide_number` | `investigational_device_exemption_ide_number_null_value_code` | FDA IDE number. |
| EU PAS Number | `eu_pas_number` | `eu_pas_number_null_value_code` | EU Post-Authorisation Study register number. |

Displayed: Registry Identifiers page, Study Summary, Protocol title page, Study Disclosure (as *secondary IDs* with an ID type of Registry Identifier / Other Identifier / EudraCT Number), SDTM Trial Summary (TS), the study metadata listing (`reg_id` block with short keys `ct_gov`, `eudract`, `utn`, `japic`, `ind`, `eutn`, `civ`, `nctn`, `jrct`, `nmpa`, `esn`, `ide`, `eupn`) and the USDM/M11 exports.

## 4. Study title

Page: *Studies › Define Study › Study Title* (`StudyTitle.vue`, form `StudyTitleForm.vue`; the form also lets you copy the titles from another study). API: `current_metadata.study_description` (`StudyDescriptionJsonModel`).

| UI label | API field | Type | Req. | Meaning |
|---|---|---|---|---|
| Study title | `study_title` | text | required to lock a study | The full title of the protocol. |
| Short study title | `study_short_title` | text | no | The short title of the protocol. |

The form also shows the read-only Project ID, Project name and Study ID. The Study Summary card lists every metadata field as *Selected values* with its *Reason for missing*, and its history view shows Field, Previous value, New value and User.

Displayed: Study Title page, Study List (Study title column), Study Summary, Protocol title page (`ProtocolTitlePage.vue`), Study Disclosure (*Official title* and *Brief title*), USDM, ICH M11, SDTM TS, EDC export.

## 5. Study properties (Study Type and Study Attributes)

Page: *Studies › Define Study › Study Properties* (`StudyProperties.vue`) with two tabs. Both are part of the mandatory SDTM Trial Summary (TS) domain. Every field has a *Reason for missing* null flavour; the forms show an *NA* option and the tabs render as summary tables (`StudyTypeSummary.vue`, `InterventionTypeSummary.vue`).

### 5.1 Study Type tab (`StudyDefineForm.vue`; API `current_metadata.high_level_study_design`, `HighLevelStudyDesignJsonModel`)

| UI label | API field | Type | Values / source | Meaning |
|---|---|---|---|---|
| Study type | `study_type_code` (+ `study_type_null_value_code`) | select | CDISC codelist C99077 *Study Type* (Interventional, Observational, Expanded Access …) | Overall study type. |
| Trial type | `trial_type_codes` (+ `trial_type_null_value_code`) | multi-select | CDISC codelist C66739 *Trial Type* | The trial type(s) within the study purpose (e.g. Efficacy, Safety, Pharmacokinetic). |
| Study phase classification | `trial_phase_code` (+ `trial_phase_null_value_code`) | select | CDISC codelist C66737 *Trial Phase* | Relevant phase or stage of the study. |
| Development stage | `development_stage_code` | select | sponsor codelist (no null flavour) | Relevant development stage of the study. |
| Extension study | `is_extension_trial` (+ null flavour) | boolean | TS parameter EXTTIND (term `C139274_EXTTIND`) | Whether the study is an extension trial. |
| Adaptive design | `is_adaptive_design` (+ null flavour) | boolean | TS parameter ADAPT (`C146995_ADAPT`) | Whether the study includes a prospectively planned opportunity to modify the design or hypotheses based on interim data. |
| Study stop rules | `study_stop_rules` (+ null flavour) | text | free text; record *NONE* if no stopping rule | The rule(s), regulation(s) or condition(s) that determine when the trial will be terminated. |
| Confirmed response minimum duration | `confirmed_response_minimum_duration` (+ null flavour) | duration | value + unit (unit definitions in the study-time subset) | Protocol-specified minimum time needed to meet the definition of a confirmed response to treatment. |
| Post authorization safety study indicator | `post_auth_indicator` (+ null flavour) | boolean | TS parameter PASSIND (`C139275_PASSIND`) | Whether the study is a post-authorisation safety study. |
| *(API only, fork)* Observational study model | `observational_model_code` | select | CDISC codelist C127259 *Observational Study Model* (Cohort, Case-Control …) | Written by the 360i importer / native study flows and read by the USDM mapper; not shown on the Vue form. |
| *(API only, fork)* Observational time perspective | `observational_time_perspective_code` | select | CDISC codelist C127261 *Time Perspective* (Prospective, Retrospective …) | Same as above. |

### 5.2 Study Attributes tab (`InterventionTypeForm.vue`; API `current_metadata.study_intervention`, `StudyInterventionJsonModel`)

| UI label | API field | Type | Values / source | Meaning |
|---|---|---|---|---|
| Intervention type | `intervention_type_code` (+ `intervention_type_null_value_code`) | select | CDISC codelist C99078 *Intervention Type* (Behavioral, Biological Agent, Dietary Supplement, Gene Therapy, Medical Device, Pharmacologic Substance, Physical Medical Procedure …) | The kind of product or procedure investigated. Required when the study type is Interventional. |
| Study intent type | `trial_intent_types_codes` (+ `trial_intent_types_null_value_code` in the API, `trial_intent_type_null_value_code` in the field configuration) | multi-select | CDISC codelist C66736 *Trial Intent Type* (Treatment, Prevention, Diagnosis …) | The planned purpose(s) of the therapy, device or agent under study. |
| Added on to existing treatments | `add_on_to_existing_treatments` (+ null flavour) | boolean | TS parameter ADDON (`C49703_ADDON`) | Whether a therapeutic product is added to the existing regimen. |
| Control type | `control_type_code` (+ null flavour) | select | CDISC codelist C66785 *Control Type* (Active, Dose Response, Placebo, None …) | Comparator against which the study treatment is evaluated. |
| Intervention model | `intervention_model_code` (+ null flavour) | select | CDISC codelist C99076 *Intervention Model* (Single Group, Parallel, Crossover, Factorial, Group Sequential) | General design of the strategy for assigning interventions to participants. |
| Randomised | `is_trial_randomised` (+ null flavour) | boolean | TS parameter RANDOM (`C25196_RANDOM`) | Whether participants are assigned to groups by randomisation. |
| Stratification factor | `stratification_factor` (+ null flavour) | text | free text | Factors used during randomisation to balance arms; one row per factor. |
| Blinding schema | `trial_blinding_schema_code` (+ null flavour) | select | CDISC codelist C66735 *Trial Blinding Schema* (Double Blind, Open Label, Single Blind, Open Label for Treatment and Double Blind to Dose …) | Level of awareness of participants and investigators of the intervention. |
| Planned study length | `planned_study_length` (+ null flavour) | duration | value + unit | Planned length of observation for a single subject. |

Displayed: Study Properties tabs, Study Summary (`StudyMetadataSummary.vue`), Protocol Elements › Study Design, Study Disclosure (study type, phase, intervention model, allocation, intervention type, primary purpose), SDTM TS, study metadata listing (`study_type` and `study_attributes` blocks), USDM, ICH M11, EDC export.

## 6. Study population

Page: *Studies › Define Study › Study Population* (`PopulationPage.vue`, form `StudyPopulationForm.vue`, summary `StudyPopulationSummary.vue`). API: `current_metadata.study_population` (`StudyPopulationJsonModel`). Every field has a null flavour (*NA* is typical for healthy-volunteer pharmacology studies, *PINF* for "no maximum age").

| UI label | API field | Type | Values / source | Meaning |
|---|---|---|---|---|
| Therapeutic area | `therapeutic_area_codes` (+ `therapeutic_area_null_value_code`) | multi-select | SNOMED CT dictionary terms | The overall research or development area the study belongs to. |
| Study disease, condition or indication | `disease_condition_or_indication_codes` (+ null flavour) | multi-select | SNOMED CT dictionary terms | The condition, disease or disorder the study investigates (e.g. Retinopathy). |
| Diagnosis group | `diagnosis_group_codes` (+ null flavour) | multi-select | SNOMED CT dictionary terms | Grouping of individuals on the basis of a shared procedure or disease (e.g. Diabetes mellitus type 2). |
| Sex of study participants | `sex_of_participants_code` (+ null flavour) | select | CDISC codelist C66732 *Sex* (Male, Female, Both) | The specific sex of the subject group. |
| Rare disease indicator | `rare_disease_indicator` (+ null flavour) | boolean | TS parameter RDIND (`C126070_RDIND`) | Whether the condition is a rare disease (FDA: fewer than 200 000 people in the US; EU/WHO: fewer than 5 per 10 000). |
| Healthy subjects | `healthy_subject_indicator` (+ null flavour) | boolean | TS parameter HLTSUBJI (`C98737_HLTSUBJI`) | Whether persons without the condition may participate. |
| Planned minimum age of subjects | `planned_minimum_age_of_subjects` (+ null flavour) | duration | value + age unit (Unit Definitions, *Age Unit* subset) | Anticipated minimum age. |
| Planned maximum age of subjects | `planned_maximum_age_of_subjects` (+ null flavour) | duration | value + age unit; *PINF* when no maximum | Anticipated maximum age. |
| Stable disease minimum duration | `stable_disease_minimum_duration` (+ null flavour) | duration | value + unit | Protocol-specified minimum time to meet the definition of stable disease. |
| Paediatric study indicator | `pediatric_study_indicator` (+ null flavour) | boolean | TS parameter PDSTIND (`C123632_PDSTIND`) | Whether the study is in children. |
| Paediatric post-market study indicator | `pediatric_postmarket_study_indicator` (+ null flavour) | boolean | TS parameter PDPSTIND (`C123631_PDPSTIND`) | Whether the study is a paediatric study done after initial approval. |
| Paediatric investigation plan indicator | `pediatric_investigation_plan_indicator` (+ null flavour) | boolean | TS parameter PIPIND (`C126069_PIPIND`) | Whether the study is part of a paediatric investigation plan (PIP). |
| Relapse criteria | `relapse_criteria` (+ null flavour) | text | free text | Standard used to judge disease relapse (e.g. NCI/Rome criteria). |
| Number of expected subjects | `number_of_expected_subjects` (+ null flavour) | number | integer | The planned enrolment target from the protocol. The fork's help text notes that actual participant totals are tracked in the EDC, not here. |

Displayed: Study Population page, Study Summary, Protocol Elements › Study Population summary, Study Disclosure (conditions, min/max age, accepts healthy volunteers, number of subjects), SDTM TS, study metadata listing (`study_population` block), USDM, ICH M11, EDC export.


## 7. Study structure

Page: *Studies › Define Study › Study Structure* (`StudyStructure.vue`) with tabs Overview, Study Arms, Study Branches, Study Cohorts, Study Epochs, Study Elements, Study Visits, Design Matrix and Disease Milestones. Backend models: `models/study_selections/study_selection.py`, `study_epoch.py`, `study_visit.py`, `study_disease_milestone.py`; routers under `routers/studies/`. Every entity below is a `StudySelection` node with the system fields `uid`, `order`, `start_date`, `end_date`, `status`, `change_type` (Create / Edit / Delete), `author_username`, `accepted_version`, plus a per-row *History* and a per-tab *audit trail*. Every table exports to CSV / JSON / XML / Excel.

### 7.1 Design class and source variable (cohort stepper)

When the first arm is added the user chooses a *study design class* (`StudyDesignClass`, `GET/POST/PUT /studies/{uid}/study-design-classes`):

| UI label | API field | Values | Meaning |
|---|---|---|---|
| Study design class | `value` | `Manual` (*Study with arms only*) or `Study with cohorts, branches and subpopulations` | The second value opens the cohort stepper (`CohortsStepper.vue`) that creates arms, cohorts and the arm × cohort branches in one flow; participants are assigned per branch and rolled up. |
| Source variable | `source_variable` (`StudySourceVariable`, `/studies/{uid}/study-source-variables`) | `Cohort`, `Subgroup`, `Stratum` | How the cohort dimension is represented in SDTM/ADaM. |
| Source variable description | `source_variable_description` | free text | |

### 7.2 Study arms (`StudyArmsForm.vue`, `StudyArmsTable.vue`; `/studies/{uid}/study-arms`)

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| Arm type | `arm_type_uid` → `arm_type` | select | sponsor codelist *Arm Type* (submission value ARMTTP; a subset of CDISC C174222: Investigational, Comparator, Placebo, Observational …) | yes | Main purpose of the arm. |
| Arm name | `name` | text | | yes | Long label of the arm, e.g. *Placebo*. |
| Arm short name | `short_name` | text | | yes | Abbreviated name, e.g. *PBO*. |
| Arm label | `label` | text | | no | Shorter version of the arm name. |
| Arm code | `code` | text | | no | Abbreviated code of the interventions in the arm, e.g. *AB*; reusable as randomisation code. |
| Randomisation group | `randomization_group` | text | capital letters; several groups separated by a pipe character | no | Group assigned in the randomisation list. |
| Planned number of subjects | `number_of_subjects` | number ≥ 0 | | no | Feeds the structure overview total and the disclosure *number of subjects*. |
| Description | `description` | text | | no | |
| Merge branches for SDTM/ADaM | `merge_branch_for_this_arm_for_sdtm_adam` | boolean | default false | no | Whether branch arms of this arm are merged in SDTM/ADaM. |

Derived on read: `arm_connected_branch_arms` (the branches under the arm), and the compact tree `GET /studies/{uid}/study-arms-branches-and-cohorts` (arm → cohorts → branches with names, short names and subject counts). Table columns: Type, Arm name, Arm label, Arm short name, No. of participants, Random. group, Random. code, Connected Branches, Description, Modified, Modified by. Batch create/update endpoint `POST /studies/{uid}/study-arms/batch`.

### 7.3 Study branch arms (`StudyBranchesForm.vue`, `StudyBranchesTable.vue`, `BranchEditForm.vue`; `/studies/{uid}/study-branch-arms`)

A branch arm is a sub-division of an arm (a decision point such as responders / non-responders). In the cohort stepper the branch name and short name are generated from arm and cohort names and can be edited.

| UI label | API field | Type | Req. | Meaning |
|---|---|---|---|---|
| Study arm | `arm_uid` → `arm_root` | reference to a study arm | yes | The arm the branching applies to. |
| Branch arm name | `name` | text | yes | e.g. *Responders*. |
| Branch arm short name | `short_name` | text | yes | e.g. *Resp*. |
| Branch arm code | `code` | text | no | Auto-populated from the randomisation group. |
| Randomisation group | `randomization_group` | text | no | Randomisation group for this arm / branch combination. |
| Planned number of subjects in branch | `number_of_subjects` | number ≥ 0 | no | Cannot exceed the arm's number. |
| Description | `description` | text | no | e.g. *HbA1c < 45 mmol/L after 4 weeks*. |
| Study cohort | `study_cohort_uid` → `study_cohorts` | reference | no | Cohort(s) the branch belongs to. |

Table columns: Branch name, Short name, No. of participants, Arm name, Cohort name, Cohort code, Random. group, Random. Code, Modified, Modified by.

### 7.4 Study cohorts (`StudyCohortsForm.vue`, `StudyCohortsTable.vue`; `/studies/{uid}/study-cohorts`)

| UI label | API field | Type | Req. | Meaning |
|---|---|---|---|---|
| Cohort name | `name` | text | yes | e.g. *Liver disease*, or in the stepper investigation + dose level such as *Metformin 500 mg*. |
| Cohort short name | `short_name` | text | yes | e.g. *LF*. |
| Cohort code | `code` | text | yes | Short code; the stepper expects a number. |
| Study arms | `arm_uids` → `arm_roots` | multi-reference | no | Arms the cohort applies to. |
| Study branch arms | `branch_arm_uids` → `branch_arm_roots` | multi-reference | no | Branches the cohort applies to. |
| Number of subjects | `number_of_subjects` | number ≥ 0 | no | Validated against the related arm / branch; cannot be given without an arm. |
| Description | `description` | text | no | e.g. *Child-Pugh score Class B+C*. |

Table columns: Cohort Name, Short Name, Cohort Code, No. of participants, Arm name, Branch name, Description, Modified, Modified by.

### 7.5 Study epochs (`StudyEpochForm.vue`, `StudyEpochTable.vue`; `/studies/{uid}/study-epochs`)

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| Epoch type | `epoch_type` (derived) | select | sponsor codelist *Epoch Type* (EPOCHTP): Pre Treatment, Treatment, No Treatment, Post Treatment | yes (drives the subtype list) | Relation of the period to treatment. |
| Epoch subtype | `epoch_subtype` | select | sponsor codelist *Epoch Sub Type* (EPOCHSTP), based on CDISC EPOCH C99079 with sponsor extensions; *Basic* is used for non-visit and unscheduled data | yes | The kind of epoch (Screening, Run-in, Treatment, Follow-up …). `GET /epochs/allowed-configs` lists the allowed type/subtype pairs. |
| Epoch name | `epoch` | select (auto) | sponsor codelist *Epoch* (EPOCH); previewed by `POST /studies/{uid}/study-epochs/preview` | – | Auto-populated from the subtype and numbered when several epochs share a subtype (e.g. *Treatment 1*). |
| Description | `description` | text | | no | Overall purpose of the epoch. |
| Start rule | `start_rule` | text | | no | When the epoch is expected to start, e.g. a specific visit. |
| End rule | `end_rule` | text | | no | When the epoch is expected to end. |
| Colour | `color_hash` | colour | default `#FFFFFF` | no | Colour on the study timeline and design matrix. |
| Order | `order` | number | reorder action | – | Position among the epochs. |
| Duration / duration unit | `duration`, `duration_unit` | number, unit | | no | Present in the API; the form marks them *pending implementation*. |

Derived on read: `epoch_name`, `epoch_subtype_name`, `epoch_type_name`, `epoch_ctterm`, `epoch_subtype_ctterm`, `epoch_type_ctterm`, `start_day`, `end_day`, `start_week`, `end_week` (from the visits assigned to the epoch), `study_visit_count`, and in the fork a `terminology_source` witness recording which CT package date and term history produced the labels of a locked version. Table columns: #, Epoch name, Epoch type, Epoch subtype, Start rule, End rule, Description, Number of visits, Assigned colour.

### 7.6 Study elements (`StudyElementsForm.vue`, `StudyElementsTable.vue`; `/studies/{uid}/study-elements`)

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| Element type | `element_type` (derived from subtype) | select | sponsor codelist *Element Type* (ELEMTP): Treatment / No Treatment | yes | Whether the element is a treatment. `GET /study-elements/allowed-element-configs` lists type/subtype pairs. |
| Element subtype | `element_subtype_uid` → `element_subtype` | select | sponsor codelist *Element Sub Type* (ELEMSTP) | yes | e.g. Screening, Wash-out, Drug A, Placebo. |
| Element name | `name` | text | | yes | e.g. *Standardised Meal*. |
| Element short name | `short_name` | text | | yes | e.g. *STD MEAL*. |
| Element code | `code` | text | | no | Becomes SDTM ETCD. |
| Description | `description` | text | | no | |
| Planned duration | `planned_duration` | duration (value + unit) | study-time unit definitions | no | Becomes SDTM TEDUR. |
| Start rule | `start_rule` | text | | no | Becomes SDTM TESTRL. |
| End rule | `end_rule` | text | | no | Becomes SDTM TEENRL. |
| Colour | `element_colour` | colour | | no | Colour in the design matrix. |

Derived: `study_compound_dosing_count` (how many compound dosings reference the element). Table columns: Element type, Element subtype, Element name, Element short name, Element Start Rule, Element End Rule, Colour, Description, Modified, Modified by.

### 7.7 Study visits (`StudyVisitForm.vue`, `StudyVisitTable.vue`, `StudyVisitsDuplicateForm.vue`, `CollapsibleVisitGroupForm.vue`, `StudyVisitUntimedTiming.vue`; `/studies/{uid}/study-visits`)

The add-visit wizard first asks for the *visit scheduling type* (visit class), then the epoch, then the details. Visit names, numbers and all study-day / study-week labels are computed by the timeline service from the timing; only *manually defined visits* let the user type them.

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| Visit scheduling type | `visit_class` | select | `SINGLE_VISIT` (Scheduled visit), `UNSCHEDULED_VISIT`, `NON_VISIT`, `SPECIAL_VISIT`, `MANUALLY_DEFINED_VISIT` | yes | Scheduled visits carry timing; unscheduled and non-visits are technical placeholders for SDTM; special visits (e.g. early discontinuation) attach to another visit and display as VxxA/VxxX; manually defined visits keep user-typed names and numbers (used for protocol amendments after first patient in). |
| Visit subclass | `visit_subclass` | select | `SINGLE_VISIT`, `ANCHOR_VISIT_IN_GROUP_OF_SUBV`, `ADDITIONAL_SUBVISIT_IN_A_GROUP_OF_SUBV`, `REPEATING_VISIT` | for scheduled visits | Single visit; anchor of a multi-day visit group; additional sub-visit (Visit 3 Day 2 …); repeating visit (shown as V3.N). |
| Study period (epoch) | `study_epoch_uid` → `study_epoch` | reference | study epochs | yes | Epoch the visit belongs to. |
| Visit type | `visit_type` (term uid) | select | sponsor codelist *Visit Type* (TIMELB / `VisitType`), e.g. Screening, Randomisation, Treatment, End of Treatment, Follow-up, Information | yes | Purpose of the visit; can be marked as a SoA milestone. |
| SoA milestone | `is_soa_milestone` | boolean | | no | Show the visit type as a milestone row in the protocol SoA. |
| Contact mode | `visit_contact_mode` (term uid) | select | CDISC codelist *Visit Contact Mode* (VISCNTMD): On Site Visit, Phone Contact, Virtual Visit … | yes (optional for untimed visits in the fork) | How the subject interacts with the investigator at the visit. |
| Global anchor visit | `is_global_anchor_visit` | boolean | only one per study; the form shows the *Current anchor visit* | no | Sets the visit's timing to 0 (Day 1); usually randomisation. Not an SDTM baseline flag. |
| Time reference | `time_reference` (term uid) | select | codelist *Time Point Reference* (TIMEREF): Global anchor visit, previous visit, anchor visit in visit group … ; `GET …/allowed-time-references` | yes for scheduled visits | Reference point the timing counts from. For special visits, the related visit. |
| Timing | `time_value` | number (negative or positive) | | yes for scheduled visits | Offset from the time reference. |
| Time unit | `time_unit_uid` | select | unit definitions in the *Study Time* subset (day, week …) | yes for scheduled visits | Unit of the timing. |
| Visit window min / max | `min_visit_window_value`, `max_visit_window_value` | number | defaults −9999 / 9999 mean "no window" | no | Earliest / latest allowable offset around the planned time point (e.g. −1 / +1). |
| Visit window unit | `visit_window_unit_uid` | select | unit definitions | no | |
| Repeating frequency | `repeating_frequency_uid` → `repeating_frequency` | select | sponsor codelist *Repeating Visit Frequency*: daily, weekly, monthly | no (repeating visits) | Interval between repetitions; leave blank and describe in the visit description when different. |
| Visit description | `description` | text | | no | Free text, e.g. specific purpose. |
| Epoch allocation rule | `epoch_allocation` (term uid) | select | sponsor codelist *Epoch Allocation* (EPCHALLC): Current Visit, Previous Visit, Date-based rules, Actual Treatment | no | Which epoch observations at this visit are attributed to (e.g. a baseline visit measured before first dose belongs to the previous epoch). |
| Visit start rule / Visit end rule | `start_rule`, `end_rule` | text | | no | When the visit starts / ends relative to the element sequence. |
| Show visit | `show_visit` | boolean | | no | Whether the visit appears in the SoA; unscheduled and non-visits default to hidden. |
| Anchor visit in visit group / Additional sub-visit reference | `visit_sublabel_reference` | reference to the group's anchor visit | | for sub-visits | Links a sub-visit to its anchor. |
| Visit name / Visit short name | `visit_name`, `visit_short_name` | text | | manual visits only | Otherwise auto-generated (*Visit 2*, *V2*). |
| Visit number / Unique visit number | `visit_number` (decimal), `unique_visit_number` (integer) | number | | manual visits only | Otherwise auto-numbered (2 and 200). |
| Timing mode *(fork)* | `timing_mode` | select | `STANDARD` (default) or `UNTIMED` (*Manual dates with nominal source timing*), manually defined visits only | – | Untimed visits have no absolute study day; actual dates are recorded in the EDC. |
| Untimed timing *(fork)* | `untimed_timing` | object | `kind` = `manual_date` (+ `repeating` yes/no), `event_relative` (+ `anchor_visit_uid`, `nominal_offset_days`), or `calendar_repeat` (+ `anchor_visit_uid`, `interval_months`, `first_occurrence`, `last_occurrence`) | when untimed | Nominal source timing relative to another visit or a bounded calendar-month follow-up. All standard timing, window and frequency fields are cleared. |

Derived and displayed (visit table columns and the visit overview): Epoch, Visit type, SoA milestone, Visit class, Visit subclass, Repeating frequency, Visit name, Anchor visit in visit group, Visit group, Global anchor visit, Contact mode, Time reference, Timing, Visit number, Unique visit number, Visit short name, Study duration days (`study_duration_days` + label), Study duration weeks, Visit window, Collapsible visit group (`consecutive_visit_group`), Show visit, Visit description, Epoch allocation rule, Visit start rule, Visit end rule, Study day (`study_day_number` / `study_day_label`, e.g. *Day 1*), Study week (`study_week_number` / label), Week in study (`week_in_study_label`, usable as a template parameter in endpoint timeframes), `visit_subnumber`, `visit_subname`, `duration_time`, Modified, Modified by. A timeline drawing above the table shows visits in days or weeks. The study day / week / duration values are stored as reusable numeric concepts (`/concepts/study-days`, `/study-weeks`, `/study-duration-days`, `/study-duration-weeks`) and visit names as `VisitName` text concepts so they can be used as syntax-template parameters.

Other visit actions: duplicate a visit (same attributes, new timing), edit in table view, *consecutive visit groups* (`POST /studies/{uid}/study-visits/consecutive-visit-groups`: `visits_to_assign` ≥ 2, `format` = `range` (V3–V5) or `list` (V3, V4, V5), `overwrite_visit_from_template`) that collapse visits into one SoA column, and the helper look-ups for the global anchor, anchor visits of sub-visit groups and anchor candidates for special visits.

### 7.8 Design matrix (`DesignMatrixTable.vue`; `/studies/{uid}/study-design-cells`)

One *design cell* per arm (or branch arm) × epoch, holding the element for that cell.

| API field | Type | Req. | Meaning |
|---|---|---|---|
| `study_arm_uid` or `study_branch_arm_uid` | reference | one of them | Row of the matrix. |
| `study_epoch_uid` | reference | yes | Column of the matrix. |
| `study_element_uid` | reference | yes | The element (treatment, wash-out …) applied in that arm during that epoch. |
| `transition_rule` | text ≤ 200 | no | Rule for transitioning into the cell (SDTM TATRANS). |
| `order` | number | no | |

Displayed with the arm, branch, epoch and element names; the matrix has a *Transition rules* mode that shows and edits the rule text per cell; edited in a batch (`POST …/study-design-cells/batch`); rendered as the design figure SVG and as SDTM TA.

### 7.9 Disease milestones (`DiseaseMilestoneForm.vue`, `DiseaseMilestoneTable.vue`; `/studies/{uid}/study-disease-milestones`)

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| Disease milestone type | `disease_milestone_type` (term uid) | select | sponsor codelist *Disease Milestone Type* (MIDSTYPE), e.g. DIAGNOSIS, HYPOGLYCAEMIC EVENT | yes | An event or activity anticipated in the course of the disease that triggers data collection but is not scheduled by visits (may pre-date the study). |
| Repetition indicator | `repetition_indicator` | boolean | | yes | Whether the milestone can occur more than once (SDTM TMRPT). |
| Order | `order` | number | | – | |

Derived: `disease_milestone_type_name`, `disease_milestone_type_definition`, and in the fork a `terminology_source` witness for locked versions. Table columns: Type, Definition, Repetition indicator, Modified, Modified by. Exported as SDTM TDM.

### 7.10 Structure overview

`StudyStructureOverview.vue` (`GET /studies/{uid}/structure-statistics`) shows counts of arms, branches, cohorts, elements, epochs, visits, epoch and visit footnotes, study activities and schedules, the design-matrix picture and the planned enrolment. The cross-study *Study Structures* report (`GET /studies/structure-overview`, `StudyStructuresOverview`) groups studies that share a structure and lists study IDs, number of arms, pre-treatment / treatment / no-treatment / post-treatment epochs, treatment / no-treatment elements and whether cohorts are used.

## 8. Study purpose: objectives, endpoints, eligibility criteria

Pages: *Studies › Define Study › Study Purpose* (`StudyPurpose.vue` with tabs Objectives, Endpoints and Estimands; the Estimands tab is a placeholder) and *Studies › Define Study › Study Criteria* (`StudyCriteria.vue`, one tab per criteria type). All three are *syntax instances*: the text is either taken from a Library template (with parameter slots such as `[Intervention]`, `[Indication]`, `[Activity]`, `[NumericValue]`, `[StudyEndpoint]` filled in), copied from another study, or written from scratch (which silently creates a study-scoped template). What is stored per selection is the reference to the instance plus the study-level attributes below.

Parameter values (`parameter_terms`) are stored per template position as: `position`, `conjunction` (and / or / ,), `terms[]` (each `uid`, `name`, `type`, `index`), optional numeric `value` and `labels`; a parameter may be hidden from the rendered text. Rendered text is kept as HTML (`name`) and plain text (`name_plain`).

### 8.1 Study objectives (`ObjectiveForm.vue`, `ObjectiveEditForm.vue`, `ObjectiveTable.vue`; `/studies/{uid}/study-objectives`)

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| Objective level | `objective_level_uid` → `objective_level` | select | sponsor codelist *Objective Level* (OBJTLEVL): Primary Objective, Secondary Objective, Exploratory Objective … | yes | Primary objectives drive the statistical planning. |
| Objective | `objective_uid` → `objective` (instance) or `objective_template_uid` + `parameter_terms` or `objective_data` (scratch) | rich text | Library objective templates / other studies | yes | The objective text as stated in the protocol. |
| Order | `order` | number | reorder action | – | |

Derived: `endpoint_count` (number of endpoints linked to the objective), `latest_objective` (newer library version available), `accepted_version`, `template`. Table columns: order, Objective level, Objective, Endpoint count, Modified, Modified by. Actions: sync to latest version, accept version, batch select, preview. A combined objectives-and-endpoints table is exported as DOCX/HTML (`/studies/{uid}/study-objectives.docx`).

### 8.2 Study endpoints (`EndpointForm.vue`, `EndpointEditForm.vue`, `EndpointTable.vue`; `/studies/{uid}/study-endpoints`)

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| Objective | `study_objective_uid` → `study_objective` | reference | study objectives | recommended | Objective the endpoint answers (an endpoint may be unlinked). |
| Endpoint level | `endpoint_level_uid` → `endpoint_level` | select | sponsor codelist *Endpoint Level* (ENDPLEVL): Primary, Secondary, Exploratory, Additional | yes | Importance of the endpoint. |
| Endpoint sub-level | `endpoint_sublevel_uid` → `endpoint_sublevel` | select | sponsor codelist *Endpoint Sub Level* (ENDPSBLV), e.g. Key Secondary, Supportive | no | |
| Endpoint | `endpoint_uid` / `endpoint_template_uid` + `parameter_terms` / `endpoint_data` | rich text | Library endpoint templates | yes | The endpoint text, e.g. *Change in HbA1c*. |
| Time frame | `timeframe_uid` → `timeframe` | rich text (instance) | Library timeframe templates, e.g. *from baseline to week 26* | no | Time point(s) at which the measurement is assessed. |
| Units | `endpoint_units.units` (unit definition uids) + `endpoint_units.separator` | multi-select + text | Library unit definitions | no | Unit(s) of measure, e.g. *mmHg*; the separator joins several units (*and*, *or*, */*). |
| Order | `order` | number | | – | |

Derived: `latest_endpoint`, `latest_timeframe`, `accepted_version`, `template`. Table columns: #, Endpoint level, Endpoint sub-level, Endpoint title, Unit, Time frame, Objective, Modified, Modified by.

### 8.3 Eligibility criteria (`EligibilityCriteriaForm.vue`, `EligibilityCriteriaEditForm.vue`, `EligibilityCriteriaTable.vue`; `/studies/{uid}/study-criteria`)

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| Criteria type | `criteria_type` (from the template's type) | tab | sponsor codelist *Criteria Type* (CRITRTP): Inclusion, Exclusion, Run-in, Randomisation, Dosing, Withdrawal … (the fork groups tabs by sponsor preferred name) | yes | Which section of the protocol the criterion belongs to. |
| Criterion | `criteria_uid` / `criteria_template_uid` + `parameter_terms` / `criteria_data` | rich text | Library criteria templates (each template carries a type, categories and sub-categories) | yes | The criterion text. |
| Key criterion | `key_criteria` | boolean | default false | no | Marks a key criterion (shown first / used in the disclosure). |
| Order (within type) | `order` | number | | – | |

Derived: `latest_criteria`, `latest_template`, `accepted_version`. Table columns (per criteria-type tab): criterion text, Guidance text, Key criteria, Modified, Modified by. The Clinical Transparency page and SDTM TI use the plain text of Inclusion and Exclusion criteria.

### 8.4 Primary, secondary and other levels: what "secondary endpoint" means in the data

There is no separate record type for secondary endpoints or secondary objectives. Every study objective and every study endpoint is one row of the same entity, and its rank is a coded attribute chosen from a sponsor codelist:

| Attribute | Codelist (submission value) | Seeded values, in display order | Used by |
|---|---|---|---|
| Objective level (`objective_level`) | *Objective Level* (OBJTLEVL) | Primary Objective, Secondary Objective, Exploratory Objective | Objective table, protocol objectives table, ADaM MDENDPNT `OBJTVLVL`, USDM `Objective.level` |
| Endpoint level (`endpoint_level`) | *Endpoint Level* (ENDPLEVL) | Primary Endpoint, Secondary Endpoint, Exploratory Endpoint, Additional Endpoint | Endpoint table, protocol objectives-and-endpoints table, Clinical Transparency outcome measures, ADaM MDENDPNT `ENDPNTLVL`, USDM `Endpoint.level` |
| Endpoint sub-level (`endpoint_sublevel`) | *Endpoint Sub Level* (ENDPSBLV) | Primary, Co-primary, Multiple, Confirmatory secondary, Supportive secondary | Endpoint table, ADaM MDENDPNT `ENDPNTSL`, USDM endpoint sub-level extension |
| Objective category (template indexing) | *Objective Category* (OBJTCAT) | Efficacy, Safety, Pharmacokinetics, Pharmacodynamics, Bioequivalence | Library objective templates |
| Endpoint category / sub-category (template indexing) | *Endpoint Category* (ENDPCAT), *Endpoint Sub Category* (ENDPSCAT) | Efficacy, Safety, Pharmacokinetics, Pharmacodynamics, Bioequivalence; Continuous (normal), Continuous (log-normal), Continuous (relative change), Binary (responder), Count, Ordinal, Time-to-event, PROs/COAs/questionnaires | Library endpoint templates |

So a "secondary endpoint" is a study endpoint whose *Endpoint level* is *Secondary Endpoint*, optionally refined by the sub-level *Confirmatory secondary* or *Supportive secondary*, and normally linked to a secondary (or primary) objective through the *Objective* reference. Endpoints are ordered by level order and then by their own `order` inside the level, which is the order used in the protocol table and exports. The levels themselves are Library codelist terms, so a library administrator can add further levels (for example *Key Secondary*) without a code change. The full seeded value lists of every codelist used by study fields are in Appendix C.

The add-objective, add-endpoint, add-criterion and add-footnote forms all offer the same three creation modes, *Select from studies* (copy from one or more other studies), *Create from template* (which also offers *Select from pre-instances*, templates with parameter values already filled in) and *Create from scratch*. The endpoint form additionally allows *Select later* for the objective link and for the timeframe, so an endpoint can exist without either.

## 9. Study activities and the Schedule of Activities

Page: *Studies › Define Study › Study Activities* (`ActivitiesPage.vue`) with tabs Study Activities, Detailed SoA, Protocol SoA, SoA Footnotes and Activity Instructions, plus the SoA Settings form. Backend: study activity, group and schedule models in `study_selection.py`; footnotes in `study_soa_footnote.py`; SoA rendering in `routers/studies/study_flowchart.py`.

### 9.1 Study activities (`StudyActivityForm.vue`, `StudyActivityEditForm.vue`, `StudyActivityBatchEditForm.vue`, `StudyActivityTable.vue`; `/studies/{uid}/study-activities`)

Activities are selected from the Library, copied from other studies, or created as a *placeholder / activity request*.

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| SoA group | `soa_group_term_uid` → `study_soa_group` | select | sponsor codelist *Flowchart Group* (FLWCRTGRP): Subject related information, Safety, Efficacy, Pharmacokinetics, Pharmacodynamics … | yes | Top-level grouping row of the SoA, specific to the study context. |
| Activity | `activity_uid` → `activity` | reference | Library activities (Final), or a placeholder in the *Requested* library | yes | The assessment or procedure performed on the subject. |
| Activity group / Activity subgroup | `activity_group_uid`, `activity_subgroup_uid` → `study_activity_group`, `study_activity_subgroup` | reference | the groupings defined on the library activity | yes when the activity has groupings | Which of the activity's library groupings is used in this study (e.g. *Laboratory assessments › Haematology*). |
| Show in protocol SoA | `show_activity_in_protocol_flowchart` | boolean | default false | no | The eye toggle: whether the activity row appears in the protocol SoA (hidden rows inherit their schedule marks up to the group). |
| Show group / subgroup / SoA group in protocol SoA | `show_activity_group_in_protocol_flowchart`, `show_activity_subgroup_in_protocol_flowchart`, `show_soa_group_in_protocol_flowchart` | boolean | defaults true / true / false | no | Same toggles at the grouping levels (`/study-activity-groups`, `/study-activity-subgroups`, `/study-soa-groups`). |
| Keep old version | `keep_old_version` | boolean | | no | The user reviewed a newer library version and chose to keep the current one (cleared when an even newer version appears). |
| Order | `order` | number | reorder within grouping; drag-and-drop in the detailed SoA | – | |
| *Placeholder / request only:* Activity name | `activity_name` | text | | yes | Temporary name for an activity that does not exist in the Library yet. |
| Rationale for activity request | `request_rationale` | text | | yes | Why the activity is needed. |
| Data collected | `is_data_collected` | boolean | | no | Whether data will be collected for it. |
| Submit request | `is_request_final` | boolean | | no | Sends the placeholder to the Library *Requested activities* queue; after approval the study can *Update to approved activity*; after rejection the reason and contact person are shown. |

Derived: `latest_activity`, `is_activity_updated` (name or groupings changed in the Library), `accepted_version`. Table columns: order, Library, SoA group, Activity group, Activity subgroup, Activity, Data collection, Modified, Modified by. The add form lists library activities with Library, SoA group, Activity group, Activity subgroup, Activity, Definition, Synonyms, Abbreviation and Data collection, offers *Show selected activities* and *Use the same SoA group for all*, and the study activities tab is split by status tabs. Batch endpoints exist for create/update/delete, activity replacement (exchange an activity while keeping schedules and footnotes) and a *changes review* batch (accept / decline newer library versions). A "lite" listing and cross-study listing (`/study-activities`) support the *select from studies* flow.

### 9.2 Detailed SoA and activity schedules (`ScheduleOfActivities/*.vue`, `StudyActivityScheduleBatchEditForm.vue`; `/studies/{uid}/study-activity-schedules`)

A *study activity schedule* is the "X" in the SoA: one record per activity × visit (`study_activity_uid`, `study_visit_uid`; derived `study_activity_instance_uid` when the operational SoA is used). Users tick circles in the Detailed SoA grid, batch-assign visits to selected activities, hide/show rows, reorder rows by drag-and-drop, and attach footnotes. `GET /studies/{uid}/study-activity-schedules?operational=true` returns the instance-level schedule. The detailed SoA history (`/detailed-soa-history`: object type, description, action, author, dates) records every schedule change. Exports (`/detailed-soa-exports`): study number, study version, SoA group, activity group, activity subgroup, visit, activity, data collected.

### 9.3 Protocol SoA settings and rendering (`SoaSettingsForm.vue`, `ProtocolFlowchart.vue`; `/studies/{uid}/flowchart…`)

| UI label | API field / endpoint | Values | Meaning |
|---|---|---|---|
| Time unit for the SoA header | `PATCH /studies/{uid}/time-units` (`unit_definition_uid`, study field `soa_preferred_time_unit`; a separate value for the protocol SoA) | day or week | Whether visit timing in the header row is shown in days or weeks. |
| Show baseline as time 0 | `soa-preferences.baseline_as_time_zero` | boolean | Use study duration (baseline = 0) instead of study day / week (baseline = 1). |
| Show epochs | `soa-preferences.show_epochs` | boolean, default true | Epoch header row in the protocol SoA. |
| Show milestones | `soa-preferences.show_milestones` | boolean, default false | Milestone row built from visits marked as SoA milestones. |
| Show all visits in lab table | `soa-preferences.show_all_visits_lab_table` | boolean | Protocol lab table layout. |
| SoA splits | `PUT /studies/{uid}/soa-splits` (visit uid) | | Visits after which the SoA table is split into a new table. |

Rendered layouts (`layout` = `protocol`, `protocol_lab_table`, `detailed`, `operational`) are available as HTML, DOCX and XLSX; a snapshot of the SoA is stored on release/lock (`/flowchart/snapshot`). The Protocol SoA is what the Word add-in inserts into protocol section 1.2.

### 9.4 SoA footnotes (`StudyFootnoteForm.vue`, `StudyFootnoteEditForm.vue`, `StudyFootnoteTable.vue`, `RemoveFootnoteForm.vue`; `/studies/{uid}/study-soa-footnotes`)

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| Footnote | `footnote_uid` / `footnote_template_uid` (+ parameter terms) / `footnote_data` | rich text | Library footnote templates (typed, indexed by activities and groups), other studies, or scratch | yes | Footnote text shown under the protocol SoA. |
| Linked to | `referenced_items[]` (`item_uid`, `item_type`, `item_name`, `visible_in_protocol_soa`) | multi-reference | SoA items: `StudySoAGroup`, `StudyActivityGroup`, `StudyActivitySubGroup`, `StudyActivity`, `StudyActivityInstance`, `StudyActivitySchedule` (an X), `StudyEpoch`, `StudyVisit` | no | Where the footnote symbol appears. Linking to a hidden activity triggers a warning. |
| Order / symbol | `order` (derived) | letter a–z | assigned automatically by position in the SoA (top-left to bottom-right) | – | |

Derived: `latest_footnote`, `accepted_version`, `template`, `modified`. Table columns: #, Footnote, Linked to. Batch edit and batch select endpoints exist.

### 9.5 Activity instructions (`StudyActivityInstructionTable.vue`, `StudyActivityInstructionBatchForm.vue`, `StudyActivityInstructionBatchEditForm.vue`; `/studies/{uid}/study-activity-instructions`)

Links a study activity (`study_activity_uid`) to an *activity instruction* instance (`activity_instruction_uid`, or `activity_instruction_data` created from a Library activity-instruction template). Intended for protocol section 8 (assessments and procedures); the help text notes the feature is not yet in use. Fields displayed: activity, instruction text, modified, modified by.

## 10. Study data specifications (activity instances and operational SoA)

Page: *Studies › Define Study › Data Specifications* (`StudyDataSpecifications.vue`) with tabs Study Activity Instances (`StudyActivityInstancesTable.vue`, `StudyActivityInstancesEditForm.vue`, `BatchUpdateActivityInstanceForm.vue`, `UpdateActivityInstanceForm.vue`) and Operational SoA (`OperationalSoa.vue`). Backend: `StudySelectionActivityInstance` in `study_selection.py`, `/studies/{uid}/study-activity-instances`.

A *study activity instance* links a study activity to one Library activity instance (the biomedical concept that carries the SDTM topic code and ADaM parameter code). One row exists per activity–instance pair; instances marked *required* or *default* in the Library are pre-selected.

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| Activity | `study_activity_uid` → `activity` | reference | study activities | yes | |
| Activity instance | `activity_instance_uid` → `activity_instance` | reference | Library activity instances available for the activity | no (state *Add instance* when missing) | e.g. *Albumin Urine* for activity *Albumin*. |
| Show in operational SoA | `show_activity_instance_in_protocol_flowchart` | boolean | | no | |
| Reviewed | `is_reviewed` | boolean | | no | User confirmation that the pair meets study requirements; cleared when the instance changes in the Library unless *keep old version* is set. |
| Important | `is_important` | boolean | | no | Flags a key instance (exposed to downstream feeds). |
| Baseline visits | `baseline_visit_uids` → `baseline_visits` | multi-reference | study visits | no | Visits that define baseline for this instance (ADaM ABLFL). |
| Data supplier | `study_data_supplier_uid` → `study_data_supplier_name` | reference | the study's data suppliers (section 12) | no | Who delivers the data for this instance. |
| Origin type | `origin_type_uid` → `origin_type` | select | CDISC codelist *Origin Type* (ORIGINT): Assigned, Collected, Copied, Derived, Protocol, Not available, Other | no | Defaulted from the data supplier; Define-XML origin type. |
| Origin source | `origin_source_uid` → `origin_source` | select | CDISC codelist *Origin Source* (ORIGINS): Clinical Study Sponsor, Investigator, Study Subject, Vendor | no | Defaulted from the data supplier; Define-XML origin source. |
| Keep old version | `keep_old_version` | boolean | | no | Declined a newer library version of the instance. |

Derived and displayed (table columns): State/Action (`state`: Review not needed, Review needed, Reviewed, Add instance, Remove instance, Not applicable), Library, SoA group, Activity group, Activity subgroup, Activity, Data collection (`is_data_collected`), Activity instance, Topic code, Test name code, Specimen, Standard unit, ADaM param code, Activity instance class, Required / Default / Multiple selection / Legacy flags, activity and instance NCI concept IDs, Important, Baseline visits, Data supplier, Origin type, Origin source, Modified, Modified by; `is_activity_instance_updated` and `latest_activity_instance` drive the red / yellow exclamation indicators and the *Review activity instance updates* batch. The Operational SoA tab is read-only (toggles *Expand table* and *Show SoA groups*) and combines epochs, visits (with window and timing), the SoA hierarchy down to activity instance, and per instance the Topic Code and ADaM PARAMCD; exportable (`/operational-soa-exports`: study number, version, SoA group, activity group, activity subgroup, visit, activity, data collected, library, epoch, activity instance, topic code, param code).

## 11. Study interventions: compounds and compound dosing

Page: *Studies › Define Study › Study Interventions* (`InterventionsPage.vue`) with tabs Study Compounds (`CompoundForm.vue`, `CompoundTable.vue`), Study Compound Dosing (`CompoundDosingForm.vue`, `CompoundDosingTable.vue`) and Overview (`InterventionOverview.vue`, one column per study compound with all attributes and dosings; also exported as DOCX/HTML via `/studies/{uid}/interventions`). Backend: `StudySelectionCompound`, `StudyCompoundDosing` in `study_selection.py`.

### 11.1 Study compounds (`/studies/{uid}/study-compounds`)

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| Type of treatment | `type_of_treatment_uid` → `type_of_treatment` | select | sponsor codelist *Type of Treatment* (TPOFTRT): Investigational Product, Comparator, Placebo, Rescue medication, Background treatment … | no | Purpose of the compound in the study. |
| Compound | `compound_alias_uid` → `compound_alias` and `compound` | reference | Library compound aliases (a compound and its preferred synonym) | yes | Defined in the Library; new compounds must be added there first. |
| Medicinal product | `medicinal_product_uid` → `medicinal_product` | reference | Library medicinal products linked to the compound | yes | Carries dosage form, strength, route, dose values, frequency, delivery device and dispenser. |
| Other information | `other_info` | text | | no | Optional additional information. |
| Reason for missing | `reason_for_missing_null_value_uid` | select | *Null Flavor* codelist | no | Why no compound is used (e.g. non-drug study). |

Derived on read: `pharmaceutical_products` (formulations, ingredients, active substances with UNII and pharmacological class, strengths, half-life, lag times), `dispenser`, `dose_frequency`, `delivery_device` (from the medicinal product), `study_compound_dosing_count`, and in the fork `native_library_bindings` (the exact historical library readings used for a locked study version). Table columns: Type of treatment, Reason for missing, Compound, Sponsor compound, Compound alias, Medicinal product, Dose frequency; the export adds pharmacological class, substance names, UNII codes, dose value, dispenser, delivery device and other information.

### 11.2 Study compound dosing (`/studies/{uid}/study-compound-dosings`)

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| Element | `study_element_uid` → `study_element` | reference | study elements | yes | The element in which the compound is given. |
| Compound | `study_compound_uid` → `study_compound` | reference | study compounds | yes | |
| Dose value | `dose_value_uid` → `dose_value` (value + unit) | select | dose values defined on the medicinal product (numeric value with unit) | no | e.g. 500 mg. |

Derived: dose frequency (from the medicinal product), `native_library_bindings` (fork). Table columns: Study Element, Compound Name, Medicinal product, Compound Alias Name, Preferred Alias, Dose Value, Dose Frequency.

## 12. Study data suppliers

Pages: *Studies › Manage Study › Data Suppliers* (`StudyDataSuppliers.vue`, overview by supplier type plus a list tab) and the edit page (`StudyDataSuppliersEdit.vue`). Backend: `StudySelectionDataSupplier` in `study_selection.py`, `/studies/{uid}/study-data-suppliers` (sync, order, per-item CRUD, audit trail). The Library defines the suppliers (name, description, order, supplier type, default origin source and origin type, API and UI base URLs; see section 16); the study only records which suppliers deliver which data type.

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| Supplier data type | `study_data_supplier_type_uid` → `study_data_supplier_type` | select | sponsor codelist *Data Supplier Type*: EDC System, Lab Data Exchange Files, eCOA Data Exchange Files, … | yes | The type of data the supplier provides in this study. |
| Data supplier | `data_supplier_uid` | reference | Library data suppliers in *Final* state; dedicated suppliers for the type are listed first, others under *Other suppliers*; *Add user-defined supplier* creates a new Library supplier | yes | Only suppliers directly delivering study data are in scope (not clinical supplies or subcontractors). |
| Order | `study_data_supplier_order` | number | | – | |

Derived: `name`, `description`, `api_base_url`, `ui_base_url` from the Library supplier. The same supplier may be assigned under several types. Selected suppliers become the choices for *Data supplier* on the activity-instance rows (section 10), and appear in the consumer API study record.

## 13. Study data standard versions, tags and scores

### 13.1 Data standard versions (`StudyDataStandardVersions.vue`, `dataStandardVersions/CTStandardVersionsForm.vue`, `CTStandardVersionsTable.vue`; `/studies/{uid}/study-standard-versions`)

| UI label | API field | Type | Values / source | Req. | Meaning |
|---|---|---|---|---|---|
| CT catalogue | (selection step) | select | CT catalogues (SDTM CT, ADaM CT …), one package per catalogue | yes | |
| Sponsor CT package | `ct_package_uid` → `ct_package` | reference | sponsor CT packages, each extending a CDISC CT package (e.g. *SDTM CT 2023-03-31*); a new sponsor package can be created from the form | yes | Pins which terminology version the study's CT selections are read with. |
| Description | `description` | text | | no | |
| Automatically created | `automatically_created` | boolean (system) | | – | Set when the system picked a package of the day at release/lock time; removed on unlock. |

Table columns: CT Catalogue, CDISC CT Package, Sponsor CT Package, Description, Automatically created, Modified, Modified by. A warning is shown when a selected term is not valid in the chosen package. Dictionary versions and data-exchange standard versions are not yet selectable.

### 13.2 Data completeness tags (`/data-completeness-tags`, `/studies/{uid}/data-completeness-tags`)

Admin-defined tags (`uid`, `name`) that can be attached to a study and are shown in the Study List *Data completeness* column and the consumer API.

### 13.3 Complexity score (`StudyComplexityScoreDetails.vue`; `GET /studies/{uid}/complexity-score`, `/complexity-score-details`)

A derived score from the study's visits and assessments. Library burden definitions (`Burden`: `burden_id`, `name`, `description`, `site_burden`, `patient_burden`, `median_cost_usd`; `ActivityBurden` per activity subgroup) are imported (`run_import_complexity_burdens.py`); the study endpoint returns per visit type and per assessment type the count and burden. The details dialog (opened from the Detailed SoA) shows two tables: visits by Type with Count, Burden and Count × Burden, and assessments by Activity subgroup with the same columns.

### 13.4 Other study-level records

* **Study template** (`/studies/template`): an admin-selected study (`study_uid` + `study_value_version`) used as the template when cloning; versioned like a library item.
* **Study integrity check** (`GET /studies/{uid}/integrity-check`): per-check pass/fail with non-compliant node counts and labels.
* **Selection containment** (`GET /studies/{uid}/study-selection-containment/{target}`): compares which selection labels of one study are contained in another (used by the fork's import verification).
* **Comments**: any study page can carry comment threads (`topic_path`, text, author, status active/resolved, replies) via the `/comments` endpoints.


## 14. Derived and exported views of a study

Nothing in this section is entered by users. Each view re-reads the study definition described in sections 2 to 13 and renders or exports it in another shape. They are listed because they are where study data is *displayed* outside the editing pages.

### 14.1 Study Summary and Protocol views (frontend)

| View | Route / file | What it shows |
|---|---|---|
| Study Summary | `/studies/{uid}/summary` (`SummaryPage.vue`, `StudyMetadataSummary.vue`, `StudyIdentificationSummary.vue`) | Identification, registry identifiers, study type, study attributes, population and title in read-only cards. The fork shows *Not provided* when a null flavour is set and *Not configured* when a field is empty. |
| Study Structure overview | `/studies/{uid}/study_structure/overview` (`StudyStructureOverview.vue`) | Counts of arms, branches, cohorts, epochs, elements and visits, the design matrix picture and the *Planned enrollment (protocol target)* figure taken from the population's number of expected subjects (fork behaviour; falls back to the sum of arm sizes). |
| Per-item overviews | `StudyArmOverview.vue`, `StudyBranchArmOverview.vue`, `StudyCohortOverview.vue`, `StudyEpochOverview.vue`, `StudyElementOverview.vue`, `StudyVisitOverview.vue`, `StudyActivityOverview.vue`, `StudyCompoundOverview.vue` | One page per selected item with all its attributes and its library counterpart. |
| Protocol Elements | *View Specifications › Protocol Elements* (`ProtocolElements.vue`, `ProtocolTitlePage.vue`, `ProtocolElementsStudyDesign.vue`, `ProtocolElementsObjectiveTable.vue`, `ProtocolElementsStudyPopulationSummary.vue`, `ProtocolElementsStudyIntervention.vue`, `ProtocolElementsProceduresAndActivities.vue`, `ProtocolFlowchart.vue`) | The protocol-ready renderings: title page (study title, short title, acronym, study ID, registry identifiers, protocol version), study design (arms, epochs, elements, design matrix figure), objectives and endpoints table, population summary, interventions, procedures and activities, and the Protocol SoA (downloadable as DOCX). The Word add-in pulls these into the protocol template. The title page API (`GET /studies/{uid}/protocol-title`, `StudyProtocolTitle`) returns study title, short title, EudraCT ID, UTN, trial phase, IND number, development stage and protocol header version; its `substance_name` slot is never populated. |
| Study design figure | `GET /studies/{uid}/design.svg` (`routers/studies/study_design_figure.py`) | SVG drawing of arms, epochs and elements. |
| Schedule of Activities exports | `routers/studies/study_flowchart.py` | Protocol, detailed and operational SoA as HTML, DOCX and XLSX; SoA snapshots stored on release/lock (`UpdateSoASnapshot` audit action). |
| USDM | *View Specifications › USDM* (`UsdmPage.vue`) | The study rendered as a CDISC/TransCelerate Unified Study Definitions Model JSON document (see 14.4). |
| ICH M11 | *View Specifications › ICH M11* (`IchM11Page.vue`) | HTML rendering of the ICH M11 protocol template filled from the study (see 14.5). |
| Clinical Transparency (study disclosure) | *View Specifications › Clinical Transparency* (`StudyDisclosure.vue`, `StudyDisclosureTable.vue`) | ClinicalTrials.gov Pharma CM view (see 14.2) with an XML download; tabs Identification, Secondary IDs, Conditions, Design, Interventions, Outcome Measures, each showing the OpenStudyBuilder term, the PharmaCM term and the value. |
| Analysis Study Metadata (New) | *View Listings* (`AnalysisStudyMetadataNew.vue`, `AnalysisMetadataTable.vue`) | ADaM metadata listings MDVISIT, MDENDPNT and MDFLOW (see 14.3). |
| SDTM Study Design Datasets | *View Specifications* (`SdtmStudyDesignDatasets.vue`, `SdtmDesignTable.vue`) | The trial design datasets TA, TE, TV, TI, TS and TDM with their SDTM variable labels (see 14.3). |
| CRF / ODM views | `CrfSpecifications.vue`, `BlankCrf.vue`, `CdashAnnotatedCrf.vue`, `SdtmAnnotatedCrf.vue`, `OdmSpecification.vue`, `CtrOdmXml.vue`, `StudyOdmViewer.vue` | The ODM forms linked to the study's activity instances (chosen in a *Form(s)* selector) rendered as HTML, as a CRF with annotations and as a downloadable Word document, plus the ODM XML and the CTR ODM XML (`GET /studies/{uid}/ctr/odm.xml`). Reachable from the study views but the *CRF Specifications* menu entry is disabled in v2.9.0. |
| Placeholder pages | `ProjectStandards.vue`, `StandardisationPlan.vue`, `TrialSuppliesSpecifications.vue`, `TerminologyPage.vue`, `MmaTrialMetadata.vue`, `DmwAdditionalMetadata.vue`, `SdtmAdditionalMetadata.vue`, `AdamDefineCst.vue`, `AdamDefineP21.vue`, `SdtmDefineCst.vue`, `SdtmDefineP21.vue`, `AdamSpecification.vue`, `SdtmSpecification.vue`, `SpecificationDashboard.vue` | Menu entries that are *under construction* in v2.9.0 and display no study data (or only a static description). |

### 14.2 Study Disclosure (ClinicalTrials.gov Pharma CM)

Backend: `GET /studies/{uid}/pharma-cm` and `GET /studies/{uid}/pharma-cm.xml` (`models/study_selections/study_pharma_cm.py`, `StudyPharmaCM` and `StudyPharmaCMXML`). The page shows the OpenStudyBuilder term next to the Pharma CM term and the value.

| Pharma CM field | Derived from |
|---|---|
| Unique protocol identification number | Study ID (`study_id_prefix-study_number`) |
| Brief title / Official title / Acronym | Study short title, study title, study acronym |
| Study type | Study type (C99077) |
| Secondary IDs (`secondary_id`, `id_type`, `description`) | Every non-empty registry identifier, typed as *EudraCT Number*, *Registry Identifier* or *Other Identifier* |
| Responsible party | Constant `Sponsor` |
| Primary disease or condition being studied | Disease/condition/indication dictionary terms |
| Primary purpose | Trial intent type terms |
| Study phase | Trial phase |
| Interventional study model | Intervention model |
| Number of arms; study arms (`arm_type`, `arm_title`, `arm_description`) | Study arms |
| Allocation | `randomized` / `not randomized` from *Randomised*, or `N/A` for non-interventional studies |
| Number of subjects | Sum of the arms' planned number of subjects |
| Intervention type | Intervention type |
| Outcome measures (`title`, `timeframe`, `description`) | Study endpoints: linked objective text, timeframe text and endpoint units |
| Minimum age / Maximum age | Planned minimum and maximum age (value and unit) |
| Accepts healthy volunteers | Healthy subject indicator |
| Inclusion criteria / Exclusion criteria | Study criteria of type Inclusion and Exclusion (plain text) |

The XML export uses the full ClinicalTrials.gov PRS schema, which also has slots for sponsors, collaborators, overall officials (investigator first/middle/last name, degrees, role, affiliation), contacts (phone, email), facilities and locations (address, city, state, zip, country), oversight (IRB, DMC, FDA regulation), IPD sharing, dates and status. **None of these are collected in OpenStudyBuilder**; they are emitted empty. In particular there is no principal-investigator, site or sponsor-contact field anywhere in the study definition.

### 14.3 Listings for downstream programming (SDTM, ADaM, study metadata)

All under the `/listings` prefix (`routers/listings/`, models in `models/listings/`). Values are read from a specific study version when requested.

**Study metadata listing** (`GET /listings/studies/study-metadata`, `StudyMetadataListingModel`): `api_ver`, `study_id`, `study_ver`, `specified_dt`, `request_dt`, `title`, `reg_id` (13 registry identifiers), `study_type`, `study_attributes`, `study_population` (each value paired with a `*_nf` null-flavour column), `arms`, `branches`, `cohorts`, `epochs`, `elements`, `design_matrix`, `visits`, `criteria`, `objectives`, `endpoints`.

**SDTM trial design datasets** (`GET /listings/studies/{uid}/sdtm/{domain}`):

| Domain | Model | Variables |
|---|---|---|
| TV Trial Visits | `StudyVisitListing` | STUDYID, DOMAIN, VISITNUM, VISIT, VISITDY, ARMCD, ARM, TVSTRL, TVENRL |
| TA Trial Arms | `StudyArmListing` | STUDYID, DOMAIN, ARMCD, ARM, TAETORD, ETCD, ELEMENT, TABRANCH, TATRANS, EPOCH |
| TE Trial Elements | `StudyElementListing` | STUDYID, DOMAIN, ETCD, ELEMENT, TESTRL, TEENRL, TEDUR |
| TI Trial Inclusion/Exclusion | `StudyCriterionListing` | STUDYID, DOMAIN, IETESTCD, IETEST, IECAT, IESCAT, TIRL, TIVERS |
| TS Trial Summary | `StudySummaryListing` | STUDYID, DOMAIN, TSPARMCD, TSPARM, TSVAL, TSVALNF, TSVALCD, TSVCDREF, TSVCDVER (one row per study property, population and intervention field, with the null flavour in TSVALNF) |
| TDM Trial Disease Milestones | `StudyDiseaseMilestoneListing` | STUDYID, DOMAIN, MIDSTYPE, TMDEF, TMRPT |

**ADaM metadata listings** (`GET /listings/studies/{uid}/adam/{report}`):

| Report | Model | Variables |
|---|---|---|
| MDVISIT | `StudyVisitAdamListing` | STUDYID, VISTPCD, AVISITN, AVISIT, AVISIT1N, VISLABEL, AVISIT1, AVISIT2, AVISIT2N, plus fork columns SOURCE_VISIT_UID, TIMING_MODE, VISIT_DESCRIPTION |
| MDENDPNT | `StudyEndpntAdamListing` | STUDYID_OBJ, OBJTVLVL, OBJTV, OBJTVPT, ENDPNTLVL, ENDPNTSL, ENDPNT, ENDPNTPT, UNITDEF, UNIT, TMFRM, TMFRMPT, RACT, RACTSGRP, RACTGRP, RACTINST |
| MDFLOW (flowchart) | `FlowchartMetadataAdamListing` | STUDYID_FLOWCHART, AVISITN, PARAMCD, AVISIT, PARAM, PARAMN, ATPTN, ATPT, TOPICCD, BASETYPE, ABLFL, ASSMTYPE, plus fork columns SOURCE_SCHEDULE_UID, SOURCE_ACTIVITY_UID, SOURCE_ACTIVITY_NAME, SOURCE_VISIT_UID, TIMING_MODE, VISIT_DESCRIPTION |

The fork's Analysis Metadata page renders *Not fixed* for the AVISIT1/AVISIT2 columns of untimed visits and *Not configured* for empty cells.

**Library listings** (not study-specific): `/listings/libraries/all/gcmd/topic-cd-def` (topic code definitions: `lb`, `topic_cd`, `short_topic_cd`, `description`, `molecular_weight`, `sas_display_format`, `general_domain_class`, `sub_domain_class`, `sub_domain_type`), `cdisc-ct-ver`, `cdisc-ct-pkg`, `cdisc-ct-list`, `cdisc-ct-val` (CT versions, packages, codelists and values), and `/listings/metadata` (dataset and variable metadata: `dataset_name`, `dataset_label`, `name`, `type`, `length`, `label`, `format`, `informat`).

### 14.4 USDM export (Digital Data Flow)

`GET /studyDefinitions/{uid}` (`routers/ddf/study_definitions.py`, `services/ddf/usdm_mapper.py` and helpers). The study is mapped to USDM 4.0 objects: `Study`, `StudyVersion`, `StudyTitle`, `StudyIdentifier`, `Organization`, `StudyDefinitionDocument`, `InterventionalStudyDesign` or `ObservationalStudyDesign` (the fork's observational model and time perspective feed the latter), `StudyDesignPopulation` (with `Range`/`Quantity` for ages), `Indication`, `StudyArm`, `StudyEpoch`, `StudyElement`, `StudyCell`, `TransitionRule`, `Objective`, `Endpoint`, `EligibilityCriterion` / `EligibilityCriterionItem`, `StudyIntervention` / `Administration` (from study compounds and dosings), `Activity`, `Encounter` (visits), `ScheduleTimeline`, `ScheduledActivityInstance`, `Timing`, `Duration`, `Code`/`AliasCode` (controlled terms with the CT package version) and `ExtensionAttribute`s. The fork's EDC study exchange (section 15) is built on this USDM document.

### 14.5 ICH M11 protocol rendering

`GET /studyDefinitions/{uid}/m11` renders the ICH M11 technical specification template (`clinical-mdr-api/m11-templates/ICH_Step4_M11_Final_TechnicalSpecification_2025_1119.json`) as HTML from the same USDM mapping: title page, study identifiers, synopsis, objectives and endpoints, design, population, interventions and the schedule of activities. Fields the study does not hold (investigators, sponsor signatories, sites) render as empty template slots.

### 14.6 Consumer API (`clinical-mdr-api/consumer_api`, v1 and v2)

A read-only API for downstream systems exposing: `Study` (uid, id, id_prefix, number, acronym, versions, data_completeness_tags, data_suppliers), `StudyVisit` (visit class/subclass, names, numbers, window, anchor flag, visit type, epoch, timing value/unit/reference, sub-visit and special-visit numbers), `StudyActivity` (SoA group, activity group/subgroup, activity, NCI concept, data-collected flag), `StudyActivityInstance`, `StudyDetailedSoA` (activity × visit rows), `StudyOperationalSoA` (activity instance × visit rows with topic code and ADaM parameter code), library activities/instances/items, codelists, terms and unit definitions, a *Papillons SoA* feed (topic code, important flag, baseline visits, SoA group, visits per study version) and a `SoACreateInput` for creating epochs, visits and activities from an external system.

### 14.7 Other exports and reports

* Every table has CSV / JSON / XML / Excel export (`routers/export.py` and per-router `Accept` headers).
* `studybuilder-export` dumps the whole database or the study list (`export.py`, `export_study_list.py`) to JSON files for migration or reporting.
* NeoDash dashboards (`neo4j-mdr-db/neodash/neodash_reports/`): activity instance export, activity library content, activity metadata check, audit trail report, CRF impact analysis, CRF library version, CT codelist term history, data exchange data models, laboratory data specification, pre-define, study metadata compare, syntax template dashboard. In the AccuraTrial deployment NeoDash is a private administrative tool only: it connects as the Neo4j `neo4j` user, and Neo4j Community has no read-only role, so it bypasses the delegated study scope that the API enforces. Development-only; not deployed to a pilot tenant (sellability plan W2.3, 2026-09-21).


## 15. AccuraTrial fork: additional data collected, stored and exchanged

Everything in sections 2 to 14 exists in upstream OpenStudyBuilder v2.9.0 unless marked *(fork)*. This section lists what the AccuraTrial fork adds. The fork's changes fall into five groups: tenant-scoped study access, extra study data, the EDC export page and bundle, the import routes that create studies from the 360i / Proposal V2 pipelines, and the governed "platform-control" records that support signed review and release.

### 15.1 What the fork does *not* collect

A frequent question is whether the system holds investigator, site or participant data. It does not:

* **Principal investigator / investigator:** no field, node, form or table anywhere in the study definition. The word appears only as the name of a user role in the fork (an *investigator* is a Command Center user with `Study.Read` who sees only studies assigned to them), in CDISC term definitions, and as empty slots in the ClinicalTrials.gov XML template (section 14.2).
* **Sites:** no native model. The EDC exchange bundle format allows an `execution.sites[]` block; if an imported EDC source bundle contained one, OpenStudyBuilder keeps it opaquely inside the retained source snapshot and echoes it back on export. It is never parsed, displayed or edited.
* **Subjects / patients:** only protocol-level *planned* figures are held (number of expected subjects, planned age range, healthy-subject indicator, arm / branch / cohort sizes). The population form's help text states that actual participant totals are tracked in the EDC. The fork adds deny-lists that reject subject or patient identifiers, dates of birth, medical record numbers, item and lab values and free-text narratives from logs, telemetry and command payloads.
* **Sponsor contacts:** none. "Sponsor" only ever denotes the *Sponsor* library or the sponsor-preferred name of a controlled term.

### 15.2 Tenant-scoped study access (Command Center identity)

Source: `clinical-mdr-api/clinical_mdr_api/services/studies/study_visibility.py`, `routers/studies/study_access.py`, `common/auth/models.py`, `common/auth/user.py`; frontend `studybuilder/src/plugins/auth.js`, `stores/auth.js`.

| Data | Where it comes from | Where it is stored | Used for |
|---|---|---|---|
| Tenant (`tenant_id`), assigned studies (`study_ids`), token purpose (`interactive-domain-access` or `workflow-orchestration`), capabilities (`study:read`, `study:write`, `candidate:*`, `package:release`, `native-identity:*`, `platform-command:execute`, `draft:*`), roles (`Study.Read/Write`, `Library.Read/Write`, `Admin.Read/Write`, `service`), subject type (human or service), issuer, actor chain, session id | Command Center RS256 access token (5-minute lifetime, audience `accuratrial-openstudybuilder`); the UI fetches it from `/__sso/token` on its own origin and keeps it in memory only | Neo4j `User` node (see section 17), updated on every authenticated request | Deciding which studies a caller may list, read or write |
| Study → tenant binding | Created when a study is created by an authenticated tenant | Neo4j `DomainStudyScope {study_uid, tenant_id, status (active / quarantined / retired), created_at, reason, platform_study_id}`; legacy studies with no tenant are *quarantined* by migration 026 | In strict mode (`OIDC_DELEGATED_CLAIMS_REQUIRED=true`, mandatory in production) a study is visible only if it is in the caller's `study_ids` **and** has an active scope for the caller's tenant; `Admin.Read` is not a wildcard. In non-strict development mode a study is visible to admins, to its `version_author`, or to callers whose `study_ids` include it. |

Consequences visible to users: the study list only returns assigned studies; cross-study listings (all objectives, endpoints, compounds, criteria, activities, activity instances, arms, data suppliers, footnotes, activity instructions across studies) return an empty page for non-admins; a study outside the caller's scope answers 404. The frontend applies the same roles to menus (Library requires `Library.Read`, *Create study* requires `Study.Write`). The user-profile edit endpoint (`PATCH /admin/users/{id}`) is disabled because identity fields are synchronised from Command Center.

### 15.3 Additional study data in the fork

| Addition | Where | What is stored / shown |
|---|---|---|
| Observational study model, observational time perspective | `high_level_study_design` (section 5.1) | Two CT-coded study fields (C127259, C127261), API-only; the fork's Study Type summary shows *Observational time perspective*. |
| Untimed visits | `StudyVisit.timing_mode`, `StudyVisit.untimed_timing` (section 7.7) | Manually defined visits without an absolute study day; SoA headers, ADaM listings and the EDC bundle show *Not fixed* / `unscheduled` for them. |
| Null adjudication | `GET/PATCH /studies/{uid}/null-adjudications` | An atomic way to sign empty metadata slots with a *reason for missing*: the request lists value path, companion path, expected current value and companion (both must still be as expected) and the null-flavour term; 44 value/companion pairs are adjudicable (13 registry identifiers, 8 study-design, 14 population, 9 intervention fields). The receipt records the request hash, checked paths and applied terms. Nothing new is persisted beyond the ordinary study fields. |
| Terminology witnesses for locked versions | `StudyEpoch.terminology_source`, `StudyDiseaseMilestone.terminology_source`, `StudySelectionCompound.native_library_bindings` and `pharmaceutical_products`, `StudyCompoundDosing.native_library_bindings` | When a locked or released study version is read, the exact CT package date, term versions and library value versions used for the labels are returned alongside the data (including the `author_id` and change description of those library versions). Read-only evidence; not stored on the study. |
| Study-list scoping, arm audit attribution | repositories | Study list queries are scoped to assigned studies; arm create/edit/delete audit records now carry the current editor as author. |
| ADaM listing columns | `SOURCE_SCHEDULE_UID`, `SOURCE_ACTIVITY_UID`, `SOURCE_ACTIVITY_NAME`, `SOURCE_VISIT_UID`, `TIMING_MODE`, `VISIT_DESCRIPTION` (section 14.3) | Native identities added to MDVISIT and MDFLOW so rows can be traced back. |
| Study template, integrity check, selection containment | section 13.4 | Present upstream; used by the fork's import verification. |

### 15.4 EDC Export page and the study exchange bundle

Page: *Studies › View Specifications › EDC Export* (`studybuilder/src/views/studies/EdcExport.vue`, route `/studies/{uid}/edc_export`). Backend: `GET /integrations/edc/studies/{uid}/study-bundle` and `POST …/study-bundle/send` (`clinical-mdr-api/clinical_mdr_api/routers/integrations/edc_export.py`, `services/integrations/edc_export.py`, `edc_study_exchange.py`). The export is a pure read of the study; **nothing is written to the database by exporting**.

**What the page shows.** *Build preview* renders: study name, selected study version, selected design, counts of visits, forms, acquisition fields and visit-form assignments; a warning when the USDM document has more than one version or design; the mapping report issues (code, message, source path, target study field, resolution); a census of every USDM `instanceType` in the document; the export census warnings (kind, reference, detail); and a *Download .ecrfstudy* button that saves the whole bundle as JSON. *Send to EDC* offers only a *Dry-run (validate only)* call (the real send was removed in the working tree; the backend refuses non-dry-run sends, production environments and any EDC base URL on the production domain) and then shows the EDC status code, the quarantine study id returned by the EDC and the full EDC response.

**What the bundle contains** (`edc-study-exchange/2`, USDM 4.0.0):

| Block | Content |
|---|---|
| `definition.document` | The full USDM document from section 14.4 (study, versions, titles, identifiers, designs, population, arms, epochs, elements, cells, objectives, endpoints, eligibility criteria, interventions, activities, encounters, schedule timelines, coded terms). |
| `execution.visits[]` | One row per study visit: `refKey`, `name`, `ordinal`, `type` (scheduled / unscheduled), `category` (epoch), `repeating`, `scheduleDay`, `minDay`, `maxDay` (from timing and window converted to days). |
| `execution.forms.forms[]` | One row per ODM form reachable from the study's activity instances (or stamped by the 360i import): `refKey`, `name`, `description`, `sections[]` (item groups) and `fields[]` (items: `refKey`, `name`, `type` mapped to one of 49 EDC field types, `label`, `required`, `length`, `description`, `sdtmVariable`, `section`, `group`, `unit`, `options[]` from the codelist terms, plus any vendor-extension pass-through such as help text or validation rules). |
| `execution.visitFormAssignments[]` | `visitRef`, `formRef`, `required`, `ordinal` (from ODM study events created by the 360i import; native draft candidates carry no assignments until reviewed). |
| `execution.studyGroupClasses[]` | *Arms* class with the study arm names and descriptions; other group classes copied from a source bundle. |
| `extensions._osbExport.native` | A verbatim retained copy of every native record read: the study DTO (all metadata sections, registry identifiers, version metadata with author and timestamp), every visit, every ODM form / item group / item / study event / method / condition, every study selection collection (epochs, objectives, endpoints, criteria, activities, elements, design cells, cohorts, branch arms, activity groups / subgroups / SoA groups, activity instances, instructions, footnotes, disease milestones, data suppliers, design class, source variable, schedules, operational schedules), and the CT codelist / term, dictionary term, unit definition and timeframe definitions they reference, each with `uid`, version, status, dates and `author_username`. Also the `studyProjection` summary (name, unique identifier, NCT number, official title, acronym, protocol type, phase, allocation, masking, control, assignment, purpose, expected total enrolment, gender, age min/max, healthy volunteers accepted, therapeutic area, indication, conditions). |
| `extensions._osbExport.mappingAuthority`, `census`, `mappingReport` | The mapping-authority mode (`legacy` / `shadow` / `enforced`), whether the export is authoritative or deployable (always *no* for this preview path), census rows and the USDM mapping issues. |
| `source.artifacts[]`, `source.valueLedger` | SHA-256-hashed, gzip-encoded copies of the USDM document, the native observations and any inherited source bundle, with a per-value ledger tracing every JSON terminal to its disposition (canonical, execution, normalised, retained, unresolved). |

Identity data leaving the system in a bundle: `author_username`, `author_id`, `version_author`, timestamps and change descriptions on the retained records; never the requesting user. The dry-run send posts the bundle to `{EDC_BASE_URL}/api/forms/import-study-bundle` with an `x-api-key` header.

### 15.5 Import routes (`studybuilder-import`)

None of these exist upstream. They reach OpenStudyBuilder only through its REST API and read the AccuraTrial platform's Postgres database (`ecrf_platform`) with a tenant-scoped, row-level-secured role.

**Route A, 360i payload importer** (`importers/run_import_360i.py`, `mappings/payload_to_osb.py`; retired to a legacy mode that only runs outside production with explicit opt-in). Reads the latest `osb_study_payloads` row for a source study and creates or updates:

| Payload section | OpenStudyBuilder data written |
|---|---|
| `source.projectId`, `projectName` | Clinical programme (default *360i*), Project (`360I-<hash>`, name, description *Imported from 360i*). |
| `study.name`, `acronym`, `officialTitle`, `shortTitle`, `registryIdentifiers` | Study number (hashed, unique), study acronym, description *Imported from 360i study … (build …)*, study title and short title, registry identifiers (keys passed through verbatim, e.g. `ct_gov_id`). |
| `study.nativeMetadata` | Study type, trial phase, observational model, time perspective, sex of participants, healthy-subject indicator, number of expected subjects, planned minimum / maximum age (years), control type, intervention model, blinding schema, randomised flag; unresolvable terms stop the field. |
| `study.attributes` | Not written; recorded in the import census only (and release-blocked if unknown). |
| `epochs[]` | Study epochs (subtype resolved by name, order, description). |
| `visits[]` | Manually defined study visits (epoch, visit type, contact mode, global anchor, timing in days from the anchor, window, description, name, short name, numbers). Unscheduled or untimed source visits are stopped with a blocker. |
| `arms[]`, `nonArmGroupClasses[]` | Study arms (name, short name, description, arm type inferred from the name and control type); one scaffolding element per arm and a design cell per arm × epoch. |
| `scheduleOfActivities` | Study activities (matched to Final library activities by name; SoA group from CDASH domain, category or override file) and activity schedules for unconditional, footnote-free cells. |
| `studyPurpose` | Objective, endpoint and criteria templates (study-scoped, text as HTML) and the study objectives (level), endpoints (level, objective link, timeframe) and criteria (Inclusion / Exclusion / Withdrawal). |
| `odm.codelists[]`, `odm.units[]` | Sponsor codelists and terms (content-addressed names), unit definitions. |
| `odm.forms[]` (item groups, items), `visits × formVisitMatrix` | ODM forms, item groups and items (with `x360i` vendor attributes: `refKey`, `fieldType`, `studyId`, `buildHash`, `ext`, `source`, `content`), ODM study events `SE.360I.<study>.<visit>` with form references. |
| `sourceBundle`, `sourceCustody` | The complete source bundle stored as chunked, never-approved Draft ODM forms (`F.SEMANTIC.SNAPSHOT.*`) so the export can round-trip it losslessly. |

Results go to the Postgres `osb_import_ledger` (import id, study id, payload hash, OSB study uid and project number, status, census of created / updated / unchanged / stopped / carried / release-blocked items, and the uid map from source keys to OSB uids).

**Route B, Proposal V2 worker** (`importers/run_import_osb_proposal_v2.py`, `mappings/proposal_v2_*.py`, `utils/osb_proposal_db.py`; the only runtime route). Claims a proposal from the Postgres outbox, submits it for review (`POST /integrations/proposal-reviews`), polls the review until every proposal object has a signed decision and an execution authorisation for a specific DRAFT study version, then executes native operations in dependency order with idempotency keys. Supported targets: study metadata fields (all the paths in Appendix A plus the observational fields), arms, elements, epochs, design cells, manually defined visits, activity schedules, study activities, objectives, endpoints, criteria, standard versions, compounds, compound dosings, activity instructions, activity instances, and ODM forms / item groups / items / methods / conditions with their links and item-to-activity bindings. Every operation writes a receipt (idempotency key, native uid, record hash, match) into `native_execution_evidence` and `reconciliation_evidence` on the outbox row.

**Route C, EDC drop and round-trip verification** (`publish_edc_drop.py`, `verify_edc_roundtrip.py`): writes the exact exported bundle bytes plus a `manifest.json` (study id, title, content hash, byte length, counts of forms, fields, visits, assignments, deviation rules, study tasks, census warning count) to a drop folder, and compares a live export with the source bundle field by field.

### 15.6 Governed platform-control records (Neo4j, prototype-gated)

These endpoints under `/integrations/platform-control`, `/integrations/proposal-reviews`, `/integrations/mapping-context` and `/integrations/study-authority` answer 503 unless `OSB_PLATFORM_COMMANDS_PROTOTYPE_ENABLED` is set (never in production). They store canonical-JSON payloads keyed by `tenant_id` and `platform_study_id`, with SHA-256 hashes and, where a human decides, a detached JWS signature reference.

| Node label(s) | What is stored |
|---|---|
| `OsbMappingContextSnapshot` | The library context (codelists, terms, units, templates, activities) a proposal was mapped against, by content hash. |
| `OsbProposalReview`, `OsbProposalReviewObject`, `OsbProposalReviewDecision`, `OsbProposalExecutionAuthorization` | The submitted proposal (`proposal_json`, hash, object count), one node per proposed object with its candidates, each human decision (action: selected candidate / create request / not applicable, candidate key, note, `signature_id` bound to the reviewer's session token, `signature_verified`), and the execution authorisation (target study uid and version, decision-set hash, actor id, authorisation content hash). |
| `OsbInboundArtifact`, `OsbCandidateRequestV1`, `OsbCandidateSetV1`, `OsbCandidateRequestLock` | Candidate requests from the platform (tenant, platform study id, OSB study identity, source fact package, semantic snapshot, requested object families, typed source intents, evidence artifact references) and the generated candidate sets (per-record candidates, deferred members, conservation counts, blockers). |
| `StudyMappingDecisionV1`, `NativeOperationEvidenceV1`, `OsbNativeEvidenceSetV1`, `PlatformManagedStudyConcept` | Signed mapping decisions (including the human electronic signature block: signer name snapshot, roles and assignments at signing, issuer-qualified identity, signature meaning), the evidence of each native operation executed, and the managed study concepts attached to a `StudyRoot` (`managed_key`, `resource_family`, `payload_json`, `content_hash`, `fact_id`, `revision`, `target_key`). |
| `PlatformNativeStudyBinding`, `PlatformNativeIdentityEffect`, `PlatformNativeIdentityAudit`, `PlatformNativeCaptureBinding` | Binding between a platform study id and a native `StudyRoot` (namespace `accuratrials-osb`), the identity commands that created it and their audit chain, and bindings of native ODM forms to the platform. |
| `PlatformCommandLock`, `PlatformCommandEffect`, `PlatformCommandAudit`, `PlatformCommandOutbox`, `PlatformCommandPreparation`, `PlatformCommandPublicationStream` | The signed-command ledger: each command's idempotency key, effect, hash-chained audit entry and outbox position for publication. |
| `DomainAuditEvent`, `DomainAuditRootCheckpoint`, `DomainAuditExport` | Domain audit events (`streamId = osb:audit:<tenant>:<platform study>`), Merkle-style root checkpoints and export bundles; an APOC trigger forbids mutation or deletion. |
| `OsbSpecialistReviewEvidenceV1`, `OsbNativePackageV2` | The specialist's checkpoint-locked review statement (displayed statement, meaning, reason, reviewer subject, native lock evidence) and the released native package (study design and capture design read-backs with hashes, terminology pins, provenance pins, artifact references for the transformation checkpoint, platform manifest and pre-release approval). |

Identity fields in these records: the actor's issuer-qualified subject (`createdBy`, `specialistSubject`, `actor_subject`), reviewer ids on authorisations and decisions, and signer name snapshots inside electronic signatures.

### 15.7 Configuration that governs the fork's data flows

Backend (`clinical-mdr-api/common/config.py`): `OIDC_DELEGATED_CLAIMS_REQUIRED`, `OIDC_EXCHANGING_CLIENTS`, `OIDC_ALLOWED_PURPOSES`, `OIDC_ALLOWED_CAPABILITIES`, `OIDC_ALLOWED_ROLES`, `OIDC_MAX_ACCESS_TOKEN_TTL_SECONDS`, `OIDC_JWKS_CACHE_TTL_SECONDS`, `OIDC_REVOKED_KIDS`, `DEPLOYMENT_ENVIRONMENT`, `MAPPING_AUTHORITY_MODE` (`legacy` / `shadow` / `enforced`), `EDC_BASE_URL` (refuses the production EDC domain), `EDC_API_KEY`, `ALLOW_UNSAFE_LEGACY_EDC_SEND`, `OSB_PLATFORM_COMMANDS_PROTOTYPE_ENABLED`, `OSB_NATIVE_IDENTITY_ENDPOINT_ENABLED`, `OSB_NATIVE_IDENTITY_INVENTORY_ENABLED`, `OSB_REVIEW_WORKSPACE_IDENTITY_ENABLED`, `OSB_NATIVE_IDENTITY_SIGNING_URL`, `OSB_PLATFORM_COMMAND_SIGNING_URL`, `OSB_PLATFORM_SIGNING_TOKEN_FILE`, `OSB_CANDIDATE_REQUEST_SIGNATURE_VERIFICATION_URL`, `PLATFORM_DOMAIN_AUDIT_MODE`, `PLATFORM_REGION`. Importer: `ECRF_PG_DSN`, `ECRF_TENANT_ID`, `ECRF_STUDY_ID`, `OSB_TARGET_STUDY_UID`, `OSB_TARGET_STUDY_VERSION`, `OSB_CLINICAL_PROGRAMME`, `ALLOW_UNSAFE_LEGACY_EDC_HELPERS`, `COMMAND_CENTER_WORKFLOW_GRANT`, `COMMAND_CENTER_TOKEN_ENDPOINT`, `SEMANTIC_API_URL`, `SEMANTIC_ADAPTER_TOKEN`, `INCLUDE_DUMMY_STUDIES`.

Privacy hardening: request logs and traces no longer carry client IP, user agent, query strings, bodies, exception messages or Cypher text; the `ObservabilityPrivacyFilter` scrubs values matching subject / patient identifiers and secrets; the NeoDash lab report lost an embedded SharePoint user e-mail.


## 16. Library data (shared standards that studies select from)

The Library module (`/library/...` pages, backend `clinical-mdr-api/clinical_mdr_api/models/{concepts,controlled_terminologies,dictionaries,syntax_templates,syntax_pre_instances,syntax_instances,odms,biomedical_concepts,standard_data_models,projects,clinical_programmes,brands,data_suppliers}`) holds the reusable, versioned definitions. Every versioned library item carries the same system block: `uid`, `library_name` (*Sponsor*, *CDISC*, *Requested*, or a dictionary name), `status` (Draft / Final / Retired), `version` (`major.minor`), `start_date`, `end_date`, `change_description`, `author_username`, `possible_actions`. Study selections point at a specific version.

### 16.1 Concepts

| Entity | User-entered fields | References |
|---|---|---|
| Activity (`concepts/activities/activity.py`) | name, sentence-case name, definition, abbreviation, NCI concept id and name, synonyms, activity groupings (group + subgroup pairs), is data collected, is multiple selection allowed; for requests: request rationale, is request final; on rejection: contact person, reason for rejecting | Activity groups / subgroups; replaced-by activity; used-by studies |
| Activity group, Activity subgroup | name, sentence-case name, definition, abbreviation, NCI concept id / name | |
| Activity instance (biomedical concept) | name, sentence-case name, definition, abbreviation, NCI concept id / name, topic code, ADaM param code, is research lab, molecular weight, is required for activity, is default selected for activity, is data sharing, is legacy usage, is derived, legacy description, activity instance class, activity groupings (activity + group + subgroup), activity items | Activity instance class; activities |
| Activity item (embedded in an instance) | activity item class, CT codelist, CT terms, unit definitions, is ADaM-param specific, is activity-instance-id specific, text value | Codelists, terms, units |
| Activity instance class / Activity item class (`biomedical_concepts/`) | name, order, definition, is domain specific, level, parent class; item class: display name, data type, role, per-instance-class flags (mandatory, ADaM-param specific enabled, additional optional, default linked), variable-class mappings, valid codelists | CDISC dataset classes / variable classes |
| Compound, Compound alias | compound: name, definition, abbreviation, is sponsor compound, external id; alias: name, compound, is preferred synonym | |
| Active substance | analyte number, short number, long number, INN, external id, UNII term (with pharmacological class) | UNII / MED-RT dictionaries |
| Pharmaceutical product | external id, dosage forms, routes of administration, formulations → ingredients (active substance, formulation name, strength value + unit, half-life value + unit, lag times per SDTM domain) | CT, active substances, numeric values |
| Medicinal product | name, external id, compound, pharmaceutical products, dose values (value + unit), dose frequency, delivery device, dispenser | CT codelists Frequency, Delivery Device, Dispenser |
| Unit definition | name, definition, CT units, unit subsets (Study Time, Age Unit …), UCUM term, unit dimension, master / SI / US conventional / display / convertible flags, conversion factor to master, use molecular weight, use complex conversion, legacy code, comment, order, template parameter flag | CT, UCUM |
| Simple concepts | text values, visit names, numeric values, numeric values with unit, lag times, time points (numeric value + unit + time reference), study day / week / duration values | generated mostly by study visits; usable as template parameters |

### 16.2 Controlled terminology and dictionaries

| Entity | Fields |
|---|---|
| CT catalogue | name (SDTM CT, ADaM CT, Define-XML CT, DDF CT …), library |
| CT package | catalogue, name (e.g. *SDTM CT 2024-09-27*), label, description, href, registration status, source, extends-package (sponsor packages extend a CDISC package), import date, effective date, author |
| CT codelist | catalogue names, library, parent / child codelists, paired codes/names codelists; *Name* part (sponsor-versioned): sponsor preferred name, template parameter flag; *Attributes* part (CDISC-versioned): name, submission value, NCI preferred name, definition, extensible, is ordinal, codelist type |
| CT term | catalogue names, library, memberships (codelist, submission value, order, ordinal); *Name* part: sponsor preferred name and sentence-case name; *Attributes* part: concept id (C-code), NCI preferred name, definition |
| CT configuration (`CTConfig`) | the study-field configuration entries of Appendix A: field name, data type, null-value companion, configured codelist or term, grouping, API name, is dictionary term |
| Dictionary codelist / term | codelist: name, template parameter flag, library (SNOMED, MED-RT, UNII, UCUM); term: dictionary id (external code), name, sentence-case name, abbreviation, definition; UNII substances add the MED-RT pharmacological class |

### 16.3 Syntax templates, pre-instances and instances

Six template families (objective, endpoint, criteria, footnote, activity instruction, timeframe): sequence id, name (HTML with `[Parameter]` slots; literal brackets as `&#91;`/`&#93;` in the fork), plain name, guidance text (not for footnotes), library, versioning, derived parameter list, study count and instantiation counts. Indexing: indications (SNOMED) on all but timeframe; categories / sub-categories on objective, endpoint and criteria; *type* on criteria (Inclusion, Exclusion …) and footnote; activities, activity groups and subgroups on footnote and activity instruction; *is confirmatory testing* on objectives. Pre-instances are templates with parameter values pre-filled (same indexing); instances are the filled-in texts a study selects (template, parameter terms, rendered name and plain name, library, study count). The parameter vocabulary (Intervention, Indication, Activity, NumericValue, StudyEndpoint, …) is served by `/template-parameters`.

### 16.4 CRF / ODM library

| Entity | Fields |
|---|---|
| Collection (study event) | name, OID, effective date, retired date, description, display in tree, forms (with order, mandatory, locked, collection exception condition) |
| Form | name, OID, repeating, SDTM version, translated texts (description, question, display text, design notes, completion instructions per language), aliases (name + context), item groups (order, mandatory, exception condition, vendor attributes), vendor elements / attributes |
| Item group | name, OID, repeating, is reference data, SAS dataset name, origin, purpose, comment, translated texts, aliases, SDTM domains, items (order, mandatory, key sequence, method OID, imputation method OID, role, role codelist OID, exception condition, vendor attributes) |
| Item | name, OID, prompt, data type, length, significant digits, SAS field name, SDS variable name, origin, comment, translated texts, aliases, unit definitions (mandatory, order), codelist (allows multi-choice), terms (mandatory, order, display text), activity-instance bindings (activity instance, item class, order, primary, preset response value, value condition, value-dependent map), vendor attributes |
| Condition, Method | name, OID, formal expressions (context + expression), translated texts, aliases; method type |
| Vendor namespace / element / attribute | prefix, URL; element name and compatible types; attribute name, data type, value regex, compatible types |

### 16.5 CDISC data models and sponsor models

Import-loaded and read-only: data models (SDTM, ADaM, CDASH, SEND …) and implementation guides with version and status; dataset classes, datasets, dataset scenarios, variable classes and dataset variables (label, title, description, data type, role, core, described value domain, value list, question text, prompt, completion and implementation notes, mapping instructions, referenced codelists). Sponsor models extend an SDTMIG version with sponsor datasets (structure, purpose, keys, sort keys, flags, comments, enrich build order …) and sponsor dataset variables (type, length, display format, XML data type, core, origin, origin type/source, role, term, algorithm, qualifiers, mapping flags, value-level metadata columns, referenced codelists and terms).

### 16.6 Administration entities

| Entity | Fields |
|---|---|
| Clinical programme | name |
| Project | project number, name, description, clinical programme; brand |
| Brand | name |
| Library | name, is editable |
| Data supplier (`data_suppliers/data_supplier.py`, page *Library › Data Suppliers*) | name, description, order, supplier type (CT *Data Supplier Type*), default origin source, default origin type, API base URL, UI base URL, library; retire / reactivate |
| Complexity burdens | burden id, name, description, site burden, patient burden, median cost (USD); activity burdens per activity subgroup |
| Data completeness tags | name |
| Feature flags | section (admin / library / studies), feature, name, enabled, description |
| Notifications (admin announcements) | title, type (information / warning / error), description, start and end of display window, published |

### 16.7 Library and Admin pages and what their tables display

The *Library* sidebar (`studybuilder/src/stores/app.js`) exposes the pages below; column headers were resolved from the components' locale keys. Menu entries that are commented out are omitted (Process Overview, CDISC, Template Collections, ADaM, the CDASH/SDTM/ADaM standards lists).

| Menu › page | Table columns displayed |
|---|---|
| About Library | Library summary tiles and links. |
| Code Lists › Code Lists (dashboard) | Library, Concept ID, Sponsor preferred name, Template parameter, Name status, Modified, Code list name, Submission value, Extensible, Code list status; plus catalogue and package statistics. |
| Code Lists › CT Catalogues, CT Packages, Sponsor, Sponsor CT Packages | Codelist table: Library, Sponsor preferred name, Template parameter, Code list status, Name modified, Concept ID, Submission value, Code list name, NCI Preferred name, Paired with, Extensible, Attributes status, Attributes modified. Codelist detail: Code list name, Submission value, NCI preferred name, Extensible, Definition, Status, Version, Sponsor preferred name, Template parameter. Terms in a codelist: Order, Submission value, Added date, Removed date, Library, Sponsor name, Name status, Name date, Concept ID, NCI Preferred name, Definition, Attributes status, Attributes date. Forms: create codelist (catalogue, sponsor preferred name, attribute values), pair codelists (pair type), create term (sponsor preferred name, attributes, order and submission value per codelist), add parent / child term (relationship). |
| Code Lists › Terms | Library, Sponsor name, Name status, Name date, Concept ID, Code list names, Code list submission values, Submission values, NCI Preferred name, Definition, Attributes status, Attributes date; term detail: Sponsor preferred name, Sentence case name, Order, Status, Version, Concept ID, NCI preferred name, Definition. |
| Dictionaries › SNOMED | SNOMED ID, Preferred synonym, Preferred synonym (lower case), Abbreviation, Definition, Status, Version, Modified. |
| Dictionaries › MedDRA, LOINC | Name, Lower case name, Abbreviation, Status, Version, Modified (generic dictionary table). |
| Dictionaries › MED-RT | MED-RT ID, Class name, Class name (lower case), Abbreviation, Definition, Status, Version, Modified. |
| Dictionaries › UNII | UNII, Substance name, Substance name (lower case), Abbreviation, Definition, Pharmaceutical class (MED-RT), Status, Version, Modified. |
| Dictionaries › UCUM | UCUM code, UCUM description, Status, Version, Modified. |
| Concepts › Activities (tabs Activities, Activity Groups, Activity Subgroups, Activity Instances, Requested Activities, Activity Instance Classes, Activity Item Classes) | Activities: Library, Activity group, Activity subgroup, Activity name, Sentence case name, Synonyms, Definition, NCI Concept ID, NCI Concept Name, Abbreviation, Data collection, Legacy usage, Modified, Modified by, Status, Version. Instances: Activity, Activity instance class, Activity Instance, Research Lab, Molecular Weight, Topic code, ADaM parameter code, Required for activity, Default selected for activity, Data sharing, Groupings modified / modified by / status / version. Requested activities add Rationale for activity request and Study ID. Instance classes: Name, Definition, Domain specific, Library, Modified, Modified by, Version, Status. Item classes: Name, Definition, NCI Code, Modified, Modified by, Version, Status. Activity items: Data type, Name, Activity Item Class, Name submission value, Code submission value. Overviews add Start date, End date, Requested flag and the grouping hierarchy. |
| Concepts › Units | Library, Name, Master unit, Display unit, Unit subsets, UCUM unit, CT Unit terms, Convertible unit, SI unit, US conventional unit, Unit dimension, Legacy code, Use molecular weight, Use complex unit conversion, Conversion factor to master, Modified, Status, Version. |
| Concepts › Compounds (tabs Compounds, Compound Aliases, Active Substances, Pharmaceutical Products, Medicinal Products) | Compounds: Sponsor compound, Compound name, Definition, Modified, Version, Status. Aliases: Compound name, Compound alias name, Sentence case name, Is preferred synonym, Definition, Modified, Version, Status. Active substances: INN, Analyte number, Substance name, Substance ID, UNII, Pharmacological class (MED-RT), Modified, Version, Status. Pharmaceutical products: Pharmaceutical product, Dosage form, Route of administration, Modified, Version, Status. Medicinal products tree: Name, Compound, Dose, Frequency, Delivered in, Dispensed in, Version, Status. |
| Data Collection Standards › CRF Viewer, CRF Builder | Collections: OID, Name, Effective, Obsolete, Modified, Modified by, Version, Status. Forms and Item groups: OID, Name, Repeating, Modified, Modified by, Version, Status. Items: OID, Name, Type, Length, SDS Var Name, Modified, Modified by, Version, Status. Tree: Collections / Forms / ItemGroups / Items, Reference attributes, Definition attributes, Status, Version, Link. Forms have steps Form / Item Group / Item, Translations (Text Type, Language, Text), Vendor Extensions (Type, Name, Namespace, Data Type, Value), Alias (Context, Name), Activity Instance Links (Order, Primary, Data type, Name, Activity Item Class, submission values), Change Description; item form adds Code list (Concept ID, name, submission value, NCI preferred name, Multiple choice, term selection with Mandatory and Displayed name), Unit (Sponsor unit, UCUM unit, CT Unit terms, Mandatory). Viewer renders HTML, CRF with annotations and a downloadable Word version. |
| Syntax Templates › Objectives, Endpoints, Time Frames, Criteria, Activity Instructions, Footnotes (tabs Parent templates, Pre-instances, User defined) | Sequence number, Parent template, Modified, Status, Version plus the indexing columns: Indication or disorder; Objective category and Confirmatory testing; Endpoint category and sub-category; Guidance text, Criterion category and sub-category; Activity group, Activity subgroup, Activity. User-defined tab: Template, Modified, Modified by. Template form steps: Create template, Test template, Index template, Change description; pre-instance form: Set template parameter values, Index template, Change description. |
| Template Instantiations › Objectives, Endpoints, Time Frames, Criteria, Activity Instructions, Footnotes | Library, Template, Modified, Status, Version, Number of studies; the studies dialog lists View, Project ID, Project name, Brand name, Study number, Study ID, Study acronym, Status. |
| Overview Pages › Study Structures | Arms, Pre-treatment epochs, Treatment epochs, Post-treatment epochs, No-treatment epochs, No-treatment elements, Treatment elements, Cohorts?, Study ID(s). |
| Data Exchange Standards › CDASH | Ordinal, Name, Label, Definition, Question Text, Prompt, Data Type, Implementation Notes, Mapping Instructions, Mapping Targets, Code List. |
| Data Exchange Standards › SDTM | Ordinal, Name, Label, Data Type, Role, Qualifies variables, Describe value domain, Notes, Usage restrictions, Description, Variable C-Code, Examples, Core, Codelist, Described Value Domain, Implements, Value List. |
| Data Exchange Standards › Sponsor SDTM | Label, Order, Type, Length, XML data type, Role, IG comment (per sponsor model dataset). |
| Admin Definitions › Clinical Programmes | Name. |
| Admin Definitions › Projects | Name, Project ID, Clinical programme, Description. |
| Admin Definitions › Data Suppliers | Name, Description, Order, API base URL, UI base URL, Default Supplier Type, Origin Source, Origin Type, Author, Modified, Change description, Version, Status. |
| Admin Definitions › Template Study | Clinical Programme, Project ID, Project name, Study number, Study ID, Study acronym, Study title, Latest version, Status. |
| Admin › Global Preferences, Feature Flags, System Announcement, Data Completeness Tags, Complexity Burdens, ODM Vendor Extensions | The entities of section 16.6: preferences (rows per page, sidebar options), feature flags (section, feature, name, enabled, description), announcements (title, type, description, display window, published), tags (name), burdens (id, name, description, site burden, patient burden, median cost) and ODM vendor namespaces / elements / attributes (Name, Prefix, URL, Status, Modified, Modified by, Version; Type, Name, Compatible Types, Version, Status). |


## 17. User, audit and system data

### 17.1 Users

A `User` node is created or updated in Neo4j on every authenticated request (`clinical-mdr-api/common/auth/user.py`) with the claims of the access token:

| Field | Source | Upstream or fork |
|---|---|---|
| `user_id` (= token `sub` in the fork), `oid`, `azp`, `username`, `name`, `email`, `roles`, `created`, `updated` | OIDC / Command Center token | upstream |
| `tenant_id`, `study_ids`, `subject_type` (human / service), `issuer`, `human_subject`, `service_actor`, `purpose`, `capabilities`, `actor_chain_json` | Command Center token | fork |

The `author_id` on every version relationship and `StudyAction` node references this user; `author_username` shown in every *Modified by* column and audit trail is resolved from it. Users cannot edit their profile in the fork (identity is synchronised from Command Center). The top bar shows the token's `name` (or username / preferred username) and roles.

Per-user preferences (`/preferences`): language, rows per page, sidebar visible, sidebar auto-minimise, with global defaults. Frontend browser storage: the selected study (`selectedStudy`, refreshed with the full study object), UI state keys (section, breadcrumbs, narrow menu, table column choices, templates tab, open form, refresh flag), the login redirect target, and in the fork a listener on the `cc_subject` key and the `cc-session` broadcast channel that logs the user out when the Command Center session ends. No token is written to browser storage.

### 17.2 Audit trail and versioning records

* **Library items:** every version is a `HAS_VERSION` relationship with `version`, `status`, `start_date`, `end_date`, `change_description`, `author_id`; history endpoints (`/{uid}/versions`) list them with the `changes` (field names that changed).
* **Studies:** `StudyAction` nodes (`Create`, `Edit`, `Delete`, `UpdateSoASnapshot`) with `date`, `status`, `author_id` link the before/after state of every selection; the study-level audit trail and the per-field audit trail (section 2.7) are built from them. Release and lock snapshots keep the version number, timestamp, author and description.
* **Comments:** threads and replies with text, author id and display name, topic path (the page they belong to), status (active / resolved) and timestamps.
* **Fork platform records:** hash-chained command and domain audit events, signed decisions and authorisations (section 15.6).

### 17.3 System and operational data

`GET /system/information` exposes API version, database version and name, build id, commit id and branch. Health checks query the database. The fork strips client IP, user agent, query strings, request bodies and exception messages from logs and traces and records only status, route, error code and a rejection id.

## 18. Summary: what a study record consists of

| Area | Collected by users | Derived by the system | Main displays |
|---|---|---|---|
| Identity and versioning (§2) | project, study number, acronym, description, subpart acronym; release / lock reasons, protocol version, change description | study ID, subpart ID, version numbers, timestamps, authors | Study List, Manage Study, Study Summary |
| Registry identifiers (§3) | 13 identifiers, each with a reason-for-missing option | inheritance to subparts | Registry Identifiers, title page, disclosure, TS |
| Title (§4) | title, short title | | everywhere the study is named |
| Study type and attributes (§5) | 9 + 9 coded / boolean / text / duration fields with null flavours; 2 API-only observational codes (fork) | | Study Properties, summary, disclosure, TS, USDM, M11 |
| Population (§6) | 14 fields with null flavours (dictionary-coded conditions, sex, ages, indicators, expected subjects) | | Population page, disclosure, TS |
| Structure (§7) | arms, branch arms, cohorts, epochs, elements, visits (timing, windows, types, rules, untimed timing in the fork), design cells, disease milestones, design class, source variable | visit names / numbers / study days / weeks, epoch day ranges, statistics | Study Structure tabs, overviews, design figure, SDTM TA/TE/TV/TDM |
| Purpose (§8) | objectives (level, text), endpoints (level, sub-level, objective, timeframe, units, text), criteria (type, text, key flag) | endpoint counts, plain text | Study Purpose, Study Criteria, protocol tables, TI, ADaM MDENDPNT |
| Activities and SoA (§9) | study activities (SoA group, grouping, visibility), schedules (activity × visit), footnotes and their links, activity instructions, SoA preferences | footnote symbols, SoA renderings, snapshots | Study Activities tabs, Protocol SoA, Word add-in |
| Data specifications (§10) | activity instance per activity, reviewed / important flags, baseline visits, data supplier, origin type / source | state, topic code, ADaM param code, instance class attributes | Data Specifications, Operational SoA, ADaM MDFLOW |
| Interventions (§11) | study compounds (type of treatment, compound alias, medicinal product, other info, reason for missing), compound dosings (element, compound, dose value) | product attributes, dosing counts | Study Interventions, Overview, USDM interventions |
| Data suppliers (§12) | supplier per data type | supplier attributes | Data Suppliers, activity instances, consumer API |
| Standards, tags, scores (§13) | CT package per catalogue, description; data completeness tags | automatic package at lock; complexity score | Data Standard Versions, Study List |
| Fork additions (§15) | null adjudications, EDC dry-run sends, imports from the platform | export bundles, evidence, tenant bindings | EDC Export page, platform-control API |


## Appendix A. Configurable study metadata fields (study field configuration)

Source: `studybuilder-import/datafiles/configuration/study_fields_configuration.csv` (102 rows, identical to upstream v2.9.0). Each row becomes a `CTConfig` node in Neo4j and drives validation, persistence and audit of one study metadata field. Data types: `text`, `int`, `bool`, `time` (value + unit), `multiselect` (list of codes), `registry` (text identifier), `project` (link to a Project), `date`.

### Identification metadata (`id_metadata`)

| Field (API name) | Data type | Null-flavour companion | Codelist / term | Dictionary term? |
|---|---|---|---|---|
| `study_number` | text |  |  |  |
| `subpart_id` | text |  |  |  |
| `study_id_prefix` | text |  |  |  |
| `study_acronym` | text |  |  |  |
| `study_subpart_acronym` | text |  |  |  |
| `description` | text |  |  |  |
| `project_number` | project |  |  |  |

### Registry identifiers (`id_metadata.registry_identifiers`)

| Field (API name) | Data type | Null-flavour companion | Codelist / term | Dictionary term? |
|---|---|---|---|---|
| `ct_gov_id` | registry | `ct_gov_id_null_value_code` |  |  |
| `ct_gov_id_null_value_code` | text |  |  |  |
| `eudract_id` | registry | `eudract_id_null_value_code` |  |  |
| `eudract_id_null_value_code` | text |  |  |  |
| `universal_trial_number_utn` | registry | `universal_trial_number_utn_null_value_code` |  |  |
| `universal_trial_number_utn_null_value_code` | text |  |  |  |
| `japanese_trial_registry_id_japic` | registry | `japanese_trial_registry_id_japic_null_value_code` |  |  |
| `japanese_trial_registry_id_japic_null_value_code` | text |  |  |  |
| `investigational_new_drug_application_number_ind` | registry | `investigational_new_drug_application_number_ind_null_value_code` |  |  |
| `investigational_new_drug_application_number_ind_null_value_code` | text |  |  |  |
| `eu_trial_number` | registry | `eu_trial_number_null_value_code` |  |  |
| `eu_trial_number_null_value_code` | text |  |  |  |
| `civ_id_sin_number` | registry | `civ_id_sin_number_null_value_code` |  |  |
| `civ_id_sin_number_null_value_code` | text |  |  |  |
| `national_clinical_trial_number` | registry | `national_clinical_trial_number_null_value_code` |  |  |
| `national_clinical_trial_number_null_value_code` | text |  |  |  |
| `japanese_trial_registry_number_jrct` | registry | `japanese_trial_registry_number_jrct_null_value_code` |  |  |
| `japanese_trial_registry_number_jrct_null_value_code` | text |  |  |  |
| `national_medical_products_administration_nmpa_number` | registry | `national_medical_products_administration_nmpa_number_null_value_code` |  |  |
| `national_medical_products_administration_nmpa_number_null_value_code` | text |  |  |  |
| `eudamed_srn_number` | registry | `eudamed_srn_number_null_value_code` |  |  |
| `eudamed_srn_number_null_value_code` | text |  |  |  |
| `investigational_device_exemption_ide_number` | registry | `investigational_device_exemption_ide_number_null_value_code` |  |  |
| `investigational_device_exemption_ide_number_null_value_code` | text |  |  |  |
| `eu_pas_number` | registry | `eu_pas_number_null_value_code` |  |  |
| `eu_pas_number_null_value_code` | text |  |  |  |

### Version metadata (`ver_metadata`)

| Field (API name) | Data type | Null-flavour companion | Codelist / term | Dictionary term? |
|---|---|---|---|---|
| `version_timestamp` | date |  |  |  |
| `version_description` | text |  |  |  |
| `version_author` | text |  |  |  |
| `version_number` | text |  |  |  |

### Study description (title page) (`study_description`)

| Field (API name) | Data type | Null-flavour companion | Codelist / term | Dictionary term? |
|---|---|---|---|---|
| `study_title` | text |  |  |  |
| `study_short_title` | text |  |  |  |

### High-level study design (Study Type tab) (`high_level_study_design`)

| Field (API name) | Data type | Null-flavour companion | Codelist / term | Dictionary term? |
|---|---|---|---|---|
| `study_type_code` | text | `study_type_null_value_code` | C99077 |  |
| `study_type_null_value_code` | text |  |  |  |
| `trial_phase_code` | text | `trial_phase_null_value_code` | C66737 |  |
| `trial_phase_null_value_code` | text |  |  |  |
| `development_stage_code` | text |  |  |  |
| `trial_type_codes` | multiselect | `trial_type_null_value_code` | C66739 |  |
| `trial_type_null_value_code` | text |  |  |  |
| `is_extension_trial` | bool | `is_extension_trial_null_value_code` | C139274_EXTTIND |  |
| `is_extension_trial_null_value_code` | text |  |  |  |
| `study_stop_rules` | text | `study_stop_rules_null_value_code` |  |  |
| `study_stop_rules_null_value_code` | text |  |  |  |
| `is_adaptive_design` | bool | `is_adaptive_design_null_value_code` | C146995_ADAPT |  |
| `is_adaptive_design_null_value_code` | text |  |  |  |
| `post_auth_indicator` | bool | `post_auth_indicator_null_value_code` | C139275_PASSIND |  |
| `post_auth_indicator_null_value_code` | text |  |  |  |
| `confirmed_response_minimum_duration` | time | `confirmed_response_minimum_duration_null_value_code` |  |  |
| `confirmed_response_minimum_duration_null_value_code` | text |  |  |  |

### Study intervention (Study Attributes tab) (`study_intervention`)

| Field (API name) | Data type | Null-flavour companion | Codelist / term | Dictionary term? |
|---|---|---|---|---|
| `trial_intent_types_codes` | multiselect | `trial_intent_type_null_value_code` | C66736 |  |
| `trial_intent_type_null_value_code` | text |  |  |  |
| `intervention_type_code` | text | `intervention_type_null_value_code` | C99078 |  |
| `intervention_type_null_value_code` | text |  |  |  |
| `add_on_to_existing_treatments` | bool | `add_on_to_existing_treatments_null_value_code` | C49703_ADDON |  |
| `add_on_to_existing_treatments_null_value_code` | text |  |  |  |
| `control_type_code` | text | `control_type_null_value_code` | C66785 |  |
| `control_type_null_value_code` | text |  |  |  |
| `intervention_model_code` | text | `intervention_model_null_value_code` | C99076 |  |
| `intervention_model_null_value_code` | text |  |  |  |
| `is_trial_randomised` | bool | `is_trial_randomised_null_value_code` | C25196_RANDOM |  |
| `is_trial_randomised_null_value_code` | text |  |  |  |
| `stratification_factor` | text | `stratification_factor_null_value_code` |  |  |
| `stratification_factor_null_value_code` | text |  |  |  |
| `trial_blinding_schema_code` | text | `trial_blinding_schema_null_value_code` | C66735 |  |
| `trial_blinding_schema_null_value_code` | text |  |  |  |
| `planned_study_length` | time | `planned_study_length_null_value_code` |  |  |
| `planned_study_length_null_value_code` | text |  |  |  |

### Study population (`study_population`)

| Field (API name) | Data type | Null-flavour companion | Codelist / term | Dictionary term? |
|---|---|---|---|---|
| `therapeutic_area_codes` | multiselect | `therapeutic_area_null_value_code` |  | yes |
| `therapeutic_area_null_value_code` | text |  |  |  |
| `disease_condition_or_indication_codes` | multiselect | `disease_condition_or_indication_null_value_code` |  | yes |
| `disease_condition_or_indication_null_value_code` | text |  |  |  |
| `diagnosis_group_codes` | multiselect | `diagnosis_group_null_value_code` |  | yes |
| `diagnosis_group_null_value_code` | text |  |  |  |
| `sex_of_participants_code` | text | `sex_of_participants_null_value_code` | C66732 |  |
| `sex_of_participants_null_value_code` | text |  |  |  |
| `rare_disease_indicator` | bool | `rare_disease_indicator_null_value_code` | C126070_RDIND |  |
| `rare_disease_indicator_null_value_code` | text |  |  |  |
| `healthy_subject_indicator` | bool | `healthy_subject_indicator_null_value_code` | C98737_HLTSUBJI |  |
| `healthy_subject_indicator_null_value_code` | text |  |  |  |
| `planned_maximum_age_of_subjects` | time | `planned_maximum_age_of_subjects_null_value_code` |  |  |
| `planned_maximum_age_of_subjects_null_value_code` | text |  |  |  |
| `planned_minimum_age_of_subjects` | time | `planned_minimum_age_of_subjects_null_value_code` |  |  |
| `planned_minimum_age_of_subjects_null_value_code` | text |  |  |  |
| `stable_disease_minimum_duration` | time | `stable_disease_minimum_duration_null_value_code` |  |  |
| `stable_disease_minimum_duration_null_value_code` | text |  |  |  |
| `pediatric_study_indicator` | bool | `pediatric_study_indicator_null_value_code` | C123632_PDSTIND |  |
| `pediatric_study_indicator_null_value_code` | text |  |  |  |
| `pediatric_postmarket_study_indicator` | bool | `pediatric_postmarket_study_indicator_null_value_code` | C123631_PDPSTIND |  |
| `pediatric_postmarket_study_indicator_null_value_code` | text |  |  |  |
| `pediatric_investigation_plan_indicator` | bool | `pediatric_investigation_plan_indicator_null_value_code` | C126069_PIPIND |  |
| `pediatric_investigation_plan_indicator_null_value_code` | text |  |  |  |
| `relapse_criteria` | text | `relapse_criteria_null_value_code` |  |  |
| `relapse_criteria_null_value_code` | text |  |  |  |
| `number_of_expected_subjects` | int | `number_of_expected_subjects_null_value_code` |  |  |
| `number_of_expected_subjects_null_value_code` | text |  |  |  |

### Fork-added API-only fields (not in the CSV)

Added programmatically by `FieldConfiguration.default_field_config()` in `clinical-mdr-api/clinical_mdr_api/domains/study_definition_aggregates/study_configuration.py`:

| Field (API name) | Data type | Codelist | Grouping |
|---|---|---|---|
| `observational_model_code` | text | C127259 (CDISC Observational Study Model) | high_level_study_design |
| `observational_time_perspective_code` | text | C127261 (CDISC Time Perspective) | high_level_study_design |


## Appendix B. Where to look in the code

| Topic | Primary source files |
|---|---|
| Study metadata models (identification, registry identifiers, design, population, intervention, description, version) | `clinical-mdr-api/clinical_mdr_api/models/study_selections/study.py`; domain rules `domains/study_definition_aggregates/study_metadata.py`, `registry_identifiers.py`, `root.py`; field configuration `domains/study_definition_aggregates/study_configuration.py` and `studybuilder-import/datafiles/configuration/study_fields_configuration.csv` |
| Study structure, purpose, activities, interventions, suppliers | `models/study_selections/study_selection.py`, `study_epoch.py`, `study_visit.py`, `visit_timing.py` *(fork)*, `study_disease_milestone.py`, `study_soa_footnote.py`, `study_standard_version.py`; services under `services/studies/`; routers under `routers/studies/` |
| Neo4j node definitions | `domain_repositories/models/study.py`, `study_field.py`, `study_selections.py`, `study_visit.py`, `study_epoch.py`, `study_disease_milestone.py`, `study_audit_trail.py`, `generic.py`; schema constraints `neo4j-mdr-db/db_schema.py`; fork migrations `db-schema-migration/migrations/migration_024.py` to `migration_032.py` |
| Frontend study pages | `studybuilder/src/views/studies/*.vue`, `studybuilder/src/components/studies/**/*.vue`, menu `studybuilder/src/stores/app.js`, routes `studybuilder/src/router/index.js`, labels and help texts `studybuilder/src/locales/en/app.json` |
| Derived views | disclosure `models/study_selections/study_pharma_cm.py`; listings `models/listings/*.py`, `listings/query_service.py`, `routers/listings/*.py`; USDM `services/ddf/*.py`, `routers/ddf/study_definitions.py`; M11 template `clinical-mdr-api/m11-templates/`; CTR XML `clinical-mdr-api/ctrxml/`; ODM `services/odms/xml_exporter.py`, `templates/odm/`; consumer API `clinical-mdr-api/consumer_api/` |
| Library models | `models/concepts/**`, `models/controlled_terminologies/**`, `models/dictionaries/**`, `models/syntax_templates/**`, `models/syntax_pre_instances/**`, `models/syntax_instances/**`, `models/odms/**`, `models/biomedical_concepts/**`, `models/standard_data_models/**`, `models/projects/`, `models/clinical_programmes/`, `models/brands/`, `models/data_suppliers/`, `models/complexity_score.py`, `models/data_completeness_tag.py`, `models/notification.py`, `models/feature_flag.py`, `models/preferences.py`, `models/user.py` |
| Fork: identity and study visibility | `common/auth/models.py`, `common/auth/user.py`, `common/auth/dependencies.py`, `services/studies/study_visibility.py`, `routers/studies/study_access.py`; frontend `studybuilder/src/plugins/auth.js`, `stores/auth.js`, `composables/accessGuard.js` |
| Fork: EDC export | `routers/integrations/edc_export.py`, `services/integrations/edc_export.py`, `edc_study_exchange.py`, `edc_field_types.py`, `edc_native_*.py`, `edc_source_snapshot.py`; frontend `views/studies/EdcExport.vue`, `api/edcExport.js`, `utils/edcStudyReview.js` |
| Fork: importers | `studybuilder-import/importers/run_import_360i.py`, `mappings/payload_to_osb.py`, `run_import_osb_proposal_v2.py`, `mappings/proposal_v2_*.py`, `utils/osb_proposal_db.py`, `utils/ecrf_platform_db.py`, `publish_edc_drop.py`, `verify_edc_roundtrip.py` |
| Fork: governed platform-control records | `services/integrations/proposal_review.py`, `mapping_context.py`, `candidate_set.py`, `mapping_decision_v1.py`, `native_identity.py`, `native_package_v2.py`, `platform_command.py`, `governed_item_association.py`; contracts `generated/platform_contracts/*.py`, `schemas/platform/*.json`; routers `routers/integrations/*.py` |
| Fork: configuration | `clinical-mdr-api/common/config.py`, `clinical-mdr-api/.env.example`, root `compose.yaml`, `compose.production.yaml`, `.env.production.example`, `studybuilder-import/.env.import` |
| User documentation of the study pages | `documentation-portal/docs/guides/userguide/studies/*.md` |


## Appendix C. Sponsor codelist values used by study fields

Source: the sponsor library seed files under `studybuilder-import/datafiles/sponsor_library/` (`sponsor_codelist_definitions.csv` and the per-codelist CSVs). These are the values loaded into a fresh database; library administrators can add, retire or rename terms afterwards, so the live instance may differ. Study fields that use CDISC codelists (Study Type C99077, Trial Phase C66737, Trial Type C66739, Trial Intent Type C66736, Sex C66732, Intervention Type C99078, Control Type C66785, Intervention Model C99076, Trial Blinding Schema C66735, Visit Contact Mode, Origin Type, Origin Source, Observational Study Model C127259, Time Perspective C127261) take their values from the imported CDISC CT packages and are not listed here, except where the sponsor extends them.

| Codelist (submission value) | Used by | Seeded values (display order) |
|---|---|---|
| Objective Level (OBJTLEVL) | study objectives | Primary Objective, Secondary Objective, Exploratory Objective |
| Objective Category (OBJTCAT) | objective templates | Efficacy, Safety, Pharmacokinetics, Pharmacodynamics, Bioequivalence |
| Endpoint Level (ENDPLEVL) | study endpoints | Primary Endpoint, Secondary Endpoint, Exploratory Endpoint, Additional Endpoint |
| Endpoint Sub Level (ENDPSBLV) | study endpoints | Primary, Co-primary, Multiple, Confirmatory secondary, Supportive secondary |
| Endpoint Category (ENDPCAT) | endpoint templates | Efficacy, Safety, Pharmacokinetics, Pharmacodynamics, Bioequivalence |
| Endpoint Sub Category (ENDPSCAT) | endpoint templates | Continuous (normal), Continuous (log-normal), Continuous (relative change), Binary (responder), Count, Ordinal, Time-to-event, PROs/COAs/questionnaires |
| Criteria Type (CRITRTP) | criteria templates and study criteria tabs | Inclusion Criteria, Exclusion Criteria, Run-in Criteria, Randomisation Criteria, Dosing Criteria, Withdrawal Criteria |
| Criteria Category (CRITCAT) | criteria templates | Demography, Diseases, Lifestyle Factors, Medications, Body Measurements, Vital Signs, Laboratory Parameters, Physical Findings, ECG Findings, GCP Related |
| Criteria Sub Category (CRITSCAT) | criteria templates | TBD (placeholder) |
| Footnote Type (FTNTTP) | footnote templates | SoA Footnote |
| Arm Type (ARMTTP) | study arms | Investigational Arm, Comparator Arm, Placebo Arm, Observational Arm |
| Element Type (ELEMTP) | study elements | Treatment, No Treatment |
| Element Sub Type (ELEMSTP) | study elements | Screening, Run-in (No Treatment); Treatment (Treatment); Wash-out, Follow-up (No Treatment) |
| Epoch Type (EPOCHTP) | study epochs | Pre Treatment, Treatment, No Treatment, Post Treatment |
| Epoch Sub Type (EPOCHSTP) | study epochs | Screening, Run-in (Pre Treatment); Titration, Dose Escalation, Treatment, Maintenance, Extension, Observation, Intervention (Treatment); Wash-out, Basic (No Treatment); Elimination, Follow-up (Post Treatment) |
| Epoch (EPOCH) | epoch names, auto-numbered per subtype | Screening, Run-in, Baseline, Treatment 1 to 5, Observation, Wash-out, Follow-up 1 to 2, Intervention (13 seeded names) |
| Visit Type (TIMELB, "VisitType") | study visits | End of trial, End of treatment, Follow-up, Post treatment activity, Pre-treatment, Pre-screening, Randomisation, Randomisation 2, Screening, Start of run-in, Start of treatment, Start of washout, Surgery, Treatment, Washout, Informed consent, No treatment, Non-visit, Unscheduled, Information, Early discontinuation, Visit with no fixed study day |
| Time Point Reference (TIMEREF) | visit timing reference | Screening, Start of run-in, Start of treatment, Start of washout, Surgery, Treatment, Informed consent, Global anchor visit, Anchor visit in visit group, Randomisation, Randomisation 2, End of study, End of treatment, End of trial |
| Visit Contact Mode (VISCNTMD) | study visits | On Site Visit, Phone Contact, Virtual Visit |
| Epoch Allocation (EPCHALLC) | study visits | Current Visit, Previous Visit, Date Current Visit, Date Previous Visit, Actual Treatment |
| Repeating Visit Frequency (REPEATING_VISIT_FREQUENCY) | repeating visits | Daily, Weekly, Monthly |
| Visit Sub Label (VISSUBLB) | sub-visits in a visit group | Day 1 to Day 15 |
| Flowchart Group (FLWCRTGRP) | study activities (SoA group) | INFORMED CONSENT, ELIGIBILITY AND OTHER CRITERIA, SUBJECT RELATED INFORMATION, EFFICACY, SAFETY, IMMUNOGENICITY, PHARMACOKINETICS, PHARMACODYNAMICS, GENETICS, BIOMARKERS, HUMAN BIOSAMPLES, HEALTH ECONOMICS, TRIAL MATERIAL, REMINDERS, HIDDEN |
| Type of Treatment (TPOFTRT) | study compounds | Comparative Treatment, Current Treatment, Investigational Product, Placebo Treatment |
| Compound Dispensed In (COMPDISP) | medicinal products | Blister, Cartridge, Pre-filled pen, Vial |
| Delivery Device (DLVRDVC) | medicinal products | Insulin pump, Pre-filled pen, Syringe |
| Frequency (FREQ, sponsor extension) | medicinal product dose frequency | Other (CDISC Frequency terms supply the rest) |
| Data Supplier Type (DATA_SUPPLIER_TYPE) | library and study data suppliers | EDC System, Lab Data Exchange Files, eCOA Data Exchange Files |
| Data Collection Mode (DATCOLMD) | activity instances | eCOA, Paper |
| Disease Milestone Type (MIDSTYPE) | disease milestones | Diagnosis of diabetes (seeded example) |
| Reason for Lock/Release (RSNFL) | study release and lock | PORT Approval, PORT Submission, Protocol QC, Final Protocol, Study Specification Updates, Other |
| Reason for Unlock (RSNFUL) | study unlock | Study Specification Updates, Protocol Amendment Updates, Other |
| Null Flavor (NULLFLVR) | every *Reason for missing* | Asked but unknown, Derived, Invalid, Masked, Not applicable, Not asked, Temporarily unavailable, No information, Negative infinity, Other, Positive infinity, Sufficient quantity, Trace, Unencoded, Unknown |
| Development Stage (DEVELOPMENT_STAGE) | Study Type tab | Pilot Stage, Pivotal Stage, Post-market Stage |
| Registry Identifier (REGISTID) | labels of the registry identifier fields | ClinicalTrials.gov ID, EUDRACT ID, Universal Trial Number (UTN), Japanese Trial Registry ID (JAPIC), Investigational New Drug Application (IND) Number, WHO ID, EU Trial Number, CIV-ID/SIN Number, National Clinical Trial Number, Japanese Trial Registry Number (jRCT), National Medical Products Administration (NMPA) Number, EUDAMED SRN Number, Investigational Device Exemption (IDE) Number |
| Trial Type sponsor extension (TTYPE) | Study Type tab, added to the CDISC list | Multi-centre, Multi-national, Treat-to-target, Multi-regional, Single-centre |
| Trial Blinding Schema sponsor extension | Study Attributes tab | Double Dummy |
| Control Type sponsor extension | Study Attributes tab | Active and Placebo |
| Therapeutic area (THERAREA) | population (dictionary-backed) | SNOMED CT terms loaded by the dictionary import |
| Unit Subset (UNITSUBS), Unit Dimension (UNITDIM) | unit definitions and duration fields | Study Time, Age Unit, Time Unit, Dose Unit, Strength Unit and the CDISC unit dimensions |
| Other sponsor codelists defined in the same file | Library only | Operator, Language, Data type (ODM), Study Milestone, Confirmatory / Non-confirmatory Purpose, Role, Finding / Event / Intervention (sub)category definitions, categories for clinical events, healthcare encounters, medical history, procedure agents, ECG, ophthalmic and reproductive findings, PK and adjudication test names and codes, treatment names (ECTRT, EXTRT), hepatic-event response list |
