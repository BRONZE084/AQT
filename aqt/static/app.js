const state = {
  summary: null,
  equityCurve: [],
  trades: [],
  plan: [],
  files: {},
  activeTab: "trades",
  busy: false,
  chartMode: "kline",
  quotes: {},
  indices: [],
  watchlist: [],
  klineSymbol: null,
  klineData: null,
  klineDays: 60,
  klinePeriod: "1d",
  riskScores: {},
  marketOpen: false,
  pollingTimer: null,
  optimizeResults: [],
  optimizeMetric: null,
};

let klineChart = null;

const form = document.querySelector("#settingsForm");
const toast = document.querySelector("#toast");

const columns = {
  trades: [
    ["date", "日期"],
    ["symbol", "代码"],
    ["side", "方向"],
    ["price", "价格"],
    ["shares", "股数"],
    ["notional", "成交额"],
    ["fees", "费用"],
    ["reason", "原因"],
  ],
  plan: [
    ["as_of", "截至"],
    ["symbol", "代码"],
    ["name", "名称"],
    ["industry", "行业"],
    ["action", "动作"],
    ["current_shares", "当前"],
    ["target_shares", "目标"],
    ["delta_shares", "变化"],
    ["reference_close", "参考收盘"],
    ["estimated_notional", "估算金额"],
    ["notes", "备注"],
  ],
  watchlist: [
    ["symbol", "代码"],
    ["name", "名称"],
    ["price", "现价"],
    ["change_pct", "涨跌"],
    ["buy_price", "买入价"],
    ["stop_loss", "止损价"],
    ["added_at", "加入时间"],
    ["risk_score", "风险分"],
    ["risk_level", "风险等级"],
    ["suitable", "适合买入"],
  ],
  optimize: [
    ["rank", "排名"],
    ["params", "参数"],
    ["total_return", "总收益"],
    ["annual_return", "年化收益"],
    ["max_drawdown", "最大回撤"],
    ["sharpe_like", "Sharpe"],
  ],
};

// ── Event wiring ──

document.querySelector("#initSampleButton").addEventListener("click", () => runInitSample());
document.querySelector("#fetchDataButton").addEventListener("click", () => runFetch());
document.querySelector("#fetchInitButton").addEventListener("click", () => runFetchInit());
document.querySelector("#fetchHistoryButton").addEventListener("click", () => runFetchHistory());
document.querySelector("#positionsTemplateButton").addEventListener("click", () => runPositionsTemplate());
document.querySelector("#runBacktestButton").addEventListener("click", () => runBacktest());
document.querySelector("#runPlanButton").addEventListener("click", () => runPlan());

// Sync history days input <-> kline days input
const historyDaysInput = document.querySelector("#historyDaysInput");
const klineDaysInput = document.querySelector("#klineDaysInput");
if (historyDaysInput && klineDaysInput) {
  historyDaysInput.addEventListener("input", () => {
    klineDaysInput.value = historyDaysInput.value;
    state.klineDays = parseInt(historyDaysInput.value) || 60;
  });
  klineDaysInput.addEventListener("input", () => {
    historyDaysInput.value = klineDaysInput.value;
    state.klineDays = parseInt(klineDaysInput.value) || 60;
    if (state.klineSymbol) loadKline(state.klineSymbol);
  });
}
document.querySelector("#refreshButton").addEventListener("click", () => loadReport());
document.querySelector("#searchButton").addEventListener("click", () => {
  const symbol = document.querySelector("#symbolSearch").value.trim();
  if (symbol) loadKline(symbol);
});
document.querySelector("#symbolSearch").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const symbol = e.target.value.trim();
    if (symbol) loadKline(symbol);
  }
});
document.querySelector("#addWatchlistButton").addEventListener("click", () => {
  if (state.klineSymbol) addToWatchlist(state.klineSymbol);
});

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    state.activeTab = button.dataset.tab;
    renderTabs();
  });
});

document.querySelectorAll(".chart-toggle .tab").forEach((button) => {
  button.addEventListener("click", () => {
    state.chartMode = button.dataset.chart;
    document.querySelectorAll(".chart-toggle .tab").forEach((b) =>
      b.classList.toggle("active", b.dataset.chart === state.chartMode)
    );
    renderChartMode();
  });
});

