from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Bar:
    date: date
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
    paused: bool
    is_st: bool
    limit_up: float
    limit_down: float


@dataclass(frozen=True)
class Fundamental:
    date: date
    symbol: str
    pe_ttm: float
    pb: float
    roe_ttm: float


@dataclass(frozen=True)
class Security:
    symbol: str
    name: str
    board: str
    industry: str
    list_date: date


@dataclass
class Position:
    symbol: str
    shares: int
    available_shares: int = 0
    cost_basis: float = 0.0
    entry_date: date | None = None
    highest_close: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0


@dataclass(frozen=True)
class Trade:
    date: date
    symbol: str
    side: str
    price: float
    shares: int
    notional: float
    fees: float
    reason: str
    pnl: float = 0.0


@dataclass(frozen=True)
class Signal:
    symbol: str
    score: float
    momentum: float = 0.0
    volatility: float = 0.0
    value: float = 0.0
    quality: float = 0.0
    direction: str = ""
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    metadata: dict | None = None

