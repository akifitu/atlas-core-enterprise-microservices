import uuid
from typing import Any, Dict

from shared.atlas_core.config import env, service_url, utc_now
from shared.atlas_core.context import require_actor, require_admin
from shared.atlas_core.db import Database
from shared.atlas_core.http import AppError, Request, ServiceApp, run_service
from shared.atlas_core.service_client import request_json


SERVICE_NAME = "finance-service"
HOST = env("FINANCE_SERVICE_HOST", "127.0.0.1")
PORT = env("FINANCE_SERVICE_PORT", 7004, int)
DATABASE_PATH = env("FINANCE_DB_PATH", "runtime/finance-service.db")
NOTIFICATION_SERVICE_URL = service_url("notification-service", 7005)

db = Database(DATABASE_PATH or "runtime/finance-service.db")
app = ServiceApp(SERVICE_NAME)


def migrate() -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS project_budgets (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            total_budget REAL NOT NULL,
            currency TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(tenant_id, project_id)
        );

        CREATE TABLE IF NOT EXISTS project_expenses (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        );
        """
    )


def require_json_object(request: Request) -> Dict[str, Any]:
    if not isinstance(request.body, dict):
        raise AppError(400, "json_object_required")
    return request.body


def require_number(payload: Dict[str, Any], field_name: str) -> float:
    value = payload.get(field_name)
    if not isinstance(value, (int, float)):
        raise AppError(400, "invalid_numeric_field", {"field": field_name})
    if float(value) <= 0:
        raise AppError(400, "numeric_field_must_be_positive", {"field": field_name})
    return round(float(value), 2)


def require_field(payload: Dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise AppError(400, "invalid_field", {"field": field_name})
    return value.strip()


def budget_by_project(tenant_id: str, project_id: str) -> Dict[str, Any]:
    budget = db.fetchone(
        "SELECT * FROM project_budgets WHERE tenant_id = ? AND project_id = ?",
        (tenant_id, project_id),
    )
    if not budget:
        raise AppError(404, "project_budget_not_found", {"project_id": project_id})
    return budget


def finance_status(tenant_id: str, project_id: str) -> Dict[str, Any]:
    budget = db.fetchone(
        "SELECT * FROM project_budgets WHERE tenant_id = ? AND project_id = ?",
        (tenant_id, project_id),
    )
    expenses = db.fetchall(
        """
        SELECT * FROM project_expenses
        WHERE tenant_id = ? AND project_id = ?
        ORDER BY recorded_at ASC
        """,
        (tenant_id, project_id),
    )
    spent = round(sum(item["amount"] for item in expenses), 2)
    budget_total = round(float(budget["total_budget"]), 2) if budget else 0.0
    utilization = round((spent / budget_total) * 100, 2) if budget_total > 0 else 0.0
    return {
        "project_id": project_id,
        "budget": budget,
        "expenses": expenses,
        "totals": {
            "budget_total": budget_total,
            "spent": spent,
            "remaining": round(budget_total - spent, 2),
            "utilization_pct": utilization,
        },
    }


def publish_budget_alert(tenant_id: str, project_id: str, utilization_pct: float) -> None:
    severity = "critical" if utilization_pct >= 100 else "warning"
    request_json(
        "POST",
        NOTIFICATION_SERVICE_URL,
        "/alerts",
        payload={
            "tenant_id": tenant_id,
            "project_id": project_id,
            "severity": severity,
            "title": "Budget threshold exceeded",
            "message": "Budget utilization reached {0}%".format(utilization_pct),
            "source": SERVICE_NAME,
        },
    )


@app.route("GET", "/health")
def health(_: Request):
    return 200, {
        "status": "ok",
        "service": SERVICE_NAME,
        "budgets": db.scalar("SELECT COUNT(*) FROM project_budgets") or 0,
        "expenses": db.scalar("SELECT COUNT(*) FROM project_expenses") or 0,
    }


@app.route("POST", "/projects/{project_id}/budget")
def set_budget(request: Request):
    actor = require_admin(request)
    payload = require_json_object(request)
    total_budget = require_number(payload, "total_budget")
    currency = require_field(payload, "currency").upper()
    timestamp = utc_now()

    existing = db.fetchone(
        "SELECT id FROM project_budgets WHERE tenant_id = ? AND project_id = ?",
        (actor["tenant_id"], request.path_params["project_id"]),
    )
    if existing:
        db.execute(
            """
            UPDATE project_budgets
            SET total_budget = ?, currency = ?, updated_at = ?
            WHERE id = ?
            """,
            (total_budget, currency, timestamp, existing["id"]),
        )
    else:
        db.execute(
            """
            INSERT INTO project_budgets (id, tenant_id, project_id, total_budget, currency, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                actor["tenant_id"],
                request.path_params["project_id"],
                total_budget,
                currency,
                timestamp,
                timestamp,
            ),
        )

    return 201, finance_status(actor["tenant_id"], request.path_params["project_id"])


@app.route("POST", "/projects/{project_id}/expenses")
def create_expense(request: Request):
    actor = require_admin(request)
    payload = require_json_object(request)
    amount = require_number(payload, "amount")
    category = require_field(payload, "category")
    budget = budget_by_project(actor["tenant_id"], request.path_params["project_id"])

    db.execute(
        """
        INSERT INTO project_expenses (id, tenant_id, project_id, amount, category, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            actor["tenant_id"],
            request.path_params["project_id"],
            amount,
            category,
            utc_now(),
        ),
    )

    status = finance_status(actor["tenant_id"], request.path_params["project_id"])
    utilization_pct = status["totals"]["utilization_pct"]
    if utilization_pct >= 85:
        publish_budget_alert(actor["tenant_id"], request.path_params["project_id"], utilization_pct)

    return 201, {
        "budget": budget,
        "expense_recorded": amount,
        "finance": status,
    }


@app.route("GET", "/projects/{project_id}/status")
def get_status(request: Request):
    actor = require_actor(request)
    return 200, finance_status(actor["tenant_id"], request.path_params["project_id"])


if __name__ == "__main__":
    migrate()
    run_service(app, HOST or "127.0.0.1", PORT or 7004)
