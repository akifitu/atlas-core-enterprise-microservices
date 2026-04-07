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
SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


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
            first_seen_at TEXT,
            last_seen_at TEXT,
            occurrence_count INTEGER NOT NULL DEFAULT 1,
            escalated_at TEXT,
            acknowledged_at TEXT,
            acknowledged_by TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_alerts_tenant_status_created_at
        ON alerts (tenant_id, status, created_at);

        CREATE INDEX IF NOT EXISTS idx_alerts_tenant_project_status_created_at
        ON alerts (tenant_id, project_id, status, created_at);

        CREATE INDEX IF NOT EXISTS idx_alerts_tenant_project_source_status
        ON alerts (tenant_id, project_id, source, status);
        """
    )

    existing_columns = {column["name"] for column in db.fetchall("PRAGMA table_info(alerts)")}
    if "first_seen_at" not in existing_columns:
        db.execute("ALTER TABLE alerts ADD COLUMN first_seen_at TEXT")
    if "last_seen_at" not in existing_columns:
        db.execute("ALTER TABLE alerts ADD COLUMN last_seen_at TEXT")
    if "occurrence_count" not in existing_columns:
        db.execute("ALTER TABLE alerts ADD COLUMN occurrence_count INTEGER NOT NULL DEFAULT 1")
    if "escalated_at" not in existing_columns:
        db.execute("ALTER TABLE alerts ADD COLUMN escalated_at TEXT")


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


def effective_severity(existing_severity: str, incoming_severity: str, occurrence_count: int) -> str:
    highest = existing_severity if SEVERITY_ORDER[existing_severity] >= SEVERITY_ORDER[incoming_severity] else incoming_severity
    if highest != "critical" and occurrence_count >= 3:
        return "critical"
    return highest


def find_open_alert(tenant_id: str, project_id: str, source: str, title: str) -> Optional[Dict[str, Any]]:
    return db.fetchone(
        """
        SELECT * FROM alerts
        WHERE tenant_id = ? AND project_id = ? AND source = ? AND title = ? AND status = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (tenant_id, project_id, source, title, "open"),
    )


def filtered_alerts(actor: Dict[str, str], request: Request) -> Dict[str, Any]:
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
    return {
        "query": query,
        "params": params,
    }


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

    title = require_field(payload, "title")
    message = require_field(payload, "message")
    source = require_field(payload, "source")
    existing_alert = find_open_alert(tenant_id, project_id, source, title)
    now = utc_now()

    if existing_alert:
        occurrence_count = int(existing_alert.get("occurrence_count") or 1) + 1
        next_severity = effective_severity(existing_alert["severity"], severity, occurrence_count)
        escalated_at = existing_alert.get("escalated_at")
        if next_severity != existing_alert["severity"] and next_severity == "critical" and not escalated_at:
            escalated_at = now

        db.execute(
            """
            UPDATE alerts
            SET severity = ?, message = ?, last_seen_at = ?, occurrence_count = ?, escalated_at = ?
            WHERE id = ? AND tenant_id = ?
            """,
            (
                next_severity,
                message,
                now,
                occurrence_count,
                escalated_at,
                existing_alert["id"],
                tenant_id,
            ),
        )
        return 200, {"alert": alert_by_id(tenant_id, existing_alert["id"])}

    alert_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO alerts (
            id, tenant_id, project_id, severity, title, message, source, status, created_at,
            first_seen_at, last_seen_at, occurrence_count, escalated_at, acknowledged_at, acknowledged_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alert_id,
            tenant_id,
            project_id,
            severity,
            title,
            message,
            source,
            "open",
            now,
            now,
            now,
            1,
            None,
            None,
            None,
        ),
    )
    return 201, {"alert": alert_by_id(tenant_id, alert_id)}


@app.route("GET", "/alerts")
def list_alerts(request: Request):
    actor = require_actor(request)
    filters = filtered_alerts(actor, request)
    query = filters["query"]
    params = filters["params"]
    query += " ORDER BY created_at DESC"

    alerts = db.fetchall(query, params)
    return 200, {"alerts": alerts}


@app.route("GET", "/alerts/summary")
def alerts_summary(request: Request):
    actor = require_actor(request)
    filters = filtered_alerts(actor, request)
    alerts = db.fetchall(filters["query"] + " ORDER BY created_at DESC", filters["params"])

    by_status: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    by_project: Dict[str, Dict[str, Any]] = {}
    total_occurrences = 0
    escalated_open_alerts = 0

    for alert in alerts:
        status = str(alert["status"])
        severity = str(alert["severity"])
        source = str(alert["source"])
        project_id = str(alert["project_id"])
        occurrence_count = int(alert.get("occurrence_count") or 1)

        by_status[status] = by_status.get(status, 0) + 1
        by_severity[severity] = by_severity.get(severity, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1
        total_occurrences += occurrence_count
        if status == "open" and alert.get("escalated_at"):
            escalated_open_alerts += 1

        project_bucket = by_project.setdefault(
            project_id,
            {
                "project_id": project_id,
                "alerts": 0,
                "open_alerts": 0,
                "critical_alerts": 0,
                "occurrences": 0,
            },
        )
        project_bucket["alerts"] += 1
        project_bucket["occurrences"] += occurrence_count
        if status == "open":
            project_bucket["open_alerts"] += 1
        if severity == "critical":
            project_bucket["critical_alerts"] += 1

    noisy_projects = sorted(
        by_project.values(),
        key=lambda item: (-item["occurrences"], -item["critical_alerts"], item["project_id"]),
    )[:5]

    return 200, {
        "summary": {
            "total_alerts": len(alerts),
            "total_occurrences": total_occurrences,
            "deduplicated_occurrences": max(0, total_occurrences - len(alerts)),
            "open_alerts": by_status.get("open", 0),
            "acked_alerts": by_status.get("acked", 0),
            "critical_open_alerts": len(
                [alert for alert in alerts if alert["status"] == "open" and alert["severity"] == "critical"]
            ),
            "escalated_open_alerts": escalated_open_alerts,
            "by_status": by_status,
            "by_severity": by_severity,
            "by_source": by_source,
            "noisy_projects": noisy_projects,
        }
    }


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
