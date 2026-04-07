# Architecture

## Domain

Atlas Core represents a multi-tenant Project Portfolio Management platform for enterprise PMO, delivery, and finance stakeholders. The domain was chosen because it naturally benefits from service decomposition while remaining easy to explain in a portfolio context.

## Service Boundaries

### API Gateway

- Public entrypoint for clients
- Validates bearer tokens with `identity-service`
- Caches validated auth contexts briefly to cut repeated identity round trips
- Injects tenant and user context into downstream requests
- Exposes platform topology and dependency health
- Aggregates operator-facing control room data in a single platform endpoint
- Orchestrates control-room operator actions for audit export and retention workflows
- Surfaces recent control-room actions back into the aggregate payload for operator observability
- Serves the operator-facing `/admin` control room directly from the gateway
- Keeps external APIs stable even if internal services evolve

### Identity Service

- Creates tenants and the first admin
- Stores users and sessions
- Returns role context used by the gateway and internal services

### Portfolio Service

- Owns portfolios and projects
- Acts as the source of truth for project identity and planning metadata

### Delivery Service

- Owns work items and delivery progress
- Emits operational alerts when work becomes blocked

### Finance Service

- Owns project budgets and expenses
- Emits budget threshold alerts when spend reaches risk zones

### Notification Service

- Stores open and acknowledged alerts
- Provides a simple alert inbox for operational governance
- Deduplicates repeated open alerts and escalates persistent warning patterns
- Produces alert summary rollups for operator dashboards and noisy-project detection

### Audit Service

- Receives tenant-scoped audit events for authenticated write operations
- Stores actor, request, resource, outcome, and entity references
- Exposes searchable audit history for admin and portfolio operators
- Exposes summary/export views and retention purge controls for governance workflows

### Analytics Service

- Aggregates data from portfolio, delivery, finance, and notification services
- Produces an executive dashboard with derived project health
- Produces tenant-wide executive summaries with ranked portfolio risk views

## Request Flow

1. A client sends a request to `api-gateway`.
2. The gateway validates the bearer token via `identity-service`.
3. The gateway forwards the request with `X-Tenant-ID`, `X-User-ID`, `X-User-Role`, and `X-Request-ID`.
4. Domain services execute tenant-scoped logic against their own SQLite databases.
5. The gateway records authenticated write operations into `audit-service`.
6. Delivery and finance publish operational alerts by calling `notification-service`.
7. `analytics-service` composes read models across services for executive reporting.

## Data Strategy

- Every service has its own SQLite database file.
- Cross-service joins are intentionally avoided.
- Analytics is composition-based rather than a shared database shortcut.
- This keeps the architecture aligned with real microservice boundaries.

## Security Model

- Bearer token validation is centralized in the gateway.
- Short-lived auth caching reduces repeat validation overhead without changing tenant boundaries.
- Tenant isolation is enforced through forwarded context.
- Services still validate actor context and role requirements locally.

## Performance Notes

- SQLite indexes were added around tenant-scoped and project-scoped lookup paths.
- Analytics aggregates project summaries in parallel for larger portfolios.
- Gateway topology reporting makes dependency regressions visible without opening every service separately.
- Audit event ingestion keeps compliance-relevant mutation history queryable per tenant.
- Repeated operational alerts are folded into a single open alert with occurrence tracking and escalation.
- The control room reduces client fan-out by aggregating topology, governance, and executive views in the gateway before the browser renders them.
- Operator actions stay gateway-mediated, so the browser never needs to understand downstream audit-service routes directly.
- Recent action history is sourced from the audit trail, which keeps action observability aligned with the existing governance model instead of introducing a second action log store.

## Tradeoffs

- Synchronous HTTP was chosen over a real broker to keep the repo runnable without extra infrastructure.
- SQLite was chosen over Postgres to avoid dependency installation while preserving persistence and schema ownership.
- Docker Compose is included for packaging, but the primary local execution path is the Python runner because Docker is not always available.
