"use strict";

const savedReportView = window.localStorage.getItem("client_report_view");
const clientState = {
  mode: "offline", analysis: null, projection: null, overview: null, running: false,
  jobId: null, pollTimer: null, deleteIntent: null,
  workspace: null, comparison: null, historyReports: [], historyFilter: "all",
  reportView: savedReportView === "professional" ? "professional" : "basic",
  chartPeriod: "daily", chartRange: 40, chartIndicators: { sma5: true, sma20: true, volume: true },
  chartHoverIndex: null, chartGeometry: null,
  auth: null, csrfToken: "",
  catalogQuery: "", catalogIndustry: "",
};
const $ = (selector) => document.querySelector(selector);

async function loadRuntimeStatus() {
  try {
    const [health, version] = await Promise.all([clientApi("/api/health"), clientApi("/api/version")]);
    const dot = $("#runtime-health-dot");
    dot.className = health.status === "ok" ? "" : "fail";
    setText("#runtime-health-label", health.maintenance_message ? `维护提示：${health.maintenance_message}` : "服务正常 · 只读研究");
    setText("#runtime-version", `v${version.version}`);
  } catch (error) {
    $("#runtime-health-dot").className = "fail";
    setText("#runtime-health-label", "服务状态不可用");
    setText("#runtime-version", "版本不可用");
  }
}

async function clientApi(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const securityHeaders = method === "GET" || method === "HEAD" || !clientState.csrfToken
    ? {} : { "X-CSRF-Token": clientState.csrfToken };
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...securityHeaders, ...(options.headers || {}) },
  });
  let body;
  const contentType = response.headers.get("Content-Type") || "";
  if (contentType.includes("application/json")) {
    try { body = await response.json(); } catch { body = { error: "后台返回了不完整的数据，请重试。" }; }
  } else {
    await response.text();
    body = {
      error: response.status === 501
        ? "当前运行的是旧版后台，暂不支持删除。请关闭旧的运行窗口，重新启动项目后刷新页面。"
        : `后台返回了非 JSON 内容 (${response.status})，请重启项目后再试。`,
    };
  }
  if (!response.ok) throw new Error(body.error || `请求失败 (${response.status})`);
  return body;
}

async function loadClientSession() {
  try {
    const session = await clientApi("/api/auth/session");
    clientState.auth = session;
    clientState.csrfToken = session.csrf_token || "";
    setText("#account-summary", `${session.username} · ${session.role === "admin" ? "管理员" : "客户账户"}`);
    setText("#account-key-status", session.model_key?.configured ? "DeepSeek：已使用本次会话 Key" : "DeepSeek：未设置会话 Key，将使用启动配置或本地解释");
    $("#account-button").textContent = session.username;
    return session;
  } catch (error) {
    window.location.assign("/login");
    throw error;
  }
}

async function saveSessionModelKey(apiKey) {
  const status = await clientApi("/api/auth/model-key", {
    method: "POST", body: JSON.stringify({ api_key: apiKey }),
  });
  setText("#account-key-status", status.configured ? "DeepSeek：本次会话已启用" : "DeepSeek：未启用");
  $("#session-model-key").value = "";
  showClientToast("DeepSeek Key 已启用，只在本次登录会话中有效。");
}

async function clearSessionModelKey() {
  await clientApi("/api/auth/model-key", { method: "DELETE" });
  setText("#account-key-status", "DeepSeek：会话 Key 已清除");
  showClientToast("会话 Key 已清除。");
}

async function logoutClient() {
  await clientApi("/api/auth/logout", { method: "POST", body: "{}" });
  window.location.assign("/login");
}

function setText(selector, value) { $(selector).textContent = value ?? "—"; }
function showClientToast(message) {
  const toast = $("#client-toast"); toast.textContent = message; toast.classList.add("show");
  window.clearTimeout(showClientToast.timer); showClientToast.timer = window.setTimeout(() => toast.classList.remove("show"), 2800);
}
function formatJobDuration(milliseconds) {
  if (milliseconds === null || milliseconds === undefined) return "";
  const value = Number(milliseconds);
  return value < 1000 ? `${Math.round(value)} 毫秒` : `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} 秒`;
}

function populateOverview(overview) {
  clientState.overview = overview;
  const industryFilter = $("#industry-filter"); industryFilter.replaceChildren();
  const allOption = document.createElement("option"); allOption.value = ""; allOption.textContent = "全部行业"; industryFilter.append(allOption);
  (overview.catalog?.industries || [...new Set(overview.securities.map((item) => item.industry).filter(Boolean))]).forEach((industry) => {
    const option = document.createElement("option"); option.value = industry; option.textContent = industry; industryFilter.append(option);
  });
  industryFilter.value = clientState.catalogIndustry;
  const catalog = overview.catalog || {};
  setText("#catalog-summary", `${catalog.visible_count || overview.securities.length} 只可研究 · ${catalog.industries?.length || 1} 个行业`);
  renderSecurityOptions();
  const strip = $("#capability-strip"); strip.replaceChildren();
  overview.capabilities.forEach((item) => { const span = document.createElement("span"); span.textContent = item; strip.append(span); });
  syncWatchlistButton();
}

function renderSecurityOptions() {
  const select = $("#stock-select");
  const previous = select.value || "sz000001";
  const query = clientState.catalogQuery.toLowerCase();
  const securities = (clientState.overview?.securities || []).filter((security) => {
    const matchesQuery = !query || `${security.name} ${security.code} ${security.symbol}`.toLowerCase().includes(query);
    const matchesIndustry = !clientState.catalogIndustry || security.industry === clientState.catalogIndustry;
    return matchesQuery && matchesIndustry;
  });
  select.replaceChildren();
  securities.forEach((security) => {
    const option = document.createElement("option"); option.value = security.symbol;
    option.dataset.modes = security.modes.join(",");
    option.textContent = `${security.name}  ${security.code} · ${security.exchange} · ${security.industry}`; select.append(option);
  });
  if (!securities.length) {
    const option = document.createElement("option"); option.value = ""; option.textContent = "没有匹配的已验证标的"; option.disabled = true; select.append(option);
  } else {
    select.value = securities.some((item) => item.symbol === previous) ? previous : securities[0].symbol;
  }
  syncModeAvailability();
}

function selectedSecurity() {
  return clientState.overview?.securities.find((item) => item.symbol === $("#stock-select").value);
}

function syncModeAvailability() {
  const security = selectedSecurity();
  if (!security) return;
  const modes = new Set(security.modes);
  $("#stock-select").disabled = clientState.running;
  document.querySelectorAll("[data-client-mode]").forEach((button) => {
    button.disabled = clientState.running || !modes.has(button.dataset.clientMode);
  });
  if (!modes.has(clientState.mode)) clientState.mode = security.modes[0];
  document.querySelectorAll("[data-client-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.clientMode === clientState.mode);
  });
  setText(
    "#mode-helper",
    modes.has("offline")
      ? (clientState.mode === "live" ? "读取最新只读数据，不会连接交易" : "稳定复现，适合查看完整功能")
      : `${security.name}使用最新只读数据`,
  );
}

function syncWatchlistButton() {
  const button = $("#watchlist-toggle");
  const symbol = $("#stock-select").value;
  const selected = clientState.workspace?.watchlist?.some((item) => item.symbol === symbol) || false;
  button.classList.toggle("active", selected);
  button.textContent = selected ? "★ 已在自选" : "☆ 加入自选";
}

function syncCurrentReportActions() {
  const reportId = clientState.projection?.report_id || clientState.analysis?.report_id;
  const report = clientState.workspace?.reports?.find((item) => item.report_id === reportId);
  const favorite = Boolean(report?.favorite);
  const favoriteButton = $("#favorite-report-button");
  favoriteButton.disabled = !reportId; favoriteButton.classList.toggle("active", favorite);
  favoriteButton.textContent = favorite ? "★ 已收藏" : "☆ 收藏报告";
  $("#export-report-button").disabled = !reportId;
  $("#print-report-button").disabled = !reportId;
  const stateNode = $("#report-data-state");
  stateNode.className = "report-data-state";
  if (!report?.state) { stateNode.textContent = "状态读取中"; return; }
  const freshness = report.state.freshness; const availability = report.state.availability;
  stateNode.textContent = `${freshness.label} · ${availability.label}`;
  stateNode.classList.add(freshness.status, availability.status);
  stateNode.title = `${freshness.note} ${availability.note}`;
}

