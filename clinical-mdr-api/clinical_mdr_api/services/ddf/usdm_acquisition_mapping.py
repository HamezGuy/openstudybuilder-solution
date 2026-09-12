"""Expose native acquisition candidates as editable, explicitly incomplete USDM."""

from usdm_model.alias_code import AliasCode
from usdm_model.biomedical_concept import BiomedicalConcept
from usdm_model.biomedical_concept_property import BiomedicalConceptProperty
from usdm_model.response_code import ResponseCode

from clinical_mdr_api.services.ddf.usdm_mapping_context import NativeExecutionReview


def _properties(source):
    return (source or {}).get("properties", {})


def _datatype(context):
    """Use a native machine submission value, never a sponsor display label."""
    if not context or not context.get("term") or not context.get("codelist"):
        return None
    uid = context["term"]["uid"]
    values = {
        member["properties"].get("submission_value")
        for member in context["codelist"].get("terms", [])
        if member.get("term", {}).get("uid") == uid
    }
    if len(values) == 1:
        value = next(iter(values))
        # USDM4 datatype is a string, not a closed enum. Preserve the native
        # machine value, including partial/interval/binary ODM types. A later
        # clinical field binding must support or explicitly resolve that type.
        if isinstance(value, str) and value:
            return value
    return None


def _response_candidates(item, context, source_path):
    """Keep a selected term narrower than its retained codelist context."""
    codelists = item.get("codelists") or []
    selected_terms = item.get("ct_terms") or []
    if codelists and selected_terms:
        context.unresolved(
            "USDM_ACQUISITION_RESPONSE_SCOPE_CONFLICT", source_path,
            "BiomedicalConceptProperty/responseCodes",
            "The native item has both whole-codelist and selected-term relationships.",
            "Resolve the conflicting native relationships. Neither scope can authorize a union of response candidates.",
        )
        return {}

    candidates = {}

    def add(codelist, member):
        key = (codelist["uid"], member["memberIdentity"])
        if key in candidates and candidates[key] != member:
            raise ValueError("USDM_ACQUISITION_RESPONSE_SOURCE_CONFLICT")
        candidates[key] = member

    for codelist in codelists:
        for member in codelist.get("terms", []):
            add(codelist, member)
    for index, entry in enumerate(selected_terms):
        entry = entry or {}
        term_uid = (entry.get("term") or {}).get("uid")
        codelist = entry.get("codelist") or {}
        matches = [
            member for member in codelist.get("terms", [])
            if term_uid and (member.get("term") or {}).get("uid") == term_uid
        ]
        if (
            not term_uid or not codelist.get("uid") or len(matches) != 1
            or codelist.get("unresolvedMemberships")
        ):
            # Unresolved membership evidence may lack its term identity, so it
            # cannot be assumed unrelated to the selected term. Keep the full
            # source context on the property and leave this response unresolved.
            context.unresolved(
                "USDM_ACQUISITION_SELECTED_RESPONSE_MEMBERSHIP_REQUIRED",
                f"{source_path}/ct_terms/{index}",
                "BiomedicalConceptProperty/responseCodes",
                "The selected native term has no unambiguous dated codelist membership.",
                "Resolve its exact term/codelist identities and membership history. Other codelist terms cannot replace or widen the selection.",
            )
            continue
        add(codelist, matches[0])
    return candidates


