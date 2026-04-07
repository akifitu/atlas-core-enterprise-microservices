const ENDPOINTS = {
  controlRoom: "/api/v1/platform/control-room",
  controlRoomActions: "/api/v1/platform/control-room/actions",
};

const STORAGE_KEYS = {
  token: "atlas_control_room_token",
  topN: "atlas_control_room_top_n",
  portfolioId: "atlas_control_room_portfolio_id",
  exportLimit: "atlas_control_room_export_limit",
  retentionDays: "atlas_control_room_retention_days",
};

const state = {
  token: "",
  topN: 5,
  selectedPortfolioId: "",
  exportLimit: 100,
  retentionDays: 30,
  executive: null,
};

const elements = {
  heroMetrics: document.getElementById("hero-metrics"),
  sessionForm: document.getElementById("session-form"),
  tokenInput: document.getElementById("token-input"),
  topNInput: document.getElementById("top-n-input"),
  portfolioSelect: document.getElementById("portfolio-select"),
  auditExportLimitInput: document.getElementById("audit-export-limit"),
  retentionDaysInput: document.getElementById("retention-days-input"),
  refreshPortfolio: document.getElementById("refresh-portfolio"),
  clearSession: document.getElementById("clear-session"),
  runAuditExport: document.getElementById("run-audit-export"),
  previewRetention: document.getElementById("preview-retention"),
  applyRetention: document.getElementById("apply-retention"),
  statusBar: document.getElementById("status-bar"),
  actionSummary: document.getElementById("action-summary"),
  actionDetail: document.getElementById("action-detail"),
  actionHistory: document.getElementById("action-history"),
  topologySummary: document.getElementById("topology-summary"),
  topologyGrid: document.getElementById("topology-grid"),
  alertsSummary: document.getElementById("alerts-summary"),
  alertsDetail: document.getElementById("alerts-detail"),
  auditSummary: document.getElementById("audit-summary"),
  auditDetail: document.getElementById("audit-detail"),
  executiveSummary: document.getElementById("executive-summary"),
  executiveDetail: document.getElementById("executive-detail"),
  portfolioSummary: document.getElementById("portfolio-summary"),
  portfolioDetail: document.getElementById("portfolio-detail"),
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, function (character) {
    return {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[character];
  });
}

function formatNumber(value) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "0";
  }
  return new Intl.NumberFormat("en-US").format(value);
}

function formatPercent(value) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "0%";
  }
  return value.toFixed(2).replace(/\.00$/, "") + "%";
}