document.querySelectorAll(".period-toggle .tab").forEach((button) => {
  button.addEventListener("click", () => {
    state.klinePeriod = button.dataset.period;
    document.querySelectorAll(".period-toggle .tab").forEach((b) =>
      b.classList.toggle("active", b.dataset.period === state.klinePeriod)
    );
    if (state.klineSymbol) loadKline(state.klineSymbol);
  });
});

const optimizeCheckbox = document.querySelector("#optimizeCheckbox");
const optimizeSection = document.querySelector("#optimizeSection");
if (optimizeCheckbox && optimizeSection) {
  optimizeCheckbox.addEventListener("change", () => {
    optimizeSection.style.display = optimizeCheckbox.checked ? "" : "none";
    document.querySelector("#runBacktestButton").textContent =
      optimizeCheckbox.checked ? "参数扫描" : "运行回测";
  });
}

window.addEventListener("resize", () => {
  if (state.chartMode === "equity") drawChart();
  if (klineChart) klineChart.resize();
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopPolling();
  else startPolling();
});

// ── Initial load ──

loadStatus();
loadReport();

// ── Form helpers ──

function formValues() {
  const values = Object.fromEntries(new FormData(form).entries());
  return {
    data_dir: values.data_dir,
    out_dir: values.out_dir,
    positions_file: values.positions_file,
    start: values.start,
    end: values.end,
    cash: Number(values.cash),
    top_n: Number(values.top_n),
    max_weight: Number(values.max_weight),
    cash_buffer: Number(values.cash_buffer),
    min_amount: Number(values.min_amount),
    strategy: values.strategy,
    rebalance_freq: values.rebalance_freq,
    stop_loss: Number(values.stop_loss),
    take_profit: Number(values.take_profit),
    trailing_stop: Number(values.trailing_stop),
    max_per_industry: Number(values.max_per_industry),
    blacklist: values.blacklist,
    as_of: values.plan_as_of || undefined,
    sample_start: values.sample_start,
    sample_end: values.sample_end,
  };
}

// ── API helpers ──

async function apiGet(path) {
  const response = await fetch(path);
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || "请求失败");
  }
  return payload;
}

async function apiPost(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok || !body.ok) {
    throw new Error(body.error || "请求失败");
  }
  return body;
}

// ── Status & Report ──

async function loadStatus() {
  const values = formValues();
  try {
    const query = new URLSearchParams({
      data_dir: values.data_dir,
      out_dir: values.out_dir,
    });
    const payload = await apiGet(`/api/status?${query}`);
    document.querySelector("#workspaceLabel").textContent = payload.workspace;
    setStatus("#dataStatus", payload.data_ready, payload.data_ready ? "数据已就绪" : "数据未完整");
    setStatus("#reportStatus", payload.report_ready, payload.report_ready ? "报告已生成" : "报告未生成");
    state.files = payload.files || state.files;
    renderFiles();
    loadWatchlist();
    startPolling();
  } catch (error) {
    showToast(error.message);
  }
}

async function loadReport() {
  const values = formValues();
  try {
    const query = new URLSearchParams({ out_dir: values.out_dir });
    const payload = await apiGet(`/api/report?${query}`);
    applyPayload(payload);
    showToast("报告已刷新");
  } catch (error) {
    renderEmpty();
  }
}

// ── Actions ──

async function runInitSample() {
  const values = formValues();
  await withBusy("正在生成样例数据...", async () => {
    await apiPost("/api/init-sample", {
      data_dir: values.data_dir,
      start: values.sample_start,
      end: values.sample_end,
    });
    await loadStatus();
    showToast("样例数据已生成");
  });
}

async function runFetch() {
  const values = formValues();
  await withBusy("正在拉取每日数据...", async () => {
    const payload = await apiPost("/api/fetch", {
      data_dir: values.data_dir,
    });
    state.files = payload.files || state.files;
    renderFiles();
    await loadStatus();
    showToast(`数据已拉取：${payload.trade_date}`);
  });
}

async function runFetchInit() {
  const values = formValues();
  const days = parseInt(historyDaysInput?.value) || 60;
  await withBusy(`正在初始化数据（${days}天历史）...`, async () => {
    const payload = await apiPost("/api/fetch", {
      data_dir: values.data_dir,
      init: true,
      history_days: days,
    });
    state.files = payload.files || state.files;
    renderFiles();
    await loadStatus();
    showToast(`数据初始化完成：${payload.trade_date || payload.message}`);
  });
}

