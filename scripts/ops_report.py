import json
import os
import sys
from pathlib import Path
from typing import Any, Dict


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from shared.atlas_core.config import env
from shared.atlas_core.service_client import request_json


GATEWAY_URL = env("API_GATEWAY_URL", "http://127.0.0.1:7000") or "http://127.0.0.1:7000"


def main() -> int:
    token = os.getenv("ATLAS_TOKEN")
    if len(sys.argv) > 1:
        token = sys.argv[1]
    if not token:
        print("Usage: ATLAS_TOKEN=<token> python3 scripts/ops_report.py", file=sys.stderr)
        print("   or: python3 scripts/ops_report.py <token>", file=sys.stderr)
        return 1

    status_code, payload = request_json(
        "GET",
        GATEWAY_URL,
        "/api/v1/platform/topology",
        headers={"Authorization": "Bearer {0}".format(token)},
    )
    if status_code >= 400:
        print(json.dumps(payload, indent=2), file=sys.stderr)
        return 1

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
