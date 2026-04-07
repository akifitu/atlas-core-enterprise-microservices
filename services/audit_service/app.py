import json
import uuid
from typing import Any, Dict

from shared.atlas_core.config import env, utc_now
from shared.atlas_core.context import require_actor
from shared.atlas_core.db import Database
from shared.atlas_core.http import AppError, Request, ServiceApp, run_service


SERVICE_NAME = "audit-service"
HOST = env("AUDIT_SERVICE_HOST", "127.0.0.1")
PORT = env("AUDIT_SERVICE_PORT", 7007, int)
DATABASE_PATH = env("AUDIT_DB_PATH", "runtime/audit-service.db")
INGEST_TOKEN = env("AUDIT_SERVICE_TOKEN", "atlas-internal-audit")

db = Database(DATABASE_PATH or "runtime/audit-service.db")
app = ServiceApp(SERVICE_NAME)


def migrate() -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            actor_user_id TEXT NOT NULL,
            actor_role TEXT NOT NULL,
            request_id TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            resource TEXT NOT NULL,
            action TEXT NOT NULL,
            service_name TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            entity_type TEXT,
            entity_id TEXT,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_audit_events_tenant_created_at
        ON audit_events (tenant_id, created_at);

        CREATE INDEX IF NOT EXISTS idx_audit_events_tenant_service_created_at
        ON audit_events (tenant_id, service_name, created_at);

        CREATE INDEX IF NOT EXISTS idx_audit_events_tenant_actor_created_at
        ON audit_events (tenant_id, actor_user_id, created_at);

        CREATE INDEX IF NOT EXISTS idx_audit_events_tenant_resource_created_at
        ON audit_events (tenant_id, resource, created_at);
        """
    )


def require_json_object(request: Request) -> Dict[str, Any]:
    if not isinstance(request.body, dict):
        raise AppError(400, "json_object_required")
    return request.body


def require_field(payload: Dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise AppError(400, "invalid_field", {"field": field_name})
    return value.strip()


def require_int_field(payload: Dict[str, Any], field_name: str) -> int:
    value = payload.get(field_name)
    if not isinstance(value, int):
        raise AppError(400, "invalid_field", {"field": field_name})
    return value


def require_internal_ingest(request: Request) -> None:
    if request.header("x-audit-token") != INGEST_TOKEN:
        raise AppError(403, "invalid_audit_ingest_token")


def require_audit_reader(request: Request) -> Dict[str, str]:
    actor = require_actor(request)
    if actor["role"] not in ("admin", "portfolio_manager"):
        raise AppError(403, "audit_reader_role_required")
    return actor


def deserialize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    metadata_json = event.get("metadata_json")
    parsed_metadata = json.loads(metadata_json) if isinstance(metadata_json, str) else {}
    result = dict(event)
    result["metadata"] = parsed_metadata
    result.pop("metadata_json", None)
    return result


@app.route("GET", "/health")
def health(_: Request):
    return 200, {
        "status": "ok",
        "service": SERVICE_NAME,
        "events": db.scalar("SELECT COUNT(*) FROM audit_events") or 0,
    }


@app.route("POST", "/events")
def create_event(request: Request):
    require_internal_ingest(request)
    payload = require_json_object(request)

    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise AppError(400, "invalid_field", {"field": "metadata"})

    event_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO audit_events (
            id, tenant_id, actor_user_id, actor_role, request_id, method, path,
            resource, action, service_name, status_code, outcome, entity_type,
            entity_id, metadata_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            require_field(payload, "tenant_id"),
            require_field(payload, "actor_user_id"),
            require_field(payload, "actor_role"),
            require_field(payload, "request_id"),
            require_field(payload, "method"),
            require_field(payload, "path"),
            require_field(payload, "resource"),
            require_field(payload, "action"),
            require_field(payload, "service_name"),
            require_int_field(payload, "status_code"),
            require_field(payload, "outcome"),
            payload.get("entity_type"),
            payload.get("entity_id"),
            json.dumps(metadata, sort_keys=True),
            utc_now(),
        ),
    )
    event = db.fetchone("SELECT * FROM audit_events WHERE id = ?", (event_id,))
    return 201, {"event": deserialize_event(event or {})}


@app.route("GET", "/events")
def list_events(request: Request):
    actor = require_audit_reader(request)
    limit_raw = request.query_value("limit", "50")
    try:
        limit = max(1, min(200, int(limit_raw or "50")))
    except ValueError as exc:
        raise AppError(400, "invalid_limit") from exc

    service_name = request.query_value("service_name")
    resource = request.query_value("resource")
    outcome = request.query_value("outcome")
    actor_user_id = request.query_value("actor_user_id")

    query = "SELECT * FROM audit_events WHERE tenant_id = ?"
    params = [actor["tenant_id"]]
    if service_name:
        query += " AND service_name = ?"
        params.append(service_name)
    if resource:
        query += " AND resource = ?"
        params.append(resource)
    if outcome:
        query += " AND outcome = ?"
        params.append(outcome)
    if actor_user_id:
        query += " AND actor_user_id = ?"
        params.append(actor_user_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    events = db.fetchall(query, params)
    return 200, {"events": [deserialize_event(event) for event in events]}


if __name__ == "__main__":
    migrate()
    run_service(app, HOST or "127.0.0.1", PORT or 7007)
