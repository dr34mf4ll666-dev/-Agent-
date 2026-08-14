"use strict";

const state = {
  overview: null,
  stage: "A",
  mode: "offline",
  currentResult: null,
  running: false,
  observability: null,
  selectedTraceId: null,
};

const elements = {
  stageNav: document.querySelector("#stage-nav"),
  actionGrid: document.querySelector("#action-grid"),
  stageEyebrow: document.querySelector("#stage-eyebrow"),
  stageTitle: document.querySelector("#stage-title"),
  stageDescription: document.querySelector("#stage-description"),
  modeNote: document.querySelector("#mode-note"),
  modelStatus: document.querySelector("#model-status"),
  assistantBadge: document.querySelector("#assistant-badge"),
  assistantThread: document.querySelector("#assistant-thread"),
  assistantForm: document.querySelector("#assistant-form"),
  assistantInput: document.querySelector("#assistant-input"),
  resultSection: document.querySelector("#result-section"),
  resultStatus: document.querySelector("#result-status"),
  resultEmpty: document.querySelector("#result-empty"),
  resultContent: document.querySelector("#result-content"),
  resultAction: document.querySelector("#result-action"),
  resultMode: document.querySelector("#result-mode"),
  resultDuration: document.querySelector("#result-duration"),
  resultSummary: document.querySelector("#result-summary"),
  resultRaw: document.querySelector("#result-raw"),
  toast: document.querySelector("#toast"),
  reliabilityMetrics: document.querySelector("#reliability-metrics"),
  traceList: document.querySelector("#trace-list"),
  traceWaterfall: document.querySelector("#trace-waterfall"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let payload;
  try { payload = await response.json(); } catch { payload = { error: "服务返回了无法识别的内容。" }; }
  if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
  return payload;
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => elements.toast.classList.remove("show"), 3200);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatObservedDuration(milliseconds) {
  const value = Number(milliseconds || 0);
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60000) return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} 秒`;
  return `${Math.floor(value / 60000)} 分 ${Math.round((value % 60000) / 1000)} 秒`;
}

function metricText(value, suffix = "") {
  return `${Number(value || 0).toLocaleString("zh-CN")}${suffix}`;
}

function renderReliability(overview) {
  state.observability = overview;
  const metricValues = [
    metricText(overview.metrics.success_rate_percent, "%"),
    formatObservedDuration(overview.metrics.latency_p50_ms),
    formatObservedDuration(overview.metrics.latency_p95_ms),
    metricText(overview.metrics.data_source_failure_rate_percent, "%"),
    metricText(overview.metrics.cache_hit_rate_percent, "%"),
    metricText(overview.metrics.total_tokens),
  ];
  elements.reliabilityMetrics.querySelectorAll("strong").forEach((node, index) => { node.textContent = metricValues[index]; });
  elements.traceList.replaceChildren();
  if (!overview.recent_traces.length) {
    elements.traceList.append(el("p", "trace-empty", "完成一次客户分析后，这里会出现追踪记录。"));
  } else {
    overview.recent_traces.forEach((trace) => {
      const button = el("button", `trace-row${trace.trace_id === state.selectedTraceId ? " active" : ""}`);
      button.type = "button"; button.dataset.traceId = trace.trace_id;
      const top = el("span"); top.append(el("strong", "", trace.request.symbol || "未知标的"), el("i", trace.status, ({succeeded:"成功",failed:"失败",cancelled:"已停止",running:"运行中",queued:"排队中"})[trace.status] || trace.status));
      const meta = el("small", "", `${trace.trace_id.slice(0, 8)} · ${formatObservedDuration(trace.duration_ms)}`);
      button.append(top, meta); elements.traceList.append(button);
    });
    if (!state.selectedTraceId || !overview.recent_traces.some((item) => item.trace_id === state.selectedTraceId)) {
      loadTrace(overview.recent_traces[0].trace_id);
    }
  }
  const slow = document.querySelector("#slow-spans"); slow.replaceChildren();
  if (!overview.slowest.length) slow.append(el("span", "", "暂无数据"));
  overview.slowest.forEach((span) => slow.append(el("span", "", `${span.component} · ${formatObservedDuration(span.duration_ms)}`)));
}

async function loadReliability() {
  try { renderReliability(await api("/api/observability/overview")); }
  catch (error) { elements.traceList.replaceChildren(el("p", "trace-empty", `可靠性数据暂不可用：${error.message}`)); }
}

async function loadTrace(traceId) {
  state.selectedTraceId = traceId;
  document.querySelectorAll(".trace-row").forEach((item) => item.classList.toggle("active", item.dataset.traceId === traceId));
  try {
    const trace = await api(`/api/observability/traces/${traceId}`);
    document.querySelector("#trace-title").textContent = `${trace.request.symbol || "分析任务"} · ${trace.trace_id}`;
    document.querySelector("#trace-status").textContent = ({succeeded:"分析成功",failed:"分析失败",cancelled:"用户停止",running:"正在运行",queued:"排队中"})[trace.status] || trace.status;
    document.querySelector("#trace-status").className = trace.status;
    document.querySelector("#trace-duration").textContent = `${trace.spans.length} 个观测步骤 · ${formatObservedDuration(trace.duration_ms)}`;
    renderWaterfall(trace);
  } catch (error) { elements.traceWaterfall.replaceChildren(el("p", "trace-empty", error.message)); }
}

function renderWaterfall(trace) {
  elements.traceWaterfall.replaceChildren();
  if (!trace.spans.length) { elements.traceWaterfall.append(el("p", "trace-empty", "任务已创建，等待第一个运行步骤。")); return; }
  const starts = trace.spans.map((span) => Date.parse(span.started_at));
  const origin = Math.min(...starts); const total = Math.max(1, trace.duration_ms);
  const layerNames = { http:"HTTP 接口", task:"分析任务", data:"数据中心", graph:"Graph 节点", harness:"安全护栏", model:"模型网关", database:"历史数据库" };
  const statusNames = { queued:"排队",running:"运行中",succeeded:"成功",failed:"失败",cancelled:"已停止",skipped:"跳过",retrying:"重试",degraded:"降级",cache_hit:"缓存命中" };
  trace.spans.forEach((span) => {
    const row = el("div", "waterfall-row");
    const label = el("div", "waterfall-label"); label.append(el("small", "", layerNames[span.layer] || span.layer), el("strong", "", span.component));
    const track = el("div", "waterfall-track");
    const bar = el("span", `waterfall-bar ${span.status}`); const left = Math.max(0, (Date.parse(span.started_at) - origin) / total * 100); const width = Math.max(1.5, Number(span.duration_ms || 0) / total * 100);
    bar.style.setProperty("--bar-left", `${Math.min(98.5, left)}%`); bar.style.setProperty("--bar-width", `${Math.min(100 - left, width)}%`);
    bar.title = `${statusNames[span.status] || span.status} · ${formatObservedDuration(span.duration_ms)}`; track.append(bar);
    const meta = el("div", "waterfall-meta"); meta.append(el("strong", span.status, statusNames[span.status] || span.status), el("small", "", `${formatObservedDuration(span.duration_ms)}${span.attempts > 1 ? ` · ${span.attempts} 次` : ""}`));
    row.append(label, track, meta); elements.traceWaterfall.append(row);
  });
}

function renderOverview() {
  const { overview } = state;
  document.querySelector("#project-description").textContent = overview.project.description;
  document.querySelector("#action-count").textContent = overview.actions.length;
  const assistant = overview.assistant;
  elements.modelStatus.innerHTML = "";
  const dot = el("i", `status-dot ${assistant.configured ? "live" : "pending"}`);
  elements.modelStatus.append(dot, document.createTextNode(assistant.configured ? `DeepSeek · ${assistant.model}` : "本地助手 · DeepSeek 未配置"));
  elements.assistantBadge.textContent = assistant.configured ? "LIVE API" : "LOCAL FALLBACK";

  elements.stageNav.replaceChildren();
  overview.stages.forEach((stage) => {
    const button = el("button", `stage-button${stage.id === state.stage ? " active" : ""}`);
    button.type = "button";
    button.dataset.stage = stage.id;
    const letter = el("span", "stage-letter", stage.id);
    const copy = el("span");
    copy.append(el("strong", "", stage.eyebrow), el("small", "", `${overview.actions.filter((item) => item.stage === stage.id).length} 项能力`));
    button.append(letter, copy);
    elements.stageNav.append(button);
  });
  renderStage();
}

function renderStage() {
  const stage = state.overview.stages.find((item) => item.id === state.stage);
  elements.stageEyebrow.textContent = `STAGE ${stage.id} / ${stage.eyebrow.toUpperCase()}`;
  elements.stageTitle.textContent = stage.title;
  elements.stageDescription.textContent = stage.description;
  document.querySelectorAll(".stage-button").forEach((button) => button.classList.toggle("active", button.dataset.stage === state.stage));
  renderActions();
}

function renderActions() {
  const actions = state.overview.actions.filter((action) => action.stage === state.stage);
  elements.actionGrid.replaceChildren();
  actions.forEach((action, index) => {
    const unavailable = state.mode === "live" && !action.supports_live;
    const card = el("article", `action-card${unavailable ? " live-unavailable" : ""}`);
    card.dataset.index = String(index + 1).padStart(2, "0");
    card.append(el("span", "card-stage", `${action.stage} / ${action.id.toUpperCase()}`));
    card.append(el("h3", "", action.title));
    card.append(el("p", "", unavailable ? `${action.description} 此功能只提供离线复现。` : action.description));
    const footer = el("div", "card-footer");
    const tags = el("div", "tag-list");
    action.tags.forEach((tag) => tags.append(el("span", "", tag)));
    const button = el("button", "run-button", unavailable ? "仅离线" : "运行 →");
    button.type = "button";
    button.dataset.runAction = action.id;
    button.disabled = unavailable || state.running;
    footer.append(tags, button);
    card.append(footer);
    elements.actionGrid.append(card);
  });
}

async function runAction(actionId) {
  if (state.running) return;
  const action = state.overview.actions.find((item) => item.id === actionId);
  if (!action) return;
  if (state.mode === "live" && !action.supports_live) {
    showToast("该功能只提供离线复现模式。");
    return;
  }
  state.running = true;
  renderActions();
  elements.resultStatus.className = "result-status running";
  elements.resultStatus.textContent = "正在运行";
  elements.resultEmpty.hidden = false;
  elements.resultContent.hidden = true;
  elements.resultEmpty.querySelector("h3").textContent = `正在运行：${action.title}`;
  elements.resultEmpty.querySelector("p").textContent = state.mode === "live" ? "正在读取真实数据或调用真实模型，请稍候。" : "正在复现固定流程并提取验收摘要。";
  elements.resultSection.scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const result = await api("/api/run", { method: "POST", body: JSON.stringify({ action_id: actionId, mode: state.mode }) });
    state.currentResult = result;
    showResult(result);
  } catch (error) {
    elements.resultStatus.className = "result-status failed";
    elements.resultStatus.textContent = "运行失败";
    elements.resultEmpty.querySelector("h3").textContent = "这次没有跑通";
    elements.resultEmpty.querySelector("p").textContent = error.message;
    showToast(error.message);
  } finally {
    state.running = false;
    renderActions();
  }
}

function showResult(result) {
  elements.resultEmpty.hidden = true;
  elements.resultContent.hidden = false;
  elements.resultStatus.className = `result-status ${result.status}`;
  elements.resultStatus.textContent = result.status === "succeeded" ? "运行成功" : "运行失败";
  elements.resultAction.textContent = result.title;
  elements.resultMode.textContent = result.mode === "live" ? "真实数据 / 模型" : "离线复现";
  elements.resultDuration.textContent = `${(result.duration_ms / 1000).toFixed(2)} 秒`;
  elements.resultSummary.textContent = result.summary || "程序已结束，但没有提取到摘要。请展开完整运行记录。";
  elements.resultRaw.textContent = result.raw_output || "无输出";
}

function addMessage(role, text) {
  const bubble = el("div", `chat-bubble ${role === "user" ? "user-message" : "assistant-message"}`);
  bubble.append(el("span", "", role === "user" ? "你" : "助手"), el("p", "", text));
  elements.assistantThread.append(bubble);
  elements.assistantThread.scrollTop = elements.assistantThread.scrollHeight;
  return bubble;
}

async function askAssistant(message) {
  addMessage("user", message);
  const pending = addMessage("assistant", "正在结合当前项目结果整理…");
  const submit = elements.assistantForm.querySelector("button[type=submit]");
  submit.disabled = true;
  try {
    const context = state.currentResult ? {
      action_id: state.currentResult.action_id,
      title: state.currentResult.title,
      summary: state.currentResult.summary,
    } : null;
    const response = await api("/api/assistant", { method: "POST", body: JSON.stringify({ message, context }) });
    pending.querySelector("p").textContent = response.answer;
    if (response.suggested_action_id && response.suggested_action_id !== "none") {
      const action = state.overview.actions.find((item) => item.id === response.suggested_action_id);
      if (action) {
        const suggestion = el("div", "suggestion-card");
        suggestion.append(el("small", "", response.reason || "建议下一步查看此功能。"));
        const button = el("button", "", `查看并运行：${action.title}`);
        button.type = "button";
        button.dataset.suggestAction = action.id;
        suggestion.append(button);
        elements.assistantThread.append(suggestion);
      }
    }
    const meta = response.provider === "deepseek" ? `DeepSeek · ${response.model} · ${response.usage.total_tokens} tokens` : "本地规则助手";
    pending.querySelector("span").textContent = meta;
  } catch (error) {
    pending.querySelector("p").textContent = `助手暂时不可用：${error.message}`;
  } finally {
    submit.disabled = false;
    elements.assistantThread.scrollTop = elements.assistantThread.scrollHeight;
  }
}

document.addEventListener("click", (event) => {
  const stageButton = event.target.closest("[data-stage]");
  if (stageButton) { state.stage = stageButton.dataset.stage; renderStage(); return; }
  const modeButton = event.target.closest("[data-mode]");
  if (modeButton) {
    state.mode = modeButton.dataset.mode;
    document.querySelectorAll("[data-mode]").forEach((button) => button.classList.toggle("active", button.dataset.mode === state.mode));
    elements.modeNote.textContent = state.mode === "live" ? "真实模式会读取外部数据或调用 DeepSeek；仍然不会连接券商或创建真实订单。" : "离线复现使用固定样本，最适合演示与验收。";
    renderActions(); return;
  }
  const runButton = event.target.closest("[data-run-action]");
  if (runButton) { runAction(runButton.dataset.runAction); return; }
  const jumpButton = event.target.closest("[data-jump-stage]");
  if (jumpButton) { state.stage = jumpButton.dataset.jumpStage; renderStage(); document.querySelector("#workspace").scrollIntoView({ behavior: "smooth" }); return; }
  const suggestionButton = event.target.closest("[data-suggest-action]");
  if (suggestionButton) {
    const action = state.overview.actions.find((item) => item.id === suggestionButton.dataset.suggestAction);
    state.stage = action.stage; renderStage(); document.querySelector("#workspace").scrollIntoView({ behavior: "smooth" }); showToast("已定位到建议功能，请确认后点击运行。");
  }
});

elements.assistantForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = elements.assistantInput.value.trim();
  if (!message) { showToast("请先输入问题。"); return; }
  elements.assistantInput.value = "";
  askAssistant(message);
});

document.querySelector("#explain-result").addEventListener("click", () => {
  if (!state.currentResult) return;
  askAssistant("请用通俗中文解释这次运行结果说明了什么，以及我答辩时应该强调什么。 ");
});

const dialog = document.querySelector("#about-dialog");
document.querySelector("#about-button").addEventListener("click", () => dialog.showModal());
document.querySelector(".dialog-close").addEventListener("click", () => dialog.close());
document.querySelector("#refresh-reliability").addEventListener("click", loadReliability);
elements.traceList.addEventListener("click", (event) => {
  const row = event.target.closest("[data-trace-id]"); if (row) loadTrace(row.dataset.traceId);
});

api("/api/overview")
  .then((overview) => { state.overview = overview; renderOverview(); })
  .catch((error) => {
    elements.stageTitle.textContent = "控制台未能读取项目状态";
    elements.stageDescription.textContent = error.message;
    showToast(error.message);
  });

loadReliability();
window.setInterval(loadReliability, 10000);
