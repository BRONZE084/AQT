"""Grid-search parameter optimizer for AQT strategies.

Usage:
    from aqt.data import DataStore
    from aqt.backtest import BacktestConfig
    from aqt.optimizer import GridSearch

    store = DataStore.load("data/sample")
    base = BacktestConfig(start=..., end=..., initial_cash=1_000_000)
    grid = {"top_n": [5, 8, 10], "lookback": [30, 60, 90]}
    gs = GridSearch(store, "multi_factor", grid, base, metric="sharpe_like")
    for r in gs.run(top_n=10):
        print(r.params, r.metrics["total_return"])
"""

from __future__ import annotations

import itertools
import sys
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .backtest import BacktestConfig, Backtester
from .data import DataStore
from .strategies import STRATEGY_REGISTRY


@dataclass
class OptimizeResult:
    params: dict[str, Any]
    metrics: dict[str, float]
    rank: int = 0


class GridSearch:
    """Enumerate parameter combinations, run backtests, rank by metric."""

    def __init__(
        self,
        store: DataStore,
        strategy_name: str,
        param_grid: dict[str, list],
        base_config: BacktestConfig,
        metric: str = "sharpe_like",
    ) -> None:
        if strategy_name not in STRATEGY_REGISTRY:
            raise ValueError(f"Unknown strategy: {strategy_name}")
        self.store = store
        self.strategy_name = strategy_name
        self.param_grid = param_grid
        self.base_config = base_config
        self.metric = metric

    def run(self, top_n: int = 20) -> list[OptimizeResult]:
        """Run grid search and return top-N results sorted by metric desc."""
        keys = list(self.param_grid)
        combos = list(itertools.product(*(self.param_grid[k] for k in keys)))
        total = len(combos)

        entry = STRATEGY_REGISTRY[self.strategy_name]
        config_cls = entry["config_cls"]
        strategy_cls = entry["cls"]

        results: list[OptimizeResult] = []

        for idx, combo in enumerate(combos):
            params = dict(zip(keys, combo))
            try:
                config = config_cls(**params)
            except TypeError as exc:
                print(f"  skip {params}: {exc}", file=sys.stderr)
                continue

            strategy = strategy_cls(config)
            bt = Backtester(self.store, strategy, self.base_config)
            result = bt.run()
            metrics = result.summary

            metric_value = metrics.get(self.metric, 0)
            results.append(
                OptimizeResult(params=params, metrics=metrics, rank=0)
            )

            if total > 1 and (idx % max(1, total // 10) == 0 or idx == total - 1):
                pct = (idx + 1) * 100 // total
                print(f"  [{pct}%] {idx + 1}/{total} combos — best {self.metric}={max(r.metrics.get(self.metric, -999) for r in results):.4f}")

        results.sort(key=lambda r: r.metrics.get(self.metric, -999), reverse=True)
        for i, r in enumerate(results[:top_n]):
            r.rank = i + 1

        return results[:top_n]

    @staticmethod
    def print_table(results: list[OptimizeResult], top_n: int = 10) -> None:
        """Print a formatted table of optimization results."""
        if not results:
            print("No results.")
            return

        metric_keys = [k for k in results[0].metrics if isinstance(results[0].metrics[k], (int, float))]
        param_keys = list(results[0].params)

        # Build compact row strings
        header = "  " + " | ".join(
            ["#"] + param_keys + [k.replace("_", " ")[:12] for k in metric_keys[:6]]
        )
        print(header)
        print("  " + "-" * len(header))

        for r in results[:top_n]:
            param_str = " | ".join(str(r.params[k]) for k in param_keys)
            metric_str = " | ".join(
                f"{r.metrics[k]:.4f}" if isinstance(r.metrics[k], float)
                else str(r.metrics[k])[:12]
                for k in metric_keys[:6]
            )
            print(f"  #{r.rank:<2d} | {param_str} | {metric_str}")
