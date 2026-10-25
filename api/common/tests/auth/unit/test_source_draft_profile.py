"""The broker's existing draft capabilities must pass the OSB token profile."""

# fixture values are plain JSON dicts pylint cannot infer
# pylint: disable=unsubscriptable-object

import ast
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


# Command Center's interactive OSB profile (server/platform-identity-core.js
# CAPABILITIES.osb), held by admin, data_manager and coordinator since CC 8d002f4.
COMMAND_CENTER_OSB_CAPABILITIES = [
    "study:read",
    "study:write",
    "candidate:read",
    "candidate:generate",
    "candidate:apply",
    "draft:stage",
    "draft:read",
    "package:release",
    "regimen:onboard",
    "native-identity:bind",
    "native-identity:inventory",
]

API_ROOT = Path(__file__).resolve().parents[4]
PRINCIPAL_HELPERS = {"_platform_principal", "principal_for"}


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
    validate(broker_claims(COMMAND_CENTER_OSB_CAPABILITIES))


def test_regimen_onboard_worker_token_validates_against_the_defaults():
    validate(worker_claims(["regimen:onboard"]))


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


def _demanded_capabilities(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.asname or alias.name: (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
    }
    found: set[str] = set()
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


def test_every_capability_a_router_demands_is_in_the_default_profile():
    routers = API_ROOT / "clinical_mdr_api" / "routers" / "integrations"
    demanded = set().union(
        *(_demanded_capabilities(path) for path in sorted(routers.glob("*.py")))
    )
    # The scan must see the known demands, or it proves nothing.
    assert {
        "draft:stage",
        "draft:read",
        "package:release",
        "regimen:onboard",
    } <= demanded
    assert demanded - default_capabilities() == set()