async function runFetchHistory() {
  const values = formValues();
  const days = parseInt(historyDaysInput?.value) || 60;
  await withBusy(`正在拉取 ${days} 天历史数据...`, async () => {
    const payload = await apiPost("/api/fetch", {
      data_dir: values.data_dir,
      history: days,
    });
    state.files = payload.files || state.files;
    renderFiles();
    await loadStatus();
    showToast(`历史数据已拉取：${payload.rows || payload.message}`);
    // Reload K-line if a symbol is active
    if (state.klineSymbol) {
      state.klineDays = days;
      klineDaysInput.value = days;
      loadKline(state.klineSymbol);
    }
  });
}

async function runPositionsTemplate() {
  const values = formValues();
  await withBusy("正在生成持仓模板...", async () => {
    const payload = await apiPost("/api/positions-template", {
      path: (values.data_dir || "data/live") + "/positions_template.csv",
    });
    showToast(`持仓模板已生成：${payload.path}`);
  });
}

async function runBacktest() {
  const values = formValues();
  const isOptimize = optimizeCheckbox && optimizeCheckbox.checked;
  if (isOptimize) {
    await withBusy("正在参数扫描...", async () => {
      const payload = await apiPost("/api/optimize", values);
      state.optimizeResults = payload.results || [];
      state.optimizeMetric = payload.metric || null;
      state.activeTab = "optimize";
      renderAll();
      showToast(`参数扫描完成 — ${state.optimizeResults.length} 组结果`);
    });
    return;
  }
  await withBusy("正在运行回测...", async () => {
    const payload = await apiPost("/api/backtest", values);
    applyPayload(payload);
    await loadStatus();
    showToast("回测完成");
  });
}

async function runPlan() {
  const values = formValues();
  await withBusy("正在生成交易计划...", async () => {
    const payload = await apiPost("/api/plan", values);
    state.plan = payload.rows || [];
    state.files = payload.files || state.files;
    state.activeTab = "plan";
    renderAll();
    await loadStatus();
    showToast("交易计划已生成");
  });
}

async function withBusy(message, task) {
  if (state.busy) return;
  state.busy = true;
  setButtonsDisabled(true);
  showToast(message);
  try {
    await task();
  } catch (error) {
    showToast(error.message);
  } finally {
    state.busy = false;
    setButtonsDisabled(false);
  }
}

function applyPayload(payload) {
  state.summary = payload.summary || state.summary;
  state.equityCurve = payload.equity_curve || state.equityCurve;
  state.trades = payload.trades || state.trades;
  state.plan = payload.plan || state.plan;
  state.files = payload.files || state.files;
  renderAll();
}

// ── Rendering ──

function renderAll() {
  renderKpis();
  renderChartMode();
  renderFiles();
  renderWatchlist();
  renderTabs();
}

function renderEmpty() {
  renderKpis();
  renderChartMode();
  renderFiles();
  renderWatchlist();
  renderTabs();
}

function renderChartMode() {
  const klineDiv = document.querySelector("#klineChart");
  const canvas = document.querySelector("#equityChart");
  const klineControls = document.querySelector("#klineControls");
  const refreshBtn = document.querySelector("#refreshButton");
  const chartTitle = document.querySelector("#chartTitle");
  const chartSub = document.querySelector("#klineSymbolLabel");

  if (state.chartMode === "kline") {
    klineDiv.style.display = "";
    canvas.style.display = "none";
    klineControls.style.display = "";
    refreshBtn.style.display = "none";
    chartTitle.textContent = "K线图";
  } else {
    klineDiv.style.display = "none";
    canvas.style.display = "";
    klineControls.style.display = "none";
    refreshBtn.style.display = "";
    chartTitle.textContent = "净值曲线";
    chartSub.textContent = "策略权益与基准走势";
    drawChart();
  }
}

function renderKpis() {
  const grid = document.querySelector("#kpiGrid");
  const summary = state.summary;
  const items = summary
    ? [
        ["总收益", formatPct(summary.total_return), `区间 ${summary.start} 至 ${summary.end}`],
        ["年化收益", formatPct(summary.annual_return), `波动 ${formatPct(summary.annual_volatility)}`],
        ["最大回撤", formatPct(summary.max_drawdown), `Sharpe-like ${Number(summary.sharpe_like).toFixed(2)}`],
        ["交易次数", formatNumber(summary.trade_count, 0), `换手 ${Number(summary.turnover).toFixed(2)}x`],
      ]
    : [
        ["总收益", "--", "运行回测后显示"],
        ["年化收益", "--", "等待报告"],
        ["最大回撤", "--", "等待报告"],
        ["交易次数", "--", "等待报告"],
      ];

  grid.innerHTML = items
    .map(
      ([label, value, hint]) => `
        <article class="kpi">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
          <small>${escapeHtml(hint)}</small>
        </article>
      `
    )
    .join("");
}

