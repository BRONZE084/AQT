from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .backtest import BacktestConfig, Backtester, save_backtest_result
from .data import DataStore, generate_sample_data, parse_date
from .planner import PlanConfig, generate_trade_plan, load_positions, save_positions_template
from .strategy import MultiFactorConfig, MultiFactorStrategy


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="aqt",
        description="A-share quant toolkit for learning, backtesting, and trade planning.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-sample", help="Generate deterministic sample A-share data.")
    init_parser.add_argument("--data-dir", default="data/sample")
    init_parser.add_argument("--start", default="2022-01-04")
    init_parser.add_argument("--end", default="2024-12-31")

    backtest_parser = subparsers.add_parser("backtest", help="Run the monthly multi-factor backtest.")
    backtest_parser.add_argument("--data-dir", default="data/sample")
    backtest_parser.add_argument("--out-dir", default="reports/demo")
    backtest_parser.add_argument("--start", required=True)
    backtest_parser.add_argument("--end", required=True)
    backtest_parser.add_argument("--cash", type=float, default=1_000_000.0)
    backtest_parser.add_argument("--top-n", type=int, default=8)
    backtest_parser.add_argument("--max-weight", type=float, default=0.15)
    backtest_parser.add_argument("--cash-buffer", type=float, default=0.02)
    backtest_parser.add_argument("--min-amount", type=float, default=20_000_000.0)

    plan_parser = subparsers.add_parser("plan", help="Generate a manual trade plan from latest data.")
    plan_parser.add_argument("--data-dir", default="data/sample")
    plan_parser.add_argument("--out-dir", default="reports/demo")
    plan_parser.add_argument("--as-of")
    plan_parser.add_argument("--cash", type=float, default=1_000_000.0)
    plan_parser.add_argument("--positions-file")
    plan_parser.add_argument("--top-n", type=int, default=8)
    plan_parser.add_argument("--max-weight", type=float, default=0.15)
    plan_parser.add_argument("--cash-buffer", type=float, default=0.02)
    plan_parser.add_argument("--min-amount", type=float, default=20_000_000.0)

    template_parser = subparsers.add_parser("positions-template", help="Create a positions CSV template.")
    template_parser.add_argument("--path", default="data/positions_template.csv")

    args = parser.parse_args()

    if args.command == "init-sample":
        start = parse_date(args.start)
        end = parse_date(args.end)
        generate_sample_data(args.data_dir, start, end)
        print(f"Sample data written to {Path(args.data_dir).resolve()}")
        return

    if args.command == "positions-template":
        save_positions_template(args.path)
        print(f"Positions template written to {Path(args.path).resolve()}")
        return

    if args.command == "backtest":
        store = DataStore.load(args.data_dir)
        strategy = MultiFactorStrategy(
            MultiFactorConfig(top_n=args.top_n, min_amount=args.min_amount)
        )
        config = BacktestConfig(
            start=parse_date(args.start),
            end=parse_date(args.end),
            initial_cash=args.cash,
            max_weight=args.max_weight,
            cash_buffer=args.cash_buffer,
        )
        result = Backtester(store, strategy, config).run()
        save_backtest_result(result, args.out_dir)
        print(f"Backtest report written to {Path(args.out_dir).resolve()}")
        print(f"Total return: {result.summary['total_return']:.2%}")
        print(f"Max drawdown: {result.summary['max_drawdown']:.2%}")
        return

    if args.command == "plan":
        store = DataStore.load(args.data_dir)
        strategy = MultiFactorStrategy(
            MultiFactorConfig(top_n=args.top_n, min_amount=args.min_amount)
        )
        positions = load_positions(args.positions_file)
        as_of: date | None = parse_date(args.as_of) if args.as_of else None
        plan_path = generate_trade_plan(
            store=store,
            strategy=strategy,
            config=PlanConfig(args.cash, args.max_weight, args.cash_buffer),
            out_dir=args.out_dir,
            as_of=as_of,
            positions=positions,
        )
        print(f"Trade plan written to {plan_path.resolve()}")
        return

