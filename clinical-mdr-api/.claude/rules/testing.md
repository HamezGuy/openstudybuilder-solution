---
paths:
  - clinical_mdr_api/tests/**
  - consumer_api/tests/**
  - extensions/tests/**
---

# Testing Strategy

## Test Types

- **Unit tests** - Test domain logic in isolation (`clinical_mdr_api/tests/unit/`)
- **Integration tests** - Test with real Neo4j database (`clinical_mdr_api/tests/integration/`)
- **Auth tests** - OAuth/RBAC testing (`clinical_mdr_api/tests/auth/`)
- **Acceptance tests** - BDD with pytest-bdd (`clinical_mdr_api/tests/acceptance/`)
- **Schemathesis** - Contract testing against OpenAPI spec

## Test Organization

- Test fixtures are in `clinical_mdr_api/tests/fixtures/`
- Shared utilities in `clinical_mdr_api/tests/utils/`
- Integration tests run in parallel with `-n 4` flag

## Writing Tests

- **Use real workflows, not shortcuts** - tests should trigger the actual code paths users hit, not manually arrange the end state
- **Use `TestUtils`** - check `clinical_mdr_api/tests/integration/utils/utils.py` before writing test setup from scratch
- **Verify the test fails without the fix** - before committing a bug fix test, confirm it actually fails on the old code
- **Search for existing test patterns** - look at similar test files in the same directory before writing a new test


