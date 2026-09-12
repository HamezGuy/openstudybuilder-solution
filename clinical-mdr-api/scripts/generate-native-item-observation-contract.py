"""Generate the additive companion schemas; does not edit old evidence contracts."""
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from clinical_mdr_api.models.integrations.native_item_observation import (
    NativeItemObservationRequest, NativeItemObservationResponse,
)

for name, model in (("request", NativeItemObservationRequest), ("response", NativeItemObservationResponse)):
    value = {"$schema": "https://json-schema.org/draft/2020-12/schema", **model.model_json_schema()}
    path = root / "clinical_mdr_api/schemas/platform" / f"osb-native-item-observation-{name}-v1.schema.json"
    encoded = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        if not path.exists() or path.read_text(encoding="utf-8") != encoded:
            raise SystemExit(f"Stale generated schema: {path.name}")
    else:
        path.write_text(encoded, encoding="utf-8")
