"""Resolve consumers' schema references against the actual registered routes."""

import pytest
from fastapi import FastAPI

from clinical_mdr_api.routers.integrations.proposal_review import router

BASE_PATH = (
    "/integrations/proposal-reviews/{proposal_hash}/objects/"
    "{proposal_object_id}/item-associations"
)


@pytest.fixture
def association_openapi():
    app = FastAPI(separate_input_output_schemas=False)
    app.include_router(router, prefix="/integrations/proposal-reviews")
    return app.openapi()


def resolve_reference(document, reference):
    assert reference.startswith("#/"), f"Unexpected external reference: {reference}"
    current = document
    for segment in reference[2:].split("/"):
        key = segment.replace("~1", "/").replace("~0", "~")
        assert (
            isinstance(current, dict) and key in current
        ), f"Unresolvable generated OpenAPI reference: {reference}"
        current = current[key]
    return current


def assert_references_resolve(document, schema):
    seen = set()

    def visit(value):
        if isinstance(value, list):
            for entry in value:
                visit(entry)
        elif isinstance(value, dict):
            reference = value.get("$ref")
            if reference is not None:
                target = resolve_reference(document, reference)
                if reference not in seen:
                    seen.add(reference)
                    visit(target)
            for entry in value.values():
                visit(entry)

    visit(schema)
    return seen


@pytest.mark.parametrize("suffix", ["", "/current-observation"])
def test_registered_association_schema_references_resolve(association_openapi, suffix):
    operation = association_openapi["paths"][f"{BASE_PATH}{suffix}"]["post"]
    assert operation["requestBody"]["required"] is True
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["additionalProperties"] is False
    # Inspect all request and response references, including nested definitions
    # and recursively referenced components, as an OpenAPI consumer must.
    assert assert_references_resolve(association_openapi, operation)
    selector = resolve_reference(
        association_openapi, request_schema["properties"]["selector"]["$ref"]
    )
    assert {"selected", "csl"} <= set(selector["required"])
    for field in ("selected", "csl"):
        nested = resolve_reference(
            association_openapi, selector["properties"][field]["$ref"]
        )
        assert nested["additionalProperties"] is False
        assert nested["required"]
