from __future__ import annotations

import csv
import json
import mimetypes
import threading
import webbrowser
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .backtest import BacktestConfig, Backtester, save_backtest_result
from .data import DataStore, generate_sample_data, parse_date
from .planner import PlanConfig, generate_trade_plan, load_positions
from .strategy import MultiFactorConfig, MultiFactorStrategy


WORKSPACE_ROOT = Path.cwd().resolve()
STATIC_ROOT = Path(__file__).with_name("static")


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
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        routes = {
            "/api/init-sample": init_sample_job,
            "/api/backtest": run_backtest_job,
            "/api/plan": run_plan_job,
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


def _strategy_from_payload(payload: dict) -> MultiFactorStrategy:
    return MultiFactorStrategy(
        MultiFactorConfig(
            top_n=int(payload.get("top_n") or 8),
            min_amount=_float(payload.get("min_amount"), 20_000_000.0),
        )
    )


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

