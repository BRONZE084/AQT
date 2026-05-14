from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..data import DataStore
from ..math_utils import sma
from ..models import Signal
from ..strategy import Strategy


@dataclass(frozen=True)
class MACrossoverConfig:
    short_window: int = 5
    long_window: int = 20
    min_listed_days: int = 252
    min_amount: float = 20_000_000.0
    stop_loss_pct: float = 0.03
    take_profit_pct: float = 0.06
    max_positions: int = 5


class MACrossoverStrategy(Strategy):
    @property
    def mode(self) -> str:
        return "signal"

    def __init__(self, config: MACrossoverConfig | None = None) -> None:
        self.config = config or MACrossoverConfig()

    def select(self, store: DataStore, as_of: date) -> list[Signal]:
        cfg = self.config
        signals: list[Signal] = []

        for symbol in store.symbols_as_of(as_of):
            security = store.universe[symbol]
            if (as_of - security.list_date).days < cfg.min_listed_days:
                continue

            bars_needed = cfg.long_window + 2
            bars = store.bars_until(symbol, as_of, bars_needed)
            if len(bars) < bars_needed:
                continue
            latest = bars[-1]
            if latest.paused or latest.is_st or latest.amount < cfg.min_amount:
                continue

            closes = [bar.close for bar in bars]
            short_vals = sma(closes, cfg.short_window)
            long_vals = sma(closes, cfg.long_window)
            if len(short_vals) < 2 or len(long_vals) < 2:
                continue

            short_prev = short_vals[-2]
            long_prev = long_vals[-2]
            short_curr = short_vals[-1]
            long_curr = long_vals[-1]

            direction = ""
            if short_prev <= long_prev and short_curr > long_curr:
                direction = "buy"
            elif short_prev >= long_prev and short_curr < long_curr:
                direction = "sell"

            if direction:
                score = short_curr / long_curr if long_curr > 0 else 1.0
                signals.append(
                    Signal(
                        symbol=symbol,
                        score=score,
                        direction=direction,
                        stop_loss_pct=cfg.stop_loss_pct,
                        take_profit_pct=cfg.take_profit_pct,
                    )
                )

        return sorted(signals, key=lambda s: s.score, reverse=True)[: cfg.max_positions]
