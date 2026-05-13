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


@dataclass(frozen=True)
class Signal:
    symbol: str
    score: float
    momentum: float
    volatility: float
    value: float
    quality: float

