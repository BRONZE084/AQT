from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .data import DataStore, format_date
from .models import Position
from .rules import round_lot
from .strategy import Strategy


@dataclass(frozen=True)
class PlanConfig:
    cash: float
    max_weight: float = 0.15
    cash_buffer: float = 0.02


def load_positions(path: str | Path | None) -> dict[str, Position]:
    if path is None:
        return {}
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"Positions file not found: {input_path}")
    positions: dict[str, Position] = {}
    with input_path.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            shares = int(float(row["shares"]))
            available = int(float(row.get("available_shares") or shares))
            positions[row["symbol"]] = Position(row["symbol"], shares, available)
    return positions


def save_positions_template(path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["symbol", "shares", "available_shares"])
        writer.writeheader()
        writer.writerow({"symbol": "600001", "shares": 0, "available_shares": 0})


def generate_trade_plan(
    store: DataStore,
    strategy: Strategy,
    config: PlanConfig,
    out_dir: str | Path,
    as_of: date | None = None,
    positions: dict[str, Position] | None = None,
) -> Path:
    positions = positions or {}
    as_of = as_of or store.latest_date()
    signals = strategy.select(store, as_of)
    selected = [signal.symbol for signal in signals]
    selected_set = set(selected)

    equity = config.cash
    for symbol, position in positions.items():
        bar = store.last_bar(symbol, as_of)
        if bar is not None:
            equity += position.shares * bar.close

    target_weight = min(
        config.max_weight,
        max(0.0, (1.0 - config.cash_buffer) / len(selected)) if selected else 0.0,
    )
    rows = []
    all_symbols = sorted(set(positions) | selected_set)
    for symbol in all_symbols:
        security = store.universe.get(symbol)
        bar = store.last_bar(symbol, as_of)
        if bar is None:
            continue
        current = positions.get(symbol).shares if symbol in positions else 0
        target_value = equity * target_weight if symbol in selected_set else 0.0
        target = round_lot(target_value / bar.close) if bar.close > 0 else 0
        delta = target - current
        if delta > 0:
            action = "buy"
        elif delta < 0:
            action = "sell"
        else:
            action = "hold"

        notes = []
        if bar.paused:
            notes.append("paused on as_of date")
        if bar.is_st:
            notes.append("ST on as_of date")
        if action == "buy" and bar.close >= bar.limit_up - 1e-6:
            notes.append("closed at limit up; recheck before buying")
        if action == "sell" and bar.close <= bar.limit_down + 1e-6:
            notes.append("closed at limit down; recheck before selling")

        rows.append(
            {
                "as_of": format_date(as_of),
                "symbol": symbol,
                "name": security.name if security else "",
                "industry": security.industry if security else "",
                "action": action,
                "current_shares": current,
                "target_shares": target,
                "delta_shares": delta,
                "reference_close": f"{bar.close:.4f}",
                "estimated_notional": f"{abs(delta) * bar.close:.4f}",
                "notes": "; ".join(notes),
            }
        )

    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"trade_plan_{as_of:%Y%m%d}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "as_of",
                "symbol",
                "name",
                "industry",
                "action",
                "current_shares",
                "target_shares",
                "delta_shares",
                "reference_close",
                "estimated_notional",
                "notes",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    return path

