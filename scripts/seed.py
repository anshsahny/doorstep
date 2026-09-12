"""Write the fictional org, roster, volunteers and relief centres to DynamoDB (make seed)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from doorstep_agent.store_dynamo import DynamoBackend  # noqa: E402
from scripts.cloud.stack import outputs, session  # noqa: E402

if __name__ == "__main__":
    out = outputs()
    backend = DynamoBackend(out["TableName"], "juniper-court", client=session().client("dynamodb"))
    print(f"seeded {backend.seed_static(ROOT / 'data')} static rows into {out['TableName']}")
