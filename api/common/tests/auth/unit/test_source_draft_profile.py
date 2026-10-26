"""The broker's existing draft capabilities must pass the OSB token profile."""

# fixture values are plain JSON dicts pylint cannot infer
# pylint: disable=unsubscriptable-object

import ast
import re
from pathlib import Path

import pytest

from common.auth.models import AccessTokenClaims, validate_delegated_claims
from common.config import Settings


def broker_claims(capabilities: list[str]) -> AccessTokenClaims:
    return AccessTokenClaims.model_validate(
        {
            "iss": "https://command-center.example.test",
            "sub": "https://idp.example.test|reviewer",
            "aud": ["accuratrial-openstudybuilder"],
            "exp": 2_000_000_000,
            "iat": 1_999_999_900,
            "azp": "accuratrial-command-center",
            "client_id": "accuratrial-command-center",
            "tenant_id": "tenant-1",
            "study_ids": ["Study_1"],
            "roles": ["Study.Read"],
            "type": "access",
            "subject_type": "human",
            "human_subject": "https://idp.example.test|reviewer",
            "service_actor": "service:accuratrial-command-center",
            "act": {
                "sub": "service:accuratrial-command-center",
                "client_id": "accuratrial-command-center",
                "iss": "https://command-center.example.test",
            },
            "actor_chain": [
                {
                    "subject": "https://idp.example.test|reviewer",
                    "type": "human",
                    "issuer": "https://idp.example.test",
                },
                {
                    "subject": "service:accuratrial-command-center",
                    "type": "service",
                    "issuer": "https://command-center.example.test",
                },
            ],
            "idp_iss": "https://idp.example.test",
            "purpose": "interactive-domain-access",
            "capabilities": capabilities,
        }
    )


def validate(claims: AccessTokenClaims) -> None:
    fields = Settings.model_fields
    validate_delegated_claims(
        claims,
        exchanging_clients=set(
            fields["ENV_OAUTH_EXCHANGING_CLIENTS"].default.split(",")
        ),
        allowed_purposes=set(fields["ENV_OAUTH_ALLOWED_PURPOSES"].default.split(",")),
        allowed_capabilities=set(
            fields["ENV_OAUTH_ALLOWED_CAPABILITIES"].default.split(",")
        ),
        allowed_roles=set(fields["ENV_OAUTH_ALLOWED_ROLES"].default.split(",")),
    )


def test_broker_osb_profile_accepts_supported_draft_capabilities():
    # Current Command Center admin profile, including the source-draft routes.
    validate(
        broker_claims(
            [
                "study:read",
                "study:write",
                "candidate:read",
                "candidate:generate",
                "candidate:apply",
                "draft:stage",
                "draft:read",
                "package:release",
                "native-identity:bind",
                "native-identity:inventory",
            ]
        )
    )


@pytest.mark.parametrize("capability", ["draft:stage", "draft:read"])
def test_downscoped_draft_workflow_still_requires_the_delegated_profile(capability):
    claims = broker_claims([capability])
    claims.purpose = "workflow-orchestration"
    validate(claims)
    claims.tenant_id = None
    with pytest.raises(ValueError):
        validate(claims)


@pytest.mark.parametrize(
    "capability", ["*", "draft:*", "configuration:activate", "draft:delete"]
)
def test_draft_support_does_not_accept_wildcards_or_unrecognized_capabilities(
    capability,
):
    with pytest.raises(ValueError, match="Capability"):
        validate(broker_claims(["study:read", capability]))


# Command Center's OSB capabilities (server/platform-identity-core.js
# CAPABILITIES.osb). An interactive token for admin, data_manager or coordinator
# carries the oidc-bridge 1.21 set below (interactiveCapabilities). regimen:onboard
# is in CAPABILITIES.osb but WORKER_ONLY_CAPABILITIES: only a command worker or an
# explicit service grant carries it, under workflow-orchestration. It was in the
# interactive token only under CC 8d002f4, which live OSB refused whole (401).
COMMAND_CENTER_INTERACTIVE_OSB_CAPABILITIES = [
    "study:read",
    "study:write",
    "candidate:read",
    "candidate:generate",
    "candidate:apply",
    "draft:stage",
    "draft:read",
    "package:release",
    "native-identity:bind",
    "native-identity:inventory",
]
COMMAND_CENTER_WORKER_ONLY_OSB_CAPABILITIES = ["regimen:onboard"]

