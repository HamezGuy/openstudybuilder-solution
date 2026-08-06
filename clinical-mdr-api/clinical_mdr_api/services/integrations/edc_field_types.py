"""ODM item datatype -> AccuraTrial EDC canonical field type, CLOSED map.

The EDC's importer (`resolveFieldType` in field-type.utils.ts) silently maps
ANY unknown type to 'text' — imported "successfully", corrupted quietly. This
module is the exporter's refusal to rely on that: every emitted type is either
restored exactly or downgraded LOUDLY into the export census.

Two-tier restoration:
  1. The `x360i:fieldType` vendor attribute, when present — the ORIGINAL
     49-value EDC type stamped by the 360i importer. Exact, lossless.
  2. Else this closed ODM-datatype map. Anything outside it ships as 'text'
     WITH a per-field census entry — never silently.
"""

# The EDC's canonical field-type vocabulary (49 values; field-type.utils.ts).
EDC_CANONICAL_FIELD_TYPES = frozenset(
    {
        "text", "textarea", "number", "decimal", "date", "datetime", "time",
        "date_of_birth", "yesno", "radio", "select", "checkbox", "combobox",
        "file", "image", "signature", "barcode", "qrcode",
        "blood_pressure", "temperature", "height", "weight", "bmi",
        "heart_rate", "respiration_rate", "oxygen_saturation",
        "calculation", "age", "bsa", "egfr", "sum", "average",
        "email", "phone", "address", "patient_name", "patient_id", "ssn",
        "medical_record_number", "medication", "diagnosis", "procedure",
        "lab_result", "section_header", "static_text", "inline_group",
        "criteria_list", "question_table", "table",
    }
)

# ODM 1.3.2 ItemDef datatype -> EDC type, for items with no x360i stamp
# (native OSB studies). `has_codelist` refines coded items.
ODM_TO_EDC = {
    "text": "text",
    "string": "text",
    "integer": "number",
    "float": "decimal",
    "double": "decimal",
    "decimal": "decimal",
    "boolean": "yesno",
    "date": "date",
    "datetime": "datetime",
    "time": "time",
    "partialdate": "date",
    "partialdatetime": "datetime",
    "base64binary": "file",
    "base64float": "file",
    "hexbinary": "file",
    "uri": "text",
    "durationdatetime": "text",
    "intervaldatetime": "text",
    "incompletedatetime": "datetime",
    "incompletedate": "date",
    "incompletetime": "time",
}


def resolve_edc_field_type(
    odm_datatype: str | None,
    x360i_field_type: str | None,
    has_codelist: bool,
    multi_choice: bool,
) -> tuple[str, str | None]:
    """Resolve one item's EDC type.

    Returns (edc_type, downgrade_reason). downgrade_reason is None when the
    resolution was faithful; a string when the caller must census the field.
    """
    if x360i_field_type:
        stamped = x360i_field_type.strip()
        if stamped in EDC_CANONICAL_FIELD_TYPES:
            return stamped, None
        return (
            "text",
            f"x360i:fieldType '{stamped}' is not in the EDC vocabulary — stamped by a "
            "newer 360i than this exporter knows; update the map",
        )

    datatype = (odm_datatype or "text").strip().lower()
    if has_codelist:
        # A coded item renders as a choice control; multi-select -> checkbox.
        return ("checkbox" if multi_choice else "select"), None
    mapped = ODM_TO_EDC.get(datatype)
    if mapped is not None:
        return mapped, None
    return (
        "text",
        f"ODM datatype '{datatype}' has no EDC mapping; shipped as 'text'",
    )
