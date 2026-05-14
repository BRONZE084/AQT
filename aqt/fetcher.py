"""
Daily A-share data fetcher.

Sources:
- Stock list: akshare (non-EastMoney source)
- OHLCV / PE / PB: Tencent Finance (qt.gtimg.cn) — reliable, no auth needed
- Benchmark (CSI 300): EastMoney (fallback to Tencent)

Outputs AQT CSVs: universe.csv, prices.csv, fundamentals.csv, benchmark.csv

Usage:
    python -m aqt fetch --data-dir data/live          # today's incremental
    python -m aqt fetch --data-dir data/live --init   # initial + CSI 300 history
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import time
from datetime import date
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════════
# Tencent Finance — stock quotes
# ══════════════════════════════════════════════════════════════════════════════

_TX_QUOTE_URL = "http://qt.gtimg.cn/q={keys}"
_TX_BATCH = 50


def _tx_fetch_quotes(tx_keys: list[str]) -> list[dict]:
    """Fetch quotes for a batch of Tencent-format keys (e.g. ['sh600000', 'sz000001'])."""
    import urllib.request

    joined = ",".join(tx_keys)
    req = urllib.request.Request(
        f"http://qt.gtimg.cn/q={joined}",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("gbk", errors="replace")

    results: list[dict] = []
    for line in raw.strip().splitlines():
        if not line.strip():
            continue
        m = re.match(r'v_([a-z]{2})(\d+)="(.+)"', line.strip().rstrip(";"))
        if not m:
            continue
        _exchange, code, data = m.groups()
        fields = data.split("~")
        if len(fields) < 47:
            continue
        try:
            results.append({
                "symbol": fields[2].zfill(6),
                "name": fields[1],
                "close": float(fields[3] or 0),
                "pre_close": float(fields[4] or 0),
                "open": float(fields[5] or 0),
                "volume": int(float(fields[6] or 0)) * 100,
                "high": float(fields[33] or 0),
                "low": float(fields[34] or 0),
                "amount": float(fields[37] or 0) * 10000,
                "pe": float(fields[39] or 0),
                "pb": float(fields[47] or 0),
            })
        except (ValueError, IndexError):
            continue
    return results


def _tx_key(symbol: str) -> str:
    """Convert A-share symbol to Tencent key: '600000' -> 'sh600000'."""
    code = symbol.zfill(6)
    if code.startswith(("600", "601", "603", "605", "688")):
        return f"sh{code}"
    return f"sz{code}"


# ══════════════════════════════════════════════════════════════════════════════
# akshare — stock list
# ══════════════════════════════════════════════════════════════════════════════


def _stock_list() -> list[dict]:
    """Return list of {symbol, name} for all A-share stocks via akshare."""
    import akshare as ak

    df = ak.stock_info_a_code_name()
    stocks: list[dict] = []
    for _, row in df.iterrows():
        code = str(row["code"]).zfill(6)
        name = str(row["name"])
        stocks.append({"symbol": code, "name": name})
    return stocks


# ══════════════════════════════════════════════════════════════════════════════
# EastMoney — benchmark (fallback with multiple backends)
# ══════════════════════════════════════════════════════════════════════════════

_EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def _em_get_json(url: str, params: dict, timeout: int = 30) -> dict:
    """Fetch JSON trying curl_cffi → subprocess curl → requests."""
    errors: list[str] = []

    # backend 1: curl_cffi
    try:
        from curl_cffi import requests as cr
        for imp in ["chrome124", "chrome110"]:
            try:
                resp = cr.get(url, params=params, timeout=timeout, impersonate=imp)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                errors.append(f"curl_cffi({imp}): {exc}")
    except ImportError:
        errors.append("curl_cffi not installed")

    # backend 2: subprocess curl
    import urllib.parse
    qs = urllib.parse.urlencode(params, doseq=True)
    full = f"{url}?{qs}"
    base = ["curl", "-s", "--max-time", str(timeout), "--connect-timeout", "10",
            "-H", "Accept: application/json",
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)"]
    for extra in ([], ["--proxy", "http://127.0.0.1:7897"]):
        try:
            r = subprocess.run(base[:1] + extra + base[1:] + [full],
                               capture_output=True, text=True, timeout=timeout + 5)
            if r.returncode == 0 and r.stdout.strip():
                return json.loads(r.stdout)
            errors.append(f"curl proxy={bool(extra)}: rc={r.returncode}")
        except FileNotFoundError:
            errors.append("curl not found"); break
        except subprocess.TimeoutExpired:
            errors.append(f"curl proxy={bool(extra)}: timeout")

    # backend 3: requests
    try:
        import requests
        for pm in (False, True):
            s = requests.Session()
            s.trust_env = pm
            try:
                resp = s.get(url, params=params, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                errors.append(f"requests proxy={pm}: {exc}")
    except ImportError:
        errors.append("requests not installed")

    raise ConnectionError("\n".join(errors))


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════


def _infer_board(symbol: str) -> str:
    code = symbol.zfill(6)
    if code.startswith(("600", "601", "603", "605")):
        return "main"
    if code.startswith(("000", "001", "002", "003", "004")):
        return "main"
    if code.startswith(("300", "301")):
        return "chi_next"
    if code.startswith("688"):
        return "star"
    if code.startswith("8"):
        return "bse"
    return "main"


def _calc_limit_prices(pre_close: float, board: str, is_st: bool) -> tuple[float, float]:
    if is_st:
        pct = 0.05
    elif board in ("star", "chi_next"):
        pct = 0.20
    elif board == "bse":
        pct = 0.30
    else:
        pct = 0.10
    return round(pre_close * (1 + pct), 2), round(pre_close * (1 - pct), 2)


def _load_existing_dates(path: Path) -> set[str]:
    if not path.exists():
        return set()
    dates: set[str] = set()
    with path.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            dates.add(row["date"])
    return dates


def _write_rows(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    if path.exists():
        with path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writerows(rows)
    else:
        _write_csv_new(path, rows)


def _write_csv_new(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _rows_from_quotes(quotes: list[dict], today_str: str) -> tuple[list[dict], list[dict], list[dict]]:
    uni: list[dict] = []
    prices: list[dict] = []
    funds: list[dict] = []

    for q in quotes:
        try:
            symbol = q["symbol"]
            name = q["name"]
            board = _infer_board(symbol)
            is_st = any(t in name.upper() for t in ("ST", "*ST"))
            pre_close = q["pre_close"]
            if pre_close <= 0:
                continue

            limit_up, limit_down = _calc_limit_prices(pre_close, board, is_st)

            prices.append({
                "date": today_str,
                "symbol": symbol,
                "open": f"{q['open']:.2f}",
                "high": f"{max(q['high'], q['open'], q['close']):.2f}",
                "low": f"{min(q['low'] or q['close'], q['open'], q['close']):.2f}",
                "close": f"{q['close']:.2f}",
                "volume": q["volume"],
                "amount": f"{q['amount']:.2f}",
                "paused": int(q["volume"] == 0),
                "is_st": int(is_st),
                "limit_up": f"{limit_up:.2f}",
                "limit_down": f"{limit_down:.2f}",
            })

            uni.append({
                "symbol": symbol,
                "name": name,
                "board": board,
                "industry": "",
                "list_date": "2000-01-01",
            })

            if q["pe"] > 0 or q["pb"] > 0:
                funds.append({
                    "date": today_str,
                    "symbol": symbol,
                    "pe_ttm": f"{q['pe']:.4f}",
                    "pb": f"{q['pb']:.4f}",
                    "roe_ttm": "0.0000",
                })
        except (ValueError, KeyError):
            continue
    return uni, prices, funds


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════


def fetch_daily(data_dir: str | Path) -> str:
    """Fetch today's A-share data; append new rows to CSVs."""
    output = Path(data_dir)
    output.mkdir(parents=True, exist_ok=True)
    today_str = date.today().isoformat()

    prices_path = output / "prices.csv"
    if today_str in _load_existing_dates(prices_path):
        print(f"Prices for {today_str} already exist, skipping spot fetch.")
    else:
        print("Fetching stock list from akshare ...")
        stocks = _stock_list()
        print(f"  Got {len(stocks)} symbols")

        tx_keys = [_tx_key(s["symbol"]) for s in stocks]
        batches = [tx_keys[i:i + _TX_BATCH] for i in range(0, len(tx_keys), _TX_BATCH)]

        all_quotes: list[dict] = []
        for idx, batch in enumerate(batches):
            if idx % 15 == 0:
                print(f"  {idx}/{len(batches)} batches ...", end="\r")
            try:
                all_quotes.extend(_tx_fetch_quotes(batch))
            except Exception as exc:
                print(f"  batch {idx} error: {exc}")
            time.sleep(0.06)
        print(f"  {len(batches)}/{len(batches)} batches — {len(all_quotes)} quotes fetched.    ")

        uni_rows, price_rows, fund_rows = _rows_from_quotes(all_quotes, today_str)
        _write_rows(prices_path, price_rows)
        print(f"  Appended {len(price_rows)} rows to prices.csv")
        _write_csv_new(output / "universe.csv", uni_rows)
        print(f"  Wrote {len(uni_rows)} symbols to universe.csv")

        fund_path = output / "fundamentals.csv"
        if today_str not in _load_existing_dates(fund_path):
            _write_rows(fund_path, fund_rows)
            print(f"  Appended {len(fund_rows)} rows to fundamentals.csv")

    # ── Benchmark ──
    bm_path = output / "benchmark.csv"
    if today_str in _load_existing_dates(bm_path):
        print(f"Benchmark for {today_str} already exists, skipping.")
    else:
        try:
            _append_benchmark(bm_path)
        except Exception as exc:
            print(f"  Benchmark skipped: {exc}")

    print(f"Done -- data at {output.resolve()}")
    return today_str


