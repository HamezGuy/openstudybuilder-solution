"""The governed Command Center command osb.regimen.onboard, executed natively.

Command Center (8d002f4) admits the command with three versioned contracts,
held here as byte copies: OsbRegimenOnboardCommandInputV1 (the OSB study and
the regimen as StudyRegimenCreateInput without its two live preconditions),
OsbRegimenOnboardExpectedStateV1 (the draft version token and the current
authored protocol document hash) and OsbRegimenOnboardEffectV1 (the native
UIDs created per request-local key).

This module only translates. The study must be the command's single active
OSB binding and in the worker token's study scope. Both preconditions travel
into the existing native regimen service, which checks them under its study
writer lock in the command's transaction; a mismatch writes nothing and is
terminal. It records an authored design only; no approval, allocation or
execution is implied.
"""

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, NoReturn

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
)
from clinical_mdr_api.generated.platform_contracts.platform_command_v1 import (
    PlatformCommandError,
)
from clinical_mdr_api.models.study_selections.study_regimen import (
    StudyRegimenCreated,
    StudyRegimenCreateInput,
)
from clinical_mdr_api.services.integrations.candidate_set import active_osb_binding
from clinical_mdr_api.services.studies.study_regimen import StudyRegimenService
from common.exceptions import AlreadyExistsException, MDRApiBaseException

ACTION = "osb.regimen.onboard"
CAPABILITY = "regimen:onboard"
EFFECT_CONTRACT = "OsbRegimenOnboardEffectV1@1.0.0"
CREATED_KINDS = (
    "arms",
    "branch_arms",
    "cohorts",
    "elements",
    "compounds",
    "compound_dosings",
    "design_cells",
)
SCHEMA_DIRECTORY = Path(__file__).resolve().parents[2] / "generated/platform_contracts"
SCHEMAS = {
    "input": "osb-regimen-onboard-command-input-v1.schema.json",
    "expected": "osb-regimen-onboard-expected-state-v1.schema.json",
    "effect": "osb-regimen-onboard-effect-v1.schema.json",
}


def _refuse(code: str, message: str, status_code: int = 422) -> NoReturn:
    raise PlatformCommandError(code, message, status_code)


@lru_cache(maxsize=len(SCHEMAS))
def _validator(kind: str) -> Draft202012Validator:
    formats = FormatChecker()
    if "date-time" not in formats.checkers:
        _refuse(
            "OSB_REGIMEN_SCHEMA_FORMATS_UNAVAILABLE",
            "The date-time format validator is unavailable.",
            503,
        )
    schema = json.loads((SCHEMA_DIRECTORY / SCHEMAS[kind]).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=formats)


def check_contract(kind: str, value: Any, code: str) -> None:
    """Validate one value against its Command Center contract; report the first error."""
    errors = sorted(
        _validator(kind).iter_errors(value), key=lambda item: list(item.path)
    )
    if errors:
        location = "/".join(str(part) for part in errors[0].absolute_path) or "$"
        _refuse(code, f"{SCHEMAS[kind]} refuses {location}: {errors[0].message}")


def native_request(
    input_payload: dict[str, Any], expected_state: dict[str, Any]
) -> StudyRegimenCreateInput:
    """The exact native request: the regimen plus both reviewed preconditions.

    Same mapping as Command Center's nativeRegimenRequest: studyVersion becomes
    expected_study_version, and the document hash becomes
    protocol_documents.expected_content_hash when the regimen saves documents.
    """
    regimen = deepcopy(input_payload["regimen"])
    documents = regimen.pop("protocol_documents", None)
    values: dict[str, Any] = {
        "expected_study_version": expected_state["studyVersion"],
        **regimen,
    }
    if documents is not None:
        values["protocol_documents"] = {
            **documents,
            "expected_content_hash": expected_state["protocolDocumentsContentHash"],
        }
    try:
        return StudyRegimenCreateInput(**values)
    except ValidationError as error:
        first = error.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "regimen"
        _refuse(
            "OSB_REGIMEN_INPUT_INVALID",
            f"StudyRegimenCreateInput refuses {location}: {first['msg']}",
        )


