from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .data import DataStore, format_date
from .math_utils import _stdev
from .models import Position, Trade
from .rules import FeeModel, can_buy, can_sell, is_trade_too_small, round_lot
from .strategy import Strategy


@dataclass(frozen=True)
class BacktestConfig:
    start: date
    end: date
    initial_cash: float = 1_000_000.0
    max_weight: float = 0.15
    cash_buffer: float = 0.02
    rebalance_freq: str = "monthly"
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    trailing_stop_pct: float = 0.0
    max_positions: int = 10


@dataclass
class BacktestResult:
    equity_curve: list[dict]
    trades: list[Trade]
    summary: dict


class Backtester:
    def __init__(
        self,
        store: DataStore,
        strategy: Strategy,
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
        self.small_trade_skips: int = 0

    def run(self) -> BacktestResult:
        dates = self.store.date_range(self.config.start, self.config.end)
        if not dates:
            raise ValueError("No trading dates in selected range.")

        equity_curve: list[dict] = []
        last_rebalance_date: date | None = None

        for current in dates:
            for position in self.positions.values():
                position.available_shares = position.shares
                if position.available_shares > 0:
                    bar = self.store.bar(position.symbol, current) or self.store.last_bar(position.symbol, current)
                    if bar is not None and bar.close > position.highest_close:
                        position.highest_close = bar.close

            self._check_exit_conditions(current)

            if self.strategy.mode == "signal":
                as_of = self.store.previous_date(current)
                if as_of is not None:
                    self._process_signals(current, as_of)
            elif self._should_rebalance(current, last_rebalance_date):
                as_of = self.store.previous_date(current)
                if as_of is not None:
                    self._rebalance(current, as_of)
                    last_rebalance_date = current

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

    def _should_rebalance(self, current: date, last: date | None) -> bool:
        freq = self.config.rebalance_freq
        if freq == "daily":
            return True
        if freq == "weekly":
            if last is None:
                return True
            return current.isocalendar().week != last.isocalendar().week
        if last is None:
            return True
        return (current.year, current.month) != (last.year, last.month)

    def _check_exit_conditions(self, trade_date: date) -> None:
        cfg = self.config
        for symbol, position in list(self.positions.items()):
            if position.shares <= 0 or position.available_shares <= 0:
                continue
            bar = self.store.bar(symbol, trade_date)
            if bar is None or not can_sell(bar):
                continue

            exit_reason = ""
            stop_price = (position.stop_loss_price if position.stop_loss_price > 0
                          else position.cost_basis * (1 - cfg.stop_loss_pct) if cfg.stop_loss_pct > 0
                          else 0.0)
            profit_price = (position.take_profit_price if position.take_profit_price > 0
                            else position.cost_basis * (1 + cfg.take_profit_pct) if cfg.take_profit_pct > 0
                            else 0.0)
            trailing_price = (position.highest_close * (1 - cfg.trailing_stop_pct)
                              if cfg.trailing_stop_pct > 0 and position.highest_close > 0
                              else 0.0)

            if stop_price > 0 and bar.open <= stop_price:
                exit_reason = "stop_loss"
            elif profit_price > 0 and bar.open >= profit_price:
                exit_reason = "take_profit"
            elif trailing_price > 0 and bar.open <= trailing_price:
                exit_reason = "trailing_stop"

            if exit_reason:
                shares = min(position.shares, position.available_shares)
                shares = round_lot(shares)
                if shares > 0:
                    self._execute_sell(trade_date, symbol, shares, bar.open, exit_reason)

    def _process_signals(self, trade_date: date, as_of: date) -> None:
        signals = self.strategy.select(self.store, as_of)
        active_positions = sum(1 for p in self.positions.values() if p.shares > 0)

        for signal in signals:
            has_position = signal.symbol in self.positions and self.positions[signal.symbol].shares > 0

            if signal.direction == "sell" and has_position:
                position = self.positions[signal.symbol]
                bar = self.store.bar(signal.symbol, trade_date)
                if bar is None or not can_sell(bar):
                    continue
                shares = min(position.shares, position.available_shares)
                shares = round_lot(shares)
                if shares > 0:
                    self._execute_sell(trade_date, signal.symbol, shares, bar.open, "exit_signal")

            elif signal.direction == "buy" and not has_position:
                if active_positions >= self.config.max_positions:
                    continue
                bar = self.store.bar(signal.symbol, trade_date)
                if bar is None or not can_buy(bar):
                    continue
                position_cash = self.cash / max(1, self.config.max_positions - active_positions)
                wanted = round_lot(position_cash / bar.open)
                shares = self._affordable_shares(wanted, bar.open)
                if shares <= 0:
                    continue
                self._execute_buy(trade_date, signal.symbol, shares, bar.open, "entry_signal")
                if signal.stop_loss_pct > 0:
                    pos = self.positions[signal.symbol]
                    pos.stop_loss_price = pos.cost_basis * (1 - signal.stop_loss_pct)
                if signal.take_profit_pct > 0:
                    pos = self.positions[signal.symbol]
                    pos.take_profit_price = pos.cost_basis * (1 + signal.take_profit_pct)
                active_positions += 1

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

        buy_candidates: list[tuple[str, int, float]] = []
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
            if wanted <= 0:
                continue
            buy_candidates.append((symbol, wanted, bar.open))

        if buy_candidates:
            self._execute_scaled_buys(trade_date, buy_candidates)

    def _affordable_shares(self, wanted: int, price: float) -> int:
        shares = round_lot(wanted)
        while shares > 0:
            if is_trade_too_small(shares, price):
                self.small_trade_skips += 1
                return 0
            fees = self.fee_model.fees("buy", shares, price)
            if shares * price + fees <= self.cash:
                return shares
            shares -= 100
        return 0

    def _execute_scaled_buys(
        self, trade_date: date, candidates: list[tuple[str, int, float]]
    ) -> None:
        total_cost = 0.0
        for _symbol, wanted, price in candidates:
            shares = round_lot(wanted)
            if shares > 0:
                fees = self.fee_model.fees("buy", shares, price)
                total_cost += shares * price + fees

        scale = 1.0
        if total_cost > self.cash and total_cost > 0:
            scale = self.cash / total_cost

        for symbol, wanted, price in candidates:
            scaled = round_lot(int(wanted * scale))
            shares = self._affordable_shares(scaled if scaled > 0 else wanted, price)
            if shares <= 0:
                continue
            self._execute_buy(trade_date, symbol, shares, price, "rebalance")

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
        if position.entry_date is None:
            position.entry_date = trade_date
        if position.highest_close == 0.0:
            position.highest_close = price
        if position.stop_loss_price == 0.0 and self.config.stop_loss_pct > 0:
            position.stop_loss_price = position.cost_basis * (1 - self.config.stop_loss_pct)
        if position.take_profit_price == 0.0 and self.config.take_profit_pct > 0:
            position.take_profit_price = position.cost_basis * (1 + self.config.take_profit_pct)
        self.trades.append(Trade(trade_date, symbol, "buy", price, shares, notional, fees, reason))

    def _execute_sell(self, trade_date: date, symbol: str, shares: int, price: float, reason: str) -> None:
        position = self.positions[symbol]
        shares = min(shares, position.available_shares, position.shares)
        if shares <= 0:
            return
        fees = self.fee_model.fees("sell", shares, price)
        notional = shares * price
        pnl = (price - position.cost_basis) * shares - fees
        self.cash += notional - fees
        position.shares -= shares
        position.available_shares -= shares
        if position.shares <= 0:
            del self.positions[symbol]
        self.trades.append(Trade(trade_date, symbol, "sell", price, shares, notional, fees, reason, pnl))

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
        total_pnl = sum(trade.pnl for trade in self.trades if trade.side == "sell")

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
            "total_pnl": round(total_pnl, 4),
            "win_count": sum(1 for t in self.trades if t.side == "sell" and t.pnl > 0),
            "loss_count": sum(1 for t in self.trades if t.side == "sell" and t.pnl <= 0),
            "small_trade_skips": self.small_trade_skips,
        }


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
            fieldnames=["date", "symbol", "side", "price", "shares", "notional", "fees", "reason", "pnl"],
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
                    "pnl": f"{trade.pnl:.4f}",
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
