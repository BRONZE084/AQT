from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

from .data import DataStore
from .math_utils import _rank, _stdev
from .models import Signal


@dataclass(frozen=True)
class MultiFactorConfig:
    top_n: int = 8
    lookback: int = 60
    min_listed_days: int = 252
    min_amount: float = 20_000_000.0
    momentum_weight: float = 0.35
    volatility_weight: float = 0.25
    value_weight: float = 0.20
    quality_weight: float = 0.20
    max_per_industry: int = 0       # 0 = no limit, N = max N stocks per industry
    blacklist: tuple[str, ...] = ()  # symbols to always skip


class Strategy(ABC):
    @property
    def mode(self) -> str:
        return "rebalance"

    @abstractmethod
    def select(self, store: DataStore, as_of: date) -> list[Signal]:
        ...


class MultiFactorStrategy(Strategy):
    def __init__(self, config: MultiFactorConfig | None = None) -> None:
        self.config = config or MultiFactorConfig()

    def select(self, store: DataStore, as_of: date) -> list[Signal]:
        cfg = self.config
        candidates: dict[str, dict[str, float]] = {}

        for symbol in store.symbols_as_of(as_of):
            if symbol in cfg.blacklist:
                continue
            security = store.universe[symbol]
            if (as_of - security.list_date).days < cfg.min_listed_days:
                continue

            bars = store.bars_until(symbol, as_of, cfg.lookback + 1)
            if len(bars) < cfg.lookback + 1:
                continue
            latest = bars[-1]
            if latest.paused or latest.is_st or latest.amount < cfg.min_amount:
                continue

            fundamental = store.latest_fundamental(symbol, as_of)
            if fundamental is None or fundamental.pe_ttm <= 0 or fundamental.pb <= 0:
                continue

            closes = [bar.close for bar in bars]
            returns = [
                closes[idx] / closes[idx - 1] - 1.0
                for idx in range(1, len(closes))
                if closes[idx - 1] > 0
            ]
            if not returns:
                continue

            momentum = closes[-1] / closes[0] - 1.0
            volatility = _stdev(returns)
            value = 0.5 * (1.0 / fundamental.pe_ttm) + 0.5 * (1.0 / fundamental.pb)
            quality = fundamental.roe_ttm

            candidates[symbol] = {
                "momentum": momentum,
                "volatility": volatility,
                "value": value,
                "quality": quality,
            }

        momentum_rank = _rank({k: v["momentum"] for k, v in candidates.items()}, True)
        volatility_rank = _rank({k: v["volatility"] for k, v in candidates.items()}, False)
        value_rank = _rank({k: v["value"] for k, v in candidates.items()}, True)
        quality_rank = _rank({k: v["quality"] for k, v in candidates.items()}, True)

        signals: list[Signal] = []
        for symbol, values in candidates.items():
            score = (
                cfg.momentum_weight * momentum_rank[symbol]
                + cfg.volatility_weight * volatility_rank[symbol]
                + cfg.value_weight * value_rank[symbol]
                + cfg.quality_weight * quality_rank[symbol]
            )
            signals.append(
                Signal(
                    symbol=symbol,
                    score=score,
                    momentum=values["momentum"],
                    volatility=values["volatility"],
                    value=values["value"],
                    quality=values["quality"],
                )
            )

        sorted_signals = sorted(signals, key=lambda item: item.score, reverse=True)
        if cfg.max_per_industry <= 0:
            return sorted_signals[: cfg.top_n]

        # Industry cap: pick top-N signals respecting per-industry limit
        selected: list[Signal] = []
        industry_counts: dict[str, int] = {}
        for sig in sorted_signals:
            sec = store.universe.get(sig.symbol)
            ind = (sec.industry or "其他") if sec else "其他"
            if industry_counts.get(ind, 0) >= cfg.max_per_industry:
                continue
            selected.append(sig)
            industry_counts[ind] = industry_counts.get(ind, 0) + 1
            if len(selected) >= cfg.top_n:
                break
        return selected

