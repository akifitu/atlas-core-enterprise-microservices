import time
from threading import Lock
from typing import Any, Dict, Optional
from urllib.parse import quote, urlencode

from shared.atlas_core.config import env, service_url, utc_now
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
AUTH_CACHE_TTL_SECONDS = env("API_GATEWAY_AUTH_CACHE_TTL_SECONDS", 30, int) or 30
AUTH_CACHE_MAX_ENTRIES = env("API_GATEWAY_AUTH_CACHE_MAX_ENTRIES", 1024, int) or 1024

app = ServiceApp(SERVICE_NAME)
AUTH_CACHE: Dict[str, Dict[str, Any]] = {}
AUTH_CACHE_STATS = {"hits": 0, "misses": 0}
AUTH_CACHE_LOCK = Lock()

DEPENDENCIES = {
    "identity-service": IDENTITY_SERVICE_URL,
    "portfolio-service": PORTFOLIO_SERVICE_URL,
    "delivery-service": DELIVERY_SERVICE_URL,
    "finance-service": FINANCE_SERVICE_URL,
    "notification-service": NOTIFICATION_SERVICE_URL,
    "analytics-service": ANALYTICS_SERVICE_URL,
}


def _prune_auth_cache(now: float) -> None:
    expired_tokens = [token for token, item in AUTH_CACHE.items() if item["expires_at"] <= now]
    for token in expired_tokens:
        AUTH_CACHE.pop(token, None)

    if len(AUTH_CACHE) < AUTH_CACHE_MAX_ENTRIES:
        return

    overflow = len(AUTH_CACHE) - AUTH_CACHE_MAX_ENTRIES + 1
    oldest_tokens = sorted(AUTH_CACHE.items(), key=lambda item: item[1]["expires_at"])[:overflow]
    for token, _ in oldest_tokens:
        AUTH_CACHE.pop(token, None)


def auth_cache_snapshot() -> Dict[str, Any]:
    now = time.time()
    with AUTH_CACHE_LOCK:
        _prune_auth_cache(now)
        return {
            "hits": AUTH_CACHE_STATS["hits"],
            "misses": AUTH_CACHE_STATS["misses"],
            "entries": len(AUTH_CACHE),
            "ttl_seconds": AUTH_CACHE_TTL_SECONDS,
            "max_entries": AUTH_CACHE_MAX_ENTRIES,
        }


def require_platform_operator(request: Request) -> Dict[str, Any]:
    context = authenticate(request)
    if context["role"] not in ("admin", "portfolio_manager"):
        raise AppError(403, "platform_operator_role_required")
    return context


def authenticate(request: Request) -> Dict[str, Any]:
    token = read_bearer_token(request.header("authorization"))
    if not token:
        raise AppError(401, "bearer_token_required")

    now = time.time()
    with AUTH_CACHE_LOCK:
        _prune_auth_cache(now)
        cached = AUTH_CACHE.get(token)
        if cached and cached["expires_at"] > now:
            AUTH_CACHE_STATS["hits"] += 1
            return dict(cached["context"])
        AUTH_CACHE_STATS["misses"] += 1

    status_code, payload = request_json(
        "GET",
        IDENTITY_SERVICE_URL,
        "/validate?token={0}".format(quote(token)),
        headers={"X-Request-ID": request.request_id},
    )
    if status_code >= 400:
        with AUTH_CACHE_LOCK:
            AUTH_CACHE.pop(token, None)
        raise AppError(401, "invalid_token", {"identity_response": payload})

    context = payload["context"]
    with AUTH_CACHE_LOCK:
        _prune_auth_cache(time.time())
        AUTH_CACHE[token] = {
            "context": dict(context),
            "expires_at": time.time() + AUTH_CACHE_TTL_SECONDS,
        }
    return context


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
                "X-Request-ID": request.request_id,
            }
        )
    else:
        headers["X-Request-ID"] = request.request_id

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


def dependency_health(service_name: str, base_url: str, request_id: str) -> Dict[str, Any]:
    started_at = time.perf_counter()
    status_code, payload = request_json(
        "GET",
        base_url,
        "/health",
        headers={"X-Request-ID": request_id},
        timeout=2,
    )
    latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
    healthy = status_code < 400 and isinstance(payload, dict) and payload.get("status") == "ok"
    return {
        "healthy": healthy,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "payload": payload,
    }


@app.route("GET", "/health")
def health(_: Request):
    return 200, {"status": "ok", "service": SERVICE_NAME, "auth_cache": auth_cache_snapshot()}


@app.route("GET", "/api/v1/platform/topology")
def topology(request: Request):
    require_platform_operator(request)
    services = {
        "api-gateway": {
            "healthy": True,
            "status_code": 200,
            "latency_ms": 0.0,
            "payload": {"status": "ok", "service": SERVICE_NAME},
        }
    }
    for service_name, base_url in DEPENDENCIES.items():
        services[service_name] = dependency_health(service_name, base_url, request.request_id)

    degraded_services = [name for name, details in services.items() if not details["healthy"]]
    return 200, {
        "generated_at": utc_now(),
        "services": services,
        "auth_cache": auth_cache_snapshot(),
        "summary": {
            "healthy_services": len(services) - len(degraded_services),
            "degraded_services": degraded_services,
        },
    }


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
