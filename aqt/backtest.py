from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .data import DataStore, format_date
from .models import Position, Trade
from .rules import FeeModel, can_buy, can_sell, round_lot
from .strategy import MultiFactorStrategy


@dataclass(frozen=True)
class BacktestConfig:
    start: date
    end: date
    initial_cash: float = 1_000_000.0
    max_weight: float = 0.15
    cash_buffer: float = 0.02


@dataclass
class BacktestResult:
    equity_curve: list[dict]
    trades: list[Trade]
    summary: dict


class Backtester:
    def __init__(
        self,
        store: DataStore,
        strategy: MultiFactorStrategy,
        config: BacktestConfig,
        fee_model: FeeModel | None = None,
    ) -> None:
        self.store = store
        self.strategy = strategy
        self.config = config
        self.fee_model = fee_model or FeeModel()
        self.cash = config.initial_cash
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.blocked: list[str] = []

    def run(self) -> BacktestResult:
        dates = self.store.date_range(self.config.start, self.config.end)
        if not dates:
            raise ValueError("No trading dates in selected range.")

        equity_curve: list[dict] = []
        last_rebalance_month: tuple[int, int] | None = None

        for current in dates:
            for position in self.positions.values():
                position.available_shares = position.shares

            month_key = (current.year, current.month)
            if month_key != last_rebalance_month:
                as_of = self.store.previous_date(current)
                if as_of is not None:
                    self._rebalance(current, as_of)
                    last_rebalance_month = month_key

            equity = self._equity_at_close(current)
            benchmark_close = self.store.benchmark.get(current)
            equity_curve.append(
                {
                    "date": format_date(current),
                    "cash": round(self.cash, 4),
                    "market_value": round(equity - self.cash, 4),
                    "equity": round(equity, 4),
                    "positions": len([p for p in self.positions.values() if p.shares > 0]),
                    "benchmark": benchmark_close if benchmark_close is not None else "",
                }
            )

        summary = self._summary(equity_curve)
        return BacktestResult(equity_curve=equity_curve, trades=self.trades, summary=summary)

    def _rebalance(self, trade_date: date, as_of: date) -> None:
        signals = self.strategy.select(self.store, as_of)
        selected = [signal.symbol for signal in signals]
        selected_set = set(selected)
        if not selected:
            self.blocked.append(f"{format_date(trade_date)} no eligible symbols")
            return

        equity = self._equity_at_open(trade_date)
        target_weight = min(
            self.config.max_weight,
            max(0.0, (1.0 - self.config.cash_buffer) / len(selected)),
        )
        desired_shares: dict[str, int] = {}
        for symbol in selected:
            bar = self.store.bar(symbol, trade_date)
            if bar is None or bar.open <= 0:
                continue
            target_value = equity * target_weight
            desired_shares[symbol] = round_lot(target_value / bar.open)

        all_symbols = sorted(set(self.positions) | selected_set)
        for symbol in all_symbols:
            position = self.positions.get(symbol)
            current_shares = position.shares if position else 0
            target_shares = desired_shares.get(symbol, 0)
            if current_shares <= target_shares:
                continue
            bar = self.store.bar(symbol, trade_date)
            if bar is None or not can_sell(bar):
                self.blocked.append(f"{format_date(trade_date)} sell blocked {symbol}")
                continue
            sellable = position.available_shares if position else 0
            shares = round_lot(min(current_shares - target_shares, sellable))
            if shares <= 0:
                continue
            self._execute_sell(trade_date, symbol, shares, bar.open, "rebalance")

        for symbol in selected:
            current_shares = self.positions.get(symbol).shares if symbol in self.positions else 0
            target_shares = desired_shares.get(symbol, 0)
            if current_shares >= target_shares:
                continue
            bar = self.store.bar(symbol, trade_date)
            if bar is None or not can_buy(bar):
                self.blocked.append(f"{format_date(trade_date)} buy blocked {symbol}")
                continue
            wanted = round_lot(target_shares - current_shares)
            shares = self._affordable_shares(wanted, bar.open)
            if shares <= 0:
                continue
            self._execute_buy(trade_date, symbol, shares, bar.open, "rebalance")

    def _affordable_shares(self, wanted: int, price: float) -> int:
        shares = round_lot(wanted)
        while shares > 0:
            fees = self.fee_model.fees("buy", shares, price)
            if shares * price + fees <= self.cash:
                return shares
            shares -= 100
        return 0

    def _execute_buy(self, trade_date: date, symbol: str, shares: int, price: float, reason: str) -> None:
        fees = self.fee_model.fees("buy", shares, price)
        notional = shares * price
        self.cash -= notional + fees
        position = self.positions.get(symbol)
        if position is None:
            position = Position(symbol=symbol, shares=0, available_shares=0, cost_basis=0.0)
            self.positions[symbol] = position
        old_value = position.cost_basis * position.shares
        position.shares += shares
        position.cost_basis = (old_value + notional + fees) / position.shares
        position.available_shares = max(0, position.available_shares)
        self.trades.append(Trade(trade_date, symbol, "buy", price, shares, notional, fees, reason))

    def _execute_sell(self, trade_date: date, symbol: str, shares: int, price: float, reason: str) -> None:
        position = self.positions[symbol]
        shares = min(shares, position.available_shares, position.shares)
        if shares <= 0:
            return
        fees = self.fee_model.fees("sell", shares, price)
        notional = shares * price
        self.cash += notional - fees
        position.shares -= shares
        position.available_shares -= shares
        if position.shares <= 0:
            del self.positions[symbol]
        self.trades.append(Trade(trade_date, symbol, "sell", price, shares, notional, fees, reason))

    def _equity_at_open(self, value_date: date) -> float:
        total = self.cash
        for symbol, position in self.positions.items():
            bar = self.store.bar(symbol, value_date) or self.store.last_bar(symbol, value_date)
            if bar is not None:
                total += position.shares * bar.open
        return total

    def _equity_at_close(self, value_date: date) -> float:
        total = self.cash
        for symbol, position in self.positions.items():
            bar = self.store.bar(symbol, value_date) or self.store.last_bar(symbol, value_date)
            if bar is not None:
                total += position.shares * bar.close
        return total

    def _summary(self, curve: list[dict]) -> dict:
        start_equity = self.config.initial_cash
        end_equity = float(curve[-1]["equity"])
        total_return = end_equity / start_equity - 1.0

        daily_returns = []
        max_equity = -math.inf
        max_drawdown = 0.0
        previous = None
        for row in curve:
            equity = float(row["equity"])
            if previous is not None and previous > 0:
                daily_returns.append(equity / previous - 1.0)
            previous = equity
            max_equity = max(max_equity, equity)
            if max_equity > 0:
                max_drawdown = min(max_drawdown, equity / max_equity - 1.0)

        years = max(1 / 252, len(curve) / 252)
        annual_return = (1.0 + total_return) ** (1.0 / years) - 1.0
        volatility = _stdev(daily_returns) * math.sqrt(252) if daily_returns else 0.0
        sharpe = annual_return / volatility if volatility > 0 else 0.0
        turnover = sum(trade.notional for trade in self.trades) / max(start_equity, 1.0)

        benchmark_return = None
        first_benchmark = next((row["benchmark"] for row in curve if row["benchmark"] != ""), None)
        last_benchmark = next((row["benchmark"] for row in reversed(curve) if row["benchmark"] != ""), None)
        if first_benchmark and last_benchmark:
            benchmark_return = float(last_benchmark) / float(first_benchmark) - 1.0

        return {
            "start": curve[0]["date"],
            "end": curve[-1]["date"],
            "initial_cash": round(start_equity, 2),
            "final_equity": round(end_equity, 2),
            "total_return": round(total_return, 6),
            "annual_return": round(annual_return, 6),
            "annual_volatility": round(volatility, 6),
            "sharpe_like": round(sharpe, 6),
            "max_drawdown": round(max_drawdown, 6),
            "trade_count": len(self.trades),
            "turnover": round(turnover, 6),
            "benchmark_return": round(benchmark_return, 6) if benchmark_return is not None else None,
            "blocked_events": len(self.blocked),
        }


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((item - mean) ** 2 for item in values) / (len(values) - 1)
    return math.sqrt(variance)


