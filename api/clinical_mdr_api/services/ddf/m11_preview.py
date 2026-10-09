"""Read-only M11 presentation of one native mapping result, without authoring facts."""

from typing import Any

from clinical_mdr_api.models.study_selections.study_definition_document import (
    AuthoredProtocolDocuments,
)
from clinical_mdr_api.models.utils import sanitize_html
from clinical_mdr_api.services.ddf.usdm_mapping_context import (
    USDMMappingAuthorityRequired,
)
from clinical_mdr_api.services.studies.study_definition_document import (
    assess_authored_documents,
)

MISSING = "Not available in the selected source"
NATIVE_EXTENSION = "https://openstudybuilder.org/usdm/extensions/native/"


def _code(value: Any) -> str:
    row = _object(value)
    if "standardCode" in row:
        row = _object(row["standardCode"])
    label = next(
        (
            row.get(key)
            for key in ("decode", "sponsor_preferred_name", "name", "term_name")
            if row.get(key)
        ),
        None,
    )
    identifier = next(
        (row.get(key) for key in ("code", "term_uid", "uid") if row.get(key)), None
    )
    if not label and not identifier:
        return MISSING
    version = row.get("codeSystemVersion") or row.get("version")
    return f"{label or MISSING} [{identifier or MISSING}{' @ ' + str(version) if version else ''}]"


def _value(value: Any) -> str:
    if value is None:
        return MISSING
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return (
            "; ".join(_value(row) for row in value) if value else "No entries recorded"
        )
    if isinstance(value, dict):
        if "numerator" in value:
            numerator = _value(value.get("numerator"))
            return (
                f"({numerator}) / ({_value(value['denominator'])})"
                if value.get("denominator") is not None
                else numerator
            )
        if "duration_value" in value:
            return f"{_value(value.get('duration_value'))} {_code(value.get('duration_unit_code'))}"
        if "value" in value:
            return f"{_value(value.get('value'))} {_code(value.get('unit'))}"
        return _code(value)
    return str(value) if str(value).strip() else "Empty source text"


def _facts(
    source: dict[str, Any], fields: tuple[tuple[str, str], ...]
) -> list[dict[str, str]]:
    result = []
    for field, label in fields:
        null_field = (
            field.removesuffix("_codes").removesuffix("_code") + "_null_value_code"
        )
        result.append(
            {
                "label": label,
                "field": field,
                "value": _value(source.get(field)),
                "null_reason": (
                    _code(source[null_field])
                    if source.get(null_field) is not None
                    else ""
                ),
            }
        )
    return result


def _extension_values(entity: dict[str, Any], url: str, field: str) -> list[Any]:
    matches = []
    pending = _objects(entity.get("extensionAttributes"))
    while pending:
        row = pending.pop()
        if row.get("url") == url and field in row:
            matches.append(row[field])
        pending.extend(_objects(row.get("extensionAttributes")))
    return matches


def _extension_value(entity: dict[str, Any], url: str, field: str) -> Any:
    matches = _extension_values(entity, url, field)
    return matches[0] if len(matches) == 1 else None


def _native_uid(entity: dict[str, Any], kind: str, field: str) -> Any:
    return _extension_value(entity, f"{NATIVE_EXTENSION}{kind}/{field}", "valueString")


