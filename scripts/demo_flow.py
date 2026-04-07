import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from shared.atlas_core.config import env
from shared.atlas_core.service_client import request_json


GATEWAY_URL = env("API_GATEWAY_URL", "http://127.0.0.1:7000")


def gateway_request(
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
):
    headers = {}
    if token:
        headers["Authorization"] = "Bearer {0}".format(token)
    status_code, response_payload = request_json(method, GATEWAY_URL or "http://127.0.0.1:7000", path, payload, headers)
    if status_code >= 400:
        raise RuntimeError("Gateway request failed: {0} {1} -> {2} {3}".format(method, path, status_code, response_payload))
    return response_payload


def main() -> int:
    suffix = str(uuid.uuid4())[:8]
    bootstrap = gateway_request(
        "POST",
        "/api/v1/identity/bootstrap-admin",
        {
            "tenant_name": "Atlas Global Holdings {0}".format(suffix),
            "tenant_slug": "atlas-global-{0}".format(suffix.lower()),
            "admin_email": "admin@atlascore.local",
            "admin_password": "StrongPass!123",
            "admin_name": "Atlas Admin",
        },
    )
    token = bootstrap["token"]

    created_user = gateway_request(
        "POST",
        "/api/v1/identity/users",
        {
            "email": "finance.lead@atlascore.local",
            "password": "StrongPass!123",
            "display_name": "Finance Lead",
            "role": "finance_manager",
        },
        token,
    )

    portfolio = gateway_request(
        "POST",
        "/api/v1/portfolio/portfolios",
        {"name": "EMEA Strategic Transformation", "status": "active"},
        token,
    )["portfolio"]

    project_alpha = gateway_request(
        "POST",
        "/api/v1/portfolio/portfolios/{0}/projects".format(portfolio["id"]),
        {
            "name": "ERP Modernization",
            "code": "ERP-{0}".format(suffix[:4].upper()),
            "status": "active",
            "start_date": "2026-04-01",
            "target_date": "2026-09-30",
        },
        token,
    )["project"]

    project_beta = gateway_request(
        "POST",
        "/api/v1/portfolio/portfolios/{0}/projects".format(portfolio["id"]),
        {
            "name": "Supply Chain AI Assistant",
            "code": "SCAI-{0}".format(suffix[4:].upper()),
            "status": "active",
            "start_date": "2026-04-15",
            "target_date": "2026-12-20",
        },
        token,
    )["project"]

    blocked_item = gateway_request(
        "POST",
        "/api/v1/delivery/projects/{0}/work-items".format(project_alpha["id"]),
        {
            "title": "Vendor API contract alignment",
            "priority": "critical",
            "assignee": "Platform Enablement",
            "due_date": "2026-05-05",
        },
        token,
    )["work_item"]

    gateway_request(
        "POST",
        "/api/v1/delivery/projects/{0}/work-items".format(project_beta["id"]),
        {
            "title": "Pilot warehouse assistant rollout",
            "priority": "high",
            "assignee": "ML Ops Squad",
            "due_date": "2026-05-12",
        },
        token,
    )

    gateway_request(
        "PATCH",
        "/api/v1/delivery/work-items/{0}/status".format(blocked_item["id"]),
        {
            "status": "blocked",
            "blocked_reason": "Enterprise vendor has not finalized SSO scopes",
        },
        token,
    )

    gateway_request(
        "POST",
        "/api/v1/finance/projects/{0}/budget".format(project_alpha["id"]),
        {"total_budget": 150000, "currency": "EUR"},
        token,
    )
    gateway_request(
        "POST",
        "/api/v1/finance/projects/{0}/expenses".format(project_alpha["id"]),
        {"amount": 90000, "category": "integration_partners"},
        token,
    )
    gateway_request(
        "POST",
        "/api/v1/finance/projects/{0}/expenses".format(project_alpha["id"]),
        {"amount": 70000, "category": "change_management"},
        token,
    )

    time.sleep(0.2)

    alerts = gateway_request("GET", "/api/v1/notifications/alerts?status=open", token=token)["alerts"]
    dashboard = gateway_request(
        "GET",
        "/api/v1/analytics/dashboard?portfolio_id={0}".format(portfolio["id"]),
        token=token,
    )

    print(
        json.dumps(
            {
                "tenant": bootstrap["tenant"],
                "admin_user": bootstrap["user"],
                "created_user": created_user["user"],
                "portfolio": portfolio,
                "projects": [project_alpha, project_beta],
                "open_alerts": alerts,
                "dashboard": dashboard,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
