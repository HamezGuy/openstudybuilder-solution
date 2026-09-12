# Dynamic Fields Implementation for Sponsor Model Import

## Overview

This implementation adds a flexible, maintainable system for handling CSV imports with both expected and dynamic fields. The system automatically transforms CSV data into API-ready payloads while allowing new columns to be added to CSVs without code changes.

This is useful for evolution of the sponsor model across versions ; and to support various implementations of sponsor models across sponsor companies.

## Architecture

### 1. Property Definition System

The core of the system consists of three main components:

#### `PropertyType` (Enum)
Defines the types of transformations available:
- `STRING` - String field (empty strings become None)
- `BOOLEAN` - Parse "Y"/"X"/"True"/"true"/"1"/"Yes" becomes True ; everything else becomes False
- `REVERSE_BOOLEAN` - Parse and invert boolean
- `INTEGER` - Convert to integer
- `LIST_SPACE_SEPARATED` - Split string by spaces
- `CUSTOM` - Use custom transformer function

#### `PropertyDefinition` (Dataclass)
Defines how a CSV field maps to an API field:
- `csv_field`: Column name in CSV
- `api_field`: Field name in API request body
- `property_type`: Type of transformation to apply
- `required`: Whether field must be present
- `custom_transformer`: Optional custom function for CUSTOM property type
- `default_value`: Default if field missing
- `conditional_check`: Function to determine if field should be processed

#### `FieldMapper` (Class)
Maps CSV rows to API bodies:
- Holds reference to parser instance for access to parsing methods
- Contains transformation functions for each PropertyType
- Automatically applies correct transformers based on property definitions
- Includes dynamic fields not in property definitions

### 2. Pre-defined Property Definitions

#### Dataset Properties
Defined in `get_dataset_property_definitions()`:
- **Required fields**: Table, Label, Class, enrich_build_order, basic_std, comment
- **Optional fields**: XmlPath, XmlTitle, Structure, Purpose, Keys, SortKeys, etc.
- **Conditional fields**: isnotcdiscstd, cdiscstd (processed based on CSV headers)

#### Dataset Variable Properties
Defined in `get_dataset_variable_property_definitions()`:
- **Required fields**: table, column, class_table, class_column, order, basic_std
- **Optional fields**: label, type, length, displayformat, xmldatatype, etc.
- **Conditional fields**: origintype, originsource, isnotcdiscstd

## Usage

### Adding a New Expected Field

To add a new expected field with transformation:

```python
# In get_dataset_property_definitions() or get_dataset_variable_property_definitions()
PropertyDefinition(
    csv_field="NewCSVColumn",
    api_field="new_api_field",
    property_type=PropertyType.BOOLEAN,  # or STRING, LIST_SPACE_SEPARATED, etc.
    required=False,  # Set to True if mandatory
)
```

### Custom Transformations

For complex transformations, use a custom transformer:

```python
PropertyDefinition(
    csv_field="ComplexField",
    api_field="complex_field",
    property_type=PropertyType.CUSTOM,
    custom_transformer=lambda v: my_complex_transformation(v),
)
```

### Conditional Fields

For fields that should only be processed if certain conditions are met:

```python
PropertyDefinition(
    csv_field="OptionalColumn",
    api_field="optional_field",
    property_type=PropertyType.STRING,
    conditional_check=lambda headers: "OptionalColumn" in headers,
)
```

## How It Works

### Step-by-Step Process

1. **CSV is opened** and headers are read
2. **Property definitions are loaded** for the entity type (dataset or dataset_variable)
3. **For each row**:
   a. For each property definition:
      - Check if conditional check passes (if defined)
      - Check if field exists in CSV headers
      - Extract value from row
      - Apply appropriate transformer based on PropertyType
      - Add transformed value to `body` dict

   b. After processing all defined properties:
      - Scan headers for any columns not in property definitions
      - Add these as dynamic `string` fields (sanitized key, empty → None)

   c. Add common body parameters (sponsor_model_name, etc.)

   d. Send to API