async function runClientAnalysis() {
  if (clientState.running) return;
  clientState.running = true;
  $("#analyze-button").disabled = true; syncModeAvailability();
  $("#analysis").hidden = true; $("#error-state").hidden = true; $("#loading-state").hidden = false;
  setText("#loading-message", "正在创建分析任务…");
  $("#job-progress").replaceChildren(); $("#cancel-analysis-button").disabled = false;
  setText("#job-reference", "等待任务编号");
  try {
    const job = await clientApi("/api/client/jobs", {
      method: "POST",
      body: JSON.stringify({ symbol: $("#stock-select").value, mode: clientState.mode }),
    });
    clientState.jobId = job.job_id; window.sessionStorage.setItem("active_analysis_job", job.job_id);
    await followAnalysisJob(job);
  } catch (error) {
    finishJobWithError(error.message);
  }
}

async function followAnalysisJob(initialJob = null) {
  let job = initialJob;
  try {
    while (clientState.jobId) {
      if (!job) job = await clientApi(`/api/client/jobs/${clientState.jobId}`);
      renderJobProgress(job);
      if (job.status === "succeeded") {
        const analysis = await clientApi(`/api/client/jobs/${job.job_id}/result`);
        clientState.analysis = analysis; clientState.jobId = null;
        window.sessionStorage.removeItem("active_analysis_job"); await renderAnalysis(analysis);
        $("#loading-state").hidden = true; $("#analysis").hidden = false;
        explainAnalysis(analysis);
        finishJobControls(); loadRecentAnalyses(); return;
      }
      if (job.status === "failed") { finishFailedJob(job); return; }
      if (job.status === "cancelled") throw new Error("本次分析已停止，你可以重新开始。");
      await new Promise((resolve) => { clientState.pollTimer = window.setTimeout(resolve, 500); });
      job = null;
    }
  } catch (error) { finishJobWithError(error.message); }
}

function renderJobProgress(job) {
  if (job.request?.symbol) {
    $("#stock-select").value = job.request.symbol;
    clientState.mode = job.request.mode || clientState.mode;
    syncModeAvailability();
  }
  clientState.jobId = job.job_id; setText("#job-reference", `任务 ${job.job_id.slice(0, 8)}`);
  const text = { queued: "已进入队列，等待开始", running: "后台正在分析，可继续停留在本页", succeeded: "分析完成", failed: "分析未完成", cancelled: "分析已停止" };
  setText("#loading-message", text[job.status] || "正在读取任务状态");
  const list = $("#job-progress"); list.replaceChildren();
  let currentGroup = null;
  const groupLabels = { setup: "研究准备", specialist: "四个 Agent", decision: "综合决策", risk: "交易与风控", report: "报告整理" };
  job.progress.stages.forEach((stage) => {
    if (stage.group !== currentGroup) {
      currentGroup = stage.group;
      const heading = document.createElement("li"); heading.className = "job-group-title";
      heading.textContent = groupLabels[currentGroup] || currentGroup; list.append(heading);
    }
    const item = document.createElement("li"); item.className = `job-stage ${stage.status}`;
    const marker = document.createElement("i"); marker.setAttribute("aria-hidden", "true");
    const copy = document.createElement("div"); const label = document.createElement("strong"); label.textContent = stage.label;
    const state = document.createElement("span"); state.textContent = ({ pending: "等待中", running: "进行中", completed: "已完成", failed: "失败", cancelled: "已停止", skipped: "无需执行", retrying: "正在重试" })[stage.status] || stage.status;
    if (stage.attempts > 1) state.textContent += ` · 第 ${stage.attempts} 次`;
    if (stage.duration_ms !== null && stage.status !== "pending") state.textContent += ` · ${formatJobDuration(stage.duration_ms)}`;
    copy.append(label, state); item.append(marker, copy); list.append(item);
  });
  $("#cancel-analysis-button").disabled = ["succeeded", "failed", "cancelled"].includes(job.status) || job.cancel_requested;
  $("#cancel-analysis-button").hidden = ["succeeded", "failed", "cancelled"].includes(job.status);
  $("#retry-job-button").hidden = !job.can_retry;
  const recovery = job.recovered ? "服务重启后已从检查点恢复。" : "";
  const retries = job.retry_count ? ` 已重试 ${job.retry_count} 次。` : "";
  const elapsed = job.duration_ms === null ? "" : ` 已用时 ${formatJobDuration(job.duration_ms)}。`;
  setText("#job-progress-note", job.cancel_requested ? "停止请求已提交，当前步骤会在下一个安全点结束。" : `${recovery}${retries}${elapsed}这里只显示程序确认的真实节点，不使用模拟百分比。`);
}

async function cancelAnalysisJob() {
  if (!clientState.jobId) return;
  const button = $("#cancel-analysis-button"); button.disabled = true; button.textContent = "正在安全停止…";
  try {
    const job = await clientApi(`/api/client/jobs/${clientState.jobId}/cancel`, { method: "POST", body: "{}" });
    renderJobProgress(job);
  } catch (error) { showClientToast(error.message); button.disabled = false; }
}

async function retryAnalysisJob() {
  if (!clientState.jobId || clientState.running) return;
  clientState.running = true; $("#analyze-button").disabled = true; syncModeAvailability();
  $("#error-state").hidden = true; $("#analysis").hidden = true; $("#loading-state").hidden = false;
  $("#retry-job-button").disabled = true; setText("#loading-message", "正在从失败步骤继续…");
  try {
    const job = await clientApi(`/api/client/jobs/${clientState.jobId}/retry`, { method: "POST", body: "{}" });
    window.sessionStorage.setItem("active_analysis_job", job.job_id); renderJobProgress(job); await followAnalysisJob(job);
  } catch (error) { finishJobWithError(error.message); }
}

function finishJobControls() { clientState.running = false; $("#analyze-button").disabled = false; $("#cancel-analysis-button").textContent = "停止本次分析"; syncModeAvailability(); }
function finishFailedJob(job) {
  window.clearTimeout(clientState.pollTimer); clientState.running = false; renderJobProgress(job);
  $("#loading-state").hidden = true; $("#analysis").hidden = true; $("#error-state").hidden = false;
  $("#retry-job-button").disabled = false; $("#analyze-button").disabled = false; syncModeAvailability();
  setText("#error-message", job.error?.message || "分析任务执行失败。");
  setText("#error-action", job.error?.user_action || "可以重新开始一次分析。");
  setText("#error-trace", `追踪号 ${job.trace_id || job.error?.trace_id || "暂不可用"}`);
  $("#retry-button").textContent = job.can_retry ? "只重试失败步骤" : "重新分析";
  showClientToast(job.error?.message || "分析任务执行失败，可从失败步骤继续。");
}
function finishJobWithError(message) {
  window.clearTimeout(clientState.pollTimer); clientState.jobId = null; window.sessionStorage.removeItem("active_analysis_job");
  $("#loading-state").hidden = true; $("#error-state").hidden = false;
  setText("#error-message", message); setText("#error-action", "请确认后台仍在运行，然后重新分析。");
  setText("#error-trace", "尚未创建追踪号"); $("#retry-button").textContent = "重新分析";
  showClientToast(message); finishJobControls();
}
async function resumeOrStartAnalysis() {
  const existing = window.sessionStorage.getItem("active_analysis_job");
  if (!existing) {
    $("#loading-state").hidden = true;
    $("#error-state").hidden = true;
    return;
  }
  clientState.jobId = existing; clientState.running = true; $("#analyze-button").disabled = true;
  syncModeAvailability();
  $("#analysis").hidden = true; $("#error-state").hidden = true; $("#loading-state").hidden = false;
  await followAnalysisJob();
}

async function renderAnalysis(data) {
  if (!data.report_id) throw new Error("分析已完成，但缺少可读取的报告编号。");
  const projection = await clientApi(`/api/client/reports/${data.report_id}/view?view=${clientState.reportView}`);
  clientState.analysis = data; clientState.projection = projection;
  renderReportProjection(projection, data);
}

