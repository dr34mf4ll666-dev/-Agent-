"use strict";

const clientState = { mode: "offline", analysis: null, overview: null, running: false, jobId: null, pollTimer: null };
const $ = (selector) => document.querySelector(selector);

async function clientApi(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  let body;
  try { body = await response.json(); } catch { body = { error: "服务返回了无法识别的内容。" }; }
  if (!response.ok) throw new Error(body.error || `请求失败 (${response.status})`);
  return body;
}

function setText(selector, value) { $(selector).textContent = value ?? "—"; }
function showClientToast(message) {
  const toast = $("#client-toast"); toast.textContent = message; toast.classList.add("show");
  window.clearTimeout(showClientToast.timer); showClientToast.timer = window.setTimeout(() => toast.classList.remove("show"), 2800);
}

function populateOverview(overview) {
  clientState.overview = overview;
  const select = $("#stock-select"); select.replaceChildren();
  overview.securities.forEach((security) => {
    const option = document.createElement("option"); option.value = security.symbol;
    option.dataset.modes = security.modes.join(",");
    option.textContent = `${security.name}  ${security.code} · ${security.exchange}`; select.append(option);
  });
  const strip = $("#capability-strip"); strip.replaceChildren();
  overview.capabilities.forEach((item) => { const span = document.createElement("span"); span.textContent = item; strip.append(span); });
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
        window.sessionStorage.removeItem("active_analysis_job"); renderAnalysis(analysis);
        $("#loading-state").hidden = true; $("#analysis").hidden = false;
        window.requestAnimationFrame(() => drawKline(analysis.data.bars)); explainAnalysis(analysis);
        finishJobControls(); return;
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
    copy.append(label, state); item.append(marker, copy); list.append(item);
  });
  $("#cancel-analysis-button").disabled = ["succeeded", "failed", "cancelled"].includes(job.status) || job.cancel_requested;
  $("#cancel-analysis-button").hidden = ["succeeded", "failed", "cancelled"].includes(job.status);
  $("#retry-job-button").hidden = !job.can_retry;
  const recovery = job.recovered ? "服务重启后已从检查点恢复。" : "";
  const retries = job.retry_count ? ` 已重试 ${job.retry_count} 次。` : "";
  setText("#job-progress-note", job.cancel_requested ? "停止请求已提交，当前步骤会在下一个安全点结束。" : `${recovery}${retries}这里只显示程序确认的真实节点，不使用模拟百分比。`);
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
  $("#retry-job-button").disabled = true; setText("#loading-message", "正在从失败步骤继续…");
  try {
    const job = await clientApi(`/api/client/jobs/${clientState.jobId}/retry`, { method: "POST", body: "{}" });
    window.sessionStorage.setItem("active_analysis_job", job.job_id); renderJobProgress(job); await followAnalysisJob(job);
  } catch (error) { finishJobWithError(error.message); }
}

function finishJobControls() { clientState.running = false; $("#analyze-button").disabled = false; $("#cancel-analysis-button").textContent = "停止本次分析"; syncModeAvailability(); }
function finishFailedJob(job) {
  window.clearTimeout(clientState.pollTimer); clientState.running = false; renderJobProgress(job);
  $("#loading-state").hidden = false; $("#analysis").hidden = true; $("#error-state").hidden = true;
  $("#retry-job-button").disabled = false; $("#analyze-button").disabled = false; syncModeAvailability();
  showClientToast(job.error?.message || "分析任务执行失败，可从失败步骤继续。");
}
function finishJobWithError(message) {
  window.clearTimeout(clientState.pollTimer); clientState.jobId = null; window.sessionStorage.removeItem("active_analysis_job");
  $("#loading-state").hidden = true; $("#error-state").hidden = false;
  setText("#error-message", message); showClientToast(message); finishJobControls();
}
async function resumeOrStartAnalysis() {
  const existing = window.sessionStorage.getItem("active_analysis_job");
  if (!existing) return runClientAnalysis();
  clientState.jobId = existing; clientState.running = true; $("#analyze-button").disabled = true;
  syncModeAvailability();
  $("#analysis").hidden = true; $("#error-state").hidden = true; $("#loading-state").hidden = false;
  await followAnalysisJob();
}