# Demanded by the governed item association routes, but in no allowlist and never
# issued by Command Center, so those routes are unreachable until owner decision
# OD-4 (OSB_PERMISSIONS.md OP-21) retires them or issues both capabilities.
KNOWN_UNISSUED_CAPABILITIES = {
    "governed-item-association:read",
    "governed-item-association:review",
}

API_ROOT = Path(__file__).resolve().parents[4]
PRINCIPAL_HELPERS = {"_platform_principal", "principal_for"}
CAPABILITY_SHAPE = re.compile(r"^[a-z][a-z0-9-]*:[a-z][a-z0-9-]*$")
# Names that hold a capability read from a stored record, not from code: the
# command receipt read requires the capability its command already demanded.
RECORDED_CAPABILITY_NAMES = {("native_identity.py", "target_capability")}


def default_capabilities() -> set[str]:
    return set(
        Settings.model_fields["ENV_OAUTH_ALLOWED_CAPABILITIES"].default.split(",")
    )


def worker_claims(capabilities: list[str]) -> AccessTokenClaims:
    worker = "service:accuratrial-control-worker"
    return AccessTokenClaims.model_validate(
        {
            "iss": "https://command-center.example.test",
            "sub": worker,
            "aud": ["accuratrial-openstudybuilder"],
            "exp": 2_000_000_000,
            "iat": 1_999_999_900,
            "azp": "accuratrial-control-worker",
            "client_id": "accuratrial-control-worker",
            "tenant_id": "tenant-1",
            "study_ids": ["Study_1"],
            "roles": ["service"],
            "type": "access",
            "subject_type": "service",
            "service_actor": worker,
            "actor_chain": [
                {
                    "subject": worker,
                    "type": "service",
                    "issuer": "https://command-center.example.test",
                }
            ],
            "purpose": "workflow-orchestration",
            "capabilities": capabilities,
        }
    )


def test_command_center_full_osb_profile_validates_against_the_defaults():
    validate(broker_claims(COMMAND_CENTER_INTERACTIVE_OSB_CAPABILITIES))


def test_regimen_onboard_worker_token_validates_against_the_defaults():
    validate(worker_claims(COMMAND_CENTER_WORKER_ONLY_OSB_CAPABILITIES))


def test_default_profile_is_exactly_what_command_center_can_issue():
    # A default capability that Command Center never issues admits nothing, and
    # one it issues outside the default refuses the whole token. A new capability
    # goes into this list first and into Command Center second (OP-13).
    assert default_capabilities() == set(
        COMMAND_CENTER_INTERACTIVE_OSB_CAPABILITIES
    ) | set(COMMAND_CENTER_WORKER_ONLY_OSB_CAPABILITIES)
    assert "platform-command:execute" not in default_capabilities()
    assert not KNOWN_UNISSUED_CAPABILITIES & default_capabilities()


def test_env_example_lists_the_default_capabilities():
    lines = (API_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    (line,) = [
        value for value in lines if value.startswith("OIDC_ALLOWED_CAPABILITIES=")
    ]
    listed = line.split("=", 1)[1].strip('"').split(",")
    assert set(listed) == default_capabilities()


def _module_constant(module: str, name: str) -> str:
    source = API_ROOT / (module.replace(".", "/") + ".py")
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
            and isinstance(node.value, ast.Constant)
        ):
            return node.value.value
    raise AssertionError(f"{module}.{name} is not a module-level string constant")