function renderReportProjection(projection, raw) {
  const data = projection.shared; const professional = projection.professional;
  const meta = $("#report-meta"); meta.hidden = false;
  setText("#report-version", `报告 v${projection.report_version || 1}`);
  setText("#report-history-state", raw.history ? "已从历史记录重新打开" : "已保存到历史记录");
  setText("#report-reference", `报告 ${projection.report_id.slice(0, 8)} · 快照 ${(data.data.snapshot_id || "未知").slice(0, 8)}`);
  syncCurrentReportActions();
  setText("#security-exchange", data.security.exchange);
  setText("#security-name", data.security.name);
  setText("#security-code", data.security.code);
  setText("#latest-close", Number(data.quote.latest_close).toFixed(2));
  const change = Number(data.quote.daily_return_percent); const changeNode = $("#daily-return");
  changeNode.textContent = `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`;
  changeNode.className = change >= 0 ? "up" : "down";
  setText("#data-label", data.data.label); setText("#data-as-of", formatDateTime(data.data.as_of));
  setText("#source-count", data.data.source_count);
  renderBasicProjection(projection.basic);
  if (projection.view === "basic") {
    setText("#verdict-kicker", "一句话结论"); setText("#verdict-label", "现在怎么看");
    setText("#action-label", projection.basic.headline); setText("#verdict-description", projection.basic.summary);
    setText("#risk-kicker", "普通版 · 风险边界"); setText("#risk-title", "价格区间与风险边界");
    setText("#risk-description", "下沿和上沿用于说明本次观察范围，不是价格预测。系统不会自动执行交易。");
  } else {
    setText("#verdict-kicker", "综合研判"); setText("#verdict-label", data.verdict.label);
    setText("#action-label", data.verdict.action_label); setText("#verdict-description", "由四个研究维度加权，并经过一致性与偏差检查。");
    setText("#risk-kicker", "RISK BEFORE RETURN"); setText("#risk-title", "先把边界画清楚，再谈观点。");
    setText("#risk-description", "系统不会替用户下单。以下区间和仓位只用于研究与模拟风险说明。");
  }
  setText("#confidence-value", data.verdict.confidence); setText("#weighted-score", data.verdict.weighted_score);
  setText("#position-cap", `${data.risk.position_cap_percent}%`);
  $("#confidence-ring").style.setProperty("--confidence", data.verdict.confidence);
  setText("#support-price", data.quote.support); setText("#reference-price", data.price_band.reference); setText("#resistance-price", data.quote.resistance);
  setText("#band-lower", data.price_band.lower); setText("#band-reference", data.price_band.reference); setText("#band-upper", data.price_band.upper);
  setText("#estimated-loss", data.risk.estimated_loss_percent ? `${data.risk.estimated_loss_percent}%` : "未计算");
  setText("#reward-risk", data.risk.reward_risk_ratio || "未计算"); setText("#risk-status", data.risk.status);
  const lower = Number(data.price_band.lower), ref = Number(data.price_band.reference), upper = Number(data.price_band.upper);
  const marker = upper > lower ? Math.max(0, Math.min(100, (ref - lower) / (upper - lower) * 100)) : 50;
  $("#price-marker").style.left = `${marker}%`;
  if (professional) renderProfessionalProjection(professional);
  applyReportViewVisibility();
  window.requestAnimationFrame(() => drawKline(data.chart));
}

function renderBasicProjection(basic) {
  const stages = $("#basic-stages"); stages.replaceChildren();
  basic.stages.forEach((stage) => {
    const item = document.createElement("li"); item.className = stage.status; item.textContent = stage.label; stages.append(item);
  });
  const guide = $("#basic-guide"); guide.replaceChildren();
  basic.guide.forEach((item) => {
    const card = document.createElement("article"); card.className = `basic-answer ${item.id}`;
    const question = document.createElement("small"); question.textContent = item.label;
    const answer = document.createElement("strong"); answer.textContent = item.answer;
    const detail = document.createElement("p"); detail.textContent = item.detail;
    card.append(question, answer, detail); guide.append(card);
  });
  setText("#basic-risk-explanation", basic.risk_explanation);
  renderCredibility(basic.credibility || {});
}

function renderCredibility(credibility) {
  const card = $("#credibility-card");
  const status = String(credibility.status || "unknown");
  card.className = `credibility-card ${status}`;
  setText("#credibility-status", credibility.label || "数据状态未知");
  setText("#credibility-as-of", formatDateTime(credibility.as_of));
  const available = credibility.dataset_count
    ? `${credibility.available_count || 0}/${credibility.dataset_count} 类数据`
    : "历史报告未记录";
  setText("#credibility-source", credibility.used_fallback ? `${available} · 使用备用/缓存` : available);
  setText("#credibility-comparison", credibility.comparison_ready ? "可以比较" : "先核对数据状态");
  setText("#credibility-note", credibility.summary || "请在专业版查看详细数据状态。");
}

function renderProfessionalProjection(professional) {
  renderSnapshotHealth(professional.snapshot);
  renderRunProvenance(professional.provenance || {});
  renderResearchBalance(professional.dimensions); renderDimensions(professional.dimensions);
  setText("#positive-claim", localizeDebateText(professional.debate.positive));
  setText("#positive-reasoning", localizeDebateText(professional.debate.positive_reasoning));
  setText("#risk-claim", localizeDebateText(professional.debate.risk));
  setText("#risk-reasoning", localizeDebateText(professional.debate.risk_reasoning));
  setText("#debate-mode-note", "当前展示经过确定性证据复核的固定辩论。");
  $("#debate-proof").hidden = true; $("#dynamic-debate-rounds").hidden = true;
  $("#dynamic-debate-button").disabled = !clientState.analysis?.analysis_id;
  const sourceList = $("#source-list"); sourceList.replaceChildren();
  professional.sources.forEach((source) => { const item = document.createElement("li"); item.textContent = source; sourceList.append(item); });
  setText("#scope-note", `${clientState.projection.shared.safety.notice} 指标、评分、区间和风控都来自同一冻结报告。`);
  renderProfessionalNodes(professional.task_nodes); renderAgentDrilldown(professional.agent_details);
}

function renderRunProvenance(provenance) {
  const quality = provenance.quality || {}; const identity = provenance.identity || {};
  const statusLabels = { complete: "数据完整", degraded: "部分数据降级", blocked: "关键数据不可用", unknown: "历史报告，来源版本未知" };
  setText("#run-quality-status", statusLabels[quality.overall_status] || "数据状态未知");
  setText("#run-quality-note", quality.comparison_note || "—");
  setText("#run-fingerprint", provenance.fingerprint || "历史报告没有运行指纹");
  setText("#run-identity", [
    `snapshot=${identity.snapshot_id || "unknown"}`,
    `security_master=${identity.security_master_version || "unknown"}`,
    `code=${identity.code_version || "unknown"}`,
    `config=${identity.config_version || "unknown"}`,
    `model_policy=${identity.model_policy_version || "unknown"}`,
    `report=v${identity.report_version || "?"}`,
  ].join(" · "));
  const container = $("#quality-datasets"); container.replaceChildren();
  (quality.items || []).forEach((dataset) => {
    const item = document.createElement("article"); item.className = `quality-dataset ${dataset.quality_status || "unknown"}`;
    const title = document.createElement("strong"); title.textContent = dataset.dataset || "未命名数据";
    const status = document.createElement("span"); status.textContent = ({ complete: "完整", degraded: "降级", unavailable: "不可用", invalid: "字段无效" })[dataset.quality_status] || "未知";
    const source = document.createElement("small"); source.textContent = `${dataset.source || "无来源"} · 数据 ${formatDateTime(dataset.as_of)} · 获取 ${formatDateTime(dataset.timestamp)}`;
    const note = document.createElement("p"); note.textContent = dataset.reason || dataset.user_action || "—";
    item.append(title, status, source, note); container.append(item);
  });
}

function renderProfessionalNodes(nodes) {
  const list = $("#professional-nodes"); list.replaceChildren();
  nodes.forEach((node) => {
    const item = document.createElement("li"); item.className = node.status;
    const label = document.createElement("strong"); label.textContent = node.label;
    const state = document.createElement("span"); state.textContent = ({completed:"已完成",skipped:"未走此路径",failed:"失败",cancelled:"已停止"})[node.status] || node.status;
    item.append(label, state); list.append(item);
  });
}

function renderAgentDrilldown(details) {
  const container = $("#agent-drilldown"); container.replaceChildren();
  details.forEach((agent) => {
    const panel = document.createElement("details");
    const summary = document.createElement("summary");
    const title = document.createElement("strong"); title.textContent = agent.name;
    const score = document.createElement("span"); score.textContent = formatSignedScore(agent.score);
    const copy = document.createElement("small"); copy.textContent = `${agent.label} · 点击查看指标和来源`;
    summary.append(title, score, copy);
    const body = document.createElement("div"); const metrics = document.createElement("div"); metrics.className = "agent-metrics";
    agent.metrics.forEach((metric) => { const item = document.createElement("span"); item.textContent = metric.label; const value = document.createElement("strong"); value.textContent = metric.value ?? "暂不可用"; item.append(value); metrics.append(item); });
    const sources = document.createElement("ul"); sources.className = "agent-evidence";
    agent.sources.forEach((source) => { const item = document.createElement("li"); item.textContent = source; sources.append(item); });
    const time = document.createElement("small"); time.className = "agent-time"; time.textContent = `证据时间 ${formatDateTime(agent.as_of)} · 获取 ${formatDateTime(agent.timestamp)}`;
    body.append(metrics, sources, time); panel.append(summary, body); container.append(panel);
  });
}

