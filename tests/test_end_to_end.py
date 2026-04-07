import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from typing import Dict, IO, List, Optional, Tuple
from urllib import request


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from shared.atlas_core.dev import SERVICES, build_runtime_environment
from shared.atlas_core.service_client import request_json


class ServiceHarness:
    def __init__(self) -> None:
        self.runtime_dir = Path(tempfile.mkdtemp(prefix="atlas-core-", dir="/tmp"))
        self.env = build_runtime_environment(ROOT_DIR, self.runtime_dir)
        self.processes: List[Tuple[subprocess.Popen, Path]] = []
        self.log_files: List[IO[str]] = []

    def start(self) -> None:
        logs_dir = self.runtime_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        try:
            for spec in SERVICES:
                log_path = logs_dir / "{0}.log".format(spec.name)
                log_file = open(log_path, "w", encoding="utf-8")
                self.log_files.append(log_file)
                process = subprocess.Popen(  # noqa: S603
                    [sys.executable, spec.script_path],
                    cwd=str(ROOT_DIR),
                    env=self.env,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
                self.processes.append((process, log_path))

            for spec in SERVICES:
                deadline = time.time() + 20
                while time.time() < deadline:
                    try:
                        with request.urlopen(spec.health_url, timeout=1) as response:
                            if response.status == 200:
                                break
                    except Exception:
                        time.sleep(0.2)
                else:
                    raise AssertionError("Service failed to become healthy: {0}".format(spec.name))
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        for process, _ in self.processes:
            if process.poll() is None:
                process.terminate()
        for process, _ in self.processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        for log_file in self.log_files:
            log_file.close()
        shutil.rmtree(self.runtime_dir, ignore_errors=True)


class AtlasCoreE2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = ServiceHarness()
        cls.harness.start()
        cls.gateway_url = "http://127.0.0.1:7000"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.harness.stop()

    def gateway_request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict] = None,
        token: Optional[str] = None,
    ) -> Dict:
        headers = {}
        if token:
            headers["Authorization"] = "Bearer {0}".format(token)
        status_code, response_payload = request_json(method, self.gateway_url, path, payload, headers)
        self.assertLess(status_code, 400, msg="Request failed: {0} {1} => {2} {3}".format(method, path, status_code, response_payload))
        return response_payload

    def gateway_request_raw(
        self,
        method: str,
        path: str,
        payload: Optional[Dict] = None,
        token: Optional[str] = None,
    ) -> Tuple[int, Dict]:
        headers = {}
        if token:
            headers["Authorization"] = "Bearer {0}".format(token)
        return request_json(method, self.gateway_url, path, payload, headers)

    def bootstrap_admin_session(self, tenant_name_prefix: str = "Atlas Test Tenant") -> Dict:
        suffix = str(uuid.uuid4())[:8]
        return self.gateway_request(
            "POST",
            "/api/v1/identity/bootstrap-admin",
            {
                "tenant_name": "{0} {1}".format(tenant_name_prefix, suffix),
                "tenant_slug": "atlas-test-{0}".format(suffix),
                "admin_email": "admin-{0}@test.local".format(suffix),
                "admin_password": "StrongPass!123",
                "admin_name": "Test Admin {0}".format(suffix),
            },
        )

    def test_end_to_end_governance_flow(self) -> None:
        bootstrap = self.bootstrap_admin_session()
        token = bootstrap["token"]

        portfolio = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios",
            {"name": "Global Transformation Board"},
            token=token,
        )["portfolio"]

        project = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios/{0}/projects".format(portfolio["id"]),
            {
                "name": "Factory ERP Rollout",
                "code": "FACTORY-ERP",
                "status": "active",
                "start_date": "2026-04-01",
                "target_date": "2026-08-31",
            },
            token=token,
        )["project"]

        work_item = self.gateway_request(
            "POST",
            "/api/v1/delivery/projects/{0}/work-items".format(project["id"]),
            {
                "title": "Plant data migration",
                "priority": "critical",
                "assignee": "Migration Guild",
            },
            token=token,
        )["work_item"]

        self.gateway_request(
            "PATCH",
            "/api/v1/delivery/work-items/{0}/status".format(work_item["id"]),
            {"status": "blocked", "blocked_reason": "Manufacturing source system freeze"},
            token=token,
        )

        self.gateway_request(
            "POST",
            "/api/v1/finance/projects/{0}/budget".format(project["id"]),
            {"total_budget": 100000, "currency": "USD"},
            token=token,
        )
        self.gateway_request(
            "POST",
            "/api/v1/finance/projects/{0}/expenses".format(project["id"]),
            {"amount": 90000, "category": "system_integrator"},
            token=token,
        )
        self.gateway_request(
            "POST",
            "/api/v1/finance/projects/{0}/expenses".format(project["id"]),
            {"amount": 20000, "category": "extended_support"},
            token=token,
        )

        alerts = self.gateway_request("GET", "/api/v1/notifications/alerts?status=open", token=token)["alerts"]
        self.assertGreaterEqual(len(alerts), 3)
        self.assertTrue(any(alert["source"] == "delivery-service" for alert in alerts))
        self.assertTrue(any(alert["source"] == "finance-service" for alert in alerts))

        dashboard = self.gateway_request(
            "GET",
            "/api/v1/analytics/dashboard?portfolio_id={0}".format(portfolio["id"]),
            token=token,
        )
        self.assertEqual(dashboard["totals"]["projects"], 1)
        self.assertEqual(dashboard["totals"]["blocked_work_items"], 1)
        self.assertEqual(dashboard["totals"]["budget_utilization_pct"], 110.0)
        self.assertEqual(dashboard["projects"][0]["health"], "critical")

        acknowledged = self.gateway_request(
            "PATCH",
            "/api/v1/notifications/alerts/{0}/ack".format(alerts[0]["id"]),
            {},
            token=token,
        )["alert"]
        self.assertEqual(acknowledged["status"], "acked")

    def test_platform_topology_exposes_service_health_and_cache_stats(self) -> None:
        bootstrap = self.bootstrap_admin_session("Atlas Ops Tenant")
        token = bootstrap["token"]

        self.gateway_request("GET", "/api/v1/portfolio/portfolios", token=token)
        topology = self.gateway_request("GET", "/api/v1/platform/topology", token=token)

        self.assertEqual(topology["summary"]["healthy_services"], 8)
        self.assertEqual(topology["summary"]["degraded_services"], [])
        self.assertGreaterEqual(topology["auth_cache"]["entries"], 1)
        self.assertGreaterEqual(topology["auth_cache"]["hits"], 1)
        self.assertIn("audit-service", topology["services"])
        self.assertIn("api-gateway", topology["services"])
        self.assertTrue(all(service["healthy"] for service in topology["services"].values()))

    def test_viewer_role_cannot_mutate_portfolio(self) -> None:
        bootstrap = self.bootstrap_admin_session("Atlas Viewer Tenant")
        admin_token = bootstrap["token"]
        tenant_id = bootstrap["tenant"]["id"]

        self.gateway_request(
            "POST",
            "/api/v1/identity/users",
            {
                "email": "viewer@test.local",
                "password": "StrongPass!123",
                "display_name": "Read Only Viewer",
                "role": "viewer",
            },
            token=admin_token,
        )

        viewer_session = self.gateway_request(
            "POST",
            "/api/v1/identity/sessions",
            {
                "tenant_slug": bootstrap["tenant"]["slug"],
                "email": "viewer@test.local",
                "password": "StrongPass!123",
            },
        )
        viewer_token = viewer_session["token"]

        users = self.gateway_request(
            "GET",
            "/api/v1/identity/tenants/{0}/users".format(tenant_id),
            token=admin_token,
        )["users"]
        self.assertTrue(any(user["role"] == "viewer" for user in users))

        status_code, payload = self.gateway_request_raw(
            "POST",
            "/api/v1/portfolio/portfolios",
            {"name": "Unauthorized Portfolio Attempt"},
            token=viewer_token,
        )
        self.assertEqual(status_code, 403)
        self.assertEqual(payload["error"], "admin_role_required")

    def test_mutations_are_recorded_in_audit_trail(self) -> None:
        bootstrap = self.bootstrap_admin_session("Atlas Audit Tenant")
        token = bootstrap["token"]

        portfolio = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios",
            {"name": "Audit Board"},
            token=token,
        )["portfolio"]
        project = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios/{0}/projects".format(portfolio["id"]),
            {
                "name": "Audit Program",
                "code": "AUDIT-PROGRAM",
                "status": "active",
                "start_date": "2026-05-01",
                "target_date": "2026-10-01",
            },
            token=token,
        )["project"]
        self.gateway_request(
            "POST",
            "/api/v1/finance/projects/{0}/budget".format(project["id"]),
            {"total_budget": 50000, "currency": "USD"},
            token=token,
        )

        events = self.gateway_request(
            "GET",
            "/api/v1/platform/audit-events?limit=20",
            token=token,
        )["events"]

        self.assertGreaterEqual(len(events), 3)
        self.assertTrue(any(event["service_name"] == "portfolio-service" for event in events))
        self.assertTrue(any(event["service_name"] == "finance-service" for event in events))
        self.assertTrue(any(event["resource"] == "portfolio" for event in events))
        self.assertTrue(any(event["entity_id"] == portfolio["id"] for event in events))
        self.assertTrue(all(event["tenant_id"] == bootstrap["tenant"]["id"] for event in events))


if __name__ == "__main__":
    unittest.main()
