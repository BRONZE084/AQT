from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..data import DataStore
from ..math_utils import bollinger_bands, rsi
from ..models import Signal
from ..strategy import Strategy


@dataclass(frozen=True)
class MeanReversionConfig:
    rsi_period: int = 14
    bb_period: int = 20
    bb_std: float = 2.0
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    min_listed_days: int = 252
    min_amount: float = 20_000_000.0
    stop_loss_pct: float = 0.03
    take_profit_pct: float = 0.05
    max_positions: int = 5


class MeanReversionStrategy(Strategy):
    @property
    def mode(self) -> str:
        return "signal"

    def __init__(self, config: MeanReversionConfig | None = None) -> None:
        self.config = config or MeanReversionConfig()

    def select(self, store: DataStore, as_of: date) -> list[Signal]:
        cfg = self.config
        signals: list[Signal] = []

        for symbol in store.symbols_as_of(as_of):
            security = store.universe[symbol]
            if (as_of - security.list_date).days < cfg.min_listed_days:
                continue

            bars_needed = max(cfg.rsi_period + 1, cfg.bb_period)
            bars = store.bars_until(symbol, as_of, bars_needed)
            if len(bars) < bars_needed:
                continue
            latest = bars[-1]
            if latest.paused or latest.is_st or latest.amount < cfg.min_amount:
                continue

            closes = [bar.close for bar in bars]
            rsi_value = rsi(closes, cfg.rsi_period)
            middle, upper, lower = bollinger_bands(closes, cfg.bb_period, cfg.bb_std)
            if middle <= 0:
                continue

            direction = ""
            score = 0.0
            if rsi_value < cfg.rsi_oversold and latest.close <= lower * 1.02:
                direction = "buy"
                score = 1.0 - rsi_value / 100.0
            elif rsi_value > cfg.rsi_overbought and latest.close >= upper * 0.98:
                direction = "sell"
                score = rsi_value / 100.0

            if direction:
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
