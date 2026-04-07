const ENDPOINTS = {
  topology: "/api/v1/platform/topology",
  alertSummary: "/api/v1/platform/alert-summary",
  auditSummary: "/api/v1/platform/audit-summary",
  executiveSummary: "/api/v1/analytics/executive-summary",
  dashboard: "/api/v1/analytics/dashboard",
};

const STORAGE_KEYS = {
  token: "atlas_control_room_token",
  topN: "atlas_control_room_top_n",
  portfolioId: "atlas_control_room_portfolio_id",
};

const state = {
  token: "",
  topN: 5,
  selectedPortfolioId: "",
  executive: null,
};

const elements = {
  heroMetrics: document.getElementById("hero-metrics"),
  sessionForm: document.getElementById("session-form"),
  tokenInput: document.getElementById("token-input"),
  topNInput: document.getElementById("top-n-input"),
  portfolioSelect: document.getElementById("portfolio-select"),
  refreshPortfolio: document.getElementById("refresh-portfolio"),
  clearSession: document.getElementById("clear-session"),
  statusBar: document.getElementById("status-bar"),
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
  elements.tokenInput.value = state.token;
  elements.topNInput.value = String(state.topN);
}

function persistState() {
  localStorage.setItem(STORAGE_KEYS.token, state.token);
  localStorage.setItem(STORAGE_KEYS.topN, String(state.topN));
  if (state.selectedPortfolioId) {
    localStorage.setItem(STORAGE_KEYS.portfolioId, state.selectedPortfolioId);
  } else {
    localStorage.removeItem(STORAGE_KEYS.portfolioId);
  }
}

async function apiGet(path) {
  const response = await fetch(path, {
    headers: {
      Accept: "application/json",
      Authorization: "Bearer " + state.token,
    },
  });
  const payload = await response.json().catch(function () {
    return {};
  });
  if (!response.ok) {
    throw new Error(payload.error || "request_failed");
  }
  return payload;
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

function populatePortfolioSelect(executive) {
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

async function loadPortfolioDashboard() {
  if (!state.token) {
    elements.portfolioSummary.innerHTML = "";
    elements.portfolioDetail.innerHTML = emptyState("Provide a token to fetch portfolio drilldown.");
    return;
  }
  if (!state.selectedPortfolioId) {
    elements.portfolioSummary.innerHTML = "";
    elements.portfolioDetail.innerHTML = emptyState("No portfolio available for drilldown yet.");
    return;
  }

  const dashboard = await apiGet(
    ENDPOINTS.dashboard + "?portfolio_id=" + encodeURIComponent(state.selectedPortfolioId)
  );
  renderPortfolioDashboard(dashboard);
}

async function refreshControlRoom() {
  state.token = elements.tokenInput.value.trim();
  state.topN = Math.max(1, Math.min(20, Number(elements.topNInput.value) || 5));
  elements.topNInput.value = String(state.topN);
  persistState();

  if (!state.token) {
    setStatus("Bearer token required. Use an admin or portfolio_manager session.", "error");
    elements.heroMetrics.innerHTML = heroCard("Gateway", "Waiting", "Token is required to pull tenant telemetry.", "risk");
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
    return;
  }

  setStatus("Loading topology, alert summary, audit summary, and executive summary.", "loading");
  try {
    const topologyPromise = apiGet(ENDPOINTS.topology);
    const alertPromise = apiGet(ENDPOINTS.alertSummary);
    const auditPromise = apiGet(ENDPOINTS.auditSummary);
    const executivePromise = apiGet(
      ENDPOINTS.executiveSummary + "?top_n=" + encodeURIComponent(String(state.topN))
    );

    const results = await Promise.all([topologyPromise, alertPromise, auditPromise, executivePromise]);
    const topology = results[0];
    const alertPayload = results[1];
    const auditPayload = results[2];
    const executive = results[3];

    state.executive = executive;
    renderHero(topology, alertPayload.summary, auditPayload.summary, executive);
    renderTopology(topology);
    renderAlerts(alertPayload.summary);
    renderAudit(auditPayload.summary);
    renderExecutive(executive);
    populatePortfolioSelect(executive);
    await loadPortfolioDashboard();

    setStatus(
      "Control room updated at " +
        formatTimestamp(executive.generated_at || topology.generated_at) +
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
  setStatus("Refreshing selected portfolio drilldown.", "loading");
  loadPortfolioDashboard()
    .then(function () {
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
  state.executive = null;
  localStorage.removeItem(STORAGE_KEYS.token);
  localStorage.removeItem(STORAGE_KEYS.topN);
  localStorage.removeItem(STORAGE_KEYS.portfolioId);
  elements.tokenInput.value = "";
  elements.topNInput.value = "5";
  elements.portfolioSelect.value = "";
  refreshControlRoom();
});

elements.portfolioSelect.addEventListener("change", function () {
  state.selectedPortfolioId = elements.portfolioSelect.value;
  persistState();
});

readStoredState();
refreshControlRoom();
