import uuid
from typing import Any, Dict

from shared.atlas_core.config import env, utc_now
from shared.atlas_core.context import require_actor, require_admin
from shared.atlas_core.db import Database
from shared.atlas_core.http import AppError, Request, ServiceApp, run_service


SERVICE_NAME = "portfolio-service"
HOST = env("PORTFOLIO_SERVICE_HOST", "127.0.0.1")
PORT = env("PORTFOLIO_SERVICE_PORT", 7002, int)
DATABASE_PATH = env("PORTFOLIO_DB_PATH", "runtime/portfolio-service.db")

db = Database(DATABASE_PATH or "runtime/portfolio-service.db")
app = ServiceApp(SERVICE_NAME)


def migrate() -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS portfolios (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            portfolio_id TEXT NOT NULL,
            name TEXT NOT NULL,
            code TEXT NOT NULL,
            status TEXT NOT NULL,
            start_date TEXT NOT NULL,
            target_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(tenant_id, code),
            FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_portfolios_tenant_created_at
        ON portfolios (tenant_id, created_at);

        CREATE INDEX IF NOT EXISTS idx_projects_tenant_portfolio_created_at
        ON projects (tenant_id, portfolio_id, created_at);

        CREATE INDEX IF NOT EXISTS idx_projects_tenant_status
        ON projects (tenant_id, status);
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


def portfolio_by_id(tenant_id: str, portfolio_id: str) -> Dict[str, Any]:
    portfolio = db.fetchone(
        "SELECT * FROM portfolios WHERE tenant_id = ? AND id = ?",
        (tenant_id, portfolio_id),
    )
    if not portfolio:
        raise AppError(404, "portfolio_not_found", {"portfolio_id": portfolio_id})
    return portfolio


def project_by_id(tenant_id: str, project_id: str) -> Dict[str, Any]:
    project = db.fetchone(
        "SELECT * FROM projects WHERE tenant_id = ? AND id = ?",
        (tenant_id, project_id),
    )
    if not project:
        raise AppError(404, "project_not_found", {"project_id": project_id})
    return project


@app.route("GET", "/health")
def health(_: Request):
    return 200, {
        "status": "ok",
        "service": SERVICE_NAME,
        "portfolios": db.scalar("SELECT COUNT(*) FROM portfolios") or 0,
        "projects": db.scalar("SELECT COUNT(*) FROM projects") or 0,
    }


@app.route("POST", "/portfolios")
def create_portfolio(request: Request):
    actor = require_admin(request)
    payload = require_json_object(request)
    name = require_field(payload, "name")
    status = payload.get("status", "active")
    if status not in ("active", "paused", "completed"):
        raise AppError(400, "invalid_status", {"status": status})

    portfolio_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO portfolios (id, tenant_id, name, status, owner_user_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (portfolio_id, actor["tenant_id"], name, status, actor["user_id"], utc_now()),
    )
    portfolio = portfolio_by_id(actor["tenant_id"], portfolio_id)
    return 201, {"portfolio": portfolio}


@app.route("GET", "/portfolios")
def list_portfolios(request: Request):
    actor = require_actor(request)
    portfolios = db.fetchall(
        """
        SELECT * FROM portfolios
        WHERE tenant_id = ?
        ORDER BY created_at ASC
        """,
        (actor["tenant_id"],),
    )
    return 200, {"portfolios": portfolios}


@app.route("POST", "/portfolios/{portfolio_id}/projects")
def create_project(request: Request):
    actor = require_admin(request)
    payload = require_json_object(request)
    portfolio = portfolio_by_id(actor["tenant_id"], request.path_params["portfolio_id"])

    name = require_field(payload, "name")
    code = require_field(payload, "code").upper()
    start_date = require_field(payload, "start_date")
    target_date = require_field(payload, "target_date")
    status = payload.get("status", "planned")
    if status not in ("planned", "active", "paused", "completed"):
        raise AppError(400, "invalid_status", {"status": status})

    existing = db.fetchone(
        "SELECT id FROM projects WHERE tenant_id = ? AND code = ?",
        (actor["tenant_id"], code),
    )
    if existing:
        raise AppError(409, "project_code_already_exists", {"code": code})

    project_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO projects (id, tenant_id, portfolio_id, name, code, status, start_date, target_date, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            actor["tenant_id"],
            portfolio["id"],
            name,
            code,
            status,
            start_date,
            target_date,
            utc_now(),
        ),
    )
    project = project_by_id(actor["tenant_id"], project_id)
    return 201, {"project": project}


@app.route("GET", "/projects/{project_id}")
def get_project(request: Request):
    actor = require_actor(request)
    return 200, {"project": project_by_id(actor["tenant_id"], request.path_params["project_id"])}


@app.route("GET", "/portfolios/{portfolio_id}/snapshot")
def portfolio_snapshot(request: Request):
    actor = require_actor(request)
    portfolio = portfolio_by_id(actor["tenant_id"], request.path_params["portfolio_id"])
    projects = db.fetchall(
        """
        SELECT * FROM projects
        WHERE tenant_id = ? AND portfolio_id = ?
        ORDER BY created_at ASC
        """,
        (actor["tenant_id"], portfolio["id"]),
    )
    return 200, {
        "portfolio": portfolio,
        "projects": projects,
        "totals": {
            "project_count": len(projects),
            "active_projects": len([item for item in projects if item["status"] == "active"]),
        },
    }


if __name__ == "__main__":
    migrate()
    run_service(app, HOST or "127.0.0.1", PORT or 7002)