def _is_capability_set(node: ast.AST) -> bool:
    """`caller.capabilities`, `review["capabilities"]`, or set() of either."""
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") in {
        "set",
        "frozenset",
    }:
        return len(node.args) == 1 and _is_capability_set(node.args[0])
    if isinstance(node, ast.Attribute):
        return node.attr == "capabilities"
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == "capabilities"
    )


def _capability_constants(node: ast.AST) -> set[str]:
    return {
        value.value
        for value in ast.walk(node)
        if isinstance(value, ast.Constant)
        and isinstance(value.value, str)
        and CAPABILITY_SHAPE.fullmatch(value.value)
    }


def _resolve_capability_name(name, functions, path, imported) -> set[str]:
    if (path.name, name.id) in RECORDED_CAPABILITY_NAMES:
        return set()
    for function in reversed(functions):
        constants: set[str] = set()
        for node in ast.walk(function):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name.id
                for target in node.targets
            ):
                constants |= _capability_constants(node.value)
        if constants:
            return constants
        # A principal helper's own parameter: its call sites are scanned instead.
        if function.name in PRINCIPAL_HELPERS and name.id in {
            argument.arg for argument in function.args.args
        }:
            return set()
    if name.id in imported:
        return {_module_constant(*imported[name.id])}
    raise AssertionError(
        f"{path.name}:{name.lineno} compares capabilities with unresolved {name.id}"
    )


def _compared_capabilities(tree: ast.Module, path: Path, imported) -> set[str]:
    """Capabilities a module tests a token's capabilities for directly:
    `"x" in caller.capabilities`, `{...} <= caller.capabilities`, or a name
    assigned capability strings in the same function, or imported."""
    pending: list = [(node, ()) for node in tree.body]
    found: set[str] = set()
    while pending:
        node, functions = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions = (*functions, node)
        operands = (
            [node.left, *node.comparators] if isinstance(node, ast.Compare) else []
        )
        if any(_is_capability_set(operand) for operand in operands):
            for operand in operands:
                if _is_capability_set(operand):
                    continue
                if isinstance(operand, ast.Name):
                    found |= _resolve_capability_name(
                        operand, functions, path, imported
                    )
                    continue
                constants = _capability_constants(operand)
                if not constants:
                    raise AssertionError(
                        f"{path.name}:{node.lineno} compares capabilities"
                        " with an unresolved value"
                    )
                found |= constants
        pending.extend((child, functions) for child in ast.iter_child_nodes(node))
    return found


def _demanded_capabilities(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.asname or alias.name: (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
    }
    found = _compared_capabilities(tree, path, imported)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in PRINCIPAL_HELPERS:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            found.add(arg.value)
        elif isinstance(arg, ast.Name) and arg.id in imported:
            found.add(_module_constant(*imported[arg.id]))
        else:
            raise AssertionError(
                f"{path.name}:{node.lineno} demands an unresolved capability"
            )
    return found


def _demanded_in(*parts: str) -> set[str]:
    folder = API_ROOT.joinpath("clinical_mdr_api", *parts)
    return set().union(
        *(_demanded_capabilities(path) for path in sorted(folder.glob("*.py")))
    )


def test_every_capability_a_router_demands_is_in_the_default_profile():
    demanded = _demanded_in("routers", "integrations")
    # The scan must see the known demands, or it proves nothing.
    assert {
        "draft:stage",
        "draft:read",
        "package:release",
        "regimen:onboard",
        "native-identity:inventory",
    } <= demanded
    assert demanded - default_capabilities() == set()


def test_every_capability_an_integration_service_demands_is_known():
    demanded = _demanded_in("services", "integrations")
    # The scan must see the known demands, or it proves nothing.
    assert {
        "candidate:read",
        "study:read",
        "study:write",
        *KNOWN_UNISSUED_CAPABILITIES,
    } <= demanded
    # Only the governed item association capabilities are outside the default,
    # and they stay outside it until OD-4.
    assert demanded - default_capabilities() == KNOWN_UNISSUED_CAPABILITIES
