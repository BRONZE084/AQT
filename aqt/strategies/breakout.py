from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..data import DataStore
from ..math_utils import sma
from ..models import Signal
from ..strategy import Strategy


@dataclass(frozen=True)
class BreakoutConfig:
    lookback: int = 20
    volume_confirm: bool = True
    min_listed_days: int = 252
    min_amount: float = 20_000_000.0
    stop_loss_pct: float = 0.03
    take_profit_pct: float = 0.09
    max_positions: int = 5


class BreakoutStrategy(Strategy):
    @property
    def mode(self) -> str:
        return "signal"

    def __init__(self, config: BreakoutConfig | None = None) -> None:
        self.config = config or BreakoutConfig()

    def select(self, store: DataStore, as_of: date) -> list[Signal]:
        cfg = self.config
        signals: list[Signal] = []

        for symbol in store.symbols_as_of(as_of):
            security = store.universe[symbol]
            if (as_of - security.list_date).days < cfg.min_listed_days:
                continue

            bars = store.bars_until(symbol, as_of, cfg.lookback + 1)
            if len(bars) < cfg.lookback + 1:
                continue
            latest = bars[-1]
            if latest.paused or latest.is_st or latest.amount < cfg.min_amount:
                continue

            prev_bars = bars[:-1]
            highest = max(bar.high for bar in prev_bars)
            lowest = max(bar.low for bar in prev_bars)  # yes: floor of lows

            direction = ""
            if latest.close > highest:
                vol_ok = True
                if cfg.volume_confirm:
                    avg_vol = sma([bar.volume for bar in bars], cfg.lookback)
                    if avg_vol:
                        vol_ok = latest.volume > avg_vol[-1] * 1.2
                if vol_ok:
                    direction = "buy"
            elif latest.close < lowest:
                direction = "sell"

            if direction:
                score = latest.close / highest if highest > 0 else 1.0
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