function applyReportViewVisibility() {
  const professional = clientState.reportView === "professional";
  $("#analysis").dataset.view = clientState.reportView;
  document.querySelectorAll("[data-professional-only]").forEach((node) => { node.hidden = !professional; });
  document.querySelectorAll("[data-basic-only]").forEach((node) => { node.hidden = professional; });
  document.querySelectorAll("[data-report-view]").forEach((button) => button.classList.toggle("active", button.dataset.reportView === clientState.reportView));
  setText("#view-depth-helper", professional ? "专业版展示指标、17 个节点、来源和证据；不会重新分析。" : "普通版只保留结论、理由、风险和数据时间。");
}

async function switchReportView(view) {
  if (!["basic", "professional"].includes(view) || view === clientState.reportView) return;
  clientState.reportView = view; window.localStorage.setItem("client_report_view", view);
  if (!clientState.analysis?.report_id) {
    applyReportViewVisibility();
    if (clientState.comparison) await runReportComparison({ silent: true });
    return;
  }
  try {
    const projection = await clientApi(`/api/client/reports/${clientState.analysis.report_id}/view?view=${view}`);
    clientState.projection = projection; renderReportProjection(projection, clientState.analysis);
    if (clientState.comparison) await runReportComparison({ silent: true });
    showClientToast(view === "professional" ? "已展开专业证据，没有重新分析。" : "已切换到普通版，没有重新分析。");
  } catch (error) { showClientToast(error.message); }
}

async function loadResearchWorkspace() {
  try {
    const workspace = await clientApi("/api/client/workspace");
    clientState.workspace = workspace; clientState.historyReports = workspace.reports;
    renderResearchWorkspace(workspace); renderRecentAnalyses();
    syncWatchlistButton(); syncCurrentReportActions();
    if (clientState.comparison) renderReportComparison(clientState.comparison);
  } catch (error) {
    setText("#workspace-status", `工作台暂时不可用：${error.message}`);
    $("#compare-button").disabled = true;
    const list = $("#recent-list"); list.replaceChildren();
    const empty = document.createElement("p"); empty.className = "recent-empty";
    empty.textContent = `历史报告暂时不可用：${error.message}`; list.append(empty);
  }
}

function renderResearchWorkspace(workspace) {
  setText(
    "#workspace-status",
    `${workspace.watchlist_count} 只自选 · ${workspace.report_count} 份冻结报告 · ${workspace.favorite_count} 份收藏`,
  );
  const shelf = $("#watchlist-items"); shelf.replaceChildren();
  if (!workspace.watchlist.length) {
    const empty = document.createElement("span"); empty.className = "watchlist-empty";
    empty.textContent = "还没有自选股票，可从上方选择后加入。"; shelf.append(empty);
  } else {
    workspace.watchlist.forEach((security) => {
      const chip = document.createElement("button"); chip.type = "button"; chip.className = "watchlist-chip";
      chip.dataset.watchlistSymbol = security.symbol;
      const name = document.createElement("span"); name.textContent = security.name;
      const code = document.createElement("small"); code.textContent = security.code;
      chip.append(name, code); shelf.append(chip);
    });
  }
  populateComparisonSelect($("#compare-left"), workspace.reports, 1);
  populateComparisonSelect($("#compare-right"), workspace.reports, 0);
  $("#compare-button").disabled = !workspace.comparison_ready;
}

function populateComparisonSelect(select, reports, fallbackIndex) {
  const previous = select.value; select.replaceChildren();
  if (!reports.length) {
    const option = document.createElement("option"); option.value = "";
    option.textContent = "暂无已保存报告"; select.append(option); return;
  }
  reports.forEach((report) => {
    const option = document.createElement("option"); option.value = report.report_id;
    const favorite = report.favorite ? "★ " : "";
    option.textContent = `${favorite}${report.name || report.symbol} · ${formatDateTime(report.as_of)} · ${report.verdict || "研究报告"}`;
    select.append(option);
  });
  if (reports.some((item) => item.report_id === previous)) select.value = previous;
  else select.value = reports[Math.min(fallbackIndex, reports.length - 1)].report_id;
}

async function toggleWatchlist(symbol = null) {
  const target = symbol || $("#stock-select").value;
  try {
    const result = await clientApi("/api/client/workspace/watchlist", {
      method: "POST", body: JSON.stringify({ symbol: target }),
    });
    clientState.workspace = result.workspace; renderResearchWorkspace(result.workspace);
    syncWatchlistButton(); showClientToast(result.message);
  } catch (error) { showClientToast(error.message); }
}

async function toggleReportFavorite(reportId) {
  if (!reportId) return;
  try {
    const result = await clientApi("/api/client/workspace/favorites", {
      method: "POST", body: JSON.stringify({ report_id: reportId }),
    });
    clientState.workspace = result.workspace; clientState.historyReports = result.workspace.reports;
    renderResearchWorkspace(result.workspace); renderRecentAnalyses();
    syncCurrentReportActions();
    if (clientState.comparison) renderReportComparison(clientState.comparison);
    showClientToast(result.message);
  } catch (error) { showClientToast(error.message); }
}

async function runReportComparison({ silent = false } = {}) {
  const leftReportId = $("#compare-left").value;
  const rightReportId = $("#compare-right").value;
  if (!leftReportId || !rightReportId) { if (!silent) showClientToast("请先保存至少两份报告。"); return; }
  const button = $("#compare-button"); button.disabled = true; button.textContent = "正在比较…";
  try {
    const result = await clientApi("/api/client/workspace/compare", {
      method: "POST",
      body: JSON.stringify({ left_report_id: leftReportId, right_report_id: rightReportId, view: clientState.reportView }),
    });
    clientState.comparison = result; renderReportComparison(result);
  } catch (error) { if (!silent) showClientToast(error.message); }
  finally { button.disabled = !clientState.workspace?.comparison_ready; button.textContent = "开始比较"; }
}

function renderReportComparison(result) {
  const container = $("#comparison-result"); container.replaceChildren(); container.hidden = false;
  const heading = document.createElement("div"); heading.className = "comparison-result-head";
  const badge = document.createElement("span"); badge.textContent = result.kind_label;
  const title = document.createElement("h3"); title.textContent = result.headline;
  const note = document.createElement("p"); note.textContent = result.notice;
  const actions = document.createElement("div"); actions.className = "comparison-result-actions";
  const exportButton = document.createElement("button"); exportButton.type = "button";
  exportButton.dataset.exportComparison = "true"; exportButton.textContent = "导出比较";
  const printButton = document.createElement("button"); printButton.type = "button";
  printButton.dataset.printTarget = "comparison"; printButton.textContent = "打印比较";
  actions.append(exportButton, printButton); heading.append(badge, title, note, actions);

  const rail = document.createElement("div"); rail.className = "comparison-rail";
  rail.append(renderComparisonCard(result.left), renderComparisonDeltas(result.changes), renderComparisonCard(result.right));
  container.append(heading, rail);
  const reasons = document.createElement("section"); reasons.className = "comparison-reasons";
  const reasonsTitle = document.createElement("strong"); reasonsTitle.textContent = "为什么不同";
  const reasonsList = document.createElement("ul");
  (result.change_reasons || []).forEach((item) => {
    const row = document.createElement("li");
    const label = document.createElement("b"); label.textContent = item.label;
    const detail = document.createElement("span"); detail.textContent = item.detail;
    row.append(label, detail); reasonsList.append(row);
  });
  reasons.append(reasonsTitle, reasonsList); container.append(reasons);
  if (result.professional) {
    const professional = document.createElement("section"); professional.className = "professional-comparison";
    const label = document.createElement("strong"); label.textContent = "专业版 · 四个研究维度差值（右侧减左侧）";
    const grid = document.createElement("div"); grid.className = "professional-comparison-grid";
    result.professional.dimension_changes.forEach((item) => {
      const cell = document.createElement("span"); cell.textContent = item.label;
      const value = document.createElement("b"); value.textContent = item.delta === null ? "不可比较" : `${item.delta} ${item.unit}`;
      cell.append(value); grid.append(cell);
    });
    professional.append(label, grid); container.append(professional);
  }
}

