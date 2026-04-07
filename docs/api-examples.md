# API Examples

## Bootstrap A Tenant

```bash
curl -s http://127.0.0.1:7000/api/v1/identity/bootstrap-admin \
  -H "X-Bootstrap-Token: $IDENTITY_BOOTSTRAP_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "tenant_name": "Atlas Global Holdings",
    "tenant_slug": "atlas-global",
    "admin_email": "admin@atlascore.local",
    "admin_password": "StrongPass!123",
    "admin_name": "Atlas Admin"
  }'
```

## Create A Portfolio

```bash
curl -s http://127.0.0.1:7000/api/v1/portfolio/portfolios \
  -H "Authorization: Bearer $ATLAS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "EMEA Strategic Transformation",
    "status": "active"
  }'
```

## Create A Project

```bash
curl -s http://127.0.0.1:7000/api/v1/portfolio/portfolios/$PORTFOLIO_ID/projects \
  -H "Authorization: Bearer $ATLAS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "ERP Modernization",
    "code": "ERP-ALPHA",
    "status": "active",
    "start_date": "2026-04-01",
    "target_date": "2026-09-30"
  }'
```

## Record Delivery Risk

```bash
curl -s http://127.0.0.1:7000/api/v1/delivery/projects/$PROJECT_ID/work-items \
  -H "Authorization: Bearer $ATLAS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "Vendor API contract alignment",
    "priority": "critical",
    "assignee": "Platform Enablement"
  }'
```

```bash
curl -s http://127.0.0.1:7000/api/v1/delivery/work-items/$WORK_ITEM_ID/status \
  -X PATCH \
  -H "Authorization: Bearer $ATLAS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "status": "blocked",
    "blocked_reason": "Vendor has not finalized SSO scopes"
  }'
```

## Record Finance Activity

```bash
curl -s http://127.0.0.1:7000/api/v1/finance/projects/$PROJECT_ID/budget \
  -H "Authorization: Bearer $ATLAS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "total_budget": 150000,
    "currency": "EUR"
  }'
```

```bash
curl -s http://127.0.0.1:7000/api/v1/finance/projects/$PROJECT_ID/expenses \
  -H "Authorization: Bearer $ATLAS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "amount": 90000,
    "category": "integration_partners"
  }'
```

## Read The Executive Dashboard

```bash
curl -s "http://127.0.0.1:7000/api/v1/analytics/dashboard?portfolio_id=$PORTFOLIO_ID" \
  -H "Authorization: Bearer $ATLAS_TOKEN"
```

## Read The Tenant Executive Summary

```bash
curl -s "http://127.0.0.1:7000/api/v1/analytics/executive-summary?top_n=5" \
  -H "Authorization: Bearer $ATLAS_TOKEN"
```

## Read Platform Health And Audit Trail

```bash
curl -s http://127.0.0.1:7000/api/v1/platform/topology \
  -H "Authorization: Bearer $ATLAS_TOKEN"
```

```bash
curl -s "http://127.0.0.1:7000/api/v1/platform/audit-events?limit=20" \
  -H "Authorization: Bearer $ATLAS_TOKEN"
```

```bash
curl -s http://127.0.0.1:7000/api/v1/platform/audit-summary \
  -H "Authorization: Bearer $ATLAS_TOKEN"
```

```bash
curl -s "http://127.0.0.1:7000/api/v1/platform/audit-export?limit=100" \
  -H "Authorization: Bearer $ATLAS_TOKEN"
```

```bash
curl -s http://127.0.0.1:7000/api/v1/platform/audit-retention \
  -H "Authorization: Bearer $ATLAS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "retention_days": 30,
    "dry_run": true
  }'
```

```bash
curl -s http://127.0.0.1:7000/api/v1/platform/alert-summary \
  -H "Authorization: Bearer $ATLAS_TOKEN"
```