function formatTimestamp(value) {
  if (!value) {
    return "n/a";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString("en-GB", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusChip(label, tone) {
  return '<span class="status-chip ' + escapeHtml(tone) + '">' + escapeHtml(label) + "</span>";
}

function metricPill(label, value, tone) {
  return [
    '<article class="metric-pill tone-',
    escapeHtml(tone),
    '"><span>',
    escapeHtml(label),
    "</span><strong>",
    escapeHtml(value),
    "</strong></article>",
  ].join("");
}

function heroCard(label, value, detail, tone) {
  return [
    '<article class="metric-card tone-',
    escapeHtml(tone),
    '"><span class="metric-label">',
    escapeHtml(label),
    '</span><strong class="metric-value">',
    escapeHtml(value),
    '</strong><span class="metric-detail">',
    escapeHtml(detail),
    "</span></article>",
  ].join("");
}

function emptyState(message) {
  return '<p class="empty-state">' + escapeHtml(message) + "</p>";
}

function setStatus(message, tone) {
  elements.statusBar.dataset.tone = tone;
  elements.statusBar.textContent = message;
}

function readStoredState() {
  state.token = localStorage.getItem(STORAGE_KEYS.token) || "";
  state.topN = Number(localStorage.getItem(STORAGE_KEYS.topN) || "5") || 5;
  state.selectedPortfolioId = localStorage.getItem(STORAGE_KEYS.portfolioId) || "";
  state.exportLimit = Number(localStorage.getItem(STORAGE_KEYS.exportLimit) || "100") || 100;
  state.retentionDays = Number(localStorage.getItem(STORAGE_KEYS.retentionDays) || "30") || 30;
  elements.tokenInput.value = state.token;
  elements.topNInput.value = String(state.topN);
  elements.auditExportLimitInput.value = String(state.exportLimit);
  elements.retentionDaysInput.value = String(state.retentionDays);
}

function persistState() {
  localStorage.setItem(STORAGE_KEYS.token, state.token);
  localStorage.setItem(STORAGE_KEYS.topN, String(state.topN));
  localStorage.setItem(STORAGE_KEYS.exportLimit, String(state.exportLimit));
  localStorage.setItem(STORAGE_KEYS.retentionDays, String(state.retentionDays));
  if (state.selectedPortfolioId) {
    localStorage.setItem(STORAGE_KEYS.portfolioId, state.selectedPortfolioId);
  } else {
    localStorage.removeItem(STORAGE_KEYS.portfolioId);
  }
}

async function apiRequest(method, path, payload) {
  const response = await fetch(path, {
    method: method,
    headers: {
      Accept: "application/json",
      Authorization: "Bearer " + state.token,
      "Content-Type": "application/json",
    },
    body: payload ? JSON.stringify(payload) : undefined,
  });
  const payload = await response.json().catch(function () {
    return {};
  });
  if (!response.ok) {
    throw new Error(payload.error || "request_failed");
  }
  return payload;
}

async function apiGet(path) {
  return apiRequest("GET", path);
}

function renderHero(topology, alertSummary, auditSummary, executive) {
  elements.heroMetrics.innerHTML = [
    heroCard(
      "Healthy services",
      String(topology.summary.healthy_services),
      topology.summary.degraded_services.length
        ? topology.summary.degraded_services.join(", ")
        : "All service dependencies are green.",
      topology.summary.degraded_services.length ? "risk" : "healthy"
    ),
    heroCard(
      "Open alerts",
      String(alertSummary.open_alerts),
      String(alertSummary.critical_open_alerts) + " critical, " + String(alertSummary.escalated_open_alerts) + " escalated",
      alertSummary.critical_open_alerts ? "critical" : "neutral"
    ),
    heroCard(
      "Audit events",
      String(auditSummary.total_events),
      "Newest event " + formatTimestamp(auditSummary.time_range.newest_event_at),
      "neutral"
    ),
    heroCard(
      "Critical projects",
      String(executive.totals.health_distribution.critical || 0),
      String(executive.top_risks.length) + " risk items in current top list",
      executive.totals.health_distribution.critical ? "critical" : "risk"
    ),
  ].join("");
}

function renderTopology(topology) {
  elements.topologySummary.innerHTML = [
    metricPill("Healthy", topology.summary.healthy_services, "healthy"),
    metricPill("Degraded", topology.summary.degraded_services.length, topology.summary.degraded_services.length ? "critical" : "neutral"),
    metricPill("Auth cache hits", topology.auth_cache.hits, "neutral"),
    metricPill("Idempotency hits", topology.idempotency.hits, "neutral"),
    metricPill("Audit writes", topology.audit.recorded, "healthy"),
  ].join("");

  elements.topologyGrid.innerHTML = Object.entries(topology.services)
    .sort(function (left, right) {
      return left[0].localeCompare(right[0]);
    })
    .map(function (entry) {
      const serviceName = entry[0];
      const service = entry[1];
      return [
        '<article class="service-card">',
        '<div class="service-row"><h3>',
        escapeHtml(serviceName),
        "</h3>",
        statusChip(service.healthy ? "healthy" : "degraded", service.healthy ? "healthy" : "degraded"),
        "</div>",
        '<div class="mini-grid service-meta"><span>Status ',
        escapeHtml(service.status_code),
        "</span><span>Latency ",
        escapeHtml(String(service.latency_ms)),
        " ms</span></div>",
        '<div class="list-muted">Payload service: ',
        escapeHtml(service.payload && service.payload.service ? service.payload.service : "n/a"),
        "</div>",
        "</article>",
      ].join("");
    })
    .join("");
}

function renderAlerts(summary) {
  elements.alertsSummary.innerHTML = [
    metricPill("Open", summary.open_alerts, summary.critical_open_alerts ? "critical" : "neutral"),
    metricPill("Critical open", summary.critical_open_alerts, summary.critical_open_alerts ? "critical" : "risk"),
    metricPill("Deduped occurrences", summary.deduplicated_occurrences, "risk"),
    metricPill("Escalated", summary.escalated_open_alerts, summary.escalated_open_alerts ? "critical" : "neutral"),
  ].join("");

  const noisyProjects = summary.noisy_projects || [];
  const bySource = Object.entries(summary.by_source || {})
    .map(function (entry) {
      return "<span>" + escapeHtml(entry[0]) + ": " + escapeHtml(String(entry[1])) + "</span>";
    })
    .join("");

  elements.alertsDetail.innerHTML = [
    noisyProjects.length
      ? noisyProjects
          .map(function (project) {
            return [
              '<article class="rank-item"><strong>',
              escapeHtml(project.project_id),
              "</strong>",
              '<div class="list-muted">',
              escapeHtml(String(project.open_alerts)),
              " open alerts, ",
              escapeHtml(String(project.critical_alerts)),
              " critical, ",
              escapeHtml(String(project.occurrences)),
              " total occurrences</div></article>",
            ].join("");
          })
          .join("")
      : emptyState("No noisy projects in the current tenant view."),
    '<article class="rank-item"><strong>By source</strong><div class="micro-list">',
    bySource || "<span>No alert traffic</span>",
    "</div></article>",
  ].join("");
}

function renderAudit(summary) {
  elements.auditSummary.innerHTML = [
    metricPill("Events", summary.total_events, "neutral"),
    metricPill("Services", Object.keys(summary.by_service || {}).length, "neutral"),
    metricPill("Resources", Object.keys(summary.by_resource || {}).length, "neutral"),
    metricPill("Rejected", summary.by_outcome && summary.by_outcome.rejected ? summary.by_outcome.rejected : 0, summary.by_outcome && summary.by_outcome.rejected ? "risk" : "healthy"),
  ].join("");

  const cards = [
    ["By service", summary.by_service],
    ["By resource", summary.by_resource],
    ["By actor role", summary.by_actor_role],
    ["By outcome", summary.by_outcome],
  ];

  elements.auditDetail.innerHTML = cards
    .map(function (card) {
      const items = Object.entries(card[1] || {})
        .map(function (entry) {
          return "<span>" + escapeHtml(entry[0]) + ": " + escapeHtml(String(entry[1])) + "</span>";
        })
        .join("");
      return [
        '<article class="rank-item"><strong>',
        escapeHtml(card[0]),
        '</strong><div class="list-muted">',
        "Newest ",
        escapeHtml(formatTimestamp(summary.time_range.newest_event_at)),
        " / Oldest ",
        escapeHtml(formatTimestamp(summary.time_range.oldest_event_at)),
        '</div><div class="micro-list">',
        items || "<span>No events</span>",
        "</div></article>",
      ].join("");
    })
    .join("");
}

function renderExecutive(executive) {
  elements.executiveSummary.innerHTML = [
    metricPill("Portfolios", executive.totals.portfolios || 0, "neutral"),
    metricPill("Projects", executive.totals.projects || 0, "neutral"),
    metricPill("Budget used", formatPercent(executive.totals.budget_utilization_pct || 0), executive.totals.budget_utilization_pct >= 85 ? "risk" : "healthy"),
    metricPill("Blocked work", executive.totals.blocked_work_items || 0, executive.totals.blocked_work_items ? "risk" : "healthy"),
  ].join("");

  elements.executiveDetail.innerHTML = executive.top_risks && executive.top_risks.length
    ? executive.top_risks
        .map(function (item) {
          return [
            '<article class="rank-item"><strong>',
            escapeHtml(item.project.name),
            "</strong>",
            '<div class="list-muted">',
            escapeHtml(item.portfolio_name),
            " / ",
            statusChip(item.health.replace("_", " "), item.health),
            '</div><div class="micro-list">',
            "<span>alerts: " + escapeHtml(String(item.open_alerts)) + "</span>",
            "<span>blocked: " + escapeHtml(String(item.blocked_work_items)) + "</span>",
            "<span>budget: " + escapeHtml(formatPercent(item.budget_utilization_pct)) + "</span>",
            "</div></article>",
          ].join("");
        })
        .join("")
    : emptyState("No portfolio risks yet. Create delivery and finance activity to populate the executive queue.");
}

function populatePortfolioSelect(executive, selectedPortfolioId) {
  const options = ['<option value="">Auto select highest risk portfolio</option>'];
  (executive.portfolios || []).forEach(function (portfolioItem) {
    options.push(
      '<option value="' +
        escapeHtml(portfolioItem.portfolio.id) +
        '">' +
        escapeHtml(portfolioItem.portfolio.name) +
        "</option>"
    );
  });
  elements.portfolioSelect.innerHTML = options.join("");

  const defaultPortfolioId =
    selectedPortfolioId ||
    state.selectedPortfolioId ||
    (executive.top_risks && executive.top_risks[0] ? executive.top_risks[0].portfolio_id : "") ||
    (executive.portfolios && executive.portfolios[0] ? executive.portfolios[0].portfolio.id : "");
  state.selectedPortfolioId = defaultPortfolioId || "";
  elements.portfolioSelect.value = state.selectedPortfolioId;
  persistState();
}

function renderPortfolioDashboard(dashboard) {
  elements.portfolioSummary.innerHTML = [
    metricPill("Portfolio", dashboard.portfolio.name, "neutral"),
    metricPill("Projects", dashboard.totals.projects || 0, "neutral"),
    metricPill("Open alerts", dashboard.totals.open_alerts || 0, dashboard.totals.open_alerts ? "risk" : "healthy"),
    metricPill("Completion", formatPercent(dashboard.totals.completion_rate || 0), "healthy"),
    metricPill("Budget used", formatPercent(dashboard.totals.budget_utilization_pct || 0), dashboard.totals.budget_utilization_pct >= 85 ? "risk" : "healthy"),
  ].join("");

  elements.portfolioDetail.innerHTML = dashboard.projects && dashboard.projects.length
    ? dashboard.projects
        .map(function (projectSummary) {
          const project = projectSummary.project;
          return [
            '<article class="project-row">',
            '<div class="project-head"><div><h3>',
            escapeHtml(project.name),
            '</h3><div class="project-meta">',
            escapeHtml(project.code),
            " / ",
            escapeHtml(project.status),
            "</div></div>",
            statusChip(projectSummary.health.replace("_", " "), projectSummary.health),
            "</div>",
            '<div class="micro-list">',
            "<span>work items: " + escapeHtml(String(projectSummary.delivery.count)) + "</span>",
            "<span>blocked: " + escapeHtml(String(projectSummary.delivery.blocked)) + "</span>",
            "<span>spent: " + escapeHtml(formatNumber(projectSummary.finance.spent)) + "</span>",
            "<span>budget: " + escapeHtml(formatNumber(projectSummary.finance.budget_total)) + "</span>",
            "<span>alerts: " + escapeHtml(String(projectSummary.open_alerts.length)) + "</span>",
            "</div>",
            "</article>",
          ].join("");
        })
        .join("")
    : emptyState("No projects in the selected portfolio.");
}

function clearDashboardViews() {
  elements.actionSummary.innerHTML = "";
  elements.actionDetail.innerHTML = emptyState("Run an operator action to inspect export samples or retention impact.");
  elements.actionHistory.innerHTML = emptyState("Recent operator actions will appear here.");
  elements.topologySummary.innerHTML = "";
  elements.topologyGrid.innerHTML = emptyState("Topology will appear after authentication.");
  elements.alertsSummary.innerHTML = "";
  elements.alertsDetail.innerHTML = emptyState("Alert data will appear after authentication.");
  elements.auditSummary.innerHTML = "";
  elements.auditDetail.innerHTML = emptyState("Audit data will appear after authentication.");
  elements.executiveSummary.innerHTML = "";
  elements.executiveDetail.innerHTML = emptyState("Executive data will appear after authentication.");
  elements.portfolioSummary.innerHTML = "";
  elements.portfolioDetail.innerHTML = emptyState("Portfolio drilldown will appear after authentication.");
}

function actionLabel(actionName) {
  return {
    audit_export: "Audit export",
    audit_retention_dry_run: "Retention preview",
    audit_retention_apply: "Retention purge",
  }[actionName] || actionName;
}

function renderActionResult(actionName, result) {
  if (!result) {
    elements.actionSummary.innerHTML = "";
    elements.actionDetail.innerHTML = emptyState("Run an operator action to inspect export samples or retention impact.");
    return;
  }

  if (actionName === "audit_export") {
    const previewEvents = (result.events || []).slice(0, 3).map(function (event) {
      return {
        created_at: event.created_at,
        service_name: event.service_name,
        action: event.action,
        outcome: event.outcome,
        resource: event.resource,
      };
    });
    elements.actionSummary.innerHTML = [
      metricPill("Action", actionLabel(actionName), "neutral"),
      metricPill("Exported events", result.count || 0, "healthy"),
      metricPill("Summary events", result.summary ? result.summary.total_events || 0 : 0, "neutral"),
    ].join("");
    elements.actionDetail.innerHTML = [
      '<article class="result-block"><strong>Export generated</strong><div class="list-muted">Exported at ',
      escapeHtml(formatTimestamp(result.exported_at)),
      ' with ',
      escapeHtml(String(result.count || 0)),
      ' events.</div><pre class="result-code">',
      escapeHtml(JSON.stringify(previewEvents, null, 2)),
      "</pre></article>",
    ].join("");
    return;
  }

  const deletedCount = result.deleted_count || 0;
  const wouldDelete = result.would_delete || 0;
  elements.actionSummary.innerHTML = [
    metricPill("Action", actionLabel(actionName), actionName === "audit_retention_apply" ? "critical" : "risk"),
    metricPill("Retention days", result.retention_days || state.retentionDays, "neutral"),
    metricPill(
      actionName === "audit_retention_apply" ? "Deleted" : "Would delete",
      actionName === "audit_retention_apply" ? deletedCount : wouldDelete,
      actionName === "audit_retention_apply" ? "critical" : "risk"
    ),
  ].join("");
  elements.actionDetail.innerHTML = [
    '<article class="result-block"><strong>',
    escapeHtml(actionLabel(actionName)),
    '</strong><div class="list-muted">Cutoff ',
    escapeHtml(result.cutoff || "n/a"),
    " / dry run ",
    escapeHtml(String(Boolean(result.dry_run))),
    '</div><div class="micro-list">',
    "<span>retention_days: " + escapeHtml(String(result.retention_days || state.retentionDays)) + "</span>",
    "<span>would_delete: " + escapeHtml(String(wouldDelete)) + "</span>",
    "<span>deleted_count: " + escapeHtml(String(deletedCount)) + "</span>",
    "</div></article>",
  ].join("");
}

function renderActionHistory(controlRoom) {
  const actions = controlRoom.recent_actions || [];
  const summary = controlRoom.recent_actions_summary || {};
  if (!actions.length) {
    elements.actionHistory.innerHTML = emptyState("No recorded operator actions for this tenant yet.");
    return;
  }

  elements.actionHistory.innerHTML = [
    '<article class="rank-item"><strong>Recent operator actions</strong><div class="list-muted">',
    escapeHtml(String(summary.count || actions.length)),
    " actions / latest ",
    escapeHtml(formatTimestamp(summary.latest_action_at)),
    "</div></article>",
    actions
      .map(function (event) {
        const metadata = event.metadata || {};
        const parameters = metadata.parameters || {};
        const actionSummary = metadata.summary || {};
        const chips = [];
        if (parameters.retention_days !== null && parameters.retention_days !== undefined) {
          chips.push("<span>retention_days: " + escapeHtml(String(parameters.retention_days)) + "</span>");
        }
        if (parameters.limit !== null && parameters.limit !== undefined) {
          chips.push("<span>limit: " + escapeHtml(String(parameters.limit)) + "</span>");
        }
        if (actionSummary.would_delete !== null && actionSummary.would_delete !== undefined) {
          chips.push("<span>would_delete: " + escapeHtml(String(actionSummary.would_delete)) + "</span>");
        }
        if (actionSummary.deleted_count !== null && actionSummary.deleted_count !== undefined) {
          chips.push("<span>deleted_count: " + escapeHtml(String(actionSummary.deleted_count)) + "</span>");
        }
        if (actionSummary.count !== null && actionSummary.count !== undefined) {
          chips.push("<span>exported: " + escapeHtml(String(actionSummary.count)) + "</span>");
        }
        if (actionSummary.dry_run !== null && actionSummary.dry_run !== undefined) {
          chips.push("<span>dry_run: " + escapeHtml(String(actionSummary.dry_run)) + "</span>");
        }
        return [
          '<article class="rank-item"><strong>',
          escapeHtml(actionLabel(event.action)),
          "</strong>",
          '<div class="list-muted">',
          escapeHtml(formatTimestamp(event.created_at)),
          " / ",
          escapeHtml(event.actor_role || "unknown"),
          " / ",
          escapeHtml(event.outcome || "unknown"),
          '</div><div class="micro-list">',
          chips.join("") || "<span>No parameters</span>",
          "</div></article>",
        ].join("");
      })
      .join(""),
  ].join("");
}

async function fetchControlRoom(selectedPortfolioId) {
  const query = new URLSearchParams();
  query.set("top_n", String(state.topN));
  if (selectedPortfolioId) {
    query.set("portfolio_id", selectedPortfolioId);
  }
  return apiGet(ENDPOINTS.controlRoom + "?" + query.toString());
}

async function runControlRoomAction(actionName) {
  state.token = elements.tokenInput.value.trim();
  state.topN = Math.max(1, Math.min(20, Number(elements.topNInput.value) || 5));
  state.exportLimit = Math.max(1, Math.min(1000, Number(elements.auditExportLimitInput.value) || 100));
  state.retentionDays = Math.max(0, Math.min(3650, Number(elements.retentionDaysInput.value) || 30));
  elements.topNInput.value = String(state.topN);
  elements.auditExportLimitInput.value = String(state.exportLimit);
  elements.retentionDaysInput.value = String(state.retentionDays);
  persistState();

  if (!state.token) {
    setStatus("Bearer token required before running operator actions.", "error");
    renderActionResult("", null);
    return;
  }

  const payload = {
    action: actionName,
    top_n: state.topN,
  };
  if (state.selectedPortfolioId) {
    payload.portfolio_id = state.selectedPortfolioId;
  }
  if (actionName === "audit_export") {
    payload.limit = state.exportLimit;
  } else {
    payload.retention_days = state.retentionDays;
  }

  setStatus("Running " + actionLabel(actionName).toLowerCase() + ".", "loading");
  const response = await apiRequest("POST", ENDPOINTS.controlRoomActions, payload);
  renderActionResult(response.action, response.result);
  renderControlRoom(response.control_room);
  setStatus(actionLabel(actionName) + " completed.", "success");
}

function renderControlRoom(controlRoom) {
  state.executive = controlRoom.executive_summary;
  state.selectedPortfolioId = controlRoom.selected_portfolio_id || "";
  renderHero(
    controlRoom.topology,
    controlRoom.alert_summary,
    controlRoom.audit_summary,
    controlRoom.executive_summary
  );
  renderTopology(controlRoom.topology);
  renderAlerts(controlRoom.alert_summary);
  renderAudit(controlRoom.audit_summary);
  renderExecutive(controlRoom.executive_summary);
  renderActionHistory(controlRoom);
  populatePortfolioSelect(controlRoom.executive_summary, controlRoom.selected_portfolio_id || "");
  if (controlRoom.portfolio_dashboard) {
    renderPortfolioDashboard(controlRoom.portfolio_dashboard);
  } else {
    elements.portfolioSummary.innerHTML = "";
    elements.portfolioDetail.innerHTML = emptyState("No portfolio available for drilldown yet.");
  }
}

async function refreshControlRoom() {
  state.token = elements.tokenInput.value.trim();
  state.topN = Math.max(1, Math.min(20, Number(elements.topNInput.value) || 5));
  state.exportLimit = Math.max(1, Math.min(1000, Number(elements.auditExportLimitInput.value) || 100));
  state.retentionDays = Math.max(0, Math.min(3650, Number(elements.retentionDaysInput.value) || 30));
  elements.topNInput.value = String(state.topN);
  elements.auditExportLimitInput.value = String(state.exportLimit);
  elements.retentionDaysInput.value = String(state.retentionDays);
  persistState();

  if (!state.token) {
    setStatus("Bearer token required. Use an admin or portfolio_manager session.", "error");
    elements.heroMetrics.innerHTML = heroCard("Gateway", "Waiting", "Token is required to pull tenant telemetry.", "risk");
    clearDashboardViews();
    return;
  }

  setStatus("Loading aggregated control room payload.", "loading");
  try {
    const controlRoom = await fetchControlRoom(state.selectedPortfolioId);
    renderControlRoom(controlRoom);

    setStatus(
      "Control room updated at " +
        formatTimestamp(controlRoom.generated_at || controlRoom.executive_summary.generated_at) +
        ".",
      "success"
    );
  } catch (error) {
    setStatus("Control room refresh failed: " + error.message, "error");
  }
}

elements.sessionForm.addEventListener("submit", function (event) {
  event.preventDefault();
  refreshControlRoom();
});

elements.refreshPortfolio.addEventListener("click", function () {
  state.selectedPortfolioId = elements.portfolioSelect.value;
  persistState();
  setStatus("Refreshing selected portfolio via aggregated control room endpoint.", "loading");
  fetchControlRoom(state.selectedPortfolioId)
    .then(function (controlRoom) {
      renderControlRoom(controlRoom);
      setStatus("Portfolio drilldown updated.", "success");
    })
    .catch(function (error) {
      setStatus("Portfolio drilldown failed: " + error.message, "error");
    });
});

elements.clearSession.addEventListener("click", function () {
  state.token = "";
  state.topN = 5;
  state.selectedPortfolioId = "";
  state.exportLimit = 100;
  state.retentionDays = 30;
  state.executive = null;
  localStorage.removeItem(STORAGE_KEYS.token);
  localStorage.removeItem(STORAGE_KEYS.topN);
  localStorage.removeItem(STORAGE_KEYS.portfolioId);
  localStorage.removeItem(STORAGE_KEYS.exportLimit);
  localStorage.removeItem(STORAGE_KEYS.retentionDays);
  elements.tokenInput.value = "";
  elements.topNInput.value = "5";
  elements.auditExportLimitInput.value = "100";
  elements.retentionDaysInput.value = "30";
  elements.portfolioSelect.value = "";
  refreshControlRoom();
});

elements.runAuditExport.addEventListener("click", function () {
  runControlRoomAction("audit_export").catch(function (error) {
    setStatus("Audit export failed: " + error.message, "error");
  });
});

elements.previewRetention.addEventListener("click", function () {
  runControlRoomAction("audit_retention_dry_run").catch(function (error) {
    setStatus("Retention preview failed: " + error.message, "error");
  });
});

elements.applyRetention.addEventListener("click", function () {
  runControlRoomAction("audit_retention_apply").catch(function (error) {
    setStatus("Retention purge failed: " + error.message, "error");
  });
});

elements.portfolioSelect.addEventListener("change", function () {
  state.selectedPortfolioId = elements.portfolioSelect.value;
  persistState();
});

readStoredState();
refreshControlRoom();