// ── Canvas equity curve (preserved) ──

function drawChart() {
  const canvas = document.querySelector("#equityChart");
  const context = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(640, Math.floor(rect.width * ratio));
  canvas.height = Math.floor(320 * ratio);
  context.scale(ratio, ratio);

  const width = rect.width;
  const height = 320;
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#fbfcfc";
  context.fillRect(0, 0, width, height);

  if (!state.equityCurve.length) {
    context.fillStyle = "#66736f";
    context.font = "14px Segoe UI, Microsoft YaHei, Arial";
    context.textAlign = "center";
    context.fillText("运行回测后显示净值曲线", width / 2, height / 2);
    return;
  }

  const padding = { top: 24, right: 18, bottom: 34, left: 54 };
  const rows = state.equityCurve;
  const equity = rows.map((row) => Number(row.equity));
  const benchmark = normalizeBenchmark(rows);
  const allValues = equity.concat(benchmark).filter((value) => Number.isFinite(value));
  const min = Math.min(...allValues) * 0.995;
  const max = Math.max(...allValues) * 1.005;

  drawGrid(context, width, height, padding, min, max);
  drawLine(context, equity, "#176b5b", width, height, padding, min, max);
  if (benchmark.length) {
    drawLine(context, benchmark, "#c14d32", width, height, padding, min, max);
  }
  drawLegend(context, width);
}

function normalizeBenchmark(rows) {
  const values = rows.map((row) => Number(row.benchmark)).filter((value) => Number.isFinite(value) && value > 0);
  if (!values.length || !state.summary) return [];
  const firstBenchmark = values[0];
  const firstEquity = Number(rows[0].equity);
  return rows.map((row) => {
    const value = Number(row.benchmark);
    return Number.isFinite(value) && value > 0 ? (value / firstBenchmark) * firstEquity : NaN;
  });
}

function drawGrid(context, width, height, padding, min, max) {
  context.strokeStyle = "#e7ecea";
  context.lineWidth = 1;
  context.fillStyle = "#66736f";
  context.font = "12px Segoe UI, Microsoft YaHei, Arial";
  context.textAlign = "right";
  for (let index = 0; index <= 4; index += 1) {
    const y = padding.top + ((height - padding.top - padding.bottom) * index) / 4;
    context.beginPath();
    context.moveTo(padding.left, y);
    context.lineTo(width - padding.right, y);
    context.stroke();
    const value = max - ((max - min) * index) / 4;
    context.fillText(formatCompact(value), padding.left - 8, y + 4);
  }
}

function drawLine(context, values, color, width, height, padding, min, max) {
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  context.strokeStyle = color;
  context.lineWidth = 2;
  context.beginPath();
  values.forEach((value, index) => {
    if (!Number.isFinite(value)) return;
    const x = padding.left + (innerWidth * index) / Math.max(1, values.length - 1);
    const y = padding.top + innerHeight - ((value - min) / Math.max(1, max - min)) * innerHeight;
    if (index === 0) {
      context.moveTo(x, y);
    } else {
      context.lineTo(x, y);
    }
  });
  context.stroke();
}

function drawLegend(context, width) {
  const items = [
    ["策略权益", "#176b5b"],
    ["基准归一", "#c14d32"],
  ];
  context.font = "12px Segoe UI, Microsoft YaHei, Arial";
  context.textAlign = "left";
  items.forEach(([label, color], index) => {
    const x = width - 150 + index * 74;
    context.fillStyle = color;
    context.fillRect(x, 18, 10, 10);
    context.fillStyle = "#4d5d58";
    context.fillText(label, x + 15, 27);
  });
}

// ── Polling ──

function isMarketOpen() {
  const now = new Date();
  const day = now.getDay();
  if (day === 0 || day === 6) return false;
  const t = now.getHours() * 100 + now.getMinutes();
  return (t >= 930 && t <= 1130) || (t >= 1300 && t <= 1500);
}