def _index(
    rows: list[dict[str, Any]], key: str, issues: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Only uniquely identified source entities can resolve a relationship."""
    result = {}
    duplicates = set()
    for row in rows:
        uid = row.get(key)
        if not isinstance(uid, str) or not uid:
            continue
        if uid in result:
            duplicates.add(uid)
        result[uid] = row
    unique = {uid: row for uid, row in result.items() if uid not in duplicates}
    if len(unique) != len(rows):
        issues.append(
            {
                "code": "M11_SOURCE_IDENTITY_UNRESOLVED",
                "message": f"The selected {key} collection contains missing or duplicate identities. Ambiguous records cannot authorize relationships or counts.",
            }
        )
    return unique


def _lookup(rows: dict[str, dict[str, Any]], identifier: Any) -> dict[str, Any] | None:
    return rows.get(identifier) if isinstance(identifier, str) else None


def _product_for_preview(product: dict[str, Any] | None) -> dict[str, Any] | None:
    if product is None:
        return None
    return {
        **product,
        "dose_form": _code(product.get("administrableDoseForm")),
        "ingredients": [
            {
                "name": _text(_object(row.get("substance")).get("name")),
                "id": _text(_object(row.get("substance")).get("id")),
                "strengths": [
                    _value(strength)
                    for strength in _objects(
                        _object(row.get("substance")).get("strengths")
                    )
                ],
            }
            for row in _objects(product.get("ingredients"))
        ],
    }


def _selected_product_matches(
    product: dict[str, Any], source: dict[str, Any], study_uid: str, version: str | None
) -> bool:
    """Verify, rather than infer, the mapper's native product-selection authority."""
    selected = set()
    for binding in _objects(source.get("native_library_bindings")):
        if (
            binding.get("kind") != "pharmaceuticalProduct"
            or binding.get("mode") != "selected-value"
        ):
            continue
        if (
            binding.get("studyUid") != study_uid
            or binding.get("studyValueVersion") != version
            or binding.get("studyCompoundUid") != source.get("study_compound_uid")
            or not binding.get("uid")
            or not binding.get("version")
        ):
            return False
        selected.add((binding["uid"], binding["version"]))
    identity = (
        _native_uid(product, "pharmaceuticalProduct", "uid"),
        _native_uid(product, "pharmaceuticalProduct", "version"),
    )
    definitions = [
        row
        for row in _objects(source.get("pharmaceutical_products"))
        if (row.get("uid"), row.get("version")) == identity
    ]
    return len(definitions) == 1 and selected == {identity}


def _cell_source_matches(
    cell: dict[str, Any],
    arm: dict[str, Any],
    epoch: dict[str, Any],
    element_uid: str,
    native_cells: dict[str, dict[str, Any]],
    native_arms: dict[str, dict[str, Any]],
    native_branches: dict[str, dict[str, Any]],
) -> bool:
    """Cross-check canonical joins against their exact retained native cell."""
    arm_uid = _native_uid(arm, "studyArm", "arm_uid")
    branch_uid = _native_uid(arm, "studyBranchArm", "branch_arm_uid")
    epoch_uid = _native_uid(epoch, "studyEpoch", "uid")
    if branch_uid:
        branch = native_branches.get(branch_uid)
        parent_uid = _object(_object(branch).get("arm_root")).get("arm_uid")
        if arm_uid or branch is None or parent_uid not in native_arms:
            return False
        arm_uid = parent_uid
    elif arm_uid not in native_arms:
        return False
    for uid in _extension_values(
        cell, f"{NATIVE_EXTENSION}studyDesignCell/design_cell_uid", "valueString"
    ):
        source = native_cells.get(uid)
        if source and (
            source.get("study_arm_uid") == arm_uid
            and source.get("study_branch_arm_uid") == branch_uid
            and source.get("study_epoch_uid") == epoch_uid
            and source.get("study_element_uid") == element_uid
        ):
            return True
    return False


def _selected_records(
    report: dict[str, Any], kind: str, study_uid: str, version: str | None
) -> list[dict[str, Any]]:
    rows = []
    for entry in _objects(report.get("nativeRecords")):
        if entry.get("kind") != kind:
            continue
        scope, record = _object(entry.get("scope")), _object(entry.get("record"))
        if (
            scope.get("studyUid") != study_uid
            or scope.get("studyValueVersion") != version
            or record.get("study_uid") != study_uid
            or (
                version is not None
                and record.get("study_version") not in (None, version)
            )
        ):
            raise USDMMappingAuthorityRequired(
                f"M11_NATIVE_SELECTION_SCOPE_MISMATCH: {kind}/{entry.get('uid')}"
            )
        rows.append(record)
    return rows


class _PreviewSources:
    """Read selected native records and report presentation ambiguities once."""

    def __init__(self, report, study_uid, version, issues):
        self.report, self.study_uid, self.version, self.issues = (
            report,
            study_uid,
            version,
            issues,
        )

    def missing(self, code: str, message: str) -> None:
        issue = {"code": code, "message": message}
        if issue not in self.issues:
            self.issues.append(issue)

    def records(self, kind: str) -> list[dict[str, Any]]:
        return _selected_records(self.report, kind, self.study_uid, self.version)

    def index(
        self, rows: list[dict[str, Any]], key: str = "id"
    ) -> dict[str, dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        result = _index(rows, key, issues)
        for issue in issues:
            self.missing(issue["code"], issue["message"])
        return result


def _eligibility_for_preview(
    sources: _PreviewSources, version: dict[str, Any], design: dict[str, Any]
) -> dict[str, Any]:
    selected_criteria = sources.index(
        sources.records("studyCriteria"), "study_criteria_uid"
    )
    items = sources.index(_objects(version.get("eligibilityCriterionItems")))
    eligibility: dict[str, list[dict[str, Any]]] = {
        "inclusion": [],
        "exclusion": [],
        "unclassified": [],
    }
    # CDISC C66797 Category of Inclusion/Exclusion. Sponsor display labels cannot
    # turn an unknown or opposite category into one of these categories.
    categories = {"C25532": "inclusion", "C25370": "exclusion"}
    rendered_criteria = set()
    for criterion in _objects(design.get("eligibilityCriteria")):
        source_uid = criterion.get("identifier")
        source = _lookup(selected_criteria, source_uid)
        item = _lookup(items, criterion.get("criterionItemId"))
        category_code = _object(criterion.get("category")).get("code")
        category = (
            categories.get(category_code, "unclassified")
            if isinstance(category_code, str)
            else "unclassified"
        )
        valid = source is not None and item is not None
        if not valid:
            sources.missing(
                "M11_CRITERION_SOURCE_UNRESOLVED",
                f"Criterion {source_uid} has no unique selected native source or criterion item.",
            )
        if category == "unclassified":
            sources.missing(
                "M11_CRITERION_CATEGORY_UNRESOLVED",
                f"Criterion {source_uid} has no supported inclusion/exclusion category code; it remains unclassified.",
            )
        source = source or {}
        definition = _object(source.get("criteria"))
        eligibility[category].append(
            {
                "id": criterion.get("id"),
                "uid": source_uid,
                "order": source.get("order"),
                "text": _text(item.get("text")) if valid and definition else MISSING,
                "source_version": _text(definition.get("version")),
                "definition_uid": _text(definition.get("uid")),
                "key": _value(source.get("key_criteria")),
                "category": _code(criterion.get("category")),
                "template_only": bool(source.get("template")) and not definition,
            }
        )
        rendered_criteria.add(source_uid)
    for uid, source in selected_criteria.items():
        if uid not in rendered_criteria:
            sources.missing(
                "M11_CRITERION_SOURCE_UNRESOLVED",
                f"Selected native criterion {uid} has no mapped criterion in the selected design.",
            )
            eligibility["unclassified"].append(
                {
                    "uid": uid,
                    "order": source.get("order"),
                    "text": MISSING,
                    "source_version": _text(
                        _object(source.get("criteria")).get("version")
                    ),
                    "key": _value(source.get("key_criteria")),
                    "category": _code(source.get("criteria_type")),
                }
            )

    return eligibility


def _interventions_for_preview(
    sources: _PreviewSources,
    version: dict[str, Any],
    design: dict[str, Any],
    native_arms: dict[str, Any],
    native_branches: dict[str, Any],
) -> dict[str, Any]:
    native_cells = sources.index(sources.records("studyDesignCell"), "design_cell_uid")
    arms = sources.index(_objects(design.get("arms")))
    epochs = sources.index(_objects(design.get("epochs")))
    elements = sources.index(_objects(design.get("elements")))
    element_identities = [
        {**row, "native_uid": _native_uid(row, "studyElement", "element_uid")}
        for row in elements.values()
    ]
    native_elements = sources.index(element_identities, "native_uid")
    compounds = sources.index(sources.records("studyCompound"), "study_compound_uid")
    dosings = sources.index(
        sources.records("studyCompoundDosing"), "study_compound_dosing_uid"
    )
    products = sources.index(_objects(version.get("administrableProducts")))
    mapped_interventions = sources.index(_objects(version.get("studyInterventions")))
    selected_ids = design.get("studyInterventionIds") or []
    intervention_rows, unresolved_dosings = [], []
    rendered_dosings = set()
    extension_base = "https://openstudybuilder.org/usdm/extensions"
    for intervention in _objects(version.get("studyInterventions")):
        identifier = intervention.get("id")
        source_uid = _extension_value(
            intervention, extension_base + "/study-compound-uid", "valueId"
        )
        source = compounds.get(source_uid)
        selected = (
            isinstance(selected_ids, list)
            and identifier in selected_ids
            and _lookup(mapped_interventions, identifier) is intervention
        )
        # Index construction keeps the original dictionary values. Duplicate IDs
        # or absent selected references never authorize an arm association.
        if not selected or source is None:
            sources.missing(
                "M11_INTERVENTION_SOURCE_UNRESOLVED",
                f"Intervention {identifier} lacks one exact selected native compound and design reference.",
            )
        administrations = []
        for administration in _objects(intervention.get("administrations")):
            dosing_uid = _extension_value(
                administration, extension_base + "/study-compound-dosing-uid", "valueId"
            )
            dosing = dosings.get(dosing_uid)
            native_element_uid = _extension_value(
                administration, extension_base + "/study-element-uid", "valueId"
            )
            element = native_elements.get(native_element_uid)
            relation_valid = (
                selected
                and source is not None
                and dosing is not None
                and element is not None
                and _object(dosing.get("study_compound")).get("study_compound_uid")
                == source_uid
                and _object(dosing.get("study_element")).get("element_uid")
                == native_element_uid
                and identifier in (element.get("studyInterventionIds") or [])
            )
            scopes = []
            if relation_valid and element is not None:
                for cell in _objects(design.get("studyCells")):
                    if element["id"] not in (cell.get("elementIds") or []):
                        continue
                    arm, epoch = _lookup(arms, cell.get("armId")), _lookup(
                        epochs, cell.get("epochId")
                    )
                    if (
                        arm is None
                        or epoch is None
                        or not _cell_source_matches(
                            cell,
                            arm,
                            epoch,
                            native_element_uid,
                            native_cells,
                            native_arms,
                            native_branches,
                        )
                    ):
                        sources.missing(
                            "M11_INTERVENTION_SCOPE_UNRESOLVED",
                            f"Dosing {dosing_uid} reaches a design cell without an exact matching native arm, epoch and element association.",
                        )
                        continue
                    scopes.append(
                        {
                            "cell_id": cell.get("id"),
                            "arm_id": arm["id"],
                            "arm": _text(arm.get("name")),
                            "epoch_id": epoch["id"],
                            "epoch": _text(epoch.get("name")),
                            "element_id": element["id"],
                            "element": _text(element.get("name")),
                        }
                    )
            if not scopes:
                sources.missing(
                    "M11_INTERVENTION_SCOPE_UNRESOLVED",
                    f"Dosing {dosing_uid} has no fully resolved arm, epoch, element and intervention relationship; no scope is inferred.",
                )
            product = _lookup(products, administration.get("administrableProductId"))
            if product is not None and (
                not relation_valid
                or source is None
                or not _selected_product_matches(
                    product, source, sources.study_uid, sources.version
                )
            ):
                product = None
            if administration.get("administrableProductId") and product is None:
                sources.missing(
                    "M11_PRODUCT_REFERENCE_UNRESOLVED",
                    f"Administration {administration.get('id')} has no unique product matching the exact native selected-value UID, version and study/compound scope.",
                )
            administrations.append(
                {
                    "id": administration.get("id"),
                    "uid": dosing_uid,
                    "dose": _value(administration.get("dose")),
                    "frequency": _code(administration.get("frequency")),
                    "route": _code(administration.get("route")),
                    "scopes": scopes,
                    "product": _product_for_preview(product),
                    "composition": _text(_object(dosing).get("composition")),
                }
            )
            rendered_dosings.add(dosing_uid)
        source = source or {}
        source_products = sources.index(
            _objects(source.get("pharmaceutical_products")), "uid"
        )
        product_rows = []
        for product in products.values():
            product_uid = _native_uid(product, "pharmaceuticalProduct", "uid")
            product_version = _native_uid(product, "pharmaceuticalProduct", "version")
            product_source = source_products.get(product_uid)
            if product_source and product_source.get("version") == product_version:
                product_rows.append(
                    {
                        "uid": product_uid,
                        "version": product_version,
                        "content": _product_for_preview(product),
                        "facts": _facts(
                            product_source,
                            (
                                ("dosage_forms", "Native dosage forms"),
                                ("routes_of_administration", "Allowed product routes"),
                            ),
                        ),
                    }
                )
        intervention_rows.append(
            {
                "id": identifier,
                "uid": source_uid,
                "name": _text(intervention.get("name")),
                "selected": selected and bool(source),
                "description": sanitize_html(_text(intervention.get("description"))),
                "role": _code(intervention.get("role")),
                "type": _code(intervention.get("type")),
                "codes": [_code(row) for row in _objects(intervention.get("codes"))],
                "source_versions": [
                    {
                        "kind": key,
                        "uid": value.get("uid"),
                        "version": value.get("version"),
                    }
                    for key in ("compound", "compound_alias", "medicinal_product")
                    if (value := _object(source.get(key)))
                ],
                "administrations": administrations,
                "products": product_rows,
                "facts": _facts(
                    source,
                    (
                        ("dispenser", "Native dispensed-in term"),
                        ("dispensed_in", "Dispense method"),
                        ("delivery_device", "Delivery device"),
                        ("other_info", "Other selected compound information"),
                        (
                            "reason_for_missing_null_value",
                            "Reason for missing compound",
                        ),
                    ),
                ),
            }
        )
    for uid, source in dosings.items():
        if uid not in rendered_dosings:
            sources.missing(
                "M11_UNMAPPED_DOSING_SOURCE",
                f"Native dosing {uid} has no mapped administration; its source reference remains unresolved.",
            )
            unresolved_dosings.append(
                {
                    "uid": uid,
                    "compound": _object(source.get("study_compound")).get(
                        "study_compound_uid"
                    ),
                    "element": _object(source.get("study_element")).get("element_uid"),
                }
            )
    for identifier in selected_ids if isinstance(selected_ids, list) else []:
        if identifier not in mapped_interventions:
            sources.missing(
                "M11_INTERVENTION_SOURCE_UNRESOLVED",
                f"Design references unavailable or ambiguous intervention {identifier}.",
            )
    return {
        "interventions": intervention_rows,
        "unresolved_dosings": unresolved_dosings,
    }


def _section_content(
    report: dict[str, Any],
    metadata: dict[str, Any],
    version: dict[str, Any],
    design: dict[str, Any],
    study_uid: str,
    selected_version: str | None,
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    """Present existing source-supported section content, never author narrative."""

    sources = _PreviewSources(report, study_uid, selected_version, issues)

    population_fields = (
        ("therapeutic_area_codes", "Therapeutic areas"),
        ("disease_condition_or_indication_codes", "Diseases / indications"),
        ("diagnosis_group_codes", "Diagnosis groups"),
        ("sex_of_participants_code", "Sex of participants"),
        ("healthy_subject_indicator", "Includes healthy participants"),
        ("rare_disease_indicator", "Rare disease indicator"),
        ("planned_minimum_age_of_subjects", "Minimum age"),
        ("planned_maximum_age_of_subjects", "Maximum age"),
        ("stable_disease_minimum_duration", "Minimum stable disease duration"),
        ("pediatric_study_indicator", "Pediatric study indicator"),
        (
            "pediatric_postmarket_study_indicator",
            "Pediatric postmarket study indicator",
        ),
        (
            "pediatric_investigation_plan_indicator",
            "Pediatric investigation plan indicator",
        ),
        ("relapse_criteria", "Relapse criteria"),
        ("number_of_expected_subjects", "Expected participants (study population)"),
    )
    intervention_metadata = _object(metadata.get("study_intervention"))
    design_facts = _facts(
        intervention_metadata,
        (
            ("intervention_type_code", "Intervention type"),
            ("intervention_model_code", "Intervention model"),
            ("control_type_code", "Control type"),
            ("trial_blinding_schema_code", "Blinding schema"),
            ("is_trial_randomised", "Randomised"),
            ("stratification_factor", "Stratification factor"),
            ("add_on_to_existing_treatments", "Added to existing treatment"),
            ("planned_study_length", "Planned study length (source definition)"),
            ("trial_intent_types_codes", "Trial intent"),
        ),
    )
    high_level = _object(metadata.get("high_level_study_design"))
    design_facts += _facts(
        high_level,
        (
            ("study_type_code", "Study type"),
            ("trial_type_codes", "Trial types"),
            ("trial_phase_code", "Trial phase"),
            ("development_stage_code", "Development stage"),
            ("is_adaptive_design", "Adaptive design"),
            ("is_extension_trial", "Extension trial"),
            ("observational_model_code", "Observational model"),
            ("observational_time_perspective_code", "Observational time perspective"),
            ("study_stop_rules", "Selected study stop rules"),
            (
                "confirmed_response_minimum_duration",
                "Minimum confirmed response duration",
            ),
            ("post_auth_indicator", "Post-authorisation study indicator"),
        ),
    )
    arm_records, branch_records = sources.records("studyArm"), sources.records(
        "studyBranchArm"
    )
    native_arms = sources.index(arm_records, "arm_uid")
    native_branches = sources.index(branch_records, "branch_arm_uid")
    cohorts = sources.records("studyCohort")
    cohort_rows = []
    for row in cohorts:
        associations = []
        for field, key, available in (
            ("arm_roots", "arm_uid", native_arms),
            ("branch_arm_roots", "branch_arm_uid", native_branches),
        ):
            for reference in _objects(row.get(field)):
                target = _lookup(available, reference.get(key))
                if target is None:
                    associations.append(f"Unresolved {key}: {reference.get(key)}")
                    sources.missing(
                        "M11_COHORT_SCOPE_UNRESOLVED",
                        f"Cohort {row.get('cohort_uid')} references an unavailable selected arm or branch.",
                    )
                else:
                    associations.append(f"{_text(target.get('name'))} [{target[key]}]")
        cohort_rows.append(
            {
                "uid": row.get("cohort_uid"),
                "name": _text(row.get("name")),
                "count": _value(row.get("number_of_subjects")),
                "associations": associations,
            }
        )

    sources.missing(
        "M11_SECTION_NARRATIVE_SOURCE_UNAVAILABLE",
        "Native metadata provides structured facts, criteria and dosing selections, "
        "but no authored protocol-section narrative bodies. Population/design rationale, "
        "benefit-risk and other clinical narratives require authoring; metadata and "
        "template instructions are not substitutes.",
    )
    return {
        "population_facts": _facts(
            _object(metadata.get("study_population")), population_fields
        ),
        "design_facts": design_facts,
        "cohorts": cohort_rows,
        "eligibility": _eligibility_for_preview(sources, version, design),
        **_interventions_for_preview(
            sources, version, design, native_arms, native_branches
        ),
        "root_arm_count": (
            len(native_arms) if len(native_arms) == len(arm_records) else MISSING
        ),
        "branch_count": (
            len(native_branches)
            if len(native_branches) == len(branch_records)
            else MISSING
        ),
    }


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _objects(value: Any) -> list[dict[str, Any]]:
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _text(value: Any) -> str:
    return value if isinstance(value, str) and value.strip() else MISSING


def _objective_for_preview(value: dict[str, Any]) -> dict[str, Any]:
    """Sanitize rich text without changing the retained native mapping evidence."""
    return {
        **value,
        "description": sanitize_html(_text(value.get("description"))),
        "endpoints": [
            {
                **endpoint,
                "description": sanitize_html(_text(endpoint.get("description"))),
            }
            for endpoint in _objects(value.get("endpoints"))
        ],
    }


def m11_preview_context(
    report: dict[str, Any],
    study_uid: str,
    study_value_version: str | None,
    document_id: str | None = None,
) -> dict[str, Any]:
    """Project retained native headers and selected USDM values, never latest fallbacks.

    The native mapper currently has no sponsor-role source. Registry organizations
    and identifier scopes therefore cannot authorize a sponsor name or address.
    A mapping-complete result is still a draft protocol preview, not an approval.
    """
    mapping = _object(report.get("mappingReport"))
    if (
        mapping.get("studyUid") != study_uid
        or mapping.get("studyValueVersion") != study_value_version
        or mapping.get("state") not in {"complete", "incomplete"}
        or not isinstance(mapping.get("issues"), list)
    ):
        raise USDMMappingAuthorityRequired(
            "M11_SOURCE_SCOPE_MISMATCH: mapping report does not match the requested study and version."
        )
    native = [
        row
        for row in _objects(report.get("nativeRecords"))
        if row.get("kind") == "study" and row.get("uid") == study_uid
    ]
    if len(native) != 1 or _object(native[0].get("record")).get("uid") != study_uid:
        raise USDMMappingAuthorityRequired(
            "M11_NATIVE_STUDY_REQUIRED: one exact retained native study is required."
        )
    metadata = _object(_object(native[0].get("record")).get("current_metadata"))
    identification = _object(metadata.get("identification_metadata"))
    source_version = _object(metadata.get("version_metadata"))
    native_version = source_version.get("version_number")
    native_version = str(native_version) if native_version is not None else None
    if study_value_version is not None and native_version != study_value_version:
        raise USDMMappingAuthorityRequired(
            "M11_SOURCE_VERSION_MISMATCH: native study does not match the requested version."
        )
    study = _object(_object(report.get("document")).get("study"))
    versions = [
        row
        for row in _objects(study.get("versions"))
        if row.get("versionIdentifier") == native_version
    ]
    if len(versions) != 1:
        raise USDMMappingAuthorityRequired(
            "M11_DOCUMENT_VERSION_MISMATCH: one mapped version must match the retained native study."
        )
    version = versions[0]
    issues = list(_objects(mapping.get("issues")))

    def missing(code: str, message: str) -> None:
        issues.append({"code": code, "message": message})

    designs = version.get("studyDesigns")
    if isinstance(designs, list) and len(designs) == 1 and isinstance(designs[0], dict):
        design = designs[0]
    else:
        design = {}
        missing(
            "M11_DESIGN_UNRESOLVED",
            "The selected version does not contain one unambiguous study design; design-dependent fields are unavailable.",
        )
    references = version.get("documentVersionIds")
    reference_ids: set[str] = set()
    if (
        isinstance(references, list)
        and all(isinstance(ref, str) and ref for ref in references)
        and len(references) == len(set(references))
    ):
        reference_ids = set(references)
    referenced = [
        item
        for document in _objects(study.get("documentedBy"))
        if _object(document.get("type")).get("code") == "C70817"
        for item in _objects(document.get("versions"))
        if isinstance(item.get("id"), str) and item["id"] in reference_ids
    ]
    protocol_version = referenced[0].get("version") if len(referenced) == 1 else None
    if protocol_version is None:
        missing(
            "M11_PROTOCOL_VERSION_UNRESOLVED",
            "The selected source does not resolve one referenced protocol document version. No current-version fallback was used.",
        )
    missing(
        "M11_SPONSOR_SOURCE_UNAVAILABLE",
        "The native source does not yet provide an authoritative sponsor role and legal address. Registry organizations are not sponsors.",
    )
    population = _object(design.get("population"))
    age = _object(population.get("plannedAge"))
    minimum, maximum = _object(age.get("minValue")), _object(age.get("maxValue"))
    objectives = [
        _objective_for_preview(row) for row in _objects(design.get("objectives"))
    ]
    phase = _object(_object(design.get("studyPhase")).get("standardCode"))
    registry = _object(identification.get("registry_identifiers"))
    # These are native registry fields, not USDM Organization IDs. The selected
    # study record is the same retained source used by the mapper above.
    registry_fields = (
        ("ct_gov_id", "ClinicalTrials.gov ID"),
        ("eudract_id", "EudraCT ID"),
        ("eu_trial_number", "EU Trial Number"),
        ("civ_id_sin_number", "CIV-ID/SIN"),
        ("eudamed_srn_number", "EUDAMED SRN"),
        ("investigational_device_exemption_ide_number", "IDE Number"),
        ("investigational_new_drug_application_number_ind", "IND Number"),
        ("japanese_trial_registry_id_japic", "JAPIC ID"),
        ("japanese_trial_registry_number_jrct", "jRCT Number"),
        ("national_clinical_trial_number", "National Clinical Trial Number"),
        ("national_medical_products_administration_nmpa_number", "NMPA Number"),
        ("universal_trial_number_utn", "WHO/UTN Number"),
        ("eu_pas_number", "EU PAS Number"),
    )

    def value_or_missing(value: Any) -> Any:
        return MISSING if value is None else value

    sections = _section_content(
        report, metadata, version, design, study_uid, study_value_version, issues
    )
    result: dict[str, Any] = {
        "source_sections": sections,
        "intervention_names": "; ".join(
            row["name"] for row in sections["interventions"] if row["selected"]
        )
        or MISSING,
        "intervention_model": _code(design.get("model")),
        "control": _code(
            _object(metadata.get("study_intervention")).get("control_type_code")
        ),
        "blinding_schema": _code(design.get("blindingSchema")),
        "study_id": study_uid,
        "source_version": native_version or MISSING,
        "source_status": _text(source_version.get("study_status")),
        "current_preview": study_value_version is None,
        "mapping_state": mapping["state"],
        "preview_issues": issues,
        "study_name": _text(study.get("name")),
        "protocol_full_title": _text(
            _object(metadata.get("study_description")).get("study_title")
        ),
        "protocol_short_title": _text(
            _object(metadata.get("study_description")).get("study_short_title")
        ),
        "trial_acronym": _text(identification.get("study_acronym")),
        "sponsor_protocol_identifier": _text(identification.get("study_id")),
        "sponsor_name": MISSING,
        "sponsor_legal_address": MISSING,
        "protocol_version": _text(protocol_version),
        "version_date": MISSING,
        "original_protocol": MISSING,
        "amendment_identifier": MISSING,
        "registry_identifiers": [
            {"label": label, "value": registry[key]}
            for key, label in registry_fields
            if isinstance(registry.get(key), str) and registry[key].strip()
        ],
        "trial_phase": {"decode": _text(phase.get("decode"))},
        "study_indications": _objects(design.get("indications")),
        "primary_objectives": [
            row
            for row in objectives
            if "primary" in str(_object(row.get("level")).get("decode", "")).lower()
        ],
        "secondary_objectives": [
            row
            for row in objectives
            if "secondary" in str(_object(row.get("level")).get("decode", "")).lower()
        ],
        "population_healthy_subjects": population.get("includesHealthySubjects"),
        "population_planned_minimum_age": value_or_missing(minimum.get("value")),
        "population_planned_minimum_age_unit": _text(
            _object(_object(minimum.get("unit")).get("standardCode")).get("decode")
        ),
        "population_planned_maximum_age": value_or_missing(maximum.get("value")),
        "population_planned_maximum_age_unit": _text(
            _object(_object(maximum.get("unit")).get("standardCode")).get("decode")
        ),
        "population_planned_enrollment_number_quantity_value": value_or_missing(
            _object(population.get("plannedEnrollmentNumber")).get("value")
        ),
        "number_of_arms": sections["root_arm_count"] if design else MISSING,
        # Last timing is not necessarily total trial duration.
        "trial_intervention_total_duration": None,
    }
    result.update(
        _authored_document_context(
            report, study, version, study_uid, study_value_version, document_id
        )
    )
    if result.get("authored_document"):
        result["preview_issues"] = [
            issue
            for issue in result["preview_issues"]
            if issue.get("code") != "M11_PROTOCOL_VERSION_UNRESOLVED"
            and not (
                issue.get("code") == "M11_SPONSOR_SOURCE_UNAVAILABLE"
                and result.get("sponsor_name") != MISSING
            )
        ]
    return result


def _authored_document_context(
    report, study, version, study_uid, study_value_version, document_id
):
    records = [
        row
        for row in _objects(report.get("nativeRecords"))
        if row.get("kind") == "studyAuthoredDocuments"
    ]
    if not records:
        if document_id is not None:
            raise USDMMappingAuthorityRequired(
                "The selected authored document is unavailable in this native study version"
            )
        return {"authored_document": None}
    if (
        len(records) != 1
        or records[0].get("uid") != study_uid
        or records[0].get("scope")
        != {"studyUid": study_uid, "studyValueVersion": study_value_version}
    ):
        raise USDMMappingAuthorityRequired(
            "Authored document provenance does not match the selected native study version"
        )
    authored = AuthoredProtocolDocuments.model_validate(records[0]["record"])
    if not authored.documents:
        if document_id is not None:
            raise USDMMappingAuthorityRequired(
                "The selected authored document is unavailable in this native study version"
            )
        return {"authored_document": None}
    bindings = {item.document_id: item for item in authored.bindings}
    if document_id is None:
        document_id = next(
            item.document_id
            for item in authored.bindings
            if item.role == "master-protocol"
        )
    selected = next(
        (item for item in authored.documents if item.id == document_id), None
    )
    if selected is None:
        raise USDMMappingAuthorityRequired(
            "The selected document is not part of this native version"
        )
    # No imported or detached carrier may replace the retained authored source.
    if study.get("documentedBy") != [
        item.model_dump(mode="json") for item in authored.documents
    ] or version.get("narrativeContentItems") != [
        item.model_dump(mode="json") for item in authored.narrative_content_items
    ]:
        raise USDMMappingAuthorityRequired(
            "Mapped authored document content differs from its retained native source"
        )
    if version.get("documentVersionIds") != [
        item.versions[0].id for item in authored.documents
    ]:
        raise USDMMappingAuthorityRequired(
            "Authored document version references differ from native selection"
        )
    native_arms = {
        row.get("uid")
        for row in _objects(report.get("nativeRecords"))
        if row.get("kind") == "studyArm"
    }
    binding = bindings[document_id]
    if not set(binding.arm_uids) <= native_arms:
        raise USDMMappingAuthorityRequired(
            "Authored applicability does not resolve to selected native arms"
        )
    items = {item.id: item for item in authored.narrative_content_items}
    dispositions = {item.content_id: item for item in authored.section_dispositions}
    assessment = assess_authored_documents(authored)
    coverage = next(
        item for item in assessment["documents"] if item["document_id"] == document_id
    )
    organization = next(
        (
            item
            for item in authored.organizations
            if item.id == binding.sponsor_organization_id
        ),
        None,
    )
    address = organization.legalAddress if organization else None
    rows = []
    for section in selected.versions[0].contents:
        item = items.get(section.contentItemId)
        disposition = dispositions.get(section.id)
        rows.append(
            {
                "id": section.id,
                "number": section.sectionNumber,
                "title": section.sectionTitle,
                "content_item_id": section.contentItemId,
                "child_ids": section.childIds,
                "state": disposition.state if disposition else "unresolved",
                "reason": disposition.reason if disposition else None,
                "html": sanitize_html(item.text, allow_tables=True) if item else None,
                "display_number": section.displaySectionNumber,
                "display_title": section.displaySectionTitle,
            }
        )
    return {
        "authored_document": {
            "id": selected.id,
            "name": selected.name,
            "description": (
                sanitize_html(selected.description) if selected.description else None
            ),
            "version_id": selected.versions[0].id,
            "version": selected.versions[0].version,
            "binding": binding.model_dump(),
            "synthetic": authored.synthetic,
            "content_hash": authored.content_hash(),
            "sections": rows,
            "coverage": coverage,
            "assessment_meaning": assessment["meaning"],
        },
        "sponsor_name": organization.name if organization else MISSING,
        "sponsor_legal_address": address.model_dump(mode="json") if address else {},
        "authored_sponsor_address": (
            "; ".join(
                str(value)
                for value in [
                    address.text,
                    *address.lines,
                    address.city,
                    address.district,
                    address.state,
                    address.postalCode,
                    _code(address.country.model_dump()) if address.country else None,
                ]
                if value
            )
            if address
            else MISSING
        ),
    }
