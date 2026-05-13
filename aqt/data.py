from __future__ import annotations

import csv
import math
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from .models import Bar, Fundamental, Security


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def format_date(value: date) -> str:
    return value.isoformat()


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def business_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class DataStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.universe: dict[str, Security] = {}
        self.prices: dict[str, list[Bar]] = defaultdict(list)
        self.prices_by_date: dict[date, dict[str, Bar]] = defaultdict(dict)
        self.fundamentals: dict[str, list[Fundamental]] = defaultdict(list)
        self.benchmark: dict[date, float] = {}
        self.dates: list[date] = []

    @classmethod
    def load(cls, data_dir: str | Path) -> "DataStore":
        store = cls(Path(data_dir))
        store._load_universe()
        store._load_prices()
        store._load_fundamentals()
        store._load_benchmark()
        return store

    def _load_universe(self) -> None:
        path = self.data_dir / "universe.csv"
        with path.open("r", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                security = Security(
                    symbol=row["symbol"],
                    name=row["name"],
                    board=row["board"],
                    industry=row["industry"],
                    list_date=parse_date(row["list_date"]),
                )
                self.universe[security.symbol] = security

    def _load_prices(self) -> None:
        path = self.data_dir / "prices.csv"
        seen_dates: set[date] = set()
        with path.open("r", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                bar = Bar(
                    date=parse_date(row["date"]),
                    symbol=row["symbol"],
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(float(row["volume"])),
                    amount=float(row["amount"]),
                    paused=parse_bool(row["paused"]),
                    is_st=parse_bool(row["is_st"]),
                    limit_up=float(row["limit_up"]),
                    limit_down=float(row["limit_down"]),
                )
                self.prices[bar.symbol].append(bar)
                self.prices_by_date[bar.date][bar.symbol] = bar
                seen_dates.add(bar.date)
        for bars in self.prices.values():
            bars.sort(key=lambda item: item.date)
        self.dates = sorted(seen_dates)

    def _load_fundamentals(self) -> None:
        path = self.data_dir / "fundamentals.csv"
        with path.open("r", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                item = Fundamental(
                    date=parse_date(row["date"]),
                    symbol=row["symbol"],
                    pe_ttm=float(row["pe_ttm"]),
                    pb=float(row["pb"]),
                    roe_ttm=float(row["roe_ttm"]),
                )
                self.fundamentals[item.symbol].append(item)
        for rows in self.fundamentals.values():
            rows.sort(key=lambda item: item.date)

    def _load_benchmark(self) -> None:
        path = self.data_dir / "benchmark.csv"
        if not path.exists():
            return
        with path.open("r", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                self.benchmark[parse_date(row["date"])] = float(row["close"])

    def date_range(self, start: date | None = None, end: date | None = None) -> list[date]:
        return [
            item
            for item in self.dates
            if (start is None or item >= start) and (end is None or item <= end)
        ]

    def latest_date(self) -> date:
        if not self.dates:
            raise ValueError("No price dates loaded.")
        return self.dates[-1]

    def previous_date(self, value: date) -> date | None:
        previous = None
        for item in self.dates:
            if item >= value:
                return previous
            previous = item
        return previous

    def bar(self, symbol: str, value: date) -> Bar | None:
        return self.prices_by_date.get(value, {}).get(symbol)

    def last_bar(self, symbol: str, as_of: date) -> Bar | None:
        result = None
        for bar in self.prices.get(symbol, []):
            if bar.date > as_of:
                break
            result = bar
        return result

    def bars_until(self, symbol: str, as_of: date, count: int) -> list[Bar]:
        bars = [bar for bar in self.prices.get(symbol, []) if bar.date <= as_of]
        return bars[-count:]

    def latest_fundamental(self, symbol: str, as_of: date) -> Fundamental | None:
        result = None
        for item in self.fundamentals.get(symbol, []):
            if item.date > as_of:
                break
            result = item
        return result

    def symbols_as_of(self, as_of: date) -> list[str]:
        return [
            symbol
            for symbol, security in self.universe.items()
            if security.list_date <= as_of
        ]


def _limit_pct(board: str, is_st: bool) -> float:
    if is_st:
        return 0.05
    if board in {"star", "chi_next"}:
        return 0.20
    return 0.10


def generate_sample_data(data_dir: str | Path, start: date, end: date) -> None:
    output = Path(data_dir)
    output.mkdir(parents=True, exist_ok=True)

    securities = [
        ("600001", "AQT Bank", "main", "bank", "2010-01-04", 8.2),
        ("600002", "AQT Steel", "main", "materials", "2011-03-18", 5.5),
        ("600003", "AQT Power", "main", "utility", "2012-06-20", 6.8),
        ("600004", "AQT Pharma", "main", "healthcare", "2013-09-02", 22.0),
        ("600005", "AQT Retail", "main", "consumer", "2014-11-17", 13.5),
        ("600006", "AQT Auto", "main", "auto", "2015-05-11", 15.0),
        ("600007", "AQT Broker", "main", "finance", "2016-02-26", 11.0),
        ("600008", "AQT Chem", "main", "materials", "2017-08-14", 9.0),
        ("000001", "AQT Food", "main", "consumer", "2011-04-12", 18.0),
        ("000002", "AQT Home", "main", "real_estate", "2012-12-03", 7.5),
        ("000003", "AQT Grid", "main", "utility", "2013-07-22", 10.5),
        ("000004", "AQT Chip", "main", "technology", "2016-10-10", 28.0),
        ("300001", "AQT Robot", "chi_next", "technology", "2015-01-19", 31.0),
        ("300002", "AQT Solar", "chi_next", "new_energy", "2016-06-06", 24.0),
        ("300003", "AQT Media", "chi_next", "media", "2018-05-07", 16.0),
        ("688001", "AQT Bio", "star", "healthcare", "2019-07-22", 42.0),
        ("688002", "AQT Cloud", "star", "technology", "2020-01-13", 55.0),
        ("688003", "AQT Battery", "star", "new_energy", "2020-08-18", 36.0),
    ]

    universe_rows = [
        {
            "symbol": symbol,
            "name": name,
            "board": board,
            "industry": industry,
            "list_date": list_date,
        }
        for symbol, name, board, industry, list_date, _price in securities
    ]
    write_csv(
        output / "universe.csv",
        ["symbol", "name", "board", "industry", "list_date"],
        universe_rows,
    )

    rng = random.Random(20260513)
    days = business_days(start, end)
    prices = {symbol: price for symbol, _name, _board, _industry, _list_date, price in securities}
    price_rows = []
    benchmark_rows = []
    fundamentals_rows = []
    benchmark = 1000.0
    industry_bias = {
        "technology": 0.00035,
        "new_energy": 0.00025,
        "healthcare": 0.00020,
        "consumer": 0.00010,
        "finance": 0.00002,
        "bank": -0.00002,
        "real_estate": -0.00012,
    }

    for idx, current in enumerate(days):
        market_ret = rng.gauss(0.00015, 0.010)
        benchmark = max(100.0, benchmark * (1 + market_ret))
        benchmark_rows.append({"date": format_date(current), "close": f"{benchmark:.4f}"})

        month_start = idx == 0 or current.month != days[idx - 1].month
        for symbol, _name, board, industry, _list_date, _initial in securities:
            previous_close = prices[symbol]
            is_st = symbol == "600008" and current >= date(2024, 7, 1)
            paused = rng.random() < 0.006
            limit_pct = _limit_pct(board, is_st)
            limit_up = round(previous_close * (1 + limit_pct), 2)
            limit_down = round(previous_close * (1 - limit_pct), 2)

            if paused:
                open_price = high = low = close = previous_close
                volume = 0
                amount = 0.0
            else:
                drift = industry_bias.get(industry, 0.0)
                noise = rng.gauss(drift, 0.018)
                daily_ret = max(-limit_pct * 0.94, min(limit_pct * 0.94, 0.55 * market_ret + noise))
                open_ret = rng.gauss(0.0, 0.006)
                open_price = max(limit_down, min(limit_up, previous_close * (1 + open_ret)))
                close = max(limit_down, min(limit_up, previous_close * (1 + daily_ret)))
                swing = abs(rng.gauss(0.012, 0.006))
                high = min(limit_up, max(open_price, close) * (1 + swing))
                low = max(limit_down, min(open_price, close) * (1 - swing))
                turnover = rng.uniform(0.004, 0.035)
                base_float_shares = 600_000_000 if board == "main" else 180_000_000
                volume = int(base_float_shares * turnover / 100) * 100
                amount = volume * close
                prices[symbol] = close

            price_rows.append(
                {
                    "date": format_date(current),
                    "symbol": symbol,
                    "open": f"{open_price:.2f}",
                    "high": f"{high:.2f}",
                    "low": f"{low:.2f}",
                    "close": f"{close:.2f}",
                    "volume": volume,
                    "amount": f"{amount:.2f}",
                    "paused": int(paused),
                    "is_st": int(is_st),
                    "limit_up": f"{limit_up:.2f}",
                    "limit_down": f"{limit_down:.2f}",
                }
            )

            if month_start:
                growth = 1 + math.sin(idx / 90.0 + len(symbol)) * 0.08
                pe = max(6.0, rng.gauss(22.0, 8.0) / growth)
                pb = max(0.6, rng.gauss(2.4, 0.9) / growth)
                roe = max(0.01, min(0.32, rng.gauss(0.12, 0.05) * growth))
                fundamentals_rows.append(
                    {
                        "date": format_date(current),
                        "symbol": symbol,
                        "pe_ttm": f"{pe:.4f}",
                        "pb": f"{pb:.4f}",
                        "roe_ttm": f"{roe:.4f}",
                    }
                )

    write_csv(
        output / "prices.csv",
        [
            "date",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "paused",
            "is_st",
            "limit_up",
            "limit_down",
        ],
        price_rows,
    )
    write_csv(output / "fundamentals.csv", ["date", "symbol", "pe_ttm", "pb", "roe_ttm"], fundamentals_rows)
    write_csv(output / "benchmark.csv", ["date", "close"], benchmark_rows)