function startPolling() {
  state.marketOpen = isMarketOpen();
  renderMarketTime();
  pollData();
  if (state.pollingTimer) clearInterval(state.pollingTimer);
  const interval = state.marketOpen ? 8000 : 30000;
  state.pollingTimer = setInterval(() => {
    state.marketOpen = isMarketOpen();
    renderMarketTime();
    pollData();
  }, interval);
}

function stopPolling() {
  if (state.pollingTimer) {
    clearInterval(state.pollingTimer);
    state.pollingTimer = null;
  }
}

async function pollData() {
  if (state.busy) return;
  try {
    const idxPayload = await apiGet("/api/market-index");
    state.indices = idxPayload.indices || [];
    renderIndices();
  } catch (e) { /* silent */ }

  const symbols = new Set(state.watchlist.map((w) => w.symbol));
  if (state.klineSymbol) symbols.add(state.klineSymbol);
  if (symbols.size > 0) {
    try {
      const qPayload = await apiGet(`/api/quotes?symbols=${[...symbols].join(",")}`);
      for (const q of qPayload.quotes || []) {
        state.quotes[q.symbol] = q;
      }
      renderWatchlist();
      renderWatchlistTab();
      updateKlineOverlay();
    } catch (e) { /* silent */ }
  }
}

// ── Market index render ──

function renderIndices() {
  document.querySelectorAll(".index-item").forEach((el) => {
    const code = el.dataset.code;
    const idx = state.indices.find((i) => i.code === code);
    if (!idx) return;
    el.querySelector(".idx-price").textContent = idx.close.toFixed(2);
    const changeEl = el.querySelector(".idx-change");
    const pct = idx.change_pct;
    changeEl.textContent = (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%";
    changeEl.className = "idx-change " + (pct >= 0 ? "up" : "down");
    el.className = "index-item " + (pct >= 0 ? "up" : "down");
  });
}

function renderMarketTime() {
  const el = document.querySelector("#marketTime");
  if (state.marketOpen) {
    el.textContent = "交易中";
    el.className = "market-time open";
  } else {
    el.textContent = "已收盘";
    el.className = "market-time closed";
  }
}

// ── ECharts K-line ──

function initKlineChart() {
  const dom = document.querySelector("#klineChart");
  if (klineChart) klineChart.dispose();
  klineChart = echarts.init(dom);
}

const periodLabels = { "1d": "日K", "1w": "周K", "1M": "月K" };

async function loadKline(symbol) {
  const values = formValues();
  // Read latest days from input (synced with historyDaysInput)
  if (klineDaysInput) state.klineDays = parseInt(klineDaysInput.value) || 60;
  try {
    const payload = await apiGet(
      `/api/kline?symbol=${symbol}&days=${state.klineDays}&period=${state.klinePeriod}&data_dir=${encodeURIComponent(values.data_dir)}`
    );
    state.klineData = payload;
    state.klineSymbol = symbol;
    const label = periodLabels[state.klinePeriod] || "日K";
    document.querySelector("#klineSymbolLabel").textContent =
      (payload.name ? payload.name + " — " : "") + `${payload.kline.length}根${label}线`;
    renderKlineChart();
    fetchRiskAssessment(symbol);
  } catch (e) {
    showToast("未找到该股票数据: " + e.message);
  }
}

function renderKlineChart() {
  if (!klineChart) initKlineChart();
  const data = state.klineData;
  if (!data || !data.kline || !data.kline.length) return;

  const dates = data.kline.map((d) => d.date);
  const ohlc = data.kline.map((d) => [d.open, d.close, d.low, d.high]);
  const volumes = data.kline.map((d) => d.volume);

  const option = {
    animation: false,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: "60", right: "20", top: "20", height: "60%" },
      { left: "60", right: "20", top: "80%", height: "16%" },
    ],
    xAxis: [
      { type: "category", data: dates, gridIndex: 0, axisLabel: { show: false }, boundaryGap: true },
      { type: "category", data: dates, gridIndex: 1, axisLabel: { show: false }, boundaryGap: true },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitArea: { show: true } },
      {
        scale: true, gridIndex: 1, splitNumber: 2,
        axisLabel: { formatter: (v) => v > 1e8 ? (v / 1e8).toFixed(1) + "亿" : (v / 1e4).toFixed(0) + "万" },
      },
    ],
    series: [
      {
        name: "K线", type: "candlestick", data: ohlc,
        xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: { color: "#c14d32", color0: "#176b5b", borderColor: "#c14d32", borderColor0: "#176b5b" },
      },
      { name: "MA5", type: "line", data: data.ma5, xAxisIndex: 0, yAxisIndex: 0,
        smooth: true, lineStyle: { width: 1, color: "#e6b422" }, symbol: "none" },
      { name: "MA10", type: "line", data: data.ma10, xAxisIndex: 0, yAxisIndex: 0,
        smooth: true, lineStyle: { width: 1, color: "#4a90d9" }, symbol: "none" },
      { name: "MA20", type: "line", data: data.ma20, xAxisIndex: 0, yAxisIndex: 0,
        smooth: true, lineStyle: { width: 1, color: "#9b59b6" }, symbol: "none" },
      { name: "MA60", type: "line", data: data.ma60, xAxisIndex: 0, yAxisIndex: 0,
        smooth: true, lineStyle: { width: 1, color: "#e74c3c" }, symbol: "none" },
      {
        name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1,
        data: volumes.map((v, i) => ({
          value: v,
          itemStyle: { color: ohlc[i][1] >= ohlc[i][0] ? "#c14d32" : "#176b5b" },
        })),
      },
    ],
  };
  klineChart.setOption(option, true);
}