def project_acquisition(mapper, row, definition, version, design):
    items = definition.get("activity_items") or []
    if not items:
        return None
    context, identifier = mapper._context, mapper._id_manager.get_id
    selection_uid = row.study_activity_instance_uid
    concept_id = identifier("BiomedicalConcept", selection_uid)
    concept_path = f"/study/versions/0/biomedicalConcepts/{len(version.biomedicalConcepts)}"
    snapshot = definition.get("nativeSnapshot")
    if snapshot is None or snapshot.get("issues"):
        context.unresolved(
            "USDM_ACTIVITY_NESTED_SOURCE_REQUIRED", f"study-activity-instances/{selection_uid}",
            "BiomedicalConcept/properties",
            "The selected instance has nested native values whose exact history is unresolved.",
            "Resolve the displayed class, CT, grouping and unit history. Current library metadata cannot replace a selected snapshot.",
        )

    def standard_code(concept_id):
        if concept_id is None:
            return None
        value = mapper.get_ct_package_term_as_usdm_code(concept_id)
        return value if value.code else None

    def alias(code, source_path):
        return context.build(
            AliasCode, source_path, id=identifier("AliasCode"),
            standardCode=code,
        ) if code is not None else None

    properties = []
    for index, item in enumerate(items):
        source_path = f"activity-instance-definitions/{definition['uid']}@{definition['version']}/activity_items/{index}"
        native_class = item.get("activity_item_class") or {}
        class_source = native_class.get("source") if snapshot is not None else None
        class_properties = _properties(class_source)
        item_identity = item.get("nativeIdentity")
        property_id = identifier("BiomedicalConceptProperty", f"{selection_uid}:{item_identity or index}")
        property_path = concept_path + f"/properties/{index}"
        review: NativeExecutionReview = {
            "kind": "property-acquisition", "state": "requires-review",
            "sourceScope": {
                "studyUid": mapper._study_uid, "studyValueVersion": mapper._study_value_version,
                "studyActivityInstanceUid": selection_uid,
                "activityInstanceUid": definition["uid"], "activityInstanceVersion": definition["version"],
                "activityItemIdentity": item_identity, "sourceIndex": index,
                "activityItemClassUid": native_class.get("uid"),
                "activityItemClassVersion": (class_source or {}).get("version"),
                "nativeAsOf": (snapshot or {}).get("asOf"),
            },
            "canonicalScope": {
                "activityId": identifier("Activity", "instance:" + selection_uid),
                "biomedicalConceptId": concept_id, "propertyId": property_id,
            },
            "draftTargets": {
                "versionId": version.id, "designId": design.id,
                "biomedicalConceptId": concept_id, "propertyId": property_id,
            },
            "documentPointers": {
                "property": property_path,
                **{field: property_path + "/" + field for field in
                   ("name", "datatype", "isRequired", "isEnabled", "code", "responseCodes")},
            },
            "requiredBindings": [
                "reviewed-acquisition-role", "exact-native-class-and-value-scope",
                "required-and-enabled-facts", "canonical-code-and-datatype",
                "reviewed-response-codes-and-units", "exact-form-field-or-reviewed-omission",
            ],
        }
        context.unresolved(
            "USDM_PROPERTY_ACQUISITION_REVIEW_REQUIRED", source_path,
            "BiomedicalConceptProperty/isRequired",
            "Native item metadata supplies candidates, not clinical requiredness, enabled state or form applicability.",
            "Review the exact class/role and value relationships, author missing property facts, and bind the intended field or a reviewed omission.",
            execution_review=review,
        )
        candidates = _response_candidates(item, context, source_path) if snapshot is not None else {}
        responses = []
        for (codelist_uid, member_id), member in candidates.items():
            term = member["term"]
            attributes = _properties(term.get("attributes"))
            responses.append(context.build(
                ResponseCode, source_path,
                id=identifier("ResponseCode", f"{selection_uid}:{item_identity or index}:{codelist_uid}:{member_id}"),
                name=_properties(term.get("name")).get("name"),
                code=standard_code(attributes.get("concept_id")),
                # Membership/constant metadata does not imply clinical enabled.
                extensionAttributes=[mapper._native_extension(
                    "acquisitionResponseCandidate", f"{property_id}:{codelist_uid}:{member_id}", member,
                )],
            ))
        properties.append(context.build(
            BiomedicalConceptProperty, source_path, id=property_id,
            name=class_properties.get("name"),
            label=class_properties.get("display_name"),
            datatype=_datatype(native_class.get("dataType")) if class_source is not None else None,
            code=alias(standard_code(class_properties.get("nci_concept_id")), source_path),
            responseCodes=responses,
            extensionAttributes=[mapper._native_extension("acquisitionPropertySource", property_id, item)],
            # isRequired/isEnabled have no native acquisition authority.
        ))
    concept = context.build(
        BiomedicalConcept, f"study-activity-instances/{selection_uid}",
        id=concept_id, name=definition.get("name"),
        reference=f"urn:openstudybuilder:activity-instance:{definition['uid']}:version:{definition['version']}",
        code=alias(standard_code(definition.get("nci_concept_id")), concept_path),
        properties=properties,
        extensionAttributes=[mapper._native_extension("activity-instance-definition", selection_uid, definition)],
    )
    version.biomedicalConcepts.append(concept)
    return concept_id