def save_backtest_result(result: BacktestResult, out_dir: str | Path) -> None:
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)

    with (output / "equity_curve.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["date", "cash", "market_value", "equity", "positions", "benchmark"],
        )
        writer.writeheader()
        writer.writerows(result.equity_curve)

    with (output / "trades.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["date", "symbol", "side", "price", "shares", "notional", "fees", "reason"],
        )
        writer.writeheader()
        for trade in result.trades:
            writer.writerow(
                {
                    "date": format_date(trade.date),
                    "symbol": trade.symbol,
                    "side": trade.side,
                    "price": f"{trade.price:.4f}",
                    "shares": trade.shares,
                    "notional": f"{trade.notional:.4f}",
                    "fees": f"{trade.fees:.4f}",
                    "reason": trade.reason,
                }
            )

    with (output / "summary.json").open("w", encoding="utf-8") as fh:
        json.dump(result.summary, fh, ensure_ascii=False, indent=2)

    report_lines = [
        "# AQT Backtest Report",
        "",
        f"- Period: {result.summary['start']} to {result.summary['end']}",
        f"- Initial cash: {result.summary['initial_cash']:,.2f}",
        f"- Final equity: {result.summary['final_equity']:,.2f}",
        f"- Total return: {result.summary['total_return']:.2%}",
        f"- Annual return: {result.summary['annual_return']:.2%}",
        f"- Max drawdown: {result.summary['max_drawdown']:.2%}",
        f"- Sharpe-like ratio: {result.summary['sharpe_like']:.2f}",
        f"- Trade count: {result.summary['trade_count']}",
        f"- Turnover: {result.summary['turnover']:.2f}x",
        f"- Benchmark return: {_format_optional_pct(result.summary['benchmark_return'])}",
        "",
        "This report is for research and learning only. It is not investment advice.",
        "",
    ]
    (output / "report.md").write_text("\n".join(report_lines), encoding="utf-8")


def _format_optional_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2%}"
