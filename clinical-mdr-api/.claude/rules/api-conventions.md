# REST API Conventions

Following [Zalando RESTful API Guidelines](https://opensource.zalando.com/restful-api-guidelines/):

## Naming Conventions

- **Paths**: Nouns only (plural form), `kebab-case`, minimize nesting and root endpoints
- **Parameters**: `snake_case` for query params and JSON fields
- **UID Parameters**: Include entity type prefix (e.g., `study_uid`, `concept_uid`)

## HTTP Methods

- GET (200) - Read operations
- POST (201) - Create new entities
- PUT (200) - Overwrite entirely (avoid if possible)
- PATCH (200) - Partial updates
- DELETE (204) - Delete operations

## Error Handling

All custom exceptions defined in `clinical_mdr_api/exceptions/__init__.py`:

- 400 - ValidationException (pydantic validation errors or business rule violations)
- 404 - NotFoundException (referenced entity doesn't exist)
- 401 - Authentication errors (missing Bearer token)
- 403 - Authorization errors (insufficient permissions)

Error responses include `time`, `path`, `method`, `type`, `message`, and `errors` array.

## OpenAPI Specifications

The OpenAPI specifications are generated from code, not written manually:

- Use `pipenv run openapi` to regenerate after endpoint changes
- Specifications are committed to version control
- Schemathesis uses these for contract testing