function updateKlineOverlay() {
  const symbol = state.klineSymbol;
  if (!symbol) return;
  const q = state.quotes[symbol];
  const risk = state.riskScores[symbol];
  if (!q && !risk) return;
  const pLabel = periodLabels[state.klinePeriod] || "日K";
  let html = (q ? q.name || symbol : symbol) + " — " + pLabel + "线";
  if (q) html += ` | 现价: ${q.close.toFixed(2)} (${q.change_pct >= 0 ? "+" : ""}${q.change_pct.toFixed(2)}%)`;
  if (risk) {
    html += ` | 风险: ${risk.risk_level} (${(risk.risk_score * 100).toFixed(0)})`;
    html += risk.suitable_to_buy
      ? ' <span class="buy-indicator suitable">适合买入</span>'
      : ' <span class="buy-indicator not-suitable">暂不建议</span>';
  }
  document.querySelector("#klineSymbolLabel").innerHTML = html;
}

// ── Risk assessment ──

async function fetchRiskAssessment(symbol) {
  const values = formValues();
  try {
    const p = await apiPost("/api/risk-assessment", {
      symbol: symbol,
      data_dir: values.data_dir,
      lookback: state.klineDays,
    });
    state.riskScores[symbol] = {
      risk_score: p.risk_score,
      risk_level: p.risk_level,
      factors: p.factors,
      suitable_to_buy: p.suitable_to_buy,
      reasons: p.reasons,
    };
    updateKlineOverlay();
    renderWatchlist();
    renderWatchlistTab();
  } catch (e) { /* data may be too sparse */ }
}

// ── Watchlist ──

async function loadWatchlist() {
  const values = formValues();
  try {
    const p = await apiGet(`/api/watchlist?data_dir=${encodeURIComponent(values.data_dir)}`);
    state.watchlist = p.items || [];
    renderWatchlist();
    renderWatchlistTab();
  } catch (e) { /* ignore */ }
}

async function addToWatchlist(symbol) {
  const values = formValues();
  try {
    await apiPost("/api/watchlist", { symbol, data_dir: values.data_dir });
    await loadWatchlist();
    showToast(`已添加 ${symbol} 到自选股`);
  } catch (e) { showToast(e.message); }
}

async function removeFromWatchlist(symbol) {
  const values = formValues();
  try {
    await apiPost("/api/watchlist", {
      _method: "DELETE", symbol, data_dir: values.data_dir,
    });
    state.watchlist = state.watchlist.filter((w) => w.symbol !== symbol);
    renderWatchlist();
    renderWatchlistTab();
  } catch (e) { showToast(e.message); }
}

async function updateWatchlistItem(symbol, field, value) {
  const values = formValues();
  try {
    await apiPost("/api/watchlist", {
      symbol, data_dir: values.data_dir, [field]: value || null,
    });
    await loadWatchlist();
  } catch (e) { /* ignore */ }
}

