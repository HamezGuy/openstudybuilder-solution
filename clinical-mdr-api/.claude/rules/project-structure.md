# Project Structure

## Important Files

- `clinical_mdr_api/main.py` - FastAPI app initialization, middleware setup, exception handlers
- `common/config.py` - Centralized settings (loaded from environment variables)
- `common/database.py` - Neo4j/neomodel configuration
- `common/exceptions.py` - Base exception classes
- `Pipfile` - All scripts and dependency definitions
- `pyproject.toml` - Tool configurations (pylint, mypy, isort, pytest)
- `openapi.json` - Generated OpenAPI specification (do not edit manually)

## Special Directories

- `sblint/` - Custom static analysis tool
- `templates/` - Jinja2 templates
- `xml_stylesheets/` - XSLT stylesheets for XML transformations
- `m11-templates/` - ICH M11 clinical trial templates
- `doc/` - Additional documentation
- `reports/` - Generated test/coverage reports

## Git Workflow

- Work only on `main` in this primary checkout; never create another branch or worktree.
- Preserve the main-only hooks and `core.hooksPath`; follow `C:\Projects\CLAUDE.md`.
- Run the configured checks, commit finished work on `main`, and push the existing `origin`.


