from concurrent.futures import ThreadPoolExecutor
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional
import json
from urllib.parse import urlencode

from shared.atlas_core.config import env, service_url, utc_now
from shared.atlas_core.http import AppError, HttpResponse, Request, ServiceApp, run_service
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
ROOT_DIR = Path(__file__).resolve().parents[2]
ADMIN_CONSOLE_DIR = ROOT_DIR / "ui" / "admin_console"
ADMIN_CONSOLE_ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
}
CONTROL_ROOM_ACTIONS = {
    "audit_export",
    "audit_retention_dry_run",
    "audit_retention_apply",
}
CONTROL_ROOM_ACTION_LIMIT = 10

DEPENDENCIES = {
    "identity-service": IDENTITY_SERVICE_URL,
    "portfolio-service": PORTFOLIO_SERVICE_URL,
    "delivery-service": DELIVERY_SERVICE_URL,
    "finance-service": FINANCE_SERVICE_URL,
    "notification-service": NOTIFICATION_SERVICE_URL,
    "analytics-service": ANALYTICS_SERVICE_URL,
    "audit-service": AUDIT_SERVICE_URL,
}


def admin_console_asset(asset_name: str) -> Any:
    content_type = ADMIN_CONSOLE_ASSETS.get(asset_name)
    if content_type is None:
        raise AppError(404, "route_not_found", {"path": "/admin/{0}".format(asset_name), "method": "GET"})

    asset_path = ADMIN_CONSOLE_DIR / asset_name
    return 200, HttpResponse(
        body=asset_path.read_bytes(),
        content_type=content_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; "
                "connect-src 'self'; "
                "img-src 'self' data:; "
                "style-src 'self'; "
                "script-src 'self'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )


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
        "POST",
        IDENTITY_SERVICE_URL,
        "/validate",
        headers={
            "Authorization": "Bearer {0}".format(token),
            "X-Request-ID": request.request_id,
        },
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


def actor_headers(actor_context: Dict[str, Any], request_id: str) -> Dict[str, str]:
    return {
        "X-Tenant-ID": actor_context["tenant_id"],
        "X-User-ID": actor_context["user_id"],
        "X-User-Role": actor_context["role"],
        "X-Request-ID": request_id,
    }


def require_json_object(request: Request) -> Dict[str, Any]:
    if not isinstance(request.body, dict):
        raise AppError(400, "json_object_required")
    return request.body


def optional_string_field(payload: Dict[str, Any], field_name: str) -> Optional[str]:
    value = payload.get(field_name)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise AppError(400, "invalid_field", {"field": field_name})
    return value


def require_bounded_int(value: Any, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AppError(400, "invalid_field", {"field": field_name})
    if value < minimum or value > maximum:
        raise AppError(
            400,
            "invalid_field",
            {"field": field_name, "minimum": minimum, "maximum": maximum},
        )
    return value


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
        headers.update(actor_headers(context, request.request_id))
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


def submit_audit_payload(request_id: str, audit_payload: Dict[str, Any]) -> None:
    audit_status, _ = request_json(
        "POST",
        AUDIT_SERVICE_URL,
        "/events",
        payload=audit_payload,
        headers={
            "X-Request-ID": request_id,
            "X-Audit-Token": AUDIT_SERVICE_TOKEN,
        },
        timeout=2,
    )
    with AUDIT_STATS_LOCK:
        if audit_status < 400:
            AUDIT_STATS["recorded"] += 1
        else:
            AUDIT_STATS["failed"] += 1


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
    submit_audit_payload(request.request_id, audit_payload)


def record_control_room_action(
    request: Request,
    actor_context: Dict[str, Any],
    action_name: str,
    status_code: int,
    payload: Any,
) -> None:
    request_payload = request.body if isinstance(request.body, dict) else {}
    audit_payload = {
        "tenant_id": actor_context["tenant_id"],
        "actor_user_id": actor_context["user_id"],
        "actor_role": actor_context["role"],
        "request_id": request.request_id,
        "method": request.method,
        "path": request.path,
        "resource": "control_room",
        "action": action_name,
        "service_name": SERVICE_NAME,
        "status_code": status_code,
        "outcome": "success" if status_code < 400 else "rejected",
        "entity_type": None,
        "entity_id": None,
        "metadata": {
            "response_error": payload.get("error") if isinstance(payload, dict) else None,
            "query": request.query,
            "parameters": {
                "top_n": request_payload.get("top_n"),
                "portfolio_id": request_payload.get("portfolio_id"),
                "limit": request_payload.get("limit"),
                "retention_days": request_payload.get("retention_days"),
            },
            "summary": {
                "dry_run": payload.get("dry_run") if isinstance(payload, dict) else None,
                "count": payload.get("count") if isinstance(payload, dict) else None,
                "would_delete": payload.get("would_delete") if isinstance(payload, dict) else None,
                "deleted_count": payload.get("deleted_count") if isinstance(payload, dict) else None,
                "cutoff": payload.get("cutoff") if isinstance(payload, dict) else None,
            },
        },
    }
    submit_audit_payload(request.request_id, audit_payload)


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


def platform_topology_payload(request_id: str) -> Dict[str, Any]:
    services = {
        "api-gateway": {
            "healthy": True,
            "status_code": 200,
            "latency_ms": 0.0,
            "payload": {"status": "ok", "service": SERVICE_NAME},
        }
    }
    for service_name, base_url in DEPENDENCIES.items():
        services[service_name] = dependency_health(service_name, base_url, request_id)

    degraded_services = [name for name, details in services.items() if not details["healthy"]]
    return {
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


def require_dependency_success(status_code: int, payload: Any, dependency_name: str) -> Any:
    if status_code >= 400:
        raise AppError(
            502,
            "dependency_call_failed",
            {"dependency": dependency_name, "status_code": status_code, "payload": payload},
        )
    return payload


def get_control_room_top_n(request: Request) -> int:
    try:
        return max(1, min(20, int(request.query_value("top_n", "5") or "5")))
    except ValueError as exc:
        raise AppError(400, "invalid_top_n") from exc


def fetch_operator_payload(
    service_name: str,
    base_url: str,
    path: str,
    headers: Dict[str, str],
) -> Any:
    return require_dependency_success(
        *request_json("GET", base_url, path, headers=headers),
        dependency_name=service_name,
    )


def select_control_room_portfolio(executive_summary_payload: Dict[str, Any], requested_portfolio_id: Optional[str]) -> Dict[str, Optional[str]]:
    if requested_portfolio_id:
        return {"portfolio_id": requested_portfolio_id, "selection_mode": "requested"}

    top_risks = executive_summary_payload.get("top_risks") or []
    if top_risks and isinstance(top_risks[0], dict):
        portfolio_id = top_risks[0].get("portfolio_id")
        if portfolio_id:
            return {"portfolio_id": portfolio_id, "selection_mode": "top_risk"}

    portfolios = executive_summary_payload.get("portfolios") or []
    if portfolios and isinstance(portfolios[0], dict):
        portfolio = portfolios[0].get("portfolio") or {}
        portfolio_id = portfolio.get("id")
        if portfolio_id:
            return {"portfolio_id": portfolio_id, "selection_mode": "first_portfolio"}

    return {"portfolio_id": None, "selection_mode": "none"}


def control_room_top_n_from_payload(payload: Dict[str, Any]) -> int:
    return require_bounded_int(payload.get("top_n", 5), "top_n", 1, 20)


def post_operator_payload(
    service_name: str,
    base_url: str,
    path: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
) -> Any:
    return require_dependency_success(
        *request_json("POST", base_url, path, payload=payload, headers=headers),
        dependency_name=service_name,
    )


def fetch_control_room_recent_actions(headers: Dict[str, str]) -> Dict[str, Any]:
    return fetch_operator_payload(
        "audit-service",
        AUDIT_SERVICE_URL,
        "/events?{0}".format(
            urlencode({"resource": "control_room", "limit": CONTROL_ROOM_ACTION_LIMIT})
        ),
        headers,
    )


def build_control_room_payload(
    request_id: str,
    actor_context: Dict[str, Any],
    top_n: int,
    requested_portfolio_id: Optional[str],
) -> Dict[str, Any]:
    headers = actor_headers(actor_context, request_id)

    with ThreadPoolExecutor(max_workers=5) as executor:
        topology_future = executor.submit(platform_topology_payload, request_id)
        alert_summary_future = executor.submit(
            fetch_operator_payload,
            "notification-service",
            NOTIFICATION_SERVICE_URL,
            "/alerts/summary",
            headers,
        )
        audit_summary_future = executor.submit(
            fetch_operator_payload,
            "audit-service",
            AUDIT_SERVICE_URL,
            "/summary",
            headers,
        )
        executive_summary_future = executor.submit(
            fetch_operator_payload,
            "analytics-service",
            ANALYTICS_SERVICE_URL,
            "/executive-summary?{0}".format(urlencode({"top_n": top_n})),
            headers,
        )
        recent_actions_future = executor.submit(fetch_control_room_recent_actions, headers)

        topology_payload = topology_future.result()
        alert_summary_payload = alert_summary_future.result()
        audit_summary_payload = audit_summary_future.result()
        executive_summary_payload = executive_summary_future.result()
        recent_actions_payload = recent_actions_future.result()

    portfolio_selection = select_control_room_portfolio(executive_summary_payload, requested_portfolio_id)
    selected_portfolio_id = portfolio_selection["portfolio_id"]
    portfolio_dashboard_payload = None
    if selected_portfolio_id:
        portfolio_dashboard_payload = fetch_operator_payload(
            "analytics-service",
            ANALYTICS_SERVICE_URL,
            "/dashboard?{0}".format(urlencode({"portfolio_id": selected_portfolio_id})),
            headers,
        )

    return {
        "generated_at": utc_now(),
        "topology": topology_payload,
        "alert_summary": alert_summary_payload["summary"],
        "audit_summary": audit_summary_payload["summary"],
        "executive_summary": executive_summary_payload,
        "selected_portfolio_id": selected_portfolio_id,
        "selection_mode": portfolio_selection["selection_mode"],
        "portfolio_dashboard": portfolio_dashboard_payload,
        "recent_actions": recent_actions_payload["events"],
        "recent_actions_summary": {
            "count": len(recent_actions_payload["events"]),
            "latest_action_at": recent_actions_payload["events"][0]["created_at"] if recent_actions_payload["events"] else None,
        },
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


@app.route("GET", "/admin")
def admin_console(_: Request):
    return admin_console_asset("index.html")


@app.route("GET", "/admin/index.html")
def admin_console_index(_: Request):
    return admin_console_asset("index.html")


@app.route("GET", "/admin/styles.css")
def admin_console_styles(_: Request):
    return admin_console_asset("styles.css")


@app.route("GET", "/admin/app.js")
def admin_console_script(_: Request):
    return admin_console_asset("app.js")


@app.route("GET", "/api/v1/platform/topology")
def topology(request: Request):
    require_platform_operator(request)
    return 200, platform_topology_payload(request.request_id)


@app.route("GET", "/api/v1/platform/control-room")
def control_room(request: Request):
    actor_context = require_platform_operator(request)
    top_n = get_control_room_top_n(request)
    requested_portfolio_id = request.query_value("portfolio_id")
    return 200, build_control_room_payload(request.request_id, actor_context, top_n, requested_portfolio_id)


@app.route("POST", "/api/v1/platform/control-room/actions")
def control_room_actions(request: Request):
    actor_context = require_platform_operator(request)
    payload = require_json_object(request)
    action_name = optional_string_field(payload, "action")
    if action_name not in CONTROL_ROOM_ACTIONS:
        raise AppError(
            400,
            "invalid_control_room_action",
            {"allowed_actions": sorted(CONTROL_ROOM_ACTIONS)},
        )

    top_n = control_room_top_n_from_payload(payload)
    requested_portfolio_id = optional_string_field(payload, "portfolio_id")
    headers = actor_headers(actor_context, request.request_id)

    if action_name == "audit_export":
        limit = require_bounded_int(payload.get("limit", 100), "limit", 1, 1000)
        result = fetch_operator_payload(
            "audit-service",
            AUDIT_SERVICE_URL,
            "/events/export?{0}".format(urlencode({"limit": limit})),
            headers,
        )
    else:
        retention_days = require_bounded_int(payload.get("retention_days"), "retention_days", 0, 3650)
        result = post_operator_payload(
            "audit-service",
            AUDIT_SERVICE_URL,
            "/retention/purge",
            headers,
            {
                "retention_days": retention_days,
                "dry_run": action_name == "audit_retention_dry_run",
            },
        )

    record_control_room_action(request, actor_context, action_name, 200, result)

    return 200, {
        "action": action_name,
        "generated_at": utc_now(),
        "result": result,
        "control_room": build_control_room_payload(request.request_id, actor_context, top_n, requested_portfolio_id),
    }


@app.route("GET", "/api/v1/platform/audit-events")
def list_audit_events(request: Request):
    require_platform_operator(request)
    return proxy_request(request, AUDIT_SERVICE_URL, "/events")


@app.route("GET", "/api/v1/platform/audit-summary")
def audit_summary(request: Request):
    require_platform_operator(request)
    return proxy_request(request, AUDIT_SERVICE_URL, "/summary")


@app.route("GET", "/api/v1/platform/audit-export")
def audit_export(request: Request):
    require_platform_operator(request)
    return proxy_request(request, AUDIT_SERVICE_URL, "/events/export")


@app.route("POST", "/api/v1/platform/audit-retention")
def audit_retention(request: Request):
    require_platform_operator(request)
    return proxy_request(request, AUDIT_SERVICE_URL, "/retention/purge")


@app.route("GET", "/api/v1/platform/alert-summary")
def alert_summary(request: Request):
    require_platform_operator(request)
    return proxy_request(request, NOTIFICATION_SERVICE_URL, "/alerts/summary")


@app.route("POST", "/api/v1/identity/bootstrap-admin")
def bootstrap_admin(request: Request):
    headers = {"X-Request-ID": request.request_id}
    bootstrap_token = request.header("x-bootstrap-token")
    if bootstrap_token:
        headers["X-Bootstrap-Token"] = bootstrap_token
    status_code, payload = request_json(
        request.method,
        IDENTITY_SERVICE_URL,
        "/bootstrap-admin",
        payload=request.body if isinstance(request.body, dict) else None,
        headers=headers,
    )
    return status_code, payload


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


@app.route("GET", "/api/v1/analytics/executive-summary")
def executive_summary(request: Request):
    return proxy_request(request, ANALYTICS_SERVICE_URL, "/executive-summary")


if __name__ == "__main__":
    run_service(app, HOST or "127.0.0.1", PORT or 7000)
