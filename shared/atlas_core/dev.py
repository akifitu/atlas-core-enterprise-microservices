import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    script_path: str
    host_env: str
    port_env: str
    port: int
    url_env: str
    db_env: Optional[str] = None
    db_filename: Optional[str] = None

    @property
    def health_url(self) -> str:
        return "http://127.0.0.1:{0}/health".format(self.port)


SERVICES: List[ServiceSpec] = [
    ServiceSpec(
        name="identity-service",
        script_path="services/identity_service/app.py",
        host_env="IDENTITY_SERVICE_HOST",
        port_env="IDENTITY_SERVICE_PORT",
        port=7001,
        url_env="IDENTITY_SERVICE_URL",
        db_env="IDENTITY_DB_PATH",
        db_filename="identity-service.db",
    ),
    ServiceSpec(
        name="portfolio-service",
        script_path="services/portfolio_service/app.py",
        host_env="PORTFOLIO_SERVICE_HOST",
        port_env="PORTFOLIO_SERVICE_PORT",
        port=7002,
        url_env="PORTFOLIO_SERVICE_URL",
        db_env="PORTFOLIO_DB_PATH",
        db_filename="portfolio-service.db",
    ),
    ServiceSpec(
        name="delivery-service",
        script_path="services/delivery_service/app.py",
        host_env="DELIVERY_SERVICE_HOST",
        port_env="DELIVERY_SERVICE_PORT",
        port=7003,
        url_env="DELIVERY_SERVICE_URL",
        db_env="DELIVERY_DB_PATH",
        db_filename="delivery-service.db",
    ),
    ServiceSpec(
        name="finance-service",
        script_path="services/finance_service/app.py",
        host_env="FINANCE_SERVICE_HOST",
        port_env="FINANCE_SERVICE_PORT",
        port=7004,
        url_env="FINANCE_SERVICE_URL",
        db_env="FINANCE_DB_PATH",
        db_filename="finance-service.db",
    ),
    ServiceSpec(
        name="notification-service",
        script_path="services/notification_service/app.py",
        host_env="NOTIFICATION_SERVICE_HOST",
        port_env="NOTIFICATION_SERVICE_PORT",
        port=7005,
        url_env="NOTIFICATION_SERVICE_URL",
        db_env="NOTIFICATION_DB_PATH",
        db_filename="notification-service.db",
    ),
    ServiceSpec(
        name="analytics-service",
        script_path="services/analytics_service/app.py",
        host_env="ANALYTICS_SERVICE_HOST",
        port_env="ANALYTICS_SERVICE_PORT",
        port=7006,
        url_env="ANALYTICS_SERVICE_URL",
    ),
    ServiceSpec(
        name="audit-service",
        script_path="services/audit_service/app.py",
        host_env="AUDIT_SERVICE_HOST",
        port_env="AUDIT_SERVICE_PORT",
        port=7007,
        url_env="AUDIT_SERVICE_URL",
        db_env="AUDIT_DB_PATH",
        db_filename="audit-service.db",
    ),
    ServiceSpec(
        name="api-gateway",
        script_path="services/api_gateway/app.py",
        host_env="API_GATEWAY_HOST",
        port_env="API_GATEWAY_PORT",
        port=7000,
        url_env="API_GATEWAY_URL",
    ),
]


def build_runtime_environment(root_dir: Path, runtime_dir: Path) -> Dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(root_dir) if not existing_pythonpath else "{0}:{1}".format(str(root_dir), existing_pythonpath)

    runtime_dir.mkdir(parents=True, exist_ok=True)
    env["PYTHONPYCACHEPREFIX"] = str(runtime_dir / "pycache")
    env["AUDIT_SERVICE_TOKEN"] = env.get("AUDIT_SERVICE_TOKEN", "atlas-internal-audit")

    for spec in SERVICES:
        env[spec.host_env] = "127.0.0.1"
        env[spec.port_env] = str(spec.port)
        env[spec.url_env] = "http://127.0.0.1:{0}".format(spec.port)
        if spec.db_env and spec.db_filename:
            env[spec.db_env] = str(runtime_dir / spec.db_filename)

    return env
