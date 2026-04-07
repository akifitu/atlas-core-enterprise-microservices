from concurrent.futures import ThreadPoolExecutor
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
HEALTH_ORDER = {"critical": 0, "at_risk": 1, "healthy": 2, "not_started": 3}


def internal_headers(actor: Dict[str, str], request_id: str) -> Dict[str, str]:
    return {
        "X-Tenant-ID": actor["tenant_id"],
        "X-User-ID": actor["user_id"],
        "X-User-Role": actor["role"],
        "X-Request-ID": request_id,
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


def build_project_summary(project: Dict[str, Any], headers: Dict[str, str], alerts_by_project: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
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

    project_alerts = alerts_by_project.get(project["id"], [])
    delivery_totals = delivery_payload["totals"]
    finance_totals = finance_payload["totals"]
    return {
        "project": project,
        "delivery": delivery_totals,
        "finance": finance_totals,
        "open_alerts": project_alerts,
        "health": derive_project_health(finance_totals, delivery_totals, project_alerts),
    }


def build_project_summaries(
    projects: List[Dict[str, Any]],
    headers: Dict[str, str],
    alerts_by_project: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(projects) or 1))) as executor:
        summaries = list(
            executor.map(
                lambda project: build_project_summary(project, headers, alerts_by_project),
                projects,
            )
        )
    summaries.sort(key=lambda item: (HEALTH_ORDER.get(item["health"], 99), item["project"]["name"]))
    return summaries


def aggregate_totals(projects_summary: List[Dict[str, Any]], open_alert_count: int) -> Dict[str, Any]:
    total_budget = round(sum(item["finance"]["budget_total"] for item in projects_summary), 2)
    total_spent = round(sum(item["finance"]["spent"] for item in projects_summary), 2)
    total_work_items = sum(item["delivery"]["count"] for item in projects_summary)
    total_blocked = sum(item["delivery"]["blocked"] for item in projects_summary)
    total_done = sum(item["delivery"]["done"] for item in projects_summary)
    completion_rate = round((total_done / total_work_items) * 100, 2) if total_work_items else 0.0
    budget_utilization = round((total_spent / total_budget) * 100, 2) if total_budget else 0.0
    health_distribution = {
        "critical": len([item for item in projects_summary if item["health"] == "critical"]),
        "at_risk": len([item for item in projects_summary if item["health"] == "at_risk"]),
        "healthy": len([item for item in projects_summary if item["health"] == "healthy"]),
        "not_started": len([item for item in projects_summary if item["health"] == "not_started"]),
    }
    return {
        "projects": len(projects_summary),
        "work_items": total_work_items,
        "blocked_work_items": total_blocked,
        "completion_rate": completion_rate,
        "budget_total": total_budget,
        "spent": total_spent,
        "budget_utilization_pct": budget_utilization,
        "open_alerts": open_alert_count,
        "health_distribution": health_distribution,
    }


def fetch_open_alerts(headers: Dict[str, str]) -> List[Dict[str, Any]]:
    alert_payload = require_success(
        *request_json(
            "GET",
            NOTIFICATION_SERVICE_URL,
            "/alerts?" + urlencode({"status": "open"}),
            headers=headers,
        ),
        dependency_name="notification-service",
    )
    return alert_payload["alerts"]


