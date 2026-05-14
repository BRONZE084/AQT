from __future__ import annotations

from ..strategy import MultiFactorConfig, MultiFactorStrategy
from .breakout import BreakoutConfig, BreakoutStrategy
from .ma_crossover import MACrossoverConfig, MACrossoverStrategy
from .mean_reversion import MeanReversionConfig, MeanReversionStrategy

STRATEGY_REGISTRY: dict[str, dict] = {
    "multi_factor": {
        "cls": MultiFactorStrategy,
        "config_cls": MultiFactorConfig,
        "label": "多因子轮动",
    },
    "ma_crossover": {
        "cls": MACrossoverStrategy,
        "config_cls": MACrossoverConfig,
        "label": "均线交叉",
    },
    "breakout": {
        "cls": BreakoutStrategy,
        "config_cls": BreakoutConfig,
        "label": "通道突破",
    },
    "mean_reversion": {
        "cls": MeanReversionStrategy,
        "config_cls": MeanReversionConfig,
        "label": "均值回归",
    },
}

__all__ = [
    "BreakoutConfig",
    "BreakoutStrategy",
    "MACrossoverConfig",
    "MACrossoverStrategy",
    "MeanReversionConfig",
    "MeanReversionStrategy",
    "STRATEGY_REGISTRY",
]
