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
        self.env["IDENTITY_BOOTSTRAP_TOKEN"] = "atlas-test-bootstrap-token"
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
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict:
        headers = {}
        if token:
            headers["Authorization"] = "Bearer {0}".format(token)
        if extra_headers:
            headers.update(extra_headers)
        status_code, response_payload = request_json(method, self.gateway_url, path, payload, headers)
        self.assertLess(status_code, 400, msg="Request failed: {0} {1} => {2} {3}".format(method, path, status_code, response_payload))
        return response_payload

    def gateway_request_raw(
        self,
        method: str,
        path: str,
        payload: Optional[Dict] = None,
        token: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Dict]:
        headers = {}
        if token:
            headers["Authorization"] = "Bearer {0}".format(token)
        if extra_headers:
            headers.update(extra_headers)
        return request_json(method, self.gateway_url, path, payload, headers)

    def gateway_fetch_raw(self, path: str) -> Tuple[int, str, str, Dict[str, str]]:
        with request.urlopen(self.gateway_url + path, timeout=5) as response:
            return (
                response.status,
                response.headers.get("Content-Type", ""),
                response.read().decode("utf-8"),
                dict(response.headers.items()),
            )

    def bootstrap_admin_session(self, tenant_name_prefix: str = "Atlas Test Tenant") -> Dict:
        suffix = str(uuid.uuid4())[:8]
        status_code, payload = request_json(
            "POST",
            self.gateway_url,
            "/api/v1/identity/bootstrap-admin",
            {
                "tenant_name": "{0} {1}".format(tenant_name_prefix, suffix),
                "tenant_slug": "atlas-test-{0}".format(suffix),
                "admin_email": "admin-{0}@test.local".format(suffix),
                "admin_password": "StrongPass!123",
                "admin_name": "Test Admin {0}".format(suffix),
            },
            {"X-Bootstrap-Token": self.harness.env["IDENTITY_BOOTSTRAP_TOKEN"]},
        )
        self.assertLess(status_code, 400, msg="Bootstrap failed: {0} {1}".format(status_code, payload))
        return payload

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
        self.assertEqual(len(alerts), 2)
        self.assertTrue(any(alert["source"] == "delivery-service" for alert in alerts))
        finance_alert = next(alert for alert in alerts if alert["source"] == "finance-service")
        self.assertEqual(finance_alert["severity"], "critical")
        self.assertEqual(finance_alert["occurrence_count"], 2)

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

    def test_admin_console_assets_are_served_by_gateway(self) -> None:
        html_status, html_type, html_body, html_headers = self.gateway_fetch_raw("/admin")
        self.assertEqual(html_status, 200)
        self.assertIn("text/html", html_type)
        self.assertIn("Atlas Core Control Room", html_body)
        self.assertIn("/admin/styles.css", html_body)
        self.assertIn("/admin/app.js", html_body)
        self.assertIn("default-src 'self'", html_headers["Content-Security-Policy"])

        css_status, css_type, css_body, _ = self.gateway_fetch_raw("/admin/styles.css")
        self.assertEqual(css_status, 200)
        self.assertIn("text/css", css_type)
        self.assertIn("--atlas-ink", css_body)

        js_status, js_type, js_body, js_headers = self.gateway_fetch_raw("/admin/app.js")
        self.assertEqual(js_status, 200)
        self.assertIn("application/javascript", js_type)
        self.assertIn("/api/v1/platform/control-room", js_body)
        self.assertIn("/api/v1/platform/control-room/actions", js_body)
        self.assertEqual(js_headers["X-Frame-Options"], "DENY")

    def test_control_room_endpoint_aggregates_operator_views(self) -> None:
        bootstrap = self.bootstrap_admin_session("Atlas Control Room Tenant")
        token = bootstrap["token"]

        portfolio = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios",
            {"name": "Control Room Portfolio"},
            token=token,
        )["portfolio"]
        project = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios/{0}/projects".format(portfolio["id"]),
            {
                "name": "Control Room Program",
                "code": "CONTROL-ROOM",
                "status": "active",
                "start_date": "2026-06-01",
                "target_date": "2026-12-01",
            },
            token=token,
        )["project"]
        work_item = self.gateway_request(
            "POST",
            "/api/v1/delivery/projects/{0}/work-items".format(project["id"]),
            {
                "title": "Regional rollout gate",
                "priority": "critical",
                "assignee": "PMO",
            },
            token=token,
        )["work_item"]
        self.gateway_request(
            "PATCH",
            "/api/v1/delivery/work-items/{0}/status".format(work_item["id"]),
            {"status": "blocked", "blocked_reason": "Supplier readiness review still open"},
            token=token,
        )
        self.gateway_request(
            "POST",
            "/api/v1/finance/projects/{0}/budget".format(project["id"]),
            {"total_budget": 120000, "currency": "USD"},
            token=token,
        )
        self.gateway_request(
            "POST",
            "/api/v1/finance/projects/{0}/expenses".format(project["id"]),
            {"amount": 95000, "category": "integration_partner"},
            token=token,
        )

        control_room = self.gateway_request(
            "GET",
            "/api/v1/platform/control-room?top_n=3&portfolio_id={0}".format(portfolio["id"]),
            token=token,
        )

        self.assertEqual(control_room["selection_mode"], "requested")
        self.assertEqual(control_room["selected_portfolio_id"], portfolio["id"])
        self.assertEqual(control_room["topology"]["summary"]["healthy_services"], 8)
        self.assertGreaterEqual(control_room["alert_summary"]["open_alerts"], 1)
        self.assertGreaterEqual(control_room["audit_summary"]["total_events"], 5)
        self.assertEqual(control_room["executive_summary"]["totals"]["projects"], 1)
        self.assertEqual(control_room["portfolio_dashboard"]["portfolio"]["id"], portfolio["id"])
        self.assertEqual(control_room["portfolio_dashboard"]["totals"]["blocked_work_items"], 1)

    def test_control_room_actions_execute_export_and_retention(self) -> None:
        bootstrap = self.bootstrap_admin_session("Atlas Control Action Tenant")
        token = bootstrap["token"]

        portfolio = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios",
            {"name": "Control Action Portfolio"},
            token=token,
        )["portfolio"]
        project = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios/{0}/projects".format(portfolio["id"]),
            {
                "name": "Control Action Program",
                "code": "CONTROL-ACTION",
                "status": "active",
                "start_date": "2026-06-01",
                "target_date": "2026-12-01",
            },
            token=token,
        )["project"]
        self.gateway_request(
            "POST",
            "/api/v1/finance/projects/{0}/budget".format(project["id"]),
            {"total_budget": 90000, "currency": "USD"},
            token=token,
        )

        export_action = self.gateway_request(
            "POST",
            "/api/v1/platform/control-room/actions",
            {"action": "audit_export", "limit": 5, "top_n": 3, "portfolio_id": portfolio["id"]},
            token=token,
        )
        self.assertEqual(export_action["action"], "audit_export")
        self.assertGreaterEqual(export_action["result"]["count"], 3)
        self.assertEqual(export_action["control_room"]["selected_portfolio_id"], portfolio["id"])

        preview_action = self.gateway_request(
            "POST",
            "/api/v1/platform/control-room/actions",
            {"action": "audit_retention_dry_run", "retention_days": 0, "top_n": 3},
            token=token,
        )
        self.assertEqual(preview_action["action"], "audit_retention_dry_run")
        self.assertTrue(preview_action["result"]["dry_run"])
        self.assertGreaterEqual(preview_action["result"]["would_delete"], 3)

        apply_action = self.gateway_request(
            "POST",
            "/api/v1/platform/control-room/actions",
            {"action": "audit_retention_apply", "retention_days": 0, "top_n": 3},
            token=token,
        )
        self.assertEqual(apply_action["action"], "audit_retention_apply")
        self.assertFalse(apply_action["result"]["dry_run"])
        self.assertGreaterEqual(apply_action["result"]["deleted_count"], 3)
        self.assertLess(apply_action["control_room"]["audit_summary"]["total_events"], preview_action["control_room"]["audit_summary"]["total_events"])

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

    def test_idempotency_replays_mutation_without_duplication(self) -> None:
        bootstrap = self.bootstrap_admin_session("Atlas Idempotency Tenant")
        token = bootstrap["token"]
        idempotency_headers = {"Idempotency-Key": "portfolio-create-fixed-key"}

        first = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios",
            {"name": "Duplicate Safe Portfolio"},
            token=token,
            extra_headers=idempotency_headers,
        )["portfolio"]
        second = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios",
            {"name": "Duplicate Safe Portfolio"},
            token=token,
            extra_headers=idempotency_headers,
        )["portfolio"]

        self.assertEqual(first["id"], second["id"])

        portfolios = self.gateway_request("GET", "/api/v1/portfolio/portfolios", token=token)["portfolios"]
        self.assertEqual(len(portfolios), 1)

        topology = self.gateway_request("GET", "/api/v1/platform/topology", token=token)
        self.assertGreaterEqual(topology["idempotency"]["hits"], 1)

        audit_events = self.gateway_request(
            "GET",
            "/api/v1/platform/audit-events?resource=portfolio&limit=20",
            token=token,
        )["events"]
        self.assertEqual(len([event for event in audit_events if event["action"] == "create_portfolio"]), 1)

    def test_idempotency_conflict_rejects_changed_payload(self) -> None:
        bootstrap = self.bootstrap_admin_session("Atlas Conflict Tenant")
        token = bootstrap["token"]
        idempotency_headers = {"Idempotency-Key": "conflicting-key"}

        self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios",
            {"name": "Conflict Base Portfolio"},
            token=token,
            extra_headers=idempotency_headers,
        )

        status_code, payload = self.gateway_request_raw(
            "POST",
            "/api/v1/portfolio/portfolios",
            {"name": "Conflict Changed Portfolio"},
            token=token,
            extra_headers=idempotency_headers,
        )
        self.assertEqual(status_code, 409)
        self.assertEqual(payload["error"], "idempotency_key_conflict")

    def test_repeated_operational_alerts_are_escalated_in_place(self) -> None:
        bootstrap = self.bootstrap_admin_session("Atlas Alert Tenant")
        token = bootstrap["token"]

        portfolio = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios",
            {"name": "Escalation Portfolio"},
            token=token,
        )["portfolio"]
        project = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios/{0}/projects".format(portfolio["id"]),
            {
                "name": "Escalation Program",
                "code": "ESCALATION-PROGRAM",
                "status": "active",
                "start_date": "2026-06-01",
                "target_date": "2026-11-01",
            },
            token=token,
        )["project"]
        work_item = self.gateway_request(
            "POST",
            "/api/v1/delivery/projects/{0}/work-items".format(project["id"]),
            {
                "title": "Vendor readiness gate",
                "priority": "high",
                "assignee": "PMO",
            },
            token=token,
        )["work_item"]

        for reason in ("Missing scope approval", "Missing scope approval", "Missing scope approval"):
            self.gateway_request(
                "PATCH",
                "/api/v1/delivery/work-items/{0}/status".format(work_item["id"]),
                {"status": "blocked", "blocked_reason": reason},
                token=token,
            )

        alerts = self.gateway_request(
            "GET",
            "/api/v1/notifications/alerts?project_id={0}&status=open".format(project["id"]),
            token=token,
        )["alerts"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["severity"], "critical")
        self.assertEqual(alerts[0]["occurrence_count"], 3)
        self.assertIsNotNone(alerts[0]["escalated_at"])

    def test_executive_summary_aggregates_across_portfolios(self) -> None:
        bootstrap = self.bootstrap_admin_session("Atlas Executive Tenant")
        token = bootstrap["token"]

        risk_portfolio = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios",
            {"name": "Risk Portfolio"},
            token=token,
        )["portfolio"]
        healthy_portfolio = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios",
            {"name": "Healthy Portfolio"},
            token=token,
        )["portfolio"]

        risk_project = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios/{0}/projects".format(risk_portfolio["id"]),
            {
                "name": "Risk Program",
                "code": "RISK-PROGRAM",
                "status": "active",
                "start_date": "2026-05-01",
                "target_date": "2026-12-01",
            },
            token=token,
        )["project"]
        healthy_project = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios/{0}/projects".format(healthy_portfolio["id"]),
            {
                "name": "Healthy Program",
                "code": "HEALTHY-PROGRAM",
                "status": "active",
                "start_date": "2026-05-01",
                "target_date": "2026-10-01",
            },
            token=token,
        )["project"]

        blocked_item = self.gateway_request(
            "POST",
            "/api/v1/delivery/projects/{0}/work-items".format(risk_project["id"]),
            {
                "title": "Identity integration",
                "priority": "critical",
                "assignee": "Platform Team",
            },
            token=token,
        )["work_item"]
        self.gateway_request(
            "PATCH",
            "/api/v1/delivery/work-items/{0}/status".format(blocked_item["id"]),
            {"status": "blocked", "blocked_reason": "SSO contract still pending"},
            token=token,
        )
        self.gateway_request(
            "POST",
            "/api/v1/finance/projects/{0}/budget".format(risk_project["id"]),
            {"total_budget": 100000, "currency": "USD"},
            token=token,
        )
        self.gateway_request(
            "POST",
            "/api/v1/finance/projects/{0}/expenses".format(risk_project["id"]),
            {"amount": 110000, "category": "vendor_change_request"},
            token=token,
        )

        healthy_item = self.gateway_request(
            "POST",
            "/api/v1/delivery/projects/{0}/work-items".format(healthy_project["id"]),
            {
                "title": "Regional rollout",
                "priority": "medium",
                "assignee": "Delivery Team",
            },
            token=token,
        )["work_item"]
        self.gateway_request(
            "PATCH",
            "/api/v1/delivery/work-items/{0}/status".format(healthy_item["id"]),
            {"status": "done"},
            token=token,
        )
        self.gateway_request(
            "POST",
            "/api/v1/finance/projects/{0}/budget".format(healthy_project["id"]),
            {"total_budget": 50000, "currency": "USD"},
            token=token,
        )
        self.gateway_request(
            "POST",
            "/api/v1/finance/projects/{0}/expenses".format(healthy_project["id"]),
            {"amount": 5000, "category": "rollout_support"},
            token=token,
        )

        summary = self.gateway_request(
            "GET",
            "/api/v1/analytics/executive-summary?top_n=1",
            token=token,
        )

        self.assertEqual(summary["totals"]["portfolios"], 2)
        self.assertEqual(summary["totals"]["projects"], 2)
        self.assertEqual(summary["totals"]["health_distribution"]["critical"], 1)
        self.assertEqual(summary["top_risks"][0]["project"]["id"], risk_project["id"])
        self.assertEqual(len(summary["portfolios"]), 2)
        self.assertTrue(any(item["portfolio"]["id"] == healthy_portfolio["id"] for item in summary["portfolios"]))

    def test_mutations_reject_unknown_project_ids(self) -> None:
        bootstrap = self.bootstrap_admin_session("Atlas Integrity Tenant")
        token = bootstrap["token"]
        missing_project_id = "00000000-0000-0000-0000-000000000404"

        status_code, payload = self.gateway_request_raw(
            "POST",
            "/api/v1/delivery/projects/{0}/work-items".format(missing_project_id),
            {"title": "Should fail", "assignee": "Nobody"},
            token=token,
        )
        self.assertEqual(status_code, 404)
        self.assertEqual(payload["error"], "project_not_found")

        status_code, payload = self.gateway_request_raw(
            "POST",
            "/api/v1/finance/projects/{0}/budget".format(missing_project_id),
            {"total_budget": 1000, "currency": "USD"},
            token=token,
        )
        self.assertEqual(status_code, 404)
        self.assertEqual(payload["error"], "project_not_found")

    def test_audit_summary_export_and_retention_controls(self) -> None:
        bootstrap = self.bootstrap_admin_session("Atlas Retention Tenant")
        token = bootstrap["token"]

        portfolio = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios",
            {"name": "Retention Portfolio"},
            token=token,
        )["portfolio"]
        project = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios/{0}/projects".format(portfolio["id"]),
            {
                "name": "Retention Program",
                "code": "RETENTION-PROGRAM",
                "status": "active",
                "start_date": "2026-07-01",
                "target_date": "2026-11-01",
            },
            token=token,
        )["project"]
        self.gateway_request(
            "POST",
            "/api/v1/finance/projects/{0}/budget".format(project["id"]),
            {"total_budget": 120000, "currency": "USD"},
            token=token,
        )

        summary = self.gateway_request("GET", "/api/v1/platform/audit-summary", token=token)["summary"]
        self.assertGreaterEqual(summary["total_events"], 3)
        self.assertIn("portfolio-service", summary["by_service"])
        self.assertIn("finance-service", summary["by_service"])

        export_payload = self.gateway_request(
            "GET",
            "/api/v1/platform/audit-export?limit=10",
            token=token,
        )
        self.assertGreaterEqual(export_payload["count"], 3)
        self.assertEqual(export_payload["summary"]["total_events"], summary["total_events"])

        dry_run = self.gateway_request(
            "POST",
            "/api/v1/platform/audit-retention",
            {"retention_days": 0, "dry_run": True},
            token=token,
        )
        self.assertTrue(dry_run["dry_run"])
        self.assertGreaterEqual(dry_run["would_delete"], 3)

        purge = self.gateway_request(
            "POST",
            "/api/v1/platform/audit-retention",
            {"retention_days": 0, "dry_run": False},
            token=token,
        )
        self.assertFalse(purge["dry_run"])
        self.assertGreaterEqual(purge["deleted_count"], 3)

        events_after = self.gateway_request(
            "GET",
            "/api/v1/platform/audit-events?limit=10",
            token=token,
        )["events"]
        self.assertEqual(len(events_after), 1)
        self.assertEqual(events_after[0]["service_name"], "audit-service")

    def test_alert_summary_aggregates_occurrences_and_sources(self) -> None:
        bootstrap = self.bootstrap_admin_session("Atlas Alert Summary Tenant")
        token = bootstrap["token"]

        portfolio = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios",
            {"name": "Summary Portfolio"},
            token=token,
        )["portfolio"]
        project = self.gateway_request(
            "POST",
            "/api/v1/portfolio/portfolios/{0}/projects".format(portfolio["id"]),
            {
                "name": "Summary Program",
                "code": "SUMMARY-PROGRAM",
                "status": "active",
                "start_date": "2026-07-01",
                "target_date": "2026-12-01",
            },
            token=token,
        )["project"]
        work_item = self.gateway_request(
            "POST",
            "/api/v1/delivery/projects/{0}/work-items".format(project["id"]),
            {"title": "Vendor readiness", "priority": "high", "assignee": "Ops"},
            token=token,
        )["work_item"]

        for reason in ("Missing access", "Missing access", "Missing access"):
            self.gateway_request(
                "PATCH",
                "/api/v1/delivery/work-items/{0}/status".format(work_item["id"]),
                {"status": "blocked", "blocked_reason": reason},
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
            {"amount": 90000, "category": "integrator"},
            token=token,
        )
        self.gateway_request(
            "POST",
            "/api/v1/finance/projects/{0}/expenses".format(project["id"]),
            {"amount": 20000, "category": "change_request"},
            token=token,
        )

        summary = self.gateway_request("GET", "/api/v1/platform/alert-summary", token=token)["summary"]
        self.assertEqual(summary["total_alerts"], 2)
        self.assertEqual(summary["total_occurrences"], 5)
        self.assertEqual(summary["deduplicated_occurrences"], 3)
        self.assertEqual(summary["critical_open_alerts"], 2)
        self.assertEqual(summary["escalated_open_alerts"], 2)
        self.assertEqual(summary["by_source"]["delivery-service"], 1)
        self.assertEqual(summary["by_source"]["finance-service"], 1)
        self.assertEqual(summary["noisy_projects"][0]["project_id"], project["id"])
        self.assertEqual(summary["noisy_projects"][0]["occurrences"], 5)


if __name__ == "__main__":
    unittest.main()
