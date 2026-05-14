const state = {
  summary: null,
  equityCurve: [],
  trades: [],
  plan: [],
  files: {},
  activeTab: "trades",
  busy: false,
};

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
};

document.querySelector("#initSampleButton").addEventListener("click", () => runInitSample());
document.querySelector("#fetchDataButton").addEventListener("click", () => runFetch());
document.querySelector("#runBacktestButton").addEventListener("click", () => runBacktest());
document.querySelector("#runPlanButton").addEventListener("click", () => runPlan());
document.querySelector("#refreshButton").addEventListener("click", () => loadReport());

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    state.activeTab = button.dataset.tab;
    renderTabs();
  });
});

window.addEventListener("resize", () => drawChart());

loadStatus();
loadReport();

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
    sample_start: values.sample_start,
    sample_end: values.sample_end,
  };
}

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

async function runBacktest() {
  const values = formValues();
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

function renderAll() {
  renderKpis();
  drawChart();
  renderFiles();
  renderTabs();
}

function renderEmpty() {
  renderKpis();
  drawChart();
  renderFiles();
  renderTabs();
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

function renderTabs() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === state.activeTab);
  });
  const content = document.querySelector("#tabContent");
  if (state.activeTab === "summary") {
    content.innerHTML = renderSummary();
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
          const value = row[key] ?? "";
          const className =
            key === "side" || key === "action"
              ? value === "buy"
                ? "side-buy"
                : value === "sell"
                ? "side-sell"
                : ""
              : "";
          return `<td class="${className}">${escapeHtml(String(value))}</td>`;
        })
        .join("");
      return `<tr>${cells}</tr>`;
    })
    .join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function setStatus(selector, ready, text) {
  const element = document.querySelector(selector);
  element.textContent = text;
  element.classList.toggle("ready", ready);
  element.classList.toggle("pending", !ready);
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
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