### Dynamic Field Handling

Any CSV column that is NOT in the property definitions is automatically:
- **Sanitized**: Lowercase, spaces/hyphens → underscores
- **Null-handled**: Empty strings converted to None
- **Passed through**: Sent to API as-is (probably string or integer)

This means you can add a new column like "custom_metadata" to your CSV and it will automatically be included in the API request without any code changes.

### Field Handling - API side

Ensure that the called API will handle your object correctly. The initial implementation proposed by OpenStudyBuilder ensures:

* Expected fields are handled specifically
* Dynamic fields will be "passed through", meaning no business rule will be applied to them and they will be stored "as-is"

## Examples

### Example 1: Standard Field with Transformation

```python
# CSV has column "include_in_raw" with values "Y" or ""
PropertyDefinition(
    csv_field="include_in_raw",
    api_field="include_in_raw",
    property_type=PropertyType.BOOLEAN,  # Automatically converts Y → True, "" → None
)
```

### Example 2: Field with Custom Transformation

```python
# CSV has "Class" column that needs special parsing
PropertyDefinition(
    csv_field="Class",
    api_field="implemented_dataset_class",
    property_type=PropertyType.CUSTOM,
    required=True,
    custom_transformer=lambda v: parser_instance.parse_dataset_class_name(
        v, row_context.get("Table", None)  # Access other row fields via row_context
    ),
)
```

### Example 3: Conditional Field

```python
# Only process "isnotcdiscstd" if it exists in CSV, otherwise use "basic_std"
PropertyDefinition(
    csv_field="isnotcdiscstd",
    api_field="is_cdisc_std",
    property_type=PropertyType.REVERSE_BOOLEAN,
    conditional_check=lambda headers: "isnotcdiscstd" in headers,
),
PropertyDefinition(
    csv_field="basic_std",
    api_field="is_cdisc_std",
    property_type=PropertyType.BOOLEAN,
    conditional_check=lambda headers: "isnotcdiscstd" not in headers,
)
```

### Example 4: Dynamic Field Automatically Included

```csv
Table,Label,Class,basic_std,comment,custom-sponsor-flag
AE,Adverse Events,Events,Y,Standard AE domain,SPONSOR_SPECIFIC
```

The `custom-sponsor-flag` column will automatically be:
- Detected as a dynamic field (not in property definitions)
- Sanitized to `custom_sponsor_flag`
- Sent to API as `{"custom_sponsor_flag": "SPONSOR_SPECIFIC"}`

## Migration Path

### For Existing CSVs (provided by Novo Nordisk)
All existing CSVs will work without changes. The system handles:
- All currently expected fields via property definitions
- Any existing extra columns for future versions as dynamic fields

### For New Columns
Simply add the column to your CSV:
1. **If transformation needed**: Add PropertyDefinition to the appropriate function
2. **If no transformation needed**: You can just add to CSV, it will be auto-included. Ideally, reference it in the PropertyDefinition list and in the API for better control.

## Testing Recommendations

### Test Cases to Validate

1. **Standard import**: All expected fields present
2. **Missing optional fields**: Ensure defaults work correctly
3. **Extra dynamic fields**: Add unknown columns, verify they're passed through
4. **Conditional fields**: Test with/without conditional columns
5. **Edge cases**: Empty strings, null values, special characters in dynamic field names

## Summary

This implementation provides a robust, flexible system for handling CSV imports with:
- ✅ Centralized field definitions
- ✅ Automatic type transformations
- ✅ Support for dynamic/unknown fields
- ✅ Conditional field handling
- ✅ Easy maintenance and extension
- ✅ Backward compatibility with existing CSVs
- ✅ Forward compatibility with API changes

The system eliminates the need to update import scripts every time a new column is added to CSVs, while maintaining type safety and transformation logic for known fields.

----------

## Property Definition System - Quick Reference

## Adding a New Expected Field

### List Field (Space-Separated)