def regimen_effect(
    identity: dict[str, Any],
    created: StudyRegimenCreated,
    documents_hash: str | None,
) -> dict[str, Any]:
    effect = {
        "contractVersion": EFFECT_CONTRACT,
        "osbStudyIdentity": identity,
        "studyVersion": created.study_version,
        "created": {kind: dict(getattr(created, kind)) for kind in CREATED_KINDS},
        "lifecycleEventUids": list(created.lifecycle_event_uids),
        "protocolDocumentsContentHash": documents_hash,
    }
    # An effect this service cannot state in the contract is a defect here; the
    # error rolls the regimen back.
    check_contract("effect", effect, "OSB_REGIMEN_EFFECT_INVALID")
    return effect


def onboard_regimen(
    command: dict[str, Any], *, tenant_id: str, study_ids: set[str] | tuple[str, ...]
) -> dict[str, Any]:
    """Execute one admitted command inside the platform command transaction."""
    if command.get("action") != ACTION or command.get("targetCapability") != CAPABILITY:
        _refuse(
            "OSB_REGIMEN_COMMAND_UNSUPPORTED",
            "Only osb.regimen.onboard is served here.",
        )
    input_payload = command.get("inputPayload")
    expected_state = command.get("expectedTargetState")
    check_contract("input", input_payload, "OSB_REGIMEN_INPUT_INVALID")
    # The contract requires an object; this narrows the type for the reader.
    assert isinstance(input_payload, dict)
    if expected_state is None:
        _refuse(
            "OSB_REGIMEN_EXPECTED_STATE_REQUIRED",
            "osb.regimen.onboard requires its live expected target state.",
        )
    check_contract("expected", expected_state, "OSB_REGIMEN_EXPECTED_STATE_INVALID")
    identity = input_payload["osbStudyIdentity"]
    if canonical_json(identity) != canonical_json(expected_state["osbStudyIdentity"]):
        _refuse(
            "OSB_REGIMEN_IDENTITY_MISMATCH",
            "The regimen and its expected state must name the same OSB study.",
        )
    study_uid = identity["nativeIdentity"]
    binding = active_osb_binding(tenant_id, command["platformStudyId"])
    if binding["nativeIdentity"] != study_uid or study_uid not in set(study_ids):
        _refuse(
            "OSB_REGIMEN_STUDY_SCOPE_DENIED",
            "The study is not the platform study's active OSB binding in this token's scope.",
            403,
        )
    request = native_request(input_payload, expected_state)
    expected_hash = expected_state["protocolDocumentsContentHash"]
    try:
        created = StudyRegimenService().create(
            study_uid, request, expected_documents_hash=expected_hash
        )
    except AlreadyExistsException as error:
        _refuse("OSB_REGIMEN_EXPECTED_STATE_MISMATCH", str(error.msg), 409)
    except MDRApiBaseException as error:
        if error.status_code >= 500:
            raise
        _refuse("OSB_REGIMEN_NATIVE_REFUSED", str(error.msg))
    # Without documents the bundle is unchanged: the hash just checked equal.
    documents_hash = (
        created.protocol_documents_content_hash
        if request.protocol_documents is not None
        else expected_hash
    )
    effect = regimen_effect(identity, created, documents_hash)
    return {
        "status": "succeeded",
        "targetIdentity": study_uid,
        "targetVersion": created.study_version,
        "targetState": {
            "studyVersion": created.study_version,
            "protocolDocumentsContentHash": documents_hash,
        },
        "conservationCounts": {
            **{kind: len(effect["created"][kind]) for kind in CREATED_KINDS},
            "lifecycle_events": len(effect["lifecycleEventUids"]),
        },
        "effectPayload": effect,
    }