function renderAnalysis(data) {
  setText("#security-exchange", data.security.exchange);
  setText("#security-name", data.security.name);
  setText("#security-code", data.security.code);
  setText("#latest-close", Number(data.quote.latest_close).toFixed(2));
  const change = Number(data.quote.daily_return_percent); const changeNode = $("#daily-return");
  changeNode.textContent = `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`;
  changeNode.className = change >= 0 ? "up" : "down";
  setText("#data-label", data.data.label); setText("#data-as-of", formatDateTime(data.data.as_of));
  setText("#source-count", data.data.source_count);
  setText("#verdict-label", data.verdict.label); setText("#action-label", data.verdict.action_label);
  setText("#confidence-value", data.verdict.confidence); setText("#weighted-score", data.verdict.weighted_score);
  setText("#position-cap", `${data.risk.position_cap_percent}%`);
  $("#confidence-ring").style.setProperty("--confidence", data.verdict.confidence);
  setText("#support-price", data.quote.support); setText("#reference-price", data.price_band.reference); setText("#resistance-price", data.quote.resistance);
  renderResearchBalance(data.dimensions);
  renderDimensions(data.dimensions);
  setText("#positive-claim", localizeDebateText(data.debate.positive)); setText("#positive-reasoning", localizeDebateText(data.debate.positive_reasoning));
  setText("#risk-claim", localizeDebateText(data.debate.risk)); setText("#risk-reasoning", localizeDebateText(data.debate.risk_reasoning));
  setText("#debate-mode-note", "当前展示经过确定性证据复核的固定辩论。");
  $("#debate-proof").hidden = true;
  $("#dynamic-debate-rounds").hidden = true;
  $("#dynamic-debate-button").disabled = !data.analysis_id;
  setText("#band-lower", data.price_band.lower); setText("#band-reference", data.price_band.reference); setText("#band-upper", data.price_band.upper);
  setText("#estimated-loss", data.risk.estimated_loss_percent ? `${data.risk.estimated_loss_percent}%` : "未计算");
  setText("#reward-risk", data.risk.reward_risk_ratio || "未计算"); setText("#risk-status", data.risk.status);
  const lower = Number(data.price_band.lower), ref = Number(data.price_band.reference), upper = Number(data.price_band.upper);
  const marker = upper > lower ? Math.max(0, Math.min(100, (ref - lower) / (upper - lower) * 100)) : 50;
  $("#price-marker").style.left = `${marker}%`;
  const sourceList = $("#source-list"); sourceList.replaceChildren();
  data.data.sources.forEach((source) => { const item = document.createElement("li"); item.textContent = source; sourceList.append(item); });
  const industry = data.dimensions.find((item) => item.id === "industry");
  setText("#scope-note", `${data.safety.notice} 当前演示的行业观察范围为“${industry.sample_scope}”；研究区间与置信度均不是收益预测。`);
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
    setText("#ai-headline", result.headline); setText("#ai-explanation", result.explanation); setText("#ai-risk", result.risk_note);
    setText("#ai-provider", result.provider === "deepseek" ? `DeepSeek · ${result.model} · ${result.usage.total_tokens} tokens` : "本地安全解读 · 未调用外部模型");
  } catch (error) {
    setText("#ai-headline", "智能解读暂时不可用"); setText("#ai-explanation", "原始四维分析与风险结果仍然有效。"); setText("#ai-risk", error.message);
  }
}

function drawKline(bars) {
  const canvas = $("#kline-chart"); const rect = canvas.getBoundingClientRect(); const scale = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * scale)); canvas.height = Math.max(1, Math.floor(rect.height * scale));
  const ctx = canvas.getContext("2d"); ctx.scale(scale, scale); const width = rect.width, height = rect.height;
  ctx.clearRect(0, 0, width, height);
  const values = bars.flatMap((bar) => [Number(bar.high), Number(bar.low)]); const min = Math.min(...values), max = Math.max(...values); const range = max - min || 1;
  const pad = { top: 12, right: 42, bottom: 23, left: 5 }; const chartH = height - pad.top - pad.bottom; const chartW = width - pad.left - pad.right;
  ctx.font = "9px Consolas"; ctx.lineWidth = 1;
  for (let i = 0; i < 4; i += 1) { const y = pad.top + chartH * i / 3; const price = max - range * i / 3; ctx.strokeStyle = "#e4e9ec"; ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(width - pad.right, y); ctx.stroke(); ctx.fillStyle = "#89949c"; ctx.fillText(price.toFixed(2), width - pad.right + 6, y + 3); }
  const slot = chartW / bars.length; const bodyWidth = Math.max(2, Math.min(8, slot * .58));
  const yFor = (price) => pad.top + (max - price) / range * chartH;
  bars.forEach((bar, index) => { const open = Number(bar.open), close = Number(bar.close), high = Number(bar.high), low = Number(bar.low); const x = pad.left + slot * index + slot / 2; const color = close >= open ? "#df4d38" : "#16806c"; ctx.strokeStyle = color; ctx.fillStyle = color; ctx.beginPath(); ctx.moveTo(x, yFor(high)); ctx.lineTo(x, yFor(low)); ctx.stroke(); const top = Math.min(yFor(open), yFor(close)); const h = Math.max(1, Math.abs(yFor(open) - yFor(close))); ctx.fillRect(x - bodyWidth / 2, top, bodyWidth, h); });
  const labels = [0, Math.floor((bars.length - 1) / 2), bars.length - 1]; ctx.fillStyle = "#89949c";
  labels.forEach((index) => { const x = pad.left + slot * index + slot / 2; const label = bars[index].date.slice(5); ctx.fillText(label, Math.max(0, x - 16), height - 5); });
}

function formatDateTime(value) { if (!value) return "—"; return `${value.slice(0, 10)} ${value.slice(11, 16)}`; }

$("#stock-form").addEventListener("submit", (event) => { event.preventDefault(); runClientAnalysis(); });
$("#retry-button").addEventListener("click", runClientAnalysis);
$("#cancel-analysis-button").addEventListener("click", cancelAnalysisJob);
$("#retry-job-button").addEventListener("click", retryAnalysisJob);
$("#dynamic-debate-button").addEventListener("click", runDynamicDebate);
$("#stock-select").addEventListener("change", syncModeAvailability);
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-client-mode]"); if (!button) return;
  if (button.disabled) return;
  clientState.mode = button.dataset.clientMode;
  syncModeAvailability();
});
window.addEventListener("resize", () => { if (clientState.analysis) drawKline(clientState.analysis.data.bars); });

clientApi("/api/client/overview")
  .then((overview) => { populateOverview(overview); return resumeOrStartAnalysis(); })
  .catch((error) => { $("#loading-state").hidden = true; $("#error-state").hidden = false; setText("#error-message", error.message); });
