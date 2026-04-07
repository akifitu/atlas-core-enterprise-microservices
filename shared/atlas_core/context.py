from typing import Dict

from shared.atlas_core.http import AppError, Request


def require_actor(request: Request) -> Dict[str, str]:
    tenant_id = request.header("x-tenant-id")
    user_id = request.header("x-user-id")
    role = request.header("x-user-role", "viewer")

    if not tenant_id or not user_id:
        raise AppError(401, "missing_actor_context")

    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "role": role,
    }


def require_admin(request: Request) -> Dict[str, str]:
    actor = require_actor(request)
    if actor["role"] not in ("admin", "portfolio_manager"):
        raise AppError(403, "admin_role_required")
    return actor