function renderComparisonCard(report) {
  const card = document.createElement("article"); card.className = "comparison-report";
  const time = document.createElement("small"); time.textContent = `数据 ${formatDateTime(report.as_of)} · 报告 ${formatDateTime(report.archived_at)}`;
  const name = document.createElement("h4"); name.textContent = `${report.name} ${report.code}`;
  const verdict = document.createElement("strong"); verdict.textContent = `${report.verdict.label} · ${report.verdict.action_label}`;
  const summary = document.createElement("p"); summary.textContent = `${report.support.summary} 主要风险：${report.risk_summary.summary}`;
  const details = document.createElement("dl");
  [["参考收盘价", report.latest_close], ["研究区间", `${report.price_band.lower}–${report.price_band.upper}`], ["判断把握度", `${report.verdict.confidence}%`], ["计划仓位上限", `${report.risk.position_cap_percent}%`]].forEach(([key, value]) => {
    const term = document.createElement("dt"); term.textContent = key; const description = document.createElement("dd"); description.textContent = value; details.append(term, description);
  });
  const state = document.createElement("div"); state.className = "recent-state";
  [report.state.freshness, report.state.availability].forEach((item) => {
    const badge = document.createElement("em"); badge.className = item.status; badge.textContent = item.label; badge.title = item.note; state.append(badge);
  });
  const actions = document.createElement("div"); actions.className = "comparison-card-actions";
  const favorite = document.createElement("button"); favorite.type = "button";
  favorite.dataset.favoriteReportId = report.report_id;
  const isFavorite = clientState.workspace?.reports?.find((item) => item.report_id === report.report_id)?.favorite;
  favorite.classList.toggle("active", Boolean(isFavorite)); favorite.textContent = isFavorite ? "★ 已收藏" : "☆ 收藏";
  const open = document.createElement("button"); open.type = "button"; open.dataset.reportId = report.report_id; open.textContent = "打开报告";
  actions.append(favorite, open);
  card.append(time, name, verdict, summary, details, state, actions); return card;
}

function renderComparisonDeltas(changes) {
  const container = document.createElement("div"); container.className = "comparison-deltas";
  changes.forEach((item) => {
    const row = document.createElement("div"); row.className = "comparison-delta";
    const label = document.createElement("span"); label.textContent = item.label;
    const value = document.createElement("strong");
    value.textContent = item.delta !== undefined && item.delta !== null ? `${item.delta} ${item.unit}` : (item.changed === true ? "发生变化" : item.changed === false ? "保持一致" : "不直接比较");
    const note = document.createElement("small"); note.textContent = item.note || `${item.left} → ${item.right}`;
    row.append(label, value, note); container.append(row);
  });
  return container;
}

async function loadRecentAnalyses() {
  await loadResearchWorkspace();
}

function renderRecentAnalyses() {
  const list = $("#recent-list"); list.replaceChildren();
  const reports = clientState.historyFilter === "favorites"
    ? clientState.historyReports.filter((item) => item.favorite)
    : clientState.historyReports;
  $("#clear-history-button").hidden = !clientState.historyReports.length;
  document.querySelectorAll("[data-history-filter]").forEach((button) => button.classList.toggle("active", button.dataset.historyFilter === clientState.historyFilter));
  if (!reports.length) {
    const empty = document.createElement("p"); empty.className = "recent-empty";
    empty.textContent = clientState.historyFilter === "favorites" ? "还没有收藏报告，点击报告上的星标即可收藏。" : "完成一次分析后，报告会出现在这里。";
    list.append(empty); return;
  }
  reports.slice(0, 8).forEach((report) => {
    const card = document.createElement("article"); card.className = "recent-card"; card.classList.toggle("favorite", report.favorite);
    const open = document.createElement("button"); open.type = "button"; open.className = "recent-open"; open.dataset.reportId = report.report_id;
    const top = document.createElement("span");
    const time = document.createElement("b"); time.textContent = formatDateTime(report.archived_at);
    const status = document.createElement("i"); status.textContent = report.task_status === "succeeded" ? "已完成" : report.task_status;
    top.append(time, status);
    const name = document.createElement("strong"); name.textContent = `${report.name || report.symbol} · ${report.verdict || "研究报告"}`;
    const detail = document.createElement("small"); detail.textContent = `${report.data_label || report.mode} · 数据 ${formatDateTime(report.as_of)} · v${report.report_version}`;
    const states = document.createElement("span"); states.className = "recent-state";
    [report.state.freshness, report.state.availability].forEach((item) => {
      const badge = document.createElement("em"); badge.className = item.status; badge.textContent = item.label; badge.title = item.note; states.append(badge);
    });
    open.append(top, name, detail, states);
    const favorite = document.createElement("button"); favorite.type = "button"; favorite.className = "recent-favorite"; favorite.classList.toggle("active", report.favorite);
    favorite.dataset.favoriteReportId = report.report_id; favorite.setAttribute("aria-label", report.favorite ? "取消收藏这份报告" : "收藏这份报告"); favorite.textContent = report.favorite ? "★" : "☆";
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "recent-delete";
    remove.dataset.deleteReportId = report.report_id; remove.dataset.deleteReportName = report.name || report.symbol;
    remove.setAttribute("aria-label", `删除${report.name || report.symbol}的这份历史报告`); remove.textContent = "×";
    card.append(open, favorite, remove); list.append(card);
  });
}

