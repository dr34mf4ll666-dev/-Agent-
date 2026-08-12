"use strict";

const clientState = { mode: "offline", analysis: null, overview: null, running: false };
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
  document.querySelectorAll("[data-client-mode]").forEach((button) => {
    button.disabled = !modes.has(button.dataset.clientMode);
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
  $("#analyze-button").disabled = true;
  $("#analysis").hidden = true; $("#error-state").hidden = true; $("#loading-state").hidden = false;
  setText("#loading-message", clientState.mode === "live" ? "正在读取最新只读数据，可能需要一点时间…" : "读取已验证行情与研究证据，形成统一结论…");
  try {
    const analysis = await clientApi("/api/client/analyze", {
      method: "POST",
      body: JSON.stringify({ symbol: $("#stock-select").value, mode: clientState.mode }),
    });
    clientState.analysis = analysis;
    renderAnalysis(analysis);
    $("#loading-state").hidden = true; $("#analysis").hidden = false;
    window.requestAnimationFrame(() => drawKline(analysis.data.bars));
    explainAnalysis(analysis);
  } catch (error) {
    $("#loading-state").hidden = true; $("#error-state").hidden = false;
    setText("#error-message", error.message); showClientToast(error.message);
  } finally {
    clientState.running = false; $("#analyze-button").disabled = false;
  }
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
  renderDimensions(data.dimensions);
  setText("#positive-claim", data.debate.positive); setText("#positive-reasoning", data.debate.positive_reasoning);
  setText("#risk-claim", data.debate.risk); setText("#risk-reasoning", data.debate.risk_reasoning);
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
    fill.style.width = `${Math.max(4, Math.min(100, (Number(item.score) + 100) / 2))}%`; if (Number(item.score) < 0) fill.style.background = "var(--jade)"; track.append(fill);
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
$("#stock-select").addEventListener("change", syncModeAvailability);
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-client-mode]"); if (!button) return;
  if (button.disabled) return;
  clientState.mode = button.dataset.clientMode;
  syncModeAvailability();
});
window.addEventListener("resize", () => { if (clientState.analysis) drawKline(clientState.analysis.data.bars); });

clientApi("/api/client/overview")
  .then((overview) => { populateOverview(overview); return runClientAnalysis(); })
  .catch((error) => { $("#loading-state").hidden = true; $("#error-state").hidden = false; setText("#error-message", error.message); });
