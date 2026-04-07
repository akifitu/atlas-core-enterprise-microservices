import time
from threading import Lock
from typing import Any, Dict, Optional
from urllib.parse import quote, urlencode
import json

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
AUDIT_SERVICE_URL = service_url("audit-service", 7007)
AUDIT_SERVICE_TOKEN = env("AUDIT_SERVICE_TOKEN", "atlas-internal-audit") or "atlas-internal-audit"
AUTH_CACHE_TTL_SECONDS = env("API_GATEWAY_AUTH_CACHE_TTL_SECONDS", 30, int) or 30
AUTH_CACHE_MAX_ENTRIES = env("API_GATEWAY_AUTH_CACHE_MAX_ENTRIES", 1024, int) or 1024
IDEMPOTENCY_TTL_SECONDS = env("API_GATEWAY_IDEMPOTENCY_TTL_SECONDS", 600, int) or 600
IDEMPOTENCY_MAX_ENTRIES = env("API_GATEWAY_IDEMPOTENCY_MAX_ENTRIES", 2048, int) or 2048

app = ServiceApp(SERVICE_NAME)
AUTH_CACHE: Dict[str, Dict[str, Any]] = {}
AUTH_CACHE_STATS = {"hits": 0, "misses": 0}
AUDIT_STATS = {"recorded": 0, "failed": 0}
IDEMPOTENCY_STORE: Dict[str, Dict[str, Any]] = {}
IDEMPOTENCY_STATS = {"hits": 0, "misses": 0, "stored": 0, "conflicts": 0}
AUTH_CACHE_LOCK = Lock()
AUDIT_STATS_LOCK = Lock()
IDEMPOTENCY_LOCK = Lock()