async function downloadExport(path) {
  try {
    const response = await fetch(path, { headers: { Accept: "text/html" } });
    if (!response.ok) {
      let message = `导出失败 (${response.status})`;
      try { const error = await response.json(); message = error.error || message; } catch { /* 保留明确状态码 */ }
      throw new Error(message);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    const link = document.createElement("a"); link.href = URL.createObjectURL(blob);
    link.download = match?.[1] || "research_report.html"; document.body.append(link); link.click(); link.remove();
    window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    showClientToast("报告已导出，内容来自冻结数据。");
  } catch (error) { showClientToast(error.message); }
}

function exportCurrentReport() {
  const reportId = clientState.projection?.report_id || clientState.analysis?.report_id;
  if (!reportId) { showClientToast("请先完成或打开一份报告。"); return; }
  downloadExport(`/api/client/reports/${reportId}/export?view=${clientState.reportView}`);
}

function exportCurrentComparison() {
  if (!clientState.comparison) { showClientToast("请先完成一次报告比较。"); return; }
  const left = encodeURIComponent(clientState.comparison.left.report_id);
  const right = encodeURIComponent(clientState.comparison.right.report_id);
  downloadExport(`/api/client/workspace/export?left_report_id=${left}&right_report_id=${right}&view=${clientState.reportView}`);
}

function printResearch(target) {
  if (target === "report" && !clientState.projection?.report_id) { showClientToast("请先完成或打开一份报告。"); return; }
  if (target === "comparison" && !clientState.comparison) { showClientToast("请先完成一次报告比较。"); return; }
  document.body.dataset.printTarget = target; window.print();
}

function requestHistoryDeletion(intent) {
  clientState.deleteIntent = intent;
  const all = intent.type === "all";
  setText("#confirm-title", all ? "确认清空全部历史？" : "确认删除这份报告？");
  setText(
    "#confirm-message",
    all
      ? "全部历史报告、快照、Agent 结果、Graph、模型记录和已完成任务检查点都会删除，且无法恢复。"
      : `${intent.name}的这份报告及关联数据会被永久删除，且无法恢复。`,
  );
  setText("#confirm-delete", all ? "确认清空" : "确认删除");
  $("#history-confirm").hidden = false; $("#confirm-cancel").focus();
}

function closeHistoryConfirmation() {
  clientState.deleteIntent = null; $("#history-confirm").hidden = true;
}

async function confirmHistoryDeletion() {
  const intent = clientState.deleteIntent; if (!intent) return;
  const button = $("#confirm-delete"); button.disabled = true;
  try {
    const result = await clientApi(
      intent.type === "all" ? "/api/client/history" : `/api/client/reports/${intent.reportId}`,
      {
        method: "DELETE",
        headers: { "Content-Type": "application/json", "X-Confirm-Delete": intent.type === "all" ? "clear-all" : "delete-one" },
      },
    );
    if (intent.type === "all" || clientState.analysis?.report_id === intent.reportId) {
      clientState.analysis = null; $("#analysis").hidden = true;
    }
    closeHistoryConfirmation(); await loadRecentAnalyses(); showClientToast(result.message);
  } catch (error) { showClientToast(error.message); }
  finally { button.disabled = false; }
}

async function openHistoricalReport(reportId) {
  if (clientState.running) { showClientToast("当前分析仍在进行，请完成或停止后再打开历史报告。"); return; }
  try {
    const analysis = await clientApi(`/api/client/reports/${reportId}`);
    clientState.analysis = analysis; await renderAnalysis(analysis);
    $("#loading-state").hidden = true; $("#error-state").hidden = true; $("#analysis").hidden = false;
    if (analysis.history?.explanation) renderExplanation(analysis.history.explanation);
    else {
      setText("#ai-headline", "历史报告已恢复");
      setText("#ai-explanation", "当时的确定性分析、四维证据和风险结果均已完整恢复；这份旧报告没有保存智能解读正文。");
      setText("#ai-risk", "为避免产生新的 Token，本次重开不会重新调用模型。");
      setText("#ai-provider", "历史冻结报告 · 未新增模型调用");
    }
    $("#analysis").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) { showClientToast(error.message); }
}

function renderSnapshotHealth(snapshot) {
  const section = $("#snapshot-health");
  if (!snapshot) { section.hidden = true; return; }
  section.hidden = false;
  const available = snapshot.datasets.filter((item) => item.status !== "not_available");
  const degraded = snapshot.datasets.filter((item) => ["backup", "cache_stale", "not_available"].includes(item.status));
  setText("#snapshot-status", snapshot.degraded ? "部分数据已降级" : "数据状态正常");
  setText("#snapshot-reference", `快照 ${snapshot.snapshot_id.slice(0, 8)}`);
  setText("#snapshot-as-of", formatDateTime(snapshot.as_of || snapshot.acquired_at));
  setText("#snapshot-count", `${snapshot.available_count}/${snapshot.dataset_count} 类`);
  setText("#snapshot-note", degraded.length ? `${degraded.length} 类有说明` : "未发生降级");
  const labels = {
    primary: "真实主源", backup: "备用来源", cache_fresh: "新鲜缓存",
    cache_stale: "历史缓存", fixture: "验证快照", not_available: "暂不可用",
  };
  const names = {
    "market.daily": "日线行情", "market.realtime": "实时报价", "market.fund_flow": "资金流",
    "fundamental.balance_sheet": "资产负债", "fundamental.income_statement": "利润数据",
    "fundamental.cash_flow": "现金流", "fundamental.indicators": "财务指标",
    "fundamental.valuation": "市场估值", "industry.snapshot": "行业行情",
    "macro.index": "市场指数", "macro.gdp": "经济增速", "macro.shibor": "市场利率",
    "macro.policy_lpr": "贷款利率", "sentiment.research": "机构研究",
  };
  const container = $("#snapshot-datasets"); container.replaceChildren();
  snapshot.datasets.forEach((item) => {
    const card = document.createElement("article"); card.className = `snapshot-dataset ${item.status}`;
    const title = document.createElement("strong"); title.textContent = names[item.dataset] || item.dataset;
    const badge = document.createElement("span"); badge.textContent = labels[item.status] || item.status;
    const time = document.createElement("small"); time.textContent = item.as_of ? formatDateTime(item.as_of) : (item.detail || "本次无可用数据");
    card.append(title, badge, time); container.append(card);
  });
}

async function runDynamicDebate() {
  if (!clientState.analysis?.analysis_id) return;
  const button = $("#dynamic-debate-button"); button.disabled = true;
  button.textContent = "正在生成并复核…";
  try {
    const result = await clientApi("/api/client/debate", {
      method: "POST",
      body: JSON.stringify({ analysis_id: clientState.analysis.analysis_id }),
    });
    const firstRound = result.report.rounds[0];
    setText("#positive-claim", localizeDebateText(firstRound.bull.claim));
    setText("#positive-reasoning", localizeDebateText(firstRound.bull.reasoning));
    setText("#risk-claim", localizeDebateText(firstRound.bear.claim));
    setText("#risk-reasoning", localizeDebateText(firstRound.bear.reasoning));
    const proof = $("#debate-proof"); proof.hidden = false;
    renderDebateRounds(result.report.rounds);
    if (result.mode === "dynamic") {
      setText("#debate-mode-note", "DeepSeek 已生成新的论证语言，证据路径、数值、来源和时间均已由程序复核。");
      proof.textContent = `${result.model} · ${result.semantic_attempts} 次候选 · ${result.usage.total_tokens} tokens · 未改变综合评分与风控`;
    } else {
      setText("#debate-mode-note", "动态候选未启用或未通过复核，当前安全使用固定证据辩论。");
      proof.textContent = `${result.fallback_reason} 综合评分、仓位和风控未改变。`;
    }
  } catch (error) {
    showClientToast(error.message);
    setText("#debate-mode-note", "动态辩论暂时不可用，固定证据辩论仍然有效。");
  } finally {
    button.disabled = false; button.textContent = "重新生成动态解读";
  }
}

function renderDebateRounds(rounds) {
  const container = $("#dynamic-debate-rounds"); container.replaceChildren(); container.hidden = false;
  rounds.forEach((round) => {
    const section = document.createElement("section");
    const heading = document.createElement("h4"); heading.textContent = `第 ${round.round} 轮`;
    const bull = document.createElement("article"); bull.className = "round-bull";
    const bullLabel = document.createElement("span"); bullLabel.textContent = "多方观点";
    const bullClaim = document.createElement("strong"); bullClaim.textContent = localizeDebateText(round.bull.claim);
    const bear = document.createElement("article"); bear.className = "round-bear";
    const bearLabel = document.createElement("span"); bearLabel.textContent = "空方回应";
    const bearClaim = document.createElement("strong"); bearClaim.textContent = localizeDebateText(round.bear.claim);
    bull.append(bullLabel, bullClaim); bear.append(bearLabel, bearClaim);
    section.append(heading, bull, bear); container.append(section);
  });
}

function localizeDebateText(value) {
  const terms = [
    ["strong_positive", "明显偏强"], ["strong_negative", "明显偏弱"],
    ["cautious_positive", "谨慎偏强"], ["risk_on", "风险偏好上升"],
    ["risk_off", "风险偏好下降"], ["bullish", "趋势偏强"],
    ["bearish", "趋势偏弱"], ["positive", "偏强"], ["negative", "偏弱"],
    ["neutral", "中性"], ["mixed", "多空交织"], ["moderate", "适中"],
    ["hot", "景气较高"], ["low", "较低"], ["high", "较高"],
    ["trend=", "趋势="], ["signal=", "信号="], ["Regime", "市场状态"],
  ];
  return terms.reduce((text, [source, target]) => text.replaceAll(source, target), String(value ?? ""));
}

function renderResearchBalance(dimensions) {
  const strongest = dimensions.reduce((best, item) => Number(item.score) > Number(best.score) ? item : best);
  const weakest = dimensions.reduce((worst, item) => Number(item.score) < Number(worst.score) ? item : worst);
  const spread = Number(strongest.score) - Number(weakest.score);
  setText("#balance-strongest", `${strongest.name} ${formatSignedScore(strongest.score)}`);
  setText("#balance-weakest", `${weakest.name} ${formatSignedScore(weakest.score)}`);
  setText("#balance-spread", spread >= 50 ? `较大 · ${spread}分` : spread >= 25 ? `中等 · ${spread}分` : `较小 · ${spread}分`);

  const rows = $("#balance-rows"); rows.replaceChildren();
  dimensions.forEach((item) => {
    const score = Math.max(-100, Math.min(100, Number(item.score)));
    const row = document.createElement("div"); row.className = "balance-row";
    row.setAttribute("aria-label", `${item.name}，${formatSignedScore(score)}分，${item.label}`);
    const name = document.createElement("strong"); name.textContent = item.name;
    const lane = document.createElement("div"); lane.className = "balance-lane"; lane.setAttribute("aria-hidden", "true");
    const bar = document.createElement("i"); bar.className = score < 0 ? "negative" : score > 0 ? "positive" : "neutral";
    bar.style.setProperty("--score-size", `${Math.abs(score) / 2}%`);
    lane.append(bar);
    const value = document.createElement("span"); value.textContent = formatSignedScore(score);
    row.append(name, lane, value); rows.append(row);
  });
}

function formatSignedScore(value) {
  const score = Number(value);
  return `${score > 0 ? "+" : ""}${score}`;
}

function renderDimensions(dimensions) {
  const grid = $("#dimension-grid"); grid.replaceChildren();
  const letters = { technical: "T", fundamental: "F", industry: "I", macro: "M" };
  dimensions.forEach((item) => {
    const card = document.createElement("article"); card.className = "dimension-card"; card.dataset.letter = letters[item.id];
    const caption = document.createElement("span"); caption.textContent = item.caption;
    const title = document.createElement("h3"); title.textContent = item.name;
    const scoreBox = document.createElement("div"); scoreBox.className = "dimension-score";
    const score = document.createElement("strong"); score.textContent = `${Number(item.score) > 0 ? "+" : ""}${item.score}`;
    const label = document.createElement("em"); label.textContent = item.label; scoreBox.append(score, label);
    const track = document.createElement("div"); track.className = "score-track"; const fill = document.createElement("i");
    const itemScore = Math.max(-100, Math.min(100, Number(item.score)));
    fill.className = itemScore < 0 ? "negative" : itemScore > 0 ? "positive" : "neutral";
    fill.style.setProperty("--score-size", `${Math.abs(itemScore) / 2}%`); track.append(fill);
    const summary = document.createElement("p"); summary.textContent = item.summary;
    card.append(caption, title, scoreBox, track, summary);
    if (item.sample_scope) { const scope = document.createElement("small"); scope.textContent = `本次观察范围：${item.sample_scope}`; card.append(scope); }
    grid.append(card);
  });
}

async function explainAnalysis(analysis) {
  setText("#ai-headline", "正在整理研究结论…"); setText("#ai-explanation", "确定性分析已经完成，正在转换成更容易理解的说明。");
  try {
    const result = await clientApi("/api/client/explain", { method: "POST", body: JSON.stringify({ analysis }) });
    renderExplanation(result);
  } catch (error) {
    setText("#ai-headline", "智能解读暂时不可用"); setText("#ai-explanation", "原始四维分析与风险结果仍然有效。"); setText("#ai-risk", error.message);
  }
}

function renderExplanation(result) {
  clientState.explanation = result;
  setText("#ai-headline", result.headline); setText("#ai-explanation", result.explanation); setText("#ai-risk", result.risk_note);
  const governance = result.governance || {};
  const providerLabel = result.provider === "deepseek"
    ? `DeepSeek · ${result.model} · ${result.usage.total_tokens} tokens`
    : "本地安全解读 · 未调用外部模型";
  const degradedLabel = result.degraded ? ` · 已降级 · ${result.fallback_reason || "模型未启用"}` : "";
  setText("#ai-provider", `${providerLabel}${degradedLabel} · 版本 ${result.explanation_version || "unknown"}`);
  setText("#ai-feedback-status", "");
  ["#ai-feedback-helpful", "#ai-feedback-not-helpful"].forEach((selector) => { $(selector).disabled = false; });
}

async function submitExplanationFeedback(rating) {
  const reportId = clientState.projection?.report_id || clientState.analysis?.report_id;
  const explanation = clientState.explanation;
  if (!reportId || !explanation) return;
  ["#ai-feedback-helpful", "#ai-feedback-not-helpful"].forEach((selector) => { $(selector).disabled = true; });
  try {
    await clientApi("/api/client/feedback", {
      method: "POST",
      body: JSON.stringify({
        report_id: reportId,
        rating,
        explanation_version: explanation.explanation_version,
        provider: explanation.provider,
        model: explanation.model,
        governance: explanation.governance,
      }),
    });
    setText("#ai-feedback-status", "反馈已记录");
  } catch (error) {
    setText("#ai-feedback-status", `反馈未保存：${error.message}`);
    ["#ai-feedback-helpful", "#ai-feedback-not-helpful"].forEach((selector) => { $(selector).disabled = false; });
  }
}

function drawKline(chart) {
  if (!chart?.series?.[clientState.chartPeriod]) return;
  const full = chart.series[clientState.chartPeriod]; const total = full.bars.length;
  const visibleCount = Math.min(clientState.chartRange, total); const start = Math.max(0, total - visibleCount);
  const bars = full.bars.slice(start); const indicators = {
    sma5: full.indicators.sma5.slice(start), sma20: full.indicators.sma20.slice(start),
  };
  const canvas = $("#kline-chart"); const rect = canvas.getBoundingClientRect(); const scale = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * scale)); canvas.height = Math.max(1, Math.floor(rect.height * scale));
  const ctx = canvas.getContext("2d"); ctx.scale(scale, scale); const width = rect.width, height = rect.height;
  ctx.clearRect(0, 0, width, height);
  const values = bars.flatMap((bar) => [Number(bar.high), Number(bar.low)]); const min = Math.min(...values), max = Math.max(...values); const range = max - min || 1;
  const volumeEnabled = clientState.reportView === "professional" && clientState.chartIndicators.volume;
  const pad = { top: 25, right: 42, bottom: 23, left: 5 }; const volumeH = volumeEnabled ? Math.max(32, height * .2) : 0;
  const chartH = height - pad.top - pad.bottom - volumeH; const chartW = width - pad.left - pad.right;
  ctx.font = "9px Consolas"; ctx.lineWidth = 1;
  for (let i = 0; i < 4; i += 1) { const y = pad.top + chartH * i / 3; const price = max - range * i / 3; ctx.strokeStyle = "#e4e9ec"; ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(width - pad.right, y); ctx.stroke(); ctx.fillStyle = "#89949c"; ctx.fillText(price.toFixed(2), width - pad.right + 6, y + 3); }
  const slot = chartW / bars.length; const bodyWidth = Math.max(2, Math.min(8, slot * .58));
  const yFor = (price) => pad.top + (max - price) / range * chartH;
  bars.forEach((bar, index) => { const open = Number(bar.open), close = Number(bar.close), high = Number(bar.high), low = Number(bar.low); const x = pad.left + slot * index + slot / 2; const color = close >= open ? "#df4d38" : "#16806c"; ctx.strokeStyle = color; ctx.fillStyle = color; ctx.beginPath(); ctx.moveTo(x, yFor(high)); ctx.lineTo(x, yFor(low)); ctx.stroke(); const top = Math.min(yFor(open), yFor(close)); const h = Math.max(1, Math.abs(yFor(open) - yFor(close))); ctx.fillRect(x - bodyWidth / 2, top, bodyWidth, h); });
  if (volumeEnabled) {
    const maxVolume = Math.max(...bars.map((bar) => Number(bar.volume)), 1); const base = pad.top + chartH + volumeH;
    bars.forEach((bar, index) => { const x = pad.left + slot * index + slot / 2; const h = Number(bar.volume) / maxVolume * (volumeH - 7); ctx.fillStyle = Number(bar.close) >= Number(bar.open) ? "rgba(223,77,56,.38)" : "rgba(22,128,108,.38)"; ctx.fillRect(x - bodyWidth / 2, base - h, bodyWidth, h); });
  }
  drawIndicatorLine(ctx, indicators.sma5, "#c38a16", pad, slot, yFor, clientState.reportView === "professional" && clientState.chartIndicators.sma5);
  drawIndicatorLine(ctx, indicators.sma20, "#345f91", pad, slot, yFor, clientState.reportView === "professional" && clientState.chartIndicators.sma20);
  const labels = [0, Math.floor((bars.length - 1) / 2), bars.length - 1]; ctx.fillStyle = "#89949c";
  labels.forEach((index) => { const x = pad.left + slot * index + slot / 2; const label = bars[index].date.slice(5); ctx.fillText(label, Math.max(0, x - 16), height - 5); });
  clientState.chartGeometry = { pad, slot, bars, chartH, yFor };
  if (clientState.reportView === "professional" && clientState.chartHoverIndex !== null) {
    renderChartCrosshair(ctx, Math.max(0, Math.min(bars.length - 1, clientState.chartHoverIndex)));
  }
  setText("#chart-title", clientState.chartPeriod === "weekly" ? "周 K 线" : "日 K 线");
  syncChartControls(chart, total);
}

