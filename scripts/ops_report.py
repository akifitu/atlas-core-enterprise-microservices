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


def fetch_report(token: str, path: str) -> Dict[str, Any]:
    status_code, payload = request_json(
        "GET",
        GATEWAY_URL,
        path,
        headers={"Authorization": "Bearer {0}".format(token)},
    )
    if status_code >= 400:
        raise RuntimeError(json.dumps(payload, indent=2))
    return payload


def main() -> int:
    token = os.getenv("ATLAS_TOKEN")
    report_name = "overview"
    if len(sys.argv) > 1:
        if sys.argv[1] in ("overview", "topology", "alert-summary", "audit-summary"):
            report_name = sys.argv[1]
            if len(sys.argv) > 2:
                token = sys.argv[2]
        else:
            token = sys.argv[1]
    if len(sys.argv) > 2 and sys.argv[1] not in ("overview", "topology", "alert-summary", "audit-summary"):
        report_name = sys.argv[2]
    if not token:
        print("Usage: ATLAS_TOKEN=<token> python3 scripts/ops_report.py [overview|topology|alert-summary|audit-summary]", file=sys.stderr)
        print("   or: python3 scripts/ops_report.py [overview|topology|alert-summary|audit-summary] <token>", file=sys.stderr)
        return 1

    try:
        if report_name == "topology":
            payload = fetch_report(token, "/api/v1/platform/topology")
        elif report_name == "alert-summary":
            payload = fetch_report(token, "/api/v1/platform/alert-summary")
        elif report_name == "audit-summary":
            payload = fetch_report(token, "/api/v1/platform/audit-summary")
        else:
            payload = {
                "topology": fetch_report(token, "/api/v1/platform/topology"),
                "alert_summary": fetch_report(token, "/api/v1/platform/alert-summary"),
                "audit_summary": fetch_report(token, "/api/v1/platform/audit-summary"),
            }
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
