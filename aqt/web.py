from __future__ import annotations

import csv
import json
import math
import mimetypes
import threading
import time as _time_module
import webbrowser
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .backtest import BacktestConfig, Backtester, save_backtest_result
from .data import DataStore, generate_sample_data, parse_date
from .fetcher import _tx_fetch_quotes, _tx_key, fetch_daily
from .planner import PlanConfig, generate_trade_plan, load_positions, save_positions_template
from .strategies import STRATEGY_REGISTRY
from .strategy import MultiFactorConfig, MultiFactorStrategy


WORKSPACE_ROOT = Path.cwd().resolve()
STATIC_ROOT = Path(__file__).with_name("static")

# DataStore cache to avoid reloading CSV on every kline/risk request
_store_cache: dict[str, tuple[float, DataStore]] = {}
_STORE_CACHE_TTL = 60.0


def _get_store(data_dir: str | Path) -> DataStore:
    key = str(_safe_path(data_dir))
    now = _time_module.time()
    if key in _store_cache:
        ts, store = _store_cache[key]
        if now - ts < _STORE_CACHE_TTL:
            return store
    store = DataStore.load(data_dir)
    _store_cache[key] = (now, store)
    return store


# ── watchlist persistence ──

def _watchlist_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "watchlist.json"


def _load_watchlist(data_dir: str | Path) -> list[dict]:
    path = _watchlist_path(data_dir)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _save_watchlist(data_dir: str | Path, items: list[dict]) -> None:
    path = _watchlist_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def run_server(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    server = ThreadingHTTPServer((host, port), AQTRequestHandler)
    url = f"http://{host}:{server.server_port}"
    print(f"AQT UI running at {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AQT UI.")
    finally:
        server.server_close()


def init_sample_job(payload: dict) -> dict:
    data_dir = _safe_path(payload.get("data_dir") or "data/sample")
    start = parse_date(payload.get("start") or "2022-01-04")
    end = parse_date(payload.get("end") or "2024-12-31")
    generate_sample_data(data_dir, start, end)
    return {
        "ok": True,
        "message": f"Sample data written to {data_dir}",
        "data_dir": str(data_dir),
    }


def run_backtest_job(payload: dict) -> dict:
    data_dir = _safe_path(payload.get("data_dir") or "data/sample")
    out_dir = _safe_path(payload.get("out_dir") or "reports/demo")
    strategy = _strategy_from_payload(payload)
    store = DataStore.load(data_dir)
    config = BacktestConfig(
        start=parse_date(payload.get("start") or "2023-07-03"),
        end=parse_date(payload.get("end") or "2024-12-31"),
        initial_cash=_float(payload.get("cash"), 1_000_000.0),
        max_weight=_float(payload.get("max_weight"), 0.15),
        cash_buffer=_float(payload.get("cash_buffer"), 0.02),
        rebalance_freq=payload.get("rebalance_freq") or "monthly",
        stop_loss_pct=_float(payload.get("stop_loss"), 0.0),
        take_profit_pct=_float(payload.get("take_profit"), 0.0),
        trailing_stop_pct=_float(payload.get("trailing_stop"), 0.0),
    )
    result = Backtester(store, strategy, config).run()
    save_backtest_result(result, out_dir)
    return {
        "ok": True,
        "summary": result.summary,
        "equity_curve": result.equity_curve,
        "trades": _trades_to_rows(result.trades),
        "files": report_files(out_dir),
    }


def run_plan_job(payload: dict) -> dict:
    data_dir = _safe_path(payload.get("data_dir") or "data/sample")
    out_dir = _safe_path(payload.get("out_dir") or "reports/demo")
    strategy = _strategy_from_payload(payload)
    store = DataStore.load(data_dir)
    positions_file = payload.get("positions_file") or None
    positions = load_positions(_safe_path(positions_file) if positions_file else None)
    as_of = parse_date(payload["as_of"]) if payload.get("as_of") else None
    path = generate_trade_plan(
        store=store,
        strategy=strategy,
        config=PlanConfig(
            cash=_float(payload.get("cash"), 1_000_000.0),
            max_weight=_float(payload.get("max_weight"), 0.15),
            cash_buffer=_float(payload.get("cash_buffer"), 0.02),
        ),
        out_dir=out_dir,
        as_of=as_of,
        positions=positions,
    )
    return {
        "ok": True,
        "path": str(path),
        "rows": read_csv_rows(path),
        "files": report_files(out_dir),
    }


def run_optimize_job(payload: dict) -> dict:
    from .optimizer import GridSearch

    data_dir = _safe_path(payload.get("data_dir") or "data/live")
    store = _get_store(data_dir)
    strategy_name = payload.get("strategy") or "multi_factor"
    base_config = BacktestConfig(
        start=parse_date(payload.get("start") or "2023-07-03"),
        end=parse_date(payload.get("end") or "2024-12-31"),
        initial_cash=_float(payload.get("cash"), 1_000_000.0),
        max_weight=_float(payload.get("max_weight"), 0.15),
        cash_buffer=_float(payload.get("cash_buffer"), 0.02),
        rebalance_freq=payload.get("rebalance_freq") or "monthly",
        stop_loss_pct=_float(payload.get("stop_loss"), 0.0),
        take_profit_pct=_float(payload.get("take_profit"), 0.0),
    )
    metric = payload.get("metric") or "sharpe_like"

    param_grid: dict[str, list] = {}
    opt_top_n = payload.get("opt_top_n") or ""
    if opt_top_n:
        param_grid["top_n"] = [int(x.strip()) for x in str(opt_top_n).split(",") if x.strip()]
    opt_lookback = payload.get("opt_lookback") or ""
    if opt_lookback:
        param_grid["lookback"] = [int(x.strip()) for x in str(opt_lookback).split(",") if x.strip()]
    if not param_grid:
        param_grid = {"top_n": [5, 8, 10, 15], "lookback": [30, 60, 90, 120]}

    top_n = int(payload.get("opt_results") or 10)
    gs = GridSearch(store, strategy_name, param_grid, base_config, metric=metric)
    results = gs.run(top_n=top_n)
    rows = []
    for r in results:
        rows.append({
            "rank": r.rank,
            "params": r.params,
            "metrics": {k: v for k, v in r.metrics.items() if isinstance(v, (int, float))},
        })
    return {"ok": True, "metric": metric, "results": rows}


def run_fetch_job(payload: dict) -> dict:
    data_dir = _safe_path(payload.get("data_dir") or "data/live")
    if payload.get("init"):
        from .fetcher import fetch_init
        days = int(payload.get("history_days") or 60)
        trade_date = fetch_init(data_dir, history_days=days)
        return {"ok": True, "message": f"Initialized with {days}d history", "trade_date": trade_date, "files": report_files(data_dir)}
    if payload.get("history"):
        from .fetcher import fetch_history
        days = int(payload.get("history") or 60)
        count = fetch_history(data_dir, days=days)
        return {"ok": True, "message": f"History: {count} rows for {days} days", "rows": count, "files": report_files(data_dir)}
    trade_date = fetch_daily(data_dir)
    return {
        "ok": True,
        "message": f"Data fetched for {trade_date}",
        "trade_date": trade_date,
        "files": report_files(data_dir),
    }


def positions_template_job(payload: dict) -> dict:
    path = Path(payload.get("path") or "data/positions_template.csv")
    save_positions_template(path)
    return {"ok": True, "path": str(path), "message": f"模板已生成: {path}"}


# ══════════════════════════════════════════════════════════════════════════════
# New real-time endpoints
# ══════════════════════════════════════════════════════════════════════════════

def get_market_index_payload() -> dict:
    index_keys = {
        "sh000001": "上证指数",
        "sz399001": "深证成指",
        "sh000300": "沪深300",
        "sz399006": "创业板指",
    }
    try:
        quotes = _tx_fetch_quotes(list(index_keys))
    except Exception as exc:
        return {"ok": False, "error": f"Failed to fetch indices: {exc}"}

    indices = []
    for q in quotes:
        pre = q["pre_close"]
        chg = round((q["close"] - pre) / pre * 100, 2) if pre > 0 else 0.0
        key = f"sh{q['symbol']}" if q["symbol"].startswith(("6", "0")) else f"sz{q['symbol']}"
        indices.append({
            "code": q["symbol"],
            "name": index_keys.get(key, q["name"]),
            "close": q["close"],
            "change_pct": chg,
        })
    return {"ok": True, "indices": indices}


def get_quotes_payload(symbols_str: str) -> dict:
    symbols = [s.strip() for s in symbols_str.split(",") if s.strip()]
    if not symbols:
        return {"ok": False, "error": "No symbols provided"}
    if len(symbols) > 50:
        symbols = symbols[:50]
    try:
        raw = _tx_fetch_quotes([_tx_key(s) for s in symbols])
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    quotes = []
    for q in raw:
        pre = q["pre_close"]
        chg = round((q["close"] - pre) / pre * 100, 2) if pre > 0 else 0.0
        quotes.append({
            "symbol": q["symbol"], "name": q["name"],
            "close": q["close"], "pre_close": pre,
            "open": q["open"], "high": q["high"], "low": q["low"],
            "volume": q["volume"], "amount": q["amount"],
            "change_pct": chg, "pe": q["pe"], "pb": q["pb"],
        })
    return {"ok": True, "quotes": quotes}


def _aggregate_bars(bars: list, period: str) -> list[dict]:
    """Aggregate daily Bar objects into weekly or monthly OHLCV dicts."""
    if period not in ("1w", "1M"):
        return [
            {"date": b.date.isoformat(), "open": b.open, "close": b.close,
             "low": b.low, "high": b.high, "volume": b.volume}
            for b in bars
        ]

    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for b in bars:
        if period == "1w":
            key = b.date.strftime("%G-W%V")
        else:
            key = b.date.strftime("%Y-%m")
        groups[key].append(b)

    result = []
    for key in sorted(groups):
        group = groups[key]
        result.append({
            "date": group[-1].date.isoformat(),
            "open": group[0].open,
            "close": group[-1].close,
            "high": max(b.high for b in group),
            "low": min(b.low for b in group),
            "volume": sum(b.volume for b in group),
        })
    return result


def get_kline_payload(query: dict[str, list[str]]) -> dict:
    from .math_utils import sma as _sma

    symbol = (_first(query, "symbol") or "").zfill(6)
    if len(symbol) != 6 or not symbol.isdigit():
        return {"ok": False, "error": "Invalid symbol"}
    data_dir = _safe_path(_first(query, "data_dir") or "data/live")
    days = min(int(_first(query, "days") or "120"), 500)
    period = _first(query, "period") or "1d"

    store = _get_store(data_dir)
    bars = store.bars_until(symbol, store.latest_date(), days)
    if not bars:
        return {"ok": False, "error": f"No data for {symbol}"}

    sec = store.universe.get(symbol)
    name = sec.name if sec else ""

    kline = _aggregate_bars(bars, period)
    closes = [b["close"] for b in kline]

    def _ma(p: int) -> list[float | None]:
        vals = _sma(closes, p)
        return [None] * (len(closes) - len(vals)) + vals

    return {
        "ok": True, "symbol": symbol, "name": name, "kline": kline,
        "period": period,
        "ma5": _ma(5), "ma10": _ma(10), "ma20": _ma(20), "ma60": _ma(60),
    }


def get_watchlist_payload(query: dict[str, list[str]]) -> dict:
    data_dir = _safe_path(_first(query, "data_dir") or "data/live")
    return {"ok": True, "items": _load_watchlist(data_dir)}


def handle_watchlist_post(payload: dict) -> dict:
    data_dir = _safe_path(payload.get("data_dir") or "data/live")
    items = _load_watchlist(data_dir)

    if payload.get("_method") == "DELETE":
        symbol = (payload.get("symbol") or "").zfill(6)
        items = [w for w in items if w["symbol"] != symbol]
        _save_watchlist(data_dir, items)
        return {"ok": True, "items": items}

    symbol = (payload.get("symbol") or "").zfill(6)
    if len(symbol) != 6 or not symbol.isdigit():
        return {"ok": False, "error": "Invalid symbol"}

    existing = next((w for w in items if w["symbol"] == symbol), None)
    if existing:
        for field in ("buy_price", "stop_loss_price"):
            if field in payload:
                val = payload[field]
                existing[field] = float(val) if val not in (None, "", 0) else None
    else:
        if len(items) >= 30:
            return {"ok": False, "error": "Watchlist full (max 30)"}
        name = ""
        try:
            qs = _tx_fetch_quotes([_tx_key(symbol)])
            if qs:
                name = qs[0]["name"]
        except Exception:
            pass
        items.append({
            "symbol": symbol,
            "name": name,
            "added_at": date.today().isoformat(),
            "buy_price": float(payload["buy_price"]) if payload.get("buy_price") else None,
            "stop_loss_price": float(payload["stop_loss_price"]) if payload.get("stop_loss_price") else None,
        })

    _save_watchlist(data_dir, items)
    return {"ok": True, "items": items}


def get_risk_payload(payload: dict) -> dict:
    from .math_utils import _stdev as _std

    symbol = (payload.get("symbol") or "").zfill(6)
    if len(symbol) != 6 or not symbol.isdigit():
        return {"ok": False, "error": "Invalid symbol"}
    data_dir = _safe_path(payload.get("data_dir") or "data/live")
    lookback = int(payload.get("lookback") or 60)

    store = _get_store(data_dir)
    bars = store.bars_until(symbol, store.latest_date(), lookback)
    if len(bars) < 10:
        return {"ok": False, "error": f"Need at least 10 bars, got {len(bars)}"}

    returns = []
    for i in range(1, len(bars)):
        if bars[i - 1].close > 0:
            returns.append((bars[i].close - bars[i - 1].close) / bars[i - 1].close)
    vol = _std(returns) if len(returns) >= 2 else 0.0
    vol_risk = round(1.0 / (1.0 + math.exp(-3 * (vol - 0.025) / 0.01)), 4) if vol > 0 else 0.5

    fund = store.latest_fundamental(symbol, store.latest_date())
    val_risk = 0.5
    if fund:
        if fund.pe_ttm > 0:
            if fund.pe_ttm > 100:
                val_risk = 0.9
            elif fund.pe_ttm < 5:
                val_risk = 0.7
            else:
                val_risk = min(1.0, max(0.0, (fund.pe_ttm - 10) / 90))
        if fund.pb > 0:
            pb_risk = min(1.0, max(0.0, (fund.pb - 1) / 9))
            val_risk = 0.5 * val_risk + 0.5 * pb_risk

    qual_risk = 0.5
    if fund and fund.roe_ttm > 0:
        qual_risk = max(0.0, 1.0 - fund.roe_ttm / 0.20)

    risk_score = round(0.4 * vol_risk + 0.3 * val_risk + 0.3 * qual_risk, 4)
    risk_score = max(0.0, min(1.0, risk_score))

    if risk_score < 0.33:
        risk_level = "low"
    elif risk_score < 0.66:
        risk_level = "medium"
    else:
        risk_level = "high"

    latest = bars[-1]
    suitable = (
        risk_level != "high"
        and not latest.paused
        and latest.open > 0
        and latest.open < latest.limit_up
    )

    reasons: list[str] = []
    if risk_level == "low":
        reasons.append("风险较低")
    elif risk_level == "medium":
        reasons.append("风险中等")
    else:
        reasons.append("风险较高")
    if latest.paused:
        reasons.append("停牌中")

    return {
        "ok": True, "symbol": symbol,
        "risk_score": risk_score, "risk_level": risk_level,
        "factors": {"volatility_risk": vol_risk, "valuation_risk": round(val_risk, 4), "quality_risk": round(qual_risk, 4)},
        "suitable_to_buy": suitable, "reasons": reasons,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Original read endpoints
# ══════════════════════════════════════════════════════════════════════════════

def read_report_payload(out_dir_value: str | None = None) -> dict:
    out_dir = _safe_path(out_dir_value or "reports/demo")
    summary_path = out_dir / "summary.json"
    equity_path = out_dir / "equity_curve.csv"
    trades_path = out_dir / "trades.csv"
    latest_plan = latest_trade_plan(out_dir)

    summary = None
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))

    return {
        "ok": True,
        "summary": summary,
        "equity_curve": read_csv_rows(equity_path) if equity_path.exists() else [],
        "trades": read_csv_rows(trades_path, limit=300) if trades_path.exists() else [],
        "plan": read_csv_rows(latest_plan) if latest_plan else [],
        "files": report_files(out_dir),
    }