function drawIndicatorLine(ctx, values, color, pad, slot, yFor, enabled) {
  if (!enabled) return; ctx.strokeStyle = color; ctx.lineWidth = 1.3; ctx.beginPath(); let started = false;
  values.forEach((value, index) => { if (value === null) { started = false; return; } const x = pad.left + slot * index + slot / 2; const y = yFor(Number(value)); if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y); });
  ctx.stroke(); ctx.lineWidth = 1;
}

function renderChartCrosshair(ctx, index) {
  const geometry = clientState.chartGeometry; if (!geometry) return; const bar = geometry.bars[index];
  const x = geometry.pad.left + geometry.slot * index + geometry.slot / 2; const y = geometry.yFor(Number(bar.close));
  ctx.save(); ctx.setLineDash([3,3]); ctx.strokeStyle = "rgba(10,36,58,.46)"; ctx.beginPath(); ctx.moveTo(x, geometry.pad.top); ctx.lineTo(x, geometry.pad.top + geometry.chartH); ctx.moveTo(geometry.pad.left, y); ctx.lineTo(ctx.canvas.width / (window.devicePixelRatio || 1) - geometry.pad.right, y); ctx.stroke(); ctx.restore();
  setText("#chart-tooltip", `${bar.date}  开 ${Number(bar.open).toFixed(2)}  高 ${Number(bar.high).toFixed(2)}  低 ${Number(bar.low).toFixed(2)}  收 ${Number(bar.close).toFixed(2)}  量 ${Number(bar.volume).toLocaleString("zh-CN")}`);
}