DEPENDENCIES = {
    "identity-service": IDENTITY_SERVICE_URL,
    "portfolio-service": PORTFOLIO_SERVICE_URL,
    "delivery-service": DELIVERY_SERVICE_URL,
    "finance-service": FINANCE_SERVICE_URL,
    "notification-service": NOTIFICATION_SERVICE_URL,
    "analytics-service": ANALYTICS_SERVICE_URL,
    "audit-service": AUDIT_SERVICE_URL,
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


def audit_stats_snapshot() -> Dict[str, Any]:
    with AUDIT_STATS_LOCK:
        return dict(AUDIT_STATS)


def _prune_idempotency_store(now: float) -> None:
    expired_keys = [key for key, item in IDEMPOTENCY_STORE.items() if item["expires_at"] <= now]
    for key in expired_keys:
        IDEMPOTENCY_STORE.pop(key, None)

    if len(IDEMPOTENCY_STORE) < IDEMPOTENCY_MAX_ENTRIES:
        return

    overflow = len(IDEMPOTENCY_STORE) - IDEMPOTENCY_MAX_ENTRIES + 1
    oldest_keys = sorted(IDEMPOTENCY_STORE.items(), key=lambda item: item[1]["expires_at"])[:overflow]
    for key, _ in oldest_keys:
        IDEMPOTENCY_STORE.pop(key, None)


def idempotency_snapshot() -> Dict[str, Any]:
    now = time.time()
    with IDEMPOTENCY_LOCK:
        _prune_idempotency_store(now)
        return {
            "hits": IDEMPOTENCY_STATS["hits"],
            "misses": IDEMPOTENCY_STATS["misses"],
            "stored": IDEMPOTENCY_STATS["stored"],
            "conflicts": IDEMPOTENCY_STATS["conflicts"],
            "entries": len(IDEMPOTENCY_STORE),
            "ttl_seconds": IDEMPOTENCY_TTL_SECONDS,
            "max_entries": IDEMPOTENCY_MAX_ENTRIES,
        }


def idempotency_fingerprint(request: Request, scope: str) -> str:
    return json.dumps(
        {
            "scope": scope,
            "method": request.method,
            "path": request.path,
            "query": request.query,
            "body": request.body,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def idempotency_cache_key(scope: str, key: str) -> str:
    return "{0}:{1}".format(scope, key)


def idempotency_scope(actor_context: Optional[Dict[str, Any]]) -> str:
    if actor_context:
        return actor_context["tenant_id"]
    return "anonymous"


def mutable_request(request: Request) -> bool:
    return request.method in ("POST", "PATCH", "PUT", "DELETE")


def get_idempotency_replay(request: Request, scope: str) -> Optional[Dict[str, Any]]:
    idempotency_key = request.header("idempotency-key")
    if not idempotency_key or not mutable_request(request):
        return None

    now = time.time()
    fingerprint = idempotency_fingerprint(request, scope)
    cache_key = idempotency_cache_key(scope, idempotency_key)
    with IDEMPOTENCY_LOCK:
        _prune_idempotency_store(now)
        record = IDEMPOTENCY_STORE.get(cache_key)
        if record is None:
            IDEMPOTENCY_STATS["misses"] += 1
            return None
        if record["fingerprint"] != fingerprint:
            IDEMPOTENCY_STATS["conflicts"] += 1
            raise AppError(409, "idempotency_key_conflict", {"idempotency_key": idempotency_key})
        IDEMPOTENCY_STATS["hits"] += 1
        return {
            "status_code": record["status_code"],
            "payload": record["payload"],
        }


def store_idempotency_result(request: Request, scope: str, status_code: int, payload: Any) -> None:
    idempotency_key = request.header("idempotency-key")
    if not idempotency_key or not mutable_request(request):
        return

    cache_key = idempotency_cache_key(scope, idempotency_key)
    fingerprint = idempotency_fingerprint(request, scope)
    with IDEMPOTENCY_LOCK:
        _prune_idempotency_store(time.time())
        IDEMPOTENCY_STORE[cache_key] = {
            "fingerprint": fingerprint,
            "status_code": status_code,
            "payload": payload,
            "expires_at": time.time() + IDEMPOTENCY_TTL_SECONDS,
        }
        IDEMPOTENCY_STATS["stored"] += 1


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
    actor_context = None
    if authenticated:
        context = authenticate(request)
        actor_context = context
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

    replay = get_idempotency_replay(request, idempotency_scope(actor_context))
    if replay is not None:
        return replay["status_code"], replay["payload"]

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
    store_idempotency_result(request, idempotency_scope(actor_context), status_code, payload)
    if actor_context and request.method in ("POST", "PATCH", "PUT", "DELETE"):
        record_audit_event(request, actor_context, service_url_value, internal_path, status_code, payload)
    return status_code, payload


def service_name_from_url(base_url: str) -> str:
    for service_name, url_value in DEPENDENCIES.items():
        if url_value == base_url:
            return service_name
    return "unknown-service"


def infer_resource_from_path(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "v1":
        if parts[2] == "platform" and len(parts) >= 4:
            return parts[3]
        return parts[2]
    return parts[-1] if parts else "root"


def infer_action(method: str, path: str) -> str:
    normalized = infer_resource_from_path(path).replace("-", "_")
    mapping = {
        "POST": "create",
        "PATCH": "update",
        "PUT": "replace",
        "DELETE": "delete",
    }
    return "{0}_{1}".format(mapping.get(method.upper(), "access"), normalized)


def extract_entity_reference(payload: Any) -> Dict[str, Optional[str]]:
    if not isinstance(payload, dict):
        return {"entity_type": None, "entity_id": None}
    for key, value in payload.items():
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            return {"entity_type": key, "entity_id": value["id"]}
    return {"entity_type": None, "entity_id": None}


def record_audit_event(
    request: Request,
    actor_context: Dict[str, Any],
    service_url_value: str,
    internal_path: str,
    status_code: int,
    payload: Any,
) -> None:
    entity_reference = extract_entity_reference(payload)
    metadata = {
        "downstream_path": internal_path,
        "response_error": payload.get("error") if isinstance(payload, dict) else None,
        "query": request.query,
    }
    audit_payload = {
        "tenant_id": actor_context["tenant_id"],
        "actor_user_id": actor_context["user_id"],
        "actor_role": actor_context["role"],
        "request_id": request.request_id,
        "method": request.method,
        "path": request.path,
        "resource": infer_resource_from_path(request.path),
        "action": infer_action(request.method, request.path),
        "service_name": service_name_from_url(service_url_value),
        "status_code": status_code,
        "outcome": "success" if status_code < 400 else "rejected",
        "entity_type": entity_reference["entity_type"],
        "entity_id": entity_reference["entity_id"],
        "metadata": metadata,
    }
    audit_status, _ = request_json(
        "POST",
        AUDIT_SERVICE_URL,
        "/events",
        payload=audit_payload,
        headers={
            "X-Request-ID": request.request_id,
            "X-Audit-Token": AUDIT_SERVICE_TOKEN,
        },
        timeout=2,
    )
    with AUDIT_STATS_LOCK:
        if audit_status < 400:
            AUDIT_STATS["recorded"] += 1
        else:
            AUDIT_STATS["failed"] += 1


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
    return 200, {
        "status": "ok",
        "service": SERVICE_NAME,
        "auth_cache": auth_cache_snapshot(),
        "audit": audit_stats_snapshot(),
        "idempotency": idempotency_snapshot(),
    }


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
        "audit": audit_stats_snapshot(),
        "idempotency": idempotency_snapshot(),
        "summary": {
            "healthy_services": len(services) - len(degraded_services),
            "degraded_services": degraded_services,
        },
    }


@app.route("GET", "/api/v1/platform/audit-events")
def list_audit_events(request: Request):
    require_platform_operator(request)
    return proxy_request(request, AUDIT_SERVICE_URL, "/events")


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
