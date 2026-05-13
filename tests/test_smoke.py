import tempfile
import unittest
from datetime import date
from pathlib import Path

from aqt.backtest import BacktestConfig, Backtester
from aqt.data import DataStore, generate_sample_data
from aqt.planner import PlanConfig, generate_trade_plan
from aqt.strategy import MultiFactorConfig, MultiFactorStrategy


class SmokeTest(unittest.TestCase):
    def test_sample_backtest_and_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            out_dir = root / "reports"
            generate_sample_data(data_dir, date(2022, 1, 4), date(2024, 12, 31))
            store = DataStore.load(data_dir)
            strategy = MultiFactorStrategy(MultiFactorConfig(top_n=5))
            result = Backtester(
                store,
                strategy,
                BacktestConfig(date(2023, 7, 3), date(2024, 12, 31), 1_000_000),
            ).run()
            self.assertGreater(len(result.equity_curve), 100)
            self.assertIn("total_return", result.summary)
            plan_path = generate_trade_plan(store, strategy, PlanConfig(1_000_000), out_dir)
            self.assertTrue(plan_path.exists())


if __name__ == "__main__":
    unittest.main()