function syncChartControls(chart, total) {
  document.querySelectorAll("[data-chart-period]").forEach((button) => button.classList.toggle("active", button.dataset.chartPeriod === clientState.chartPeriod));
  document.querySelectorAll("[data-chart-range]").forEach((button) => { const range = Number(button.dataset.chartRange); button.classList.toggle("active", range === clientState.chartRange); button.disabled = range > total && range !== clientState.chartRange; });
}

function updateChartHover(clientX) {
  const canvas = $("#kline-chart"); const geometry = clientState.chartGeometry; if (!geometry || clientState.reportView !== "professional") return;
  const x = clientX - canvas.getBoundingClientRect().left; clientState.chartHoverIndex = Math.max(0, Math.min(geometry.bars.length - 1, Math.floor((x - geometry.pad.left) / geometry.slot)));
  drawKline(clientState.projection.shared.chart);
}

function formatDateTime(value) { if (!value) return "—"; return `${value.slice(0, 10)} ${value.slice(11, 16)}`; }

$("#stock-form").addEventListener("submit", (event) => { event.preventDefault(); runClientAnalysis(); });
$("#retry-button").addEventListener("click", () => {
  if (clientState.jobId) retryAnalysisJob(); else runClientAnalysis();
});
$("#cancel-analysis-button").addEventListener("click", cancelAnalysisJob);
$("#retry-job-button").addEventListener("click", retryAnalysisJob);
$("#dynamic-debate-button").addEventListener("click", runDynamicDebate);
$("#ai-feedback-helpful").addEventListener("click", () => submitExplanationFeedback("helpful"));
$("#ai-feedback-not-helpful").addEventListener("click", () => submitExplanationFeedback("not_helpful"));
$("#clear-history-button").addEventListener("click", () => requestHistoryDeletion({ type: "all" }));
$("#watchlist-toggle").addEventListener("click", () => toggleWatchlist());
$("#compare-button").addEventListener("click", () => runReportComparison());
$("#favorite-report-button").addEventListener("click", () => toggleReportFavorite(clientState.projection?.report_id || clientState.analysis?.report_id));
$("#export-report-button").addEventListener("click", exportCurrentReport);
$("#print-report-button").addEventListener("click", () => printResearch("report"));
$("#confirm-cancel").addEventListener("click", closeHistoryConfirmation);
$("#confirm-delete").addEventListener("click", confirmHistoryDeletion);
$("#stock-select").addEventListener("change", () => { syncModeAvailability(); syncWatchlistButton(); });
$("#security-search").addEventListener("input", (event) => { clientState.catalogQuery = event.target.value.trim(); renderSecurityOptions(); syncWatchlistButton(); });
$("#industry-filter").addEventListener("change", (event) => { clientState.catalogIndustry = event.target.value; renderSecurityOptions(); syncWatchlistButton(); });
document.addEventListener("click", (event) => {
  const favoriteReport = event.target.closest("[data-favorite-report-id]");
  if (favoriteReport) { toggleReportFavorite(favoriteReport.dataset.favoriteReportId); return; }
  const historyFilter = event.target.closest("[data-history-filter]");
  if (historyFilter) { clientState.historyFilter = historyFilter.dataset.historyFilter; renderRecentAnalyses(); return; }
  const exportComparison = event.target.closest("[data-export-comparison]");
  if (exportComparison) { exportCurrentComparison(); return; }
  const printTarget = event.target.closest("[data-print-target]");
  if (printTarget) { printResearch(printTarget.dataset.printTarget); return; }
  const watchlist = event.target.closest("[data-watchlist-symbol]");
  if (watchlist) {
    clientState.catalogQuery = ""; clientState.catalogIndustry = "";
    $("#security-search").value = ""; $("#industry-filter").value = "";
    renderSecurityOptions();
    $("#stock-select").value = watchlist.dataset.watchlistSymbol;
    syncModeAvailability(); syncWatchlistButton();
    document.querySelector(".research-entry").scrollIntoView({ behavior: "smooth", block: "center" }); return;
  }
  const deleteButton = event.target.closest("[data-delete-report-id]");
  if (deleteButton) {
    requestHistoryDeletion({ type: "one", reportId: deleteButton.dataset.deleteReportId, name: deleteButton.dataset.deleteReportName }); return;
  }
  const historyCard = event.target.closest("[data-report-id]");
  if (historyCard) { openHistoricalReport(historyCard.dataset.reportId); return; }
  const reportView = event.target.closest("[data-report-view]");
  if (reportView) { switchReportView(reportView.dataset.reportView); return; }
  const chartPeriod = event.target.closest("[data-chart-period]");
  if (chartPeriod) { clientState.chartPeriod = chartPeriod.dataset.chartPeriod; clientState.chartHoverIndex = null; drawKline(clientState.projection.shared.chart); return; }
  const chartRange = event.target.closest("[data-chart-range]");
  if (chartRange) { clientState.chartRange = Number(chartRange.dataset.chartRange); clientState.chartHoverIndex = null; drawKline(clientState.projection.shared.chart); return; }
  const button = event.target.closest("[data-client-mode]"); if (!button) return;
  if (button.disabled) return;
  clientState.mode = button.dataset.clientMode;
  syncModeAvailability();
});
document.addEventListener("change", (event) => {
  const input = event.target.closest("[data-chart-indicator]"); if (!input) return;
  clientState.chartIndicators[input.dataset.chartIndicator] = input.checked;
  if (clientState.projection) drawKline(clientState.projection.shared.chart);
});
$("#kline-chart").addEventListener("pointermove", (event) => updateChartHover(event.clientX));
$("#kline-chart").addEventListener("pointerleave", () => { clientState.chartHoverIndex = null; if (clientState.projection) drawKline(clientState.projection.shared.chart); setText("#chart-tooltip", "移动鼠标或使用左右方向键查看开高低收和成交量"); });
$("#kline-chart").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight"].includes(event.key) || clientState.reportView !== "professional") return;
  event.preventDefault(); const total = clientState.chartGeometry?.bars.length || 0; if (!total) return;
  const fallback = event.key === "ArrowLeft" ? total - 1 : 0; const delta = event.key === "ArrowLeft" ? -1 : 1;
  clientState.chartHoverIndex = clientState.chartHoverIndex === null ? fallback : Math.max(0, Math.min(total - 1, clientState.chartHoverIndex + delta));
  drawKline(clientState.projection.shared.chart);
});
window.addEventListener("resize", () => { if (clientState.projection) drawKline(clientState.projection.shared.chart); });
window.addEventListener("afterprint", () => { delete document.body.dataset.printTarget; });
$("#account-button").addEventListener("click", () => $("#account-dialog").showModal());
$(".account-close").addEventListener("click", () => $("#account-dialog").close());
$("#model-key-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const value = $("#session-model-key").value.trim();
  if (!value) { showClientToast("请先输入 DeepSeek API Key。"); return; }
  saveSessionModelKey(value).catch((error) => showClientToast(error.message));
});
$("#clear-model-key").addEventListener("click", () => clearSessionModelKey().catch((error) => showClientToast(error.message)));
$("#logout-button").addEventListener("click", () => logoutClient().catch((error) => showClientToast(error.message)));

applyReportViewVisibility();

loadRuntimeStatus();
window.setInterval(loadRuntimeStatus, 10000);

loadClientSession()
  .then(() => clientApi("/api/client/overview"))
  .then((overview) => { populateOverview(overview); loadRecentAnalyses(); return resumeOrStartAnalysis(); })
  .catch((error) => { $("#loading-state").hidden = true; $("#error-state").hidden = false; setText("#error-message", error.message); });