function renderWatchlist() {
  const container = document.querySelector("#watchlistContainer");
  const countEl = document.querySelector("#watchlistCount");

  if (!state.watchlist.length) {
    container.innerHTML = '<div class="empty-state">暂无自选股<br/>在K线图输入代码后点击 + 添加</div>';
    countEl.textContent = "0 只";
    return;
  }
  countEl.textContent = `${state.watchlist.length} 只`;

  container.innerHTML = state.watchlist.map((w) => {
    const q = state.quotes[w.symbol];
    const price = q ? q.close.toFixed(2) : "--";
    const change = q ? q.change_pct : null;
    const cls = change !== null ? (change >= 0 ? "up" : "down") : "";
    const changeStr = change !== null ? (change >= 0 ? "+" : "") + change.toFixed(2) + "%" : "--";
    const risk = state.riskScores[w.symbol];
    const buyPrice = w.buy_price || "";
    const stopLoss = w.stop_loss_price || "";

    return `
      <div class="watchlist-item" data-symbol="${w.symbol}">
        <div class="wl-header">
          <strong class="wl-clickable" title="查看K线">${escapeHtml(w.symbol)}</strong>
          <small>${escapeHtml(q ? q.name : (w.name || ""))}</small>
        </div>
        <div class="wl-price">
          <span class="price-value">${price}</span>
          <span class="price-change ${cls}">${changeStr}</span>
        </div>
        ${risk ? `
          <div class="wl-risk risk-${risk.risk_level}">
            风险${(risk.risk_score * 100).toFixed(0)} · ${risk.suitable_to_buy ? "建议买入" : "暂不建议"}
          </div>
        ` : ""}
        <div class="wl-actions">
          <input class="wl-input" type="number" step="0.01" placeholder="买入价"
                 value="${buyPrice}"
                 onchange="updateWatchlistItem('${w.symbol}','buy_price',this.value)" />
          <input class="wl-input" type="number" step="0.01" placeholder="止损价"
                 value="${stopLoss}"
                 onchange="updateWatchlistItem('${w.symbol}','stop_loss_price',this.value)" />
          <button class="wl-remove" title="移除" onclick="event.stopPropagation();removeFromWatchlist('${w.symbol}')">&times;</button>
        </div>
      </div>
    `;
  }).join("");

  // Wire click-to-load-kline
  container.querySelectorAll(".wl-clickable").forEach((el) => {
    el.addEventListener("click", () => {
      const sym = el.closest(".watchlist-item").dataset.symbol;
      document.querySelector("#symbolSearch").value = sym;
      loadKline(sym);
    });
  });
}

function renderWatchlistTab() {
  if (state.activeTab !== "watchlist") return;
  const content = document.querySelector("#tabContent");
  if (!state.watchlist.length) {
    content.innerHTML = '<div class="empty-state">暂无自选股</div>';
    return;
  }

  const rows = state.watchlist.map((w) => {
    const q = state.quotes[w.symbol] || {};
    const risk = state.riskScores[w.symbol];
    return {
      symbol: w.symbol,
      name: q.name || w.name || "",
      price: q.close != null ? q.close.toFixed(2) : "--",
      change_pct: q.change_pct != null ? (q.change_pct >= 0 ? "+" : "") + q.change_pct.toFixed(2) + "%" : "--",
      buy_price: w.buy_price != null ? w.buy_price : "--",
      stop_loss: w.stop_loss_price != null ? w.stop_loss_price : "--",
      added_at: w.added_at || "--",
      risk_score: risk ? (risk.risk_score * 100).toFixed(0) : "--",
      risk_level: risk ? risk.risk_level : "--",
      suitable: risk ? (risk.suitable_to_buy ? "是" : "否") : "--",
    };
  });

  content.innerHTML = renderTable(rows, columns.watchlist);
}

// ── Tabs ──

