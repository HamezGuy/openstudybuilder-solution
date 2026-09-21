"""Generate only additive native association contracts; historical schemas stay fixed."""
import json
from pathlib import Path
import sys
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from clinical_mdr_api.models.integrations.governed_item_association import (
    GovernedItemAssociationReview, GovernedItemAssociationRead, GovernedItemAssociationResponse, GovernedItemAssociationRecord)
for name,model in [("review",GovernedItemAssociationReview),("read",GovernedItemAssociationRead),
                   ("response",GovernedItemAssociationResponse),("record",GovernedItemAssociationRecord)]:
    value={"$schema":"https://json-schema.org/draft/2020-12/schema",**model.model_json_schema()}
    path=root/"clinical_mdr_api/schemas/platform"/f"osb-governed-item-association-{name}-v1.schema.json"
    content=json.dumps(value,indent=2,ensure_ascii=False)+"\n"
    if "--check" in sys.argv:
        if not path.exists() or path.read_text(encoding="utf-8") != content: raise SystemExit(f"Stale schema: {path.name}")
    else: path.write_text(content,encoding="utf-8")