```python
PropertyDefinition(
    csv_field="Tags",  # CSV: "tag1 tag2 tag3"
    api_field="tags",   # API: ["tag1", "tag2", "tag3"]
    property_type=PropertyType.LIST_SPACE_SEPARATED,
)
```

### Custom Transformation

```python
PropertyDefinition(
    csv_field="ComplexField",
    api_field="complex_field",
    property_type=PropertyType.CUSTOM,
    custom_transformer=lambda v: your_custom_function(v),
)
```

### Custom with Access to Other Row Fields

```python
PropertyDefinition(
    csv_field="Field1",
    api_field="field1",
    property_type=PropertyType.CUSTOM,
    custom_transformer=lambda v: process_with_context(
        v,
        row_context.get("Field2")  # Access other field from same row
    ),
)
```

## Field Options

### Required Field

```python
PropertyDefinition(
    csv_field="MandatoryColumn",
    api_field="mandatory_field",
    property_type=PropertyType.STRING,
    required=True,  # Will raise error if missing from CSV
)
```

### Field with Default Value

```python
PropertyDefinition(
    csv_field="OptionalColumn",
    api_field="optional_field",
    property_type=PropertyType.STRING,
    default_value="default_value",  # Used if field exists in CSV but cell is empty
)
```

### Conditional Field

```python
PropertyDefinition(
    csv_field="RareColumn",
    api_field="rare_field",
    property_type=PropertyType.STRING,
    conditional_check=lambda headers: "OtherRareColumn" in headers,
)
```

### Mutually Exclusive Conditional Fields

```python
# Use Field1 if present, otherwise use Field2
PropertyDefinition(
    csv_field="Field1",
    api_field="result",
    property_type=PropertyType.STRING,
    conditional_check=lambda headers: "Field1" in headers,
),
PropertyDefinition(
    csv_field="Field2",
    api_field="result",
    property_type=PropertyType.STRING,
    conditional_check=lambda headers: "Field1" not in headers,
)
```

## Where to Add Definitions

### For Dataset Fields
Edit the list returned by `get_dataset_property_definitions()` in [run_import_sponsormodels.py](run_import_sponsormodels.py)

### For Dataset Variable Fields
Edit the list returned by `get_dataset_variable_property_definitions()` in [run_import_sponsormodels.py](run_import_sponsormodels.py)

## Common Mistakes to Avoid

❌ **Don't do this**:
```python
# Missing custom_transformer when using PropertyType.CUSTOM
PropertyDefinition(
    csv_field="Field",
    api_field="field",
    property_type=PropertyType.CUSTOM,  # ERROR: Will raise ValueError
)
```

✅ **Do this instead**:
```python
PropertyDefinition(
    csv_field="Field",
    api_field="field",
    property_type=PropertyType.CUSTOM,
    custom_transformer=lambda v: transform(v),  # Required!
)
```

---

❌ **Don't do this**:
```python
# Accessing row fields directly in custom_transformer
PropertyDefinition(
    csv_field="Field1",
    api_field="field1",
    property_type=PropertyType.CUSTOM,
    custom_transformer=lambda v: combine(v, row[headers.index("Field2")]),  # ERROR
)
```

✅ **Do this instead**:
```python
PropertyDefinition(
    csv_field="Field1",
    api_field="field1",
    property_type=PropertyType.CUSTOM,
    custom_transformer=lambda v: combine(v, row_context.get("Field2")),  # Use row_context
)
```

---

❌ **Don't do this**:
```python
# Duplicate api_field without conditional_check
PropertyDefinition(csv_field="Field1", api_field="result", ...),
PropertyDefinition(csv_field="Field2", api_field="result", ...),  # Both will be processed!
```

✅ **Do this instead**:
```python
PropertyDefinition(
    csv_field="Field1",
    api_field="result",
    conditional_check=lambda headers: "Field1" in headers,
),
PropertyDefinition(
    csv_field="Field2",
    api_field="result",
    conditional_check=lambda headers: "Field1" not in headers,  # Mutually exclusive
)
```


