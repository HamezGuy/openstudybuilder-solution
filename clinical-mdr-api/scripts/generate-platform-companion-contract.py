"""Generate the additive platform companion contracts from their pydantic models.

One script for the three companion families that used to have a copy each
(generate-native-item-observation-contract.py, generate-selected-activity-item-
contract.py, generate-governed-item-association-contract.py); the copies
differed only in the model tuple and the file-name prefix.

    python scripts/generate-platform-companion-contract.py <family> [<family> ...]
    python scripts/generate-platform-companion-contract.py all --check

Families and the schemas they own under clinical_mdr_api/schemas/platform:

    native-item-observation    osb-native-item-observation-{request,response}-v1.schema.json
    selected-activity-item     osb-selected-activity-item-{request,response}-v1.schema.json
    governed-item-association  osb-governed-item-association-{review,read,response,record}-v1.schema.json

Historical evidence contracts are never rewritten; only these companion
schemas are. `--check` writes nothing and exits non-zero naming every schema
whose text differs from what its model generates now. The comparison is
newline-normalised (git stores these files with LF; a Windows checkout may
carry CRLF), exactly as the three former scripts compared.
"""

import argparse
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIRECTORY = ROOT / "clinical_mdr_api" / "schemas" / "platform"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

FAMILIES = {
    "native-item-observation": (
        "clinical_mdr_api.models.integrations.native_item_observation",
        (
            ("request", "NativeItemObservationRequest"),
            ("response", "NativeItemObservationResponse"),
        ),
    ),
    "selected-activity-item": (
        "clinical_mdr_api.models.integrations.selected_activity_item_observation",
        (
            ("request", "SelectedActivityItemRequest"),
            ("response", "SelectedActivityItemResponse"),
        ),
    ),
    "governed-item-association": (
        "clinical_mdr_api.models.integrations.governed_item_association",
        (
            ("review", "GovernedItemAssociationReview"),
            ("read", "GovernedItemAssociationRead"),
            ("response", "GovernedItemAssociationResponse"),
            ("record", "GovernedItemAssociationRecord"),
        ),
    ),
}


def render(model) -> str:
    value = {"$schema": JSON_SCHEMA_DIALECT, **model.model_json_schema()}
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def schema_path(family: str, name: str) -> Path:
    return SCHEMA_DIRECTORY / f"osb-{family}-{name}-v1.schema.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "family",
        nargs="+",
        choices=[*FAMILIES, "all"],
        help="companion family to (re)generate, or 'all'",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the schemas on disk match the models; write nothing",
    )
    args = parser.parse_args(argv)
    families = list(FAMILIES) if "all" in args.family else list(dict.fromkeys(args.family))

    sys.path.insert(0, str(ROOT))
    stale: list[str] = []
    for family in families:
        module_name, models = FAMILIES[family]
        module = importlib.import_module(module_name)
        for name, class_name in models:
            path = schema_path(family, name)
            encoded = render(getattr(module, class_name))
            if args.check:
                if not path.exists() or path.read_text(encoding="utf-8") != encoded:
                    stale.append(path.name)
            else:
                path.write_text(encoded, encoding="utf-8")
                print(f"wrote {path.relative_to(ROOT).as_posix()}")
    if stale:
        print("Stale generated schema: " + ", ".join(stale), file=sys.stderr)
        return 1
    if args.check:
        print(f"companion schemas current: {', '.join(families)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
