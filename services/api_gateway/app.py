from typing import Any, Dict, Optional
from urllib.parse import quote, urlencode

from shared.atlas_core.config import env, service_url
from shared.atlas_core.http import AppError, Request, ServiceApp, run_service
from shared.atlas_core.security import read_bearer_token
from shared.atlas_core.service_client import request_json


SERVICE_NAME = "api-gateway"
HOST = env("API_GATEWAY_HOST", "127.0.0.1")
PORT = env("API_GATEWAY_PORT", 7000, int)
IDENTITY_SERVICE_URL = service_url("identity-service", 7001)
PORTFOLIO_SERVICE_URL = service_url("portfolio-service", 7002)
DELIVERY_SERVICE_URL = service_url("delivery-service", 7003)
FINANCE_SERVICE_URL = service_url("finance-service", 7004)
NOTIFICATION_SERVICE_URL = service_url("notification-service", 7005)
ANALYTICS_SERVICE_URL = service_url("analytics-service", 7006)

app = ServiceApp(SERVICE_NAME)


def authenticate(request: Request) -> Dict[str, Any]:
    token = read_bearer_token(request.header("authorization"))
    if not token:
        raise AppError(401, "bearer_token_required")

    status_code, payload = request_json(
        "GET",
        IDENTITY_SERVICE_URL,
        "/validate?token={0}".format(quote(token)),
    )
    if status_code >= 400:
        raise AppError(401, "invalid_token", {"identity_response": payload})
    return payload["context"]


def proxy_request(
    request: Request,
    service_url_value: str,
    internal_path: str,
    authenticated: bool = True,
) -> Any:
    headers: Dict[str, str] = {}
    if authenticated:
        context = authenticate(request)
        headers.update(
            {
                "X-Tenant-ID": context["tenant_id"],
                "X-User-ID": context["user_id"],
                "X-User-Role": context["role"],
            }
        )

    query_suffix = ""
    if request.query:
        query_suffix = "?" + urlencode(request.query, doseq=True)

    status_code, payload = request_json(
        request.method,
        service_url_value,
        internal_path + query_suffix,
        payload=request.body if isinstance(request.body, dict) else None,
        headers=headers,
    )
    return status_code, payload


@app.route("GET", "/health")
def health(_: Request):
    return 200, {"status": "ok", "service": SERVICE_NAME}


@app.route("POST", "/api/v1/identity/bootstrap-admin")
def bootstrap_admin(request: Request):
    return proxy_request(request, IDENTITY_SERVICE_URL, "/bootstrap-admin", authenticated=False)


@app.route("POST", "/api/v1/identity/sessions")
def create_session(request: Request):
    return proxy_request(request, IDENTITY_SERVICE_URL, "/sessions", authenticated=False)


@app.route("POST", "/api/v1/identity/users")
def create_user(request: Request):
    return proxy_request(request, IDENTITY_SERVICE_URL, "/users")


@app.route("GET", "/api/v1/identity/tenants/{tenant_id}/users")
def list_users(request: Request):
    return proxy_request(
        request,
        IDENTITY_SERVICE_URL,
        "/tenants/{0}/users".format(request.path_params["tenant_id"]),
    )


@app.route("POST", "/api/v1/portfolio/portfolios")
def create_portfolio(request: Request):
    return proxy_request(request, PORTFOLIO_SERVICE_URL, "/portfolios")


@app.route("GET", "/api/v1/portfolio/portfolios")
def list_portfolios(request: Request):
    return proxy_request(request, PORTFOLIO_SERVICE_URL, "/portfolios")


@app.route("POST", "/api/v1/portfolio/portfolios/{portfolio_id}/projects")
def create_project(request: Request):
    return proxy_request(
        request,
        PORTFOLIO_SERVICE_URL,
        "/portfolios/{0}/projects".format(request.path_params["portfolio_id"]),
    )


@app.route("GET", "/api/v1/portfolio/projects/{project_id}")
def get_project(request: Request):
    return proxy_request(
        request,
        PORTFOLIO_SERVICE_URL,
        "/projects/{0}".format(request.path_params["project_id"]),
    )


@app.route("GET", "/api/v1/portfolio/portfolios/{portfolio_id}/snapshot")
def portfolio_snapshot(request: Request):
    return proxy_request(
        request,
        PORTFOLIO_SERVICE_URL,
        "/portfolios/{0}/snapshot".format(request.path_params["portfolio_id"]),
    )


@app.route("POST", "/api/v1/delivery/projects/{project_id}/work-items")
def create_work_item(request: Request):
    return proxy_request(
        request,
        DELIVERY_SERVICE_URL,
        "/projects/{0}/work-items".format(request.path_params["project_id"]),
    )


@app.route("GET", "/api/v1/delivery/projects/{project_id}/work-items")
def list_work_items(request: Request):
    return proxy_request(
        request,
        DELIVERY_SERVICE_URL,
        "/projects/{0}/work-items".format(request.path_params["project_id"]),
    )


@app.route("PATCH", "/api/v1/delivery/work-items/{work_item_id}/status")
def update_work_item_status(request: Request):
    return proxy_request(
        request,
        DELIVERY_SERVICE_URL,
        "/work-items/{0}/status".format(request.path_params["work_item_id"]),
    )


@app.route("GET", "/api/v1/delivery/projects/{project_id}/summary")
def delivery_summary(request: Request):
    return proxy_request(
        request,
        DELIVERY_SERVICE_URL,
        "/projects/{0}/summary".format(request.path_params["project_id"]),
    )


@app.route("POST", "/api/v1/finance/projects/{project_id}/budget")
def set_budget(request: Request):
    return proxy_request(
        request,
        FINANCE_SERVICE_URL,
        "/projects/{0}/budget".format(request.path_params["project_id"]),
    )


@app.route("POST", "/api/v1/finance/projects/{project_id}/expenses")
def create_expense(request: Request):
    return proxy_request(
        request,
        FINANCE_SERVICE_URL,
        "/projects/{0}/expenses".format(request.path_params["project_id"]),
    )


@app.route("GET", "/api/v1/finance/projects/{project_id}/status")
def finance_status(request: Request):
    return proxy_request(
        request,
        FINANCE_SERVICE_URL,
        "/projects/{0}/status".format(request.path_params["project_id"]),
    )


@app.route("GET", "/api/v1/notifications/alerts")
def list_alerts(request: Request):
    return proxy_request(request, NOTIFICATION_SERVICE_URL, "/alerts")


@app.route("PATCH", "/api/v1/notifications/alerts/{alert_id}/ack")
def acknowledge_alert(request: Request):
    return proxy_request(
        request,
        NOTIFICATION_SERVICE_URL,
        "/alerts/{0}/ack".format(request.path_params["alert_id"]),
    )


@app.route("GET", "/api/v1/analytics/dashboard")
def dashboard(request: Request):
    return proxy_request(request, ANALYTICS_SERVICE_URL, "/dashboard")


if __name__ == "__main__":
    run_service(app, HOST or "127.0.0.1", PORT or 7000)
