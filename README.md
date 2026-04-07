# Atlas Core

Atlas Core is a portfolio-grade, enterprise-style microservice platform for multi-tenant Project Portfolio Management. It models a realistic governance flow for large organizations: tenant onboarding, user management, portfolio and project creation, delivery tracking, financial oversight, operational alerting, and executive analytics.

The project is intentionally built on Python's standard library plus SQLite so the system stays runnable without external package installation. That makes it practical in restricted environments while still demonstrating service boundaries, gateway-driven auth, tenant isolation, and cross-service orchestration.

## Product Idea

The fictional product solves a common enterprise pain point: leadership can fund portfolios and programs, but execution visibility is fragmented across PMO, delivery, and finance tools. Atlas Core centralizes:

- Identity and tenant bootstrap
- Portfolio and project governance
- Delivery execution and blockers
- Budget planning and expense tracking
- Alerting for blocked work and budget thresholds with deduplication and escalation
- Tenant-scoped audit trails for state-changing operations
- Executive dashboards aggregated across services

## Service Topology

| Service | Port | Responsibility |
| --- | --- | --- |
| `api-gateway` | `7000` | External entrypoint, token validation, request forwarding |
| `identity-service` | `7001` | Tenant bootstrap, users, sessions, role context |
| `portfolio-service` | `7002` | Portfolios and projects |
| `delivery-service` | `7003` | Work items, delivery summaries, blocker alerts |
| `finance-service` | `7004` | Budgets, expenses, utilization thresholds |
| `notification-service` | `7005` | Operational alerts and acknowledgements |
| `analytics-service` | `7006` | Cross-service executive dashboard aggregation |
| `audit-service` | `7007` | Tenant-scoped audit event ingestion and query |

## Architecture Highlights

- Multi-tenant isolation via `X-Tenant-ID` propagated by the gateway
- Gateway-centralized bearer token validation
- Gateway-side auth cache to reduce repeated identity lookups
- Central audit trail for every authenticated state-changing request
- SQLite-backed independent persistence per service
- Synchronous service-to-service calls for analytics and alert creation
- Notification deduplication to reduce duplicate open-alert noise
- Audit governance controls for summary, export, and retention operations
- Request tracing via `X-Request-ID`
- Clear internal and external API separation
- Gateway-served admin control room for live operator visibility
- Operator action panel for audit export and retention controls
- Executable end-to-end test that boots the full stack
- GitHub Actions CI for compile and full-stack verification

## Repository Layout

```text
.
├── docs/
├── infra/
├── scripts/
├── services/
├── shared/
├── ui/
└── tests/
```

## Quick Start

### 1. Start the platform

```bash
python3 scripts/dev_runner.py --reset-data
```

This starts all services locally and writes logs under `runtime/logs/`.

For repeatable or non-local bootstrap flows, set a bootstrap secret first:

```bash
export IDENTITY_BOOTSTRAP_TOKEN='replace-with-a-long-random-secret'
```

If `IDENTITY_BOOTSTRAP_TOKEN` is unset, only the very first tenant bootstrap is allowed. Additional bootstrap attempts are rejected.

### 2. Execute the demo scenario

In a second terminal:

```bash
python3 scripts/demo_flow.py
```

The script bootstraps a tenant, creates users, creates a portfolio and projects, records blocked work and budget overruns, then fetches the executive dashboard.

The output also includes a `control_room` block with a ready-to-use `/admin` URL, bearer token, and portfolio ID for the operator console.

### 3. Run the end-to-end test

```bash
PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m unittest tests.test_end_to_end -v
```

### 4. Inspect platform topology

Once you have a bearer token:

```bash
ATLAS_TOKEN=<token> python3 scripts/ops_report.py
```

This returns service health, gateway auth cache metrics, and a platform summary from `GET /api/v1/platform/topology`.

### 5. Open the admin console

After bootstrap or `scripts/demo_flow.py`, open:

```text
http://127.0.0.1:7000/admin
```

Paste the bearer token into the control room and it will load an aggregated operator payload from `GET /api/v1/platform/control-room`, including topology, alert summary, audit summary, executive summary, portfolio drilldown, and recent operator action history. The same UI now also runs audit export plus retention preview/apply actions through `POST /api/v1/platform/control-room/actions`.

### 6. Read audit trail

```bash
curl -s "http://127.0.0.1:7000/api/v1/platform/audit-events?limit=20" \
  -H "Authorization: Bearer <token>"
```

### 7. Generate ops overview

```bash
ATLAS_TOKEN=<token> python3 scripts/ops_report.py overview
```

This combines platform topology, alert summary, and audit summary in one operator-facing report.

Common operator shortcuts:

```bash
make ops-control-room
make ops-topology
make ops-alert-summary
make ops-audit-summary
make ops-audit-export
make ops-audit-retention-dry-run RETENTION_DAYS=30
```

## Main Flows

### Tenant and Auth

1. `POST /api/v1/identity/bootstrap-admin`
2. `POST /api/v1/identity/sessions`
3. Gateway validates the bearer token against `identity-service` via `POST /validate`

### Portfolio Governance

1. `POST /api/v1/portfolio/portfolios`
2. `POST /api/v1/portfolio/portfolios/{portfolio_id}/projects`
3. `GET /api/v1/portfolio/portfolios/{portfolio_id}/snapshot`

### Delivery and Finance Risk

1. `POST /api/v1/delivery/projects/{project_id}/work-items`
2. `PATCH /api/v1/delivery/work-items/{work_item_id}/status`
3. `POST /api/v1/finance/projects/{project_id}/budget`
4. `POST /api/v1/finance/projects/{project_id}/expenses`
5. `GET /api/v1/notifications/alerts`

### Executive Analytics

1. `GET /api/v1/analytics/dashboard?portfolio_id=<id>`
2. `GET /api/v1/analytics/executive-summary?top_n=<n>`

The analytics service composes project, delivery, finance, and alert data into both a portfolio dashboard and a tenant-wide executive summary with ranked risks.

### Platform Operations

1. `GET /api/v1/platform/topology`
2. `GET /api/v1/platform/control-room?top_n=<n>&portfolio_id=<id>`
3. `POST /api/v1/platform/control-room/actions`
4. `GET /api/v1/platform/audit-events`
5. `GET /api/v1/platform/audit-summary`
6. `GET /api/v1/platform/audit-export`
7. `POST /api/v1/platform/audit-retention`
8. `GET /api/v1/platform/alert-summary`
9. Inspect per-service health, latency, auth cache, tenant audit history, and alert pressure
10. Open `/admin` for a gateway-served control room over the same API surface

`control-room` responses now also include `recent_actions` and `recent_actions_summary` so operators can see the latest export and retention runs without opening the raw audit feed.

## Why This Works As A Portfolio Project

- It demonstrates service decomposition beyond toy CRUD examples.
- It contains a full platform story rather than an isolated service.
- It includes infra, docs, scripts, and tests, not only source code.
- It is practical to clone and run without dependency installation.

## Supporting Docs

- `docs/architecture.md`
- `docs/implementation-plan.md`
- `docs/openapi-gateway.yaml`
- `docs/api-examples.md`
- `infra/docker-compose.yml`
- `.github/workflows/ci.yml`