def map_alerts_by_project(open_alerts: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    alerts_by_project: Dict[str, List[Dict[str, Any]]] = {}
    for alert in open_alerts:
        alerts_by_project.setdefault(alert["project_id"], []).append(alert)
    return alerts_by_project


def build_portfolio_summary(
    portfolio_snapshot: Dict[str, Any],
    headers: Dict[str, str],
    alerts_by_project: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    project_summaries = build_project_summaries(portfolio_snapshot["projects"], headers, alerts_by_project)
    portfolio_alert_count = sum(len(item["open_alerts"]) for item in project_summaries)
    return {
        "portfolio": portfolio_snapshot["portfolio"],
        "projects": project_summaries,
        "totals": aggregate_totals(project_summaries, portfolio_alert_count),
    }


def fetch_portfolio_snapshot(portfolio_id: str, headers: Dict[str, str]) -> Dict[str, Any]:
    return require_success(
        *request_json(
            "GET",
            PORTFOLIO_SERVICE_URL,
            "/portfolios/{0}/snapshot".format(portfolio_id),
            headers=headers,
        ),
        dependency_name="portfolio-service",
    )


def executive_risk_view(portfolio_summaries: List[Dict[str, Any]], top_n: int) -> List[Dict[str, Any]]:
    risk_items = []
    for portfolio_summary in portfolio_summaries:
        portfolio = portfolio_summary["portfolio"]
        for project_summary in portfolio_summary["projects"]:
            risk_items.append(
                {
                    "portfolio_id": portfolio["id"],
                    "portfolio_name": portfolio["name"],
                    "project": project_summary["project"],
                    "health": project_summary["health"],
                    "blocked_work_items": project_summary["delivery"]["blocked"],
                    "budget_utilization_pct": project_summary["finance"]["utilization_pct"],
                    "open_alerts": len(project_summary["open_alerts"]),
                }
            )
    risk_items.sort(
        key=lambda item: (
            HEALTH_ORDER.get(item["health"], 99),
            -item["open_alerts"],
            -item["blocked_work_items"],
            -item["budget_utilization_pct"],
            item["project"]["name"],
        )
    )
    return risk_items[:top_n]


@app.route("GET", "/health")
def health(_: Request):
    return 200, {"status": "ok", "service": SERVICE_NAME}


@app.route("GET", "/dashboard")
def dashboard(request: Request):
    actor = require_actor(request)
    portfolio_id = request.query_value("portfolio_id")
    if not portfolio_id:
        raise AppError(400, "portfolio_id_query_parameter_required")

    headers = internal_headers(actor, request.request_id)
    portfolio_payload = fetch_portfolio_snapshot(portfolio_id, headers)
    open_alerts = fetch_open_alerts(headers)
    alerts_by_project = map_alerts_by_project(open_alerts)
    projects_summary = build_project_summaries(portfolio_payload["projects"], headers, alerts_by_project)
    totals = aggregate_totals(projects_summary, len(open_alerts))

    return 200, {
        "portfolio": portfolio_payload["portfolio"],
        "generated_at": utc_now(),
        "totals": totals,
        "projects": projects_summary,
    }


@app.route("GET", "/executive-summary")
def executive_summary(request: Request):
    actor = require_actor(request)
    try:
        top_n = max(1, min(20, int(request.query_value("top_n", "5") or "5")))
    except ValueError as exc:
        raise AppError(400, "invalid_top_n") from exc

    headers = internal_headers(actor, request.request_id)
    portfolio_list_payload = require_success(
        *request_json(
            "GET",
            PORTFOLIO_SERVICE_URL,
            "/portfolios",
            headers=headers,
        ),
        dependency_name="portfolio-service",
    )
    open_alerts = fetch_open_alerts(headers)
    alerts_by_project = map_alerts_by_project(open_alerts)

    portfolios = portfolio_list_payload["portfolios"]
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(portfolios) or 1))) as executor:
        portfolio_snapshots = list(
            executor.map(lambda portfolio: fetch_portfolio_snapshot(portfolio["id"], headers), portfolios)
        )

    portfolio_summaries = [
        build_portfolio_summary(portfolio_snapshot, headers, alerts_by_project)
        for portfolio_snapshot in portfolio_snapshots
    ]
    portfolio_summaries.sort(key=lambda item: item["portfolio"]["name"])

    all_project_summaries = [
        project_summary
        for portfolio_summary in portfolio_summaries
        for project_summary in portfolio_summary["projects"]
    ]
    totals = aggregate_totals(all_project_summaries, len(open_alerts))
    totals["portfolios"] = len(portfolio_summaries)
    top_risks = executive_risk_view(portfolio_summaries, top_n)

    return 200, {
        "generated_at": utc_now(),
        "totals": totals,
        "portfolios": portfolio_summaries,
        "top_risks": top_risks,
    }


if __name__ == "__main__":
    run_service(app, HOST or "127.0.0.1", PORT or 7006)
