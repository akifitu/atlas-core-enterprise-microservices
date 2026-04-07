import uuid
from typing import Any, Dict

from shared.atlas_core.config import env, utc_now
from shared.atlas_core.context import require_admin
from shared.atlas_core.db import Database
from shared.atlas_core.http import AppError, Request, ServiceApp, run_service
from shared.atlas_core.security import hash_password, issue_token, verify_password


SERVICE_NAME = "identity-service"
HOST = env("IDENTITY_SERVICE_HOST", "127.0.0.1")
PORT = env("IDENTITY_SERVICE_PORT", 7001, int)
DATABASE_PATH = env("IDENTITY_DB_PATH", "runtime/identity-service.db")

db = Database(DATABASE_PATH or "runtime/identity-service.db")
app = ServiceApp(SERVICE_NAME)


def migrate() -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS tenants (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            email TEXT NOT NULL,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(tenant_id, email),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            issued_at TEXT NOT NULL,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
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


def serialize_user(user: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": user["id"],
        "tenant_id": user["tenant_id"],
        "email": user["email"],
        "display_name": user["display_name"],
        "role": user["role"],
        "created_at": user["created_at"],
    }


def build_session_response(token: str, tenant: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "token": token,
        "tenant": {
            "id": tenant["id"],
            "name": tenant["name"],
            "slug": tenant["slug"],
        },
        "user": serialize_user(user),
        "context": {
            "tenant_id": tenant["id"],
            "user_id": user["id"],
            "role": user["role"],
        },
    }


@app.route("GET", "/health")
def health(_: Request):
    tenant_count = db.scalar("SELECT COUNT(*) FROM tenants") or 0
    return 200, {"status": "ok", "service": SERVICE_NAME, "tenants": tenant_count}


@app.route("POST", "/bootstrap-admin")
def bootstrap_admin(request: Request):
    payload = require_json_object(request)
    tenant_name = require_field(payload, "tenant_name")
    tenant_slug = require_field(payload, "tenant_slug").lower().replace(" ", "-")
    admin_email = require_field(payload, "admin_email").lower()
    admin_password = require_field(payload, "admin_password")
    admin_name = require_field(payload, "admin_name")

    existing_tenant = db.fetchone("SELECT id FROM tenants WHERE slug = ?", (tenant_slug,))
    if existing_tenant:
        raise AppError(409, "tenant_slug_already_exists", {"tenant_slug": tenant_slug})

    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    issued_at = utc_now()
    token = issue_token()

    db.execute(
        "INSERT INTO tenants (id, name, slug, created_at) VALUES (?, ?, ?, ?)",
        (tenant_id, tenant_name, tenant_slug, issued_at),
    )
    db.execute(
        """
        INSERT INTO users (id, tenant_id, email, display_name, password_hash, role, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, tenant_id, admin_email, admin_name, hash_password(admin_password), "admin", issued_at),
    )
    db.execute(
        "INSERT INTO sessions (token, tenant_id, user_id, issued_at) VALUES (?, ?, ?, ?)",
        (token, tenant_id, user_id, issued_at),
    )

    tenant = db.fetchone("SELECT * FROM tenants WHERE id = ?", (tenant_id,))
    user = db.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
    return 201, build_session_response(token, tenant or {}, user or {})


@app.route("POST", "/sessions")
def create_session(request: Request):
    payload = require_json_object(request)
    tenant_slug = require_field(payload, "tenant_slug").lower()
    email = require_field(payload, "email").lower()
    password = require_field(payload, "password")

    user = db.fetchone(
        """
        SELECT users.*, tenants.name AS tenant_name, tenants.slug AS tenant_slug
        FROM users
        JOIN tenants ON tenants.id = users.tenant_id
        WHERE tenants.slug = ? AND users.email = ?
        """,
        (tenant_slug, email),
    )
    if not user or not verify_password(password, user["password_hash"]):
        raise AppError(401, "invalid_credentials")

    tenant = db.fetchone("SELECT * FROM tenants WHERE id = ?", (user["tenant_id"],))
    token = issue_token()
    db.execute(
        "INSERT INTO sessions (token, tenant_id, user_id, issued_at) VALUES (?, ?, ?, ?)",
        (token, user["tenant_id"], user["id"], utc_now()),
    )
    return 201, build_session_response(token, tenant or {}, user)


@app.route("GET", "/validate")
def validate_session(request: Request):
    token = request.query_value("token")
    if not token:
        raise AppError(400, "token_query_parameter_required")

    session = db.fetchone(
        """
        SELECT sessions.token, sessions.tenant_id, sessions.user_id, sessions.issued_at,
               users.email, users.display_name, users.role,
               tenants.name AS tenant_name, tenants.slug AS tenant_slug
        FROM sessions
        JOIN users ON users.id = sessions.user_id
        JOIN tenants ON tenants.id = sessions.tenant_id
        WHERE sessions.token = ?
        """,
        (token,),
    )
    if not session:
        raise AppError(401, "invalid_token")

    return 200, {
        "valid": True,
        "context": {
            "tenant_id": session["tenant_id"],
            "user_id": session["user_id"],
            "role": session["role"],
        },
        "tenant": {
            "id": session["tenant_id"],
            "name": session["tenant_name"],
            "slug": session["tenant_slug"],
        },
        "user": {
            "id": session["user_id"],
            "email": session["email"],
            "display_name": session["display_name"],
            "role": session["role"],
        },
    }


@app.route("POST", "/users")
def create_user(request: Request):
    actor = require_admin(request)
    payload = require_json_object(request)
    email = require_field(payload, "email").lower()
    password = require_field(payload, "password")
    display_name = require_field(payload, "display_name")
    role = require_field(payload, "role")

    if role not in ("admin", "portfolio_manager", "delivery_lead", "finance_manager", "viewer"):
        raise AppError(400, "invalid_role", {"role": role})

    existing_user = db.fetchone(
        "SELECT id FROM users WHERE tenant_id = ? AND email = ?",
        (actor["tenant_id"], email),
    )
    if existing_user:
        raise AppError(409, "user_email_already_exists", {"email": email})

    user_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO users (id, tenant_id, email, display_name, password_hash, role, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, actor["tenant_id"], email, display_name, hash_password(password), role, utc_now()),
    )
    user = db.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
    return 201, {"user": serialize_user(user or {})}


@app.route("GET", "/tenants/{tenant_id}/users")
def list_users(request: Request):
    actor = require_admin(request)
    tenant_id = request.path_params["tenant_id"]
    if tenant_id != actor["tenant_id"]:
        raise AppError(403, "cross_tenant_access_forbidden")

    users = db.fetchall(
        """
        SELECT id, tenant_id, email, display_name, role, created_at
        FROM users
        WHERE tenant_id = ?
        ORDER BY created_at ASC
        """,
        (tenant_id,),
    )
    return 200, {"users": users}


if __name__ == "__main__":
    migrate()
    run_service(app, HOST or "127.0.0.1", PORT or 7001)
