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
REPORT_NAMES = {
    "overview",
    "control-room",
    "topology",
    "alert-summary",
    "audit-summary",
    "audit-export",
    "audit-retention-dry-run",
    "audit-retention-apply",
}
RETENTION_REPORTS = {"audit-retention-dry-run", "audit-retention-apply"}


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


def post_report(token: str, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    status_code, response_payload = request_json(
        "POST",
        GATEWAY_URL,
        path,
        payload=payload,
        headers={"Authorization": "Bearer {0}".format(token)},
    )
    if status_code >= 400:
        raise RuntimeError(json.dumps(response_payload, indent=2))
    return response_payload


def parse_cli_args(args: Any, env_token: Any, env_retention_days: Any) -> Dict[str, Any]:
    token = env_token
    report_name = "overview"
    retention_days = int(env_retention_days)

    if args:
        if args[0] in REPORT_NAMES:
            report_name = args[0]
            remaining = args[1:]
            if report_name in RETENTION_REPORTS:
                if len(remaining) == 1:
                    if remaining[0].isdigit():
                        retention_days = int(remaining[0])
                    else:
                        token = remaining[0]
                elif len(remaining) >= 2:
                    token = remaining[0]
                    retention_days = int(remaining[1])
            elif remaining:
                token = remaining[0]
        else:
            token = args[0]
            if len(args) > 1:
                report_name = args[1]
            if report_name in RETENTION_REPORTS and len(args) > 2:
                retention_days = int(args[2])

    return {
        "token": token,
        "report_name": report_name,
        "retention_days": retention_days,
    }


def main() -> int:
    parsed_args = parse_cli_args(
        sys.argv[1:],
        os.getenv("ATLAS_TOKEN"),
        os.getenv("RETENTION_DAYS", "30"),
    )
    token = parsed_args["token"]
    report_name = parsed_args["report_name"]
    retention_days = parsed_args["retention_days"]
    if not token:
        print(
            "Usage: ATLAS_TOKEN=<token> python3 scripts/ops_report.py [overview|control-room|topology|alert-summary|audit-summary|audit-export|audit-retention-dry-run|audit-retention-apply] [retention_days]",
            file=sys.stderr,
        )
        print(
            "   or: python3 scripts/ops_report.py [overview|control-room|topology|alert-summary|audit-summary|audit-export|audit-retention-dry-run|audit-retention-apply] <token> [retention_days]",
            file=sys.stderr,
        )
        return 1

    try:
        if report_name == "control-room":
            payload = fetch_report(token, "/api/v1/platform/control-room?top_n=5")
        elif report_name == "topology":
            payload = fetch_report(token, "/api/v1/platform/topology")
        elif report_name == "alert-summary":
            payload = fetch_report(token, "/api/v1/platform/alert-summary")
        elif report_name == "audit-summary":
            payload = fetch_report(token, "/api/v1/platform/audit-summary")
        elif report_name == "audit-export":
            payload = fetch_report(token, "/api/v1/platform/audit-export?limit=200")
        elif report_name == "audit-retention-dry-run":
            payload = post_report(
                token,
                "/api/v1/platform/audit-retention",
                {"retention_days": retention_days, "dry_run": True},
            )
        elif report_name == "audit-retention-apply":
            payload = post_report(
                token,
                "/api/v1/platform/audit-retention",
                {"retention_days": retention_days, "dry_run": False},
            )
        else:
            control_room = fetch_report(token, "/api/v1/platform/control-room?top_n=5")
            payload = {
                "topology": control_room["topology"],
                "alert_summary": {"summary": control_room["alert_summary"]},
                "audit_summary": {"summary": control_room["audit_summary"]},
                "selected_portfolio_id": control_room["selected_portfolio_id"],
                "selection_mode": control_room["selection_mode"],
            }
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