function renderOptimizeTab() {
  const content = document.querySelector("#tabContent");
  if (!state.optimizeResults || !state.optimizeResults.length) {
    content.innerHTML = '<div class="empty-state">勾选"参数扫描"后运行回测查看优化结果</div>';
    return;
  }
  const metricLabel = state.optimizeMetric || "sharpe_like";
  let html = `<div class="optimize-header">排名指标: ${metricLabel} &mdash; 共 ${state.optimizeResults.length} 组结果</div>`;
  html += '<table class="data-table"><thead><tr>';
  html += '<th>排名</th><th>参数</th><th>总收益</th><th>年化收益</th><th>最大回撤</th><th>Sharpe</th>';
  html += '</tr></thead><tbody>';
  for (const r of state.optimizeResults) {
    const m = r.metrics || {};
    const paramsStr = Object.entries(r.params || {}).map(([k, v]) => `${k}=${v}`).join(", ");
    html += '<tr>';
    html += `<td><strong>#${r.rank}</strong></td>`;
    html += `<td class="opt-params">${paramsStr}</td>`;
    html += `<td class="${(m.total_return || 0) >= 0 ? 'positive' : 'negative'}">${fmtPct(m.total_return)}</td>`;
    html += `<td class="${(m.annual_return || 0) >= 0 ? 'positive' : 'negative'}">${fmtPct(m.annual_return)}</td>`;
    html += `<td class="negative">${fmtPct(m.max_drawdown)}</td>`;
    html += `<td>${(m.sharpe_like || 0).toFixed(2)}</td>`;
    html += '</tr>';
  }
  html += '</tbody></table>';
  content.innerHTML = html;
}

function renderTabs() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === state.activeTab);
  });
  const content = document.querySelector("#tabContent");
  if (state.activeTab === "summary") {
    content.innerHTML = renderSummary();
    return;
  }
  if (state.activeTab === "watchlist") {
    renderWatchlistTab();
    return;
  }
  if (state.activeTab === "optimize") {
    renderOptimizeTab();
    return;
  }
  const rows = state.activeTab === "trades" ? state.trades : state.plan;
  content.innerHTML = renderTable(rows, columns[state.activeTab]);
}

function renderSummary() {
  if (!state.summary) {
    return '<div class="empty-state">运行回测后显示指标明细</div>';
  }
  const rows = Object.entries(state.summary).map(([key, value]) => ({ key, value }));
  return renderTable(rows, [
    ["key", "字段"],
    ["value", "值"],
  ]);
}

function renderTable(rows, tableColumns) {
  if (!rows || !rows.length) {
    return '<div class="empty-state">暂无数据</div>';
  }
  const head = tableColumns.map(([, label]) => `<th>${escapeHtml(label)}</th>`).join("");
  const body = rows
    .map((row) => {
      const cells = tableColumns
        .map(([key]) => {
          const value = row[key] != null ? row[key] : "";
          let cls = "";
          if (key === "side" || key === "action") {
            cls = value === "buy" ? "side-buy" : value === "sell" ? "side-sell" : "";
          }
          if (key === "suitable") {
            cls = value === "是" ? "side-buy" : "side-sell";
          }
          if (key === "change_pct" && typeof value === "string") {
            cls = value.startsWith("+") ? "side-buy" : value.startsWith("-") ? "side-sell" : "";
          }
          return `<td class="${cls}">${escapeHtml(String(value))}</td>`;
        })
        .join("");
      return `<tr>${cells}</tr>`;
    })
    .join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

// ── File list ──

function renderFiles() {
  const container = document.querySelector("#fileList");
  const entries = Object.entries(state.files || {}).filter(([, value]) => value);
  if (!entries.length) {
    container.innerHTML = '<div class="empty-state">暂无输出文件</div>';
    return;
  }
  container.innerHTML = entries
    .map(([key, value]) => {
      return `<div class="file-item"><strong>${escapeHtml(fileLabel(key))}</strong><span>${escapeHtml(value)}</span></div>`;
    })
    .join("");
}

// ── Utilities ──

function setStatus(selector, ready, text) {
  const element = document.querySelector(selector);
  element.textContent = text;
  element.classList.toggle("ready", ready);
  element.classList.toggle("pending", !ready);
}

function fmtPct(value) {
  if (value == null) return "--";
  return (value * 100).toFixed(2) + "%";
}

function setButtonsDisabled(disabled) {
  document.querySelectorAll("button").forEach((button) => {
    button.disabled = disabled;
  });
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 2800);
}

function formatPct(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return `${(number * 100).toFixed(2)}%`;
}

function formatNumber(value, digits = 2) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return number.toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function formatCompact(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  if (Math.abs(number) >= 10000) return `${(number / 10000).toFixed(1)}万`;
  return number.toFixed(0);
}

function fileLabel(key) {
  return {
    report: "Markdown 报告",
    summary: "指标 JSON",
    equity_curve: "净值 CSV",
    trades: "交易流水 CSV",
    latest_plan: "最新交易计划",
  }[key] || key;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
