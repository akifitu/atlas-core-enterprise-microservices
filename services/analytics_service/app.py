from typing import Any, Dict, List
from urllib.parse import urlencode

from shared.atlas_core.config import env, service_url, utc_now
from shared.atlas_core.context import require_actor
from shared.atlas_core.http import AppError, Request, ServiceApp, run_service
from shared.atlas_core.service_client import request_json


SERVICE_NAME = "analytics-service"
HOST = env("ANALYTICS_SERVICE_HOST", "127.0.0.1")
PORT = env("ANALYTICS_SERVICE_PORT", 7006, int)
PORTFOLIO_SERVICE_URL = service_url("portfolio-service", 7002)
DELIVERY_SERVICE_URL = service_url("delivery-service", 7003)
FINANCE_SERVICE_URL = service_url("finance-service", 7004)
NOTIFICATION_SERVICE_URL = service_url("notification-service", 7005)

app = ServiceApp(SERVICE_NAME)


def internal_headers(actor: Dict[str, str]) -> Dict[str, str]:
    return {
        "X-Tenant-ID": actor["tenant_id"],
        "X-User-ID": actor["user_id"],
        "X-User-Role": actor["role"],
    }


def require_success(status_code: int, payload: Any, dependency_name: str) -> Any:
    if status_code >= 400:
        raise AppError(
            502,
            "dependency_call_failed",
            {"dependency": dependency_name, "status_code": status_code, "payload": payload},
        )
    return payload


def derive_project_health(finance_totals: Dict[str, Any], delivery_totals: Dict[str, Any], open_alerts: List[Dict[str, Any]]) -> str:
    if any(alert["severity"] == "critical" for alert in open_alerts):
        return "critical"
    if delivery_totals["blocked"] > 0 or finance_totals["utilization_pct"] >= 85 or open_alerts:
        return "at_risk"
    if delivery_totals["count"] == 0:
        return "not_started"
    return "healthy"


@app.route("GET", "/health")
def health(_: Request):
    return 200, {"status": "ok", "service": SERVICE_NAME}


@app.route("GET", "/dashboard")
def dashboard(request: Request):
    actor = require_actor(request)
    portfolio_id = request.query_value("portfolio_id")
    if not portfolio_id:
        raise AppError(400, "portfolio_id_query_parameter_required")

    headers = internal_headers(actor)
    portfolio_payload = require_success(
        *request_json(
            "GET",
            PORTFOLIO_SERVICE_URL,
            "/portfolios/{0}/snapshot".format(portfolio_id),
            headers=headers,
        ),
        dependency_name="portfolio-service",
    )
    alert_payload = require_success(
        *request_json(
            "GET",
            NOTIFICATION_SERVICE_URL,
            "/alerts?" + urlencode({"status": "open"}),
            headers=headers,
        ),
        dependency_name="notification-service",
    )

    open_alerts = alert_payload["alerts"]
    projects_summary = []
    total_budget = 0.0
    total_spent = 0.0
    total_work_items = 0
    total_blocked = 0
    total_done = 0

    for project in portfolio_payload["projects"]:
        delivery_payload = require_success(
            *request_json(
                "GET",
                DELIVERY_SERVICE_URL,
                "/projects/{0}/summary".format(project["id"]),
                headers=headers,
            ),
            dependency_name="delivery-service",
        )
        finance_payload = require_success(
            *request_json(
                "GET",
                FINANCE_SERVICE_URL,
                "/projects/{0}/status".format(project["id"]),
                headers=headers,
            ),
            dependency_name="finance-service",
        )

        project_alerts = [alert for alert in open_alerts if alert["project_id"] == project["id"]]
        delivery_totals = delivery_payload["totals"]
        finance_totals = finance_payload["totals"]

        total_budget += finance_totals["budget_total"]
        total_spent += finance_totals["spent"]
        total_work_items += delivery_totals["count"]
        total_blocked += delivery_totals["blocked"]
        total_done += delivery_totals["done"]

        projects_summary.append(
            {
                "project": project,
                "delivery": delivery_totals,
                "finance": finance_totals,
                "open_alerts": project_alerts,
                "health": derive_project_health(finance_totals, delivery_totals, project_alerts),
            }
        )

    completion_rate = round((total_done / total_work_items) * 100, 2) if total_work_items else 0.0
    budget_utilization = round((total_spent / total_budget) * 100, 2) if total_budget else 0.0

    return 200, {
        "portfolio": portfolio_payload["portfolio"],
        "generated_at": utc_now(),
        "totals": {
            "projects": len(projects_summary),
            "work_items": total_work_items,
            "blocked_work_items": total_blocked,
            "completion_rate": completion_rate,
            "budget_total": round(total_budget, 2),
            "spent": round(total_spent, 2),
            "budget_utilization_pct": budget_utilization,
            "open_alerts": len(open_alerts),
        },
        "projects": projects_summary,
    }


if __name__ == "__main__":
    run_service(app, HOST or "127.0.0.1", PORT or 7006)
