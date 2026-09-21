"""Generate only the new selected-activity companion contracts."""
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from clinical_mdr_api.models.integrations.selected_activity_item_observation import SelectedActivityItemRequest, SelectedActivityItemResponse

for name, model in (("request", SelectedActivityItemRequest), ("response", SelectedActivityItemResponse)):
    value = {"$schema": "https://json-schema.org/draft/2020-12/schema", **model.model_json_schema()}
    path = root / "clinical_mdr_api/schemas/platform" / f"osb-selected-activity-item-{name}-v1.schema.json"
    text = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            raise SystemExit(f"Stale generated schema: {path.name}")
    else:
        path.write_text(text, encoding="utf-8")