def fetch_init(data_dir: str | Path) -> str:
    """First-time setup: today's spot + CSI 300 history."""
    output = Path(data_dir)
    output.mkdir(parents=True, exist_ok=True)

    print("Fetching stock list from akshare ...")
    stocks = _stock_list()
    print(f"  Got {len(stocks)} symbols")

    tx_keys = [_tx_key(s["symbol"]) for s in stocks]
    batches = [tx_keys[i:i + _TX_BATCH] for i in range(0, len(tx_keys), _TX_BATCH)]

    all_quotes: list[dict] = []
    for idx, batch in enumerate(batches):
        if idx % 15 == 0:
            print(f"  {idx}/{len(batches)} batches ...", end="\r")
        try:
            all_quotes.extend(_tx_fetch_quotes(batch))
        except Exception:
            pass
        time.sleep(0.06)
    print(f"  {len(batches)}/{len(batches)} batches — {len(all_quotes)} quotes fetched.    ")

    today_str = date.today().isoformat()
    uni_rows, price_rows, fund_rows = _rows_from_quotes(all_quotes, today_str)
    _write_csv_new(output / "universe.csv", uni_rows)
    _write_csv_new(output / "prices.csv", price_rows)
    _write_csv_new(output / "fundamentals.csv", fund_rows)
    print(f"  Wrote {len(price_rows)} price rows")

    print("Fetching CSI 300 benchmark history via EastMoney ...")
    try:
        bm_rows = _fetch_csi300_history()
        _write_csv_new(output / "benchmark.csv", bm_rows)
        print(f"  Wrote {len(bm_rows)} rows")
    except Exception as exc:
        print(f"  Benchmark failed: {exc}")

    print(f"Done -- initial data written to {output.resolve()}")
    return today_str


def _append_benchmark(path: Path) -> None:
    params = {
        "secid": "1.000300",
        "ut": "fa5fd1943c7b386f172d1897d0f7d7b1",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101", "fqt": "1", "end": "20500101", "lmt": "30",
    }
    data = _em_get_json(_EM_KLINE_URL, params)
    klines = (data.get("data") or {}).get("klines") or []
    existing = _load_existing_dates(path)
    new_rows = []
    for line in klines:
        parts = line.split(",")
        if len(parts) >= 3 and parts[0] not in existing:
            new_rows.append({"date": parts[0], "close": parts[2]})
    if new_rows:
        _write_rows(path, new_rows)
        print(f"  Appended {len(new_rows)} rows to benchmark.csv")


def _fetch_csi300_history() -> list[dict]:
    params = {
        "secid": "1.000300",
        "ut": "fa5fd1943c7b386f172d1897d0f7d7b1",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101", "fqt": "1", "end": "20500101", "lmt": "10000",
    }
    data = _em_get_json(_EM_KLINE_URL, params)
    klines = (data.get("data") or {}).get("klines") or []
    return [{"date": p.split(",")[0], "close": p.split(",")[2]}
            for p in klines if len(p.split(",")) >= 3]