def read_status_payload(data_dir_value: str | None = None, out_dir_value: str | None = None) -> dict:
    data_dir = _safe_path(data_dir_value or "data/sample")
    out_dir = _safe_path(out_dir_value or "reports/demo")
    required_data = ["universe.csv", "prices.csv", "fundamentals.csv", "benchmark.csv"]
    data_files = {name: (data_dir / name).exists() for name in required_data}
    summary_path = out_dir / "summary.json"
    latest_plan = latest_trade_plan(out_dir)
    return {
        "ok": True,
        "workspace": str(WORKSPACE_ROOT),
        "data_dir": str(data_dir),
        "out_dir": str(out_dir),
        "data_ready": all(data_files.values()),
        "data_files": data_files,
        "report_ready": summary_path.exists(),
        "latest_plan": latest_plan.name if latest_plan else None,
        "files": report_files(out_dir),
    }


class AQTRequestHandler(BaseHTTPRequestHandler):
    server_version = "AQTUI/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._send_file(STATIC_ROOT / "index.html")
            return
        if parsed.path.startswith("/static/"):
            self._send_file(STATIC_ROOT / parsed.path.removeprefix("/static/"))
            return
        if parsed.path == "/api/status":
            query = parse_qs(parsed.query)
            self._send_json(
                HTTPStatus.OK,
                read_status_payload(_first(query, "data_dir"), _first(query, "out_dir")),
            )
            return
        if parsed.path == "/api/report":
            query = parse_qs(parsed.query)
            self._send_json(HTTPStatus.OK, read_report_payload(_first(query, "out_dir")))
            return
        if parsed.path == "/api/market-index":
            self._send_json(HTTPStatus.OK, get_market_index_payload())
            return
        if parsed.path == "/api/quotes":
            query = parse_qs(parsed.query)
            self._send_json(HTTPStatus.OK, get_quotes_payload(_first(query, "symbols") or ""))
            return
        if parsed.path == "/api/kline":
            query = parse_qs(parsed.query)
            self._send_json(HTTPStatus.OK, get_kline_payload(query))
            return
        if parsed.path == "/api/watchlist":
            query = parse_qs(parsed.query)
            self._send_json(HTTPStatus.OK, get_watchlist_payload(query))
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        routes = {
            "/api/init-sample": init_sample_job,
            "/api/backtest": run_backtest_job,
            "/api/plan": run_plan_job,
            "/api/optimize": run_optimize_job,
            "/api/fetch": run_fetch_job,
            "/api/positions-template": positions_template_job,
            "/api/watchlist": handle_watchlist_post,
            "/api/risk-assessment": get_risk_payload,
        }
        parsed = urlparse(self.path)
        job = routes.get(parsed.path)
        if job is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return
        try:
            payload = self._read_json()
            self._send_json(HTTPStatus.OK, job(payload))
        except Exception as exc:  # noqa: BLE001 - API must return user-readable errors.
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_file(self, path: Path) -> None:
        resolved = path.resolve()
        try:
            resolved.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Forbidden"})
            return
        if not resolved.exists() or not resolved.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "File not found"})
            return
        content = resolved.read_bytes()
        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def read_csv_rows(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows.append(row)
    if limit is not None:
        return rows[-limit:]
    return rows


def report_files(out_dir: Path) -> dict:
    latest_plan = latest_trade_plan(out_dir)
    return {
        "report": str(out_dir / "report.md") if (out_dir / "report.md").exists() else None,
        "summary": str(out_dir / "summary.json") if (out_dir / "summary.json").exists() else None,
        "equity_curve": str(out_dir / "equity_curve.csv") if (out_dir / "equity_curve.csv").exists() else None,
        "trades": str(out_dir / "trades.csv") if (out_dir / "trades.csv").exists() else None,
        "latest_plan": str(latest_plan) if latest_plan else None,
    }


def latest_trade_plan(out_dir: Path) -> Path | None:
    plans = sorted(out_dir.glob("trade_plan_*.csv"))
    return plans[-1] if plans else None


def _trades_to_rows(trades: list) -> list[dict]:
    return [
        {
            "date": trade.date.isoformat(),
            "symbol": trade.symbol,
            "side": trade.side,
            "price": f"{trade.price:.4f}",
            "shares": str(trade.shares),
            "notional": f"{trade.notional:.4f}",
            "fees": f"{trade.fees:.4f}",
            "reason": trade.reason,
        }
        for trade in trades[-300:]
    ]


def _strategy_from_payload(payload: dict):
    name = payload.get("strategy") or "multi_factor"
    entry = STRATEGY_REGISTRY.get(name)
    if entry is None:
        raise ValueError(f"Unknown strategy: {name}")
    config_cls = entry["config_cls"]
    strategy_cls = entry["cls"]
    if name == "multi_factor":
        blacklist_raw = payload.get("blacklist") or ""
        blacklist = tuple(s.strip() for s in blacklist_raw.split(",") if s.strip())
        config = config_cls(
            top_n=int(payload.get("top_n") or 8),
            min_amount=_float(payload.get("min_amount"), 20_000_000.0),
            max_per_industry=int(payload.get("max_per_industry") or 0),
            blacklist=blacklist,
        )
    else:
        config = config_cls()
    return strategy_cls(config)


def _safe_path(value: str | Path) -> Path:
    raw = Path(value)
    resolved = raw.resolve() if raw.is_absolute() else (WORKSPACE_ROOT / raw).resolve()
    try:
        resolved.relative_to(WORKSPACE_ROOT)
    except ValueError as exc:
        raise ValueError(f"Path must be inside workspace: {value}") from exc
    return resolved


def _float(value: object, default: float) -> float:
    if value in {None, ""}:
        return default
    return float(value)


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None

