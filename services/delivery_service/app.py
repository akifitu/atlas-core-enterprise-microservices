import uuid
from typing import Any, Dict

from shared.atlas_core.config import env, service_url, utc_now
from shared.atlas_core.context import require_actor, require_admin
from shared.atlas_core.db import Database
from shared.atlas_core.http import AppError, Request, ServiceApp, run_service
from shared.atlas_core.service_client import request_json


SERVICE_NAME = "delivery-service"
HOST = env("DELIVERY_SERVICE_HOST", "127.0.0.1")
PORT = env("DELIVERY_SERVICE_PORT", 7003, int)
DATABASE_PATH = env("DELIVERY_DB_PATH", "runtime/delivery-service.db")
PORTFOLIO_SERVICE_URL = service_url("portfolio-service", 7002)
NOTIFICATION_SERVICE_URL = service_url("notification-service", 7005)

db = Database(DATABASE_PATH or "runtime/delivery-service.db")
app = ServiceApp(SERVICE_NAME)


def migrate() -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS work_items (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            priority TEXT NOT NULL,
            assignee TEXT NOT NULL,
            blocked_reason TEXT,
            due_date TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_work_items_tenant_project_created_at
        ON work_items (tenant_id, project_id, created_at);

        CREATE INDEX IF NOT EXISTS idx_work_items_tenant_project_status
        ON work_items (tenant_id, project_id, status);
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


def work_item_by_id(tenant_id: str, work_item_id: str) -> Dict[str, Any]:
    item = db.fetchone(
        "SELECT * FROM work_items WHERE tenant_id = ? AND id = ?",
        (tenant_id, work_item_id),
    )
    if not item:
        raise AppError(404, "work_item_not_found", {"work_item_id": work_item_id})
    return item


def actor_headers(actor: Dict[str, str], request: Request) -> Dict[str, str]:
    return {
        "X-Tenant-ID": actor["tenant_id"],
        "X-User-ID": actor["user_id"],
        "X-User-Role": actor["role"],
        "X-Request-ID": request.request_id,
    }


def require_project(actor: Dict[str, str], request: Request, project_id: str) -> Dict[str, Any]:
    status_code, payload = request_json(
        "GET",
        PORTFOLIO_SERVICE_URL,
        "/projects/{0}".format(project_id),
        headers=actor_headers(actor, request),
    )
    if status_code == 404:
        raise AppError(404, "project_not_found", {"project_id": project_id})
    if status_code >= 400:
        raise AppError(
            502,
            "portfolio_dependency_failed",
            {"project_id": project_id, "status_code": status_code, "payload": payload},
        )
    return payload["project"]


def publish_blocked_alert(tenant_id: str, project_id: str, title: str, blocked_reason: str) -> None:
    request_json(
        "POST",
        NOTIFICATION_SERVICE_URL,
        "/alerts",
        payload={
            "tenant_id": tenant_id,
            "project_id": project_id,
            "severity": "warning",
            "title": "Blocked delivery item",
            "message": "{0}: {1}".format(title, blocked_reason or "No reason provided"),
            "source": SERVICE_NAME,
        },
    )


@app.route("GET", "/health")
def health(_: Request):
    return 200, {
        "status": "ok",
        "service": SERVICE_NAME,
        "work_items": db.scalar("SELECT COUNT(*) FROM work_items") or 0,
    }


@app.route("POST", "/projects/{project_id}/work-items")
def create_work_item(request: Request):
    actor = require_admin(request)
    project = require_project(actor, request, request.path_params["project_id"])
    payload = require_json_object(request)
    title = require_field(payload, "title")
    priority = payload.get("priority", "medium")
    if priority not in ("low", "medium", "high", "critical"):
        raise AppError(400, "invalid_priority", {"priority": priority})

    assignee = require_field(payload, "assignee")
    due_date = payload.get("due_date")
    if due_date is not None and not isinstance(due_date, str):
        raise AppError(400, "invalid_field", {"field": "due_date"})

    work_item_id = str(uuid.uuid4())
    timestamp = utc_now()
    db.execute(
        """
        INSERT INTO work_items (id, tenant_id, project_id, title, status, priority, assignee, blocked_reason, due_date, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            work_item_id,
            actor["tenant_id"],
            project["id"],
            title,
            "backlog",
            priority,
            assignee,
            None,
            due_date,
            timestamp,
            timestamp,
        ),
    )
    return 201, {"work_item": work_item_by_id(actor["tenant_id"], work_item_id)}


@app.route("GET", "/projects/{project_id}/work-items")
def list_work_items(request: Request):
    actor = require_actor(request)
    project = require_project(actor, request, request.path_params["project_id"])
    items = db.fetchall(
        """
        SELECT * FROM work_items
        WHERE tenant_id = ? AND project_id = ?
        ORDER BY created_at ASC
        """,
        (actor["tenant_id"], project["id"]),
    )
    return 200, {"work_items": items}


@app.route("PATCH", "/work-items/{work_item_id}/status")
def update_work_item_status(request: Request):
    actor = require_admin(request)
    payload = require_json_object(request)
    status = require_field(payload, "status")
    if status not in ("backlog", "in_progress", "blocked", "done"):
        raise AppError(400, "invalid_status", {"status": status})

    blocked_reason = payload.get("blocked_reason")
    if blocked_reason is not None and not isinstance(blocked_reason, str):
        raise AppError(400, "invalid_field", {"field": "blocked_reason"})

    item = work_item_by_id(actor["tenant_id"], request.path_params["work_item_id"])
    db.execute(
        """
        UPDATE work_items
        SET status = ?, blocked_reason = ?, updated_at = ?
        WHERE id = ? AND tenant_id = ?
        """,
        (
            status,
            blocked_reason if status == "blocked" else None,
            utc_now(),
            item["id"],
            actor["tenant_id"],
        ),
    )

    updated_item = work_item_by_id(actor["tenant_id"], item["id"])
    if status == "blocked":
        publish_blocked_alert(actor["tenant_id"], item["project_id"], item["title"], blocked_reason or "")

    return 200, {"work_item": updated_item}


@app.route("GET", "/projects/{project_id}/summary")
def project_summary(request: Request):
    actor = require_actor(request)
    project = require_project(actor, request, request.path_params["project_id"])
    items = db.fetchall(
        """
        SELECT * FROM work_items
        WHERE tenant_id = ? AND project_id = ?
        ORDER BY created_at ASC
        """,
        (actor["tenant_id"], project["id"]),
    )
    return 200, {
        "project_id": project["id"],
        "work_items": items,
        "totals": {
            "count": len(items),
            "done": len([item for item in items if item["status"] == "done"]),
            "blocked": len([item for item in items if item["status"] == "blocked"]),
            "in_progress": len([item for item in items if item["status"] == "in_progress"]),
            "completion_rate": round(
                (len([item for item in items if item["status"] == "done"]) / len(items)) * 100, 2
            )
            if items
            else 0.0,
        },
    }


if __name__ == "__main__":
    migrate()
    run_service(app, HOST or "127.0.0.1", PORT or 7003)
