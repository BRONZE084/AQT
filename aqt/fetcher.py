"""
Daily A-share data fetcher.

All OHLCV prices are **forward-adjusted (前复权, fqt=1)** from EastMoney.
PE/PB and stock names come from Tencent Finance.
Stock list comes from akshare.

Sources:
- Stock list: akshare (stock_info_a_code_name)
- OHLCV: EastMoney (push2his.eastmoney.com) — forward-adjusted daily K-line
- PE / PB: Tencent Finance (qt.gtimg.cn)
- Benchmark (CSI 300): EastMoney

Outputs AQT CSVs: universe.csv, prices.csv, fundamentals.csv, benchmark.csv

Usage:
    python -m aqt fetch --data-dir data/live                 # today's spot + adjusted OHLCV
    python -m aqt fetch --data-dir data/live --init          # spot + 60d history + benchmark
    python -m aqt fetch --data-dir data/live --history 120   # backfill 120 days
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
# EastMoney — stock K-line history
# ══════════════════════════════════════════════════════════════════════════════

_EM_SECID_CACHE: dict[str, str] = {}


def _em_secid(symbol: str) -> str:
    """Convert A-share symbol to EastMoney secid: '600000' -> '1.600000'."""
    if symbol in _EM_SECID_CACHE:
        return _EM_SECID_CACHE[symbol]
    code = symbol.zfill(6)
    if code.startswith(("600", "601", "603", "605", "688")):
        secid = f"1.{code}"
    else:
        secid = f"0.{code}"
    _EM_SECID_CACHE[symbol] = secid
    return secid


def _em_stock_kline(symbol: str, days: int, fqt: str = "1") -> list[dict]:
    """Fetch daily K-line for one stock from EastMoney. Returns price-format dicts.

    fqt: "1" = 前复权 (forward-adjusted, default), "0" = 不复权 (raw),
         "2" = 后复权 (backward-adjusted).
    """
    secid = _em_secid(symbol)
    params = {
        "secid": secid,
        "ut": "fa5fd1943c7b386f172d1897d0f7d7b1",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": "101", "fqt": fqt, "end": "20500101", "lmt": str(days + 10),
    }
    try:
        data = _em_get_json(_EM_KLINE_URL, params, timeout=20)
        klines = (data.get("data") or {}).get("klines") or []
    except Exception:
        return []

    board = _infer_board(symbol)
    rows: list[dict] = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 7:
            continue
        try:
            dt, opn_str, cls_str, high_str, low_str, vol_str, amt_str = parts[:7]
            opn = float(opn_str or 0)
            cls = float(cls_str or 0)
            high = float(high_str or 0)
            low = float(low_str or 0)
            vol = int(float(vol_str or 0))
            amt = float(amt_str or 0)
            if opn <= 0:
                continue
            pre_close = opn  # approximate for limit calc — not strictly correct per-bar
            limit_up, limit_down = _calc_limit_prices(cls, board, False)
            rows.append({
                "date": dt,
                "symbol": symbol,
                "open": f"{opn:.2f}",
                "high": f"{max(high, opn, cls):.2f}",
                "low": f"{min(low or cls, opn, cls):.2f}",
                "close": f"{cls:.2f}",
                "volume": vol,
                "amount": f"{amt:.2f}",
                "paused": 0,
                "is_st": 0,
                "limit_up": f"{limit_up:.2f}",
                "limit_down": f"{limit_down:.2f}",
            })
        except (ValueError, IndexError):
            continue
    return rows[-days:] if len(rows) > days else rows


def _em_fetch_daily_bars(symbols: list[str], days: int = 3, concurrency: int = 8) -> list[dict]:
    """Fetch latest N days of forward-adjusted OHLCV bars for a batch of symbols.

    Returns price-format dicts. Used by fetch_daily() to get EastMoney adjusted bars
    instead of raw Tencent prices.
    """
    all_rows: list[dict] = []
    done = 0
    errors = 0

    def _fetch_one(sym: str) -> list[dict]:
        try:
            return _em_stock_kline(sym, days)
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_fetch_one, s): s for s in symbols}
        for future in as_completed(futures):
            try:
                rows = future.result()
                all_rows.extend(rows)
                done += 1
                if done % 200 == 0:
                    print(f"  EastMoney bars: {done}/{len(symbols)} ...", end="\r")
            except Exception:
                errors += 1
            time.sleep(0.02)

    print(f"  EastMoney bars: {done}/{len(symbols)} — {len(all_rows)} rows, {errors} errors.    ")
    return all_rows


def _load_existing_pairs(path: str | Path) -> set[tuple[str, str]]:
    """Return set of (date, symbol) tuples already in a CSV file."""
    path = Path(path)
    pairs: set[tuple[str, str]] = set()
    if path.exists():
        with path.open("r", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                pairs.add((row["date"], row["symbol"]))
    return pairs


def _merge_prices(path: str | Path, new_rows: list[dict]) -> int:
    """Merge new price rows into CSV, overwriting any existing (date, symbol) pairs."""
    path = Path(path)
    if not new_rows:
        return 0
    new_pairs = {(r["date"], r["symbol"]) for r in new_rows}
    existing_rows: list[dict] = []
    fieldnames = list(new_rows[0].keys())
    if path.exists():
        with path.open("r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames:
                fieldnames = reader.fieldnames
            for row in reader:
                if (row["date"], row["symbol"]) not in new_pairs:
                    existing_rows.append(row)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for row in existing_rows:
            w.writerow(row)
        for row in new_rows:
            w.writerow(row)
    return len(new_rows)


def fetch_history(data_dir: str | Path, days: int = 60, concurrency: int = 8) -> int:
    """Backfill N days of daily K-line history for all symbols in universe.csv.

    Returns the number of new rows appended to prices.csv.
    """
    output = Path(data_dir)
    prices_path = output / "prices.csv"
    universe_path = output / "universe.csv"

    if not universe_path.exists():
        print("universe.csv not found — run fetch_daily first to create stock list.")
        return 0

    # Build set of existing (date, symbol) pairs
    existing: set[tuple[str, str]] = set()
    if prices_path.exists():
        with prices_path.open("r", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                existing.add((row["date"], row["symbol"]))

    # Load symbols from universe.csv
    symbols: list[str] = []
    with universe_path.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            symbols.append(row["symbol"])

    print(f"Backfilling {days} days of K-line for {len(symbols)} symbols ...")
    new_rows: list[dict] = []
    errors = 0
    done = 0

    def _fetch_one(sym: str) -> list[dict]:
        try:
            bars = _em_stock_kline(sym, days)
        except Exception:
            return []
        return [b for b in bars if (b["date"], b["symbol"]) not in existing]

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_fetch_one, s): s for s in symbols}
        for future in as_completed(futures):
            try:
                batch = future.result()
                new_rows.extend(batch)
                done += 1
                if done % 200 == 0:
                    print(f"  {done}/{len(symbols)} symbols ({len(new_rows)} bars) ...", end="\r")
            except Exception:
                errors += 1
            time.sleep(0.02)

    print(f"  {done}/{len(symbols)} symbols — {len(new_rows)} bars, {errors} errors.    ")

    if new_rows:
        merged = _merge_prices(prices_path, new_rows)
        print(f"  Merged {merged} new rows into prices.csv (overwrite mode)")

    return len(new_rows)


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════


def fetch_daily(data_dir: str | Path) -> str:
    """Fetch today's A-share data; append new rows to CSVs.

    OHLCV prices come from EastMoney (forward-adjusted, fqt=1).
    PE/PB/name come from Tencent Finance.
    Stock list comes from akshare.
    """
    output = Path(data_dir)
    output.mkdir(parents=True, exist_ok=True)
    today_str = date.today().isoformat()

    prices_path = output / "prices.csv"
    universe_path = output / "universe.csv"
    fund_path = output / "fundamentals.csv"

    # 1. Stock list from akshare
    print("Fetching stock list from akshare ...")
    stocks = _stock_list()
    print(f"  Got {len(stocks)} symbols")

    tx_keys = [_tx_key(s["symbol"]) for s in stocks]
    batches = [tx_keys[i:i + _TX_BATCH] for i in range(0, len(tx_keys), _TX_BATCH)]

    # 2. PE/PB from Tencent → universe.csv + fundamentals.csv
    all_quotes: list[dict] = []
    for idx, batch in enumerate(batches):
        if idx % 15 == 0:
            print(f"  Tencent quotes: {idx}/{len(batches)} batches ...", end="\r")
        try:
            all_quotes.extend(_tx_fetch_quotes(batch))
        except Exception as exc:
            print(f"  batch {idx} error: {exc}")
        time.sleep(0.06)
    print(f"  Tencent quotes: {len(batches)}/{len(batches)} — {len(all_quotes)} stocks.    ")

    uni_rows, _price_rows_unused, fund_rows = _rows_from_quotes(all_quotes, today_str)
    _write_csv_new(universe_path, uni_rows)
    print(f"  Wrote {len(uni_rows)} symbols to universe.csv")

    if today_str not in _load_existing_dates(fund_path):
        _write_rows(fund_path, fund_rows)
        print(f"  Appended {len(fund_rows)} rows to fundamentals.csv")
    else:
        print(f"  Fundamentals for {today_str} already exist, skipping.")

    # 3. OHLCV from EastMoney fqt=1 → prices.csv (forward-adjusted, consistent with history)
    existing_pairs = _load_existing_pairs(prices_path)
    if today_str in {p[0] for p in existing_pairs}:
        print(f"Prices for {today_str} already exist, skipping spot fetch.")
    else:
        symbols = [s["symbol"] for s in stocks]
        print(f"Fetching adjusted OHLCV from EastMoney for {len(symbols)} symbols ...")
        new_bars = _em_fetch_daily_bars(symbols, days=3)
        fresh_bars = [b for b in new_bars if (b["date"], b["symbol"]) not in existing_pairs]
        if fresh_bars:
            _write_rows(prices_path, fresh_bars)
            print(f"  Appended {len(fresh_bars)} rows to prices.csv")
        else:
            print("  No new price rows to add.")

    # 4. Benchmark
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


def fetch_init(data_dir: str | Path, history_days: int = 60, fqt: str = "1") -> str:
    """First-time setup: backfill K-line history + today's spot + CSI 300 history.

    All OHLCV prices come from EastMoney with the specified fqt adjustment mode
    (default fqt=1 = forward-adjusted). PE/PB come from Tencent.
    """
    output = Path(data_dir)
    output.mkdir(parents=True, exist_ok=True)

    # Step 1: Stock list from akshare + PE/PB from Tencent
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
    uni_rows, _price_rows_unused, fund_rows = _rows_from_quotes(all_quotes, today_str)
    _write_csv_new(output / "universe.csv", uni_rows)
    _write_csv_new(output / "fundamentals.csv", fund_rows)
    print(f"  Wrote {len(uni_rows)} symbols to universe.csv")
    print(f"  Wrote {len(fund_rows)} rows to fundamentals.csv")

    # Step 2: Recent bars from EastMoney fqt=1 → prices.csv
    symbols = [s["symbol"] for s in stocks]
    recent = _em_fetch_daily_bars(symbols, days=3)
    if recent:
        _write_csv_new(output / "prices.csv", recent)
        print(f"  Wrote {len(recent)} recent rows to prices.csv")

    # Step 3: Backfill historical K-line (uses overwrite mode via _merge_prices)
    print(f"Backfilling {history_days} days of K-line history ...")
    fetch_history(data_dir, days=history_days)

    # Step 4: Benchmark history
    bm_path = output / "benchmark.csv"
    print("Fetching CSI 300 benchmark history via EastMoney ...")
    try:
        bm_rows = _fetch_csi300_history()
        _write_csv_new(bm_path, bm_rows)
        print(f"  Wrote {len(bm_rows)} rows")
    except Exception as exc:
        print(f"  Benchmark failed: {exc}")

    # Step 5: Industry classification
    print("Building industry classification map ...")
    try:
        industry_map = _build_industry_map()
        if industry_map:
            _save_industry_csv(data_dir, industry_map)
            print(f"  Saved {len(industry_map)} industry mappings to industry.csv")
        else:
            print("  No industry data fetched (akshare may not be installed).")
    except Exception as exc:
        print(f"  Industry fetch skipped: {exc}")

    print(f"Done -- data written to {output.resolve()}")
    return today_str


def _build_industry_map() -> dict[str, str]:
    """Build symbol→industry mapping via akshare industry board APIs.

    Iterates over all Shenwan industry boards and collects stock members.
    Returns {symbol: industry_name}.
    """
    try:
        import akshare as ak
    except ImportError:
        return {}

    result: dict[str, str] = {}
    try:
        df_boards = ak.stock_board_industry_name_em()
    except Exception:
        return {}

    for _, row in df_boards.iterrows():
        board_name = str(row.get("板块名称") or row.get("板块") or "")
        if not board_name:
            continue
        try:
            df_stocks = ak.stock_board_industry_cons_em(symbol=board_name)
        except Exception:
            continue
        for _, sr in df_stocks.iterrows():
            try:
                code = str(sr.get("代码") or sr.get("code") or "").zfill(6)
                if len(code) == 6 and code.isdigit():
                    result[code] = board_name
            except Exception:
                continue
        time.sleep(0.1)

    return result


def _save_industry_csv(data_dir: str | Path, industry_map: dict[str, str]) -> None:
    """Write industry.csv with columns symbol, industry."""
    path = Path(data_dir) / "industry.csv"
    rows = [{"symbol": s, "industry": i} for s, i in sorted(industry_map.items())]
    _write_csv_new(path, rows)


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
