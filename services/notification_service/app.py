import uuid
from typing import Any, Dict, Optional

from shared.atlas_core.config import env, utc_now
from shared.atlas_core.context import require_actor
from shared.atlas_core.db import Database
from shared.atlas_core.http import AppError, Request, ServiceApp, run_service


SERVICE_NAME = "notification-service"
HOST = env("NOTIFICATION_SERVICE_HOST", "127.0.0.1")
PORT = env("NOTIFICATION_SERVICE_PORT", 7005, int)
DATABASE_PATH = env("NOTIFICATION_DB_PATH", "runtime/notification-service.db")

db = Database(DATABASE_PATH or "runtime/notification-service.db")
app = ServiceApp(SERVICE_NAME)


def migrate() -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            acknowledged_at TEXT,
            acknowledged_by TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_alerts_tenant_status_created_at
        ON alerts (tenant_id, status, created_at);

        CREATE INDEX IF NOT EXISTS idx_alerts_tenant_project_status_created_at
        ON alerts (tenant_id, project_id, status, created_at);
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


def alert_by_id(tenant_id: str, alert_id: str) -> Dict[str, Any]:
    alert = db.fetchone("SELECT * FROM alerts WHERE tenant_id = ? AND id = ?", (tenant_id, alert_id))
    if not alert:
        raise AppError(404, "alert_not_found", {"alert_id": alert_id})
    return alert


def tenant_from_request(request: Request, payload: Optional[Dict[str, Any]] = None) -> str:
    tenant_id = request.header("x-tenant-id")
    if tenant_id:
        return tenant_id
    if payload:
        candidate = payload.get("tenant_id")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise AppError(400, "tenant_id_required")


@app.route("GET", "/health")
def health(_: Request):
    return 200, {
        "status": "ok",
        "service": SERVICE_NAME,
        "alerts": db.scalar("SELECT COUNT(*) FROM alerts") or 0,
    }


@app.route("POST", "/alerts")
def create_alert(request: Request):
    payload = require_json_object(request)
    tenant_id = tenant_from_request(request, payload)
    project_id = require_field(payload, "project_id")
    severity = require_field(payload, "severity").lower()
    if severity not in ("info", "warning", "critical"):
        raise AppError(400, "invalid_severity", {"severity": severity})

    alert_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO alerts (id, tenant_id, project_id, severity, title, message, source, status, created_at, acknowledged_at, acknowledged_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alert_id,
            tenant_id,
            project_id,
            severity,
            require_field(payload, "title"),
            require_field(payload, "message"),
            require_field(payload, "source"),
            "open",
            utc_now(),
            None,
            None,
        ),
    )
    return 201, {"alert": alert_by_id(tenant_id, alert_id)}


@app.route("GET", "/alerts")
def list_alerts(request: Request):
    actor = require_actor(request)
    project_id = request.query_value("project_id")
    status = request.query_value("status")
    if status and status not in ("open", "acked"):
        raise AppError(400, "invalid_status", {"status": status})

    query = "SELECT * FROM alerts WHERE tenant_id = ?"
    params = [actor["tenant_id"]]
    if project_id:
        query += " AND project_id = ?"
        params.append(project_id)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"

    alerts = db.fetchall(query, params)
    return 200, {"alerts": alerts}


@app.route("PATCH", "/alerts/{alert_id}/ack")
def acknowledge_alert(request: Request):
    actor = require_actor(request)
    alert = alert_by_id(actor["tenant_id"], request.path_params["alert_id"])
    if alert["status"] == "acked":
        return 200, {"alert": alert}

    db.execute(
        """
        UPDATE alerts
        SET status = ?, acknowledged_at = ?, acknowledged_by = ?
        WHERE id = ? AND tenant_id = ?
        """,
        ("acked", utc_now(), actor["user_id"], alert["id"], actor["tenant_id"]),
    )
    return 200, {"alert": alert_by_id(actor["tenant_id"], alert["id"])}


if __name__ == "__main__":
    migrate()
    run_service(app, HOST or "127.0.0.1", PORT or 7005)
