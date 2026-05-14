from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .backtest import BacktestConfig, Backtester, save_backtest_result
from .data import DataStore, generate_sample_data, parse_date
from .fetcher import fetch_daily, fetch_init
from .planner import PlanConfig, generate_trade_plan, load_positions, save_positions_template
from .strategies import STRATEGY_REGISTRY
from .strategy import MultiFactorConfig, MultiFactorStrategy
from .web import run_server


def _build_strategy(name: str, args: argparse.Namespace):
    entry = STRATEGY_REGISTRY[name]
    config_cls = entry["config_cls"]
    strategy_cls = entry["cls"]
    if name == "multi_factor":
        config = config_cls(top_n=args.top_n, min_amount=args.min_amount)
    else:
        config = config_cls()
    return strategy_cls(config)


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
    backtest_parser.add_argument("--rebalance-freq", choices=["daily", "weekly", "monthly"], default="monthly")
    backtest_parser.add_argument("--stop-loss", type=float, default=0.0)
    backtest_parser.add_argument("--take-profit", type=float, default=0.0)
    backtest_parser.add_argument(
        "--strategy", choices=list(STRATEGY_REGISTRY), default="multi_factor"
    )

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
    plan_parser.add_argument(
        "--strategy", choices=list(STRATEGY_REGISTRY), default="multi_factor"
    )

    template_parser = subparsers.add_parser("positions-template", help="Create a positions CSV template.")
    template_parser.add_argument("--path", default="data/positions_template.csv")

    fetch_parser = subparsers.add_parser("fetch", help="Fetch latest A-share daily data from EastMoney.")
    fetch_parser.add_argument("--data-dir", default="data/live", help="Target data directory.")
    fetch_parser.add_argument("--init", action="store_true", help="Initial fetch with CSI 300 history (otherwise incremental).")

    ui_parser = subparsers.add_parser("ui", help="Start the local browser UI.")
    ui_parser.add_argument("--host", default="127.0.0.1")
    ui_parser.add_argument("--port", type=int, default=8765)
    ui_parser.add_argument("--open", action="store_true", help="Open the UI in the default browser.")

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

    if args.command == "fetch":
        if args.init:
            fetch_init(args.data_dir)
        else:
            fetch_daily(args.data_dir)
        return

    if args.command == "ui":
        run_server(args.host, args.port, args.open)
        return

    if args.command == "backtest":
        store = DataStore.load(args.data_dir)
        strategy = _build_strategy(args.strategy, args)
        config = BacktestConfig(
            start=parse_date(args.start),
            end=parse_date(args.end),
            initial_cash=args.cash,
            max_weight=args.max_weight,
            cash_buffer=args.cash_buffer,
            rebalance_freq=args.rebalance_freq,
            stop_loss_pct=args.stop_loss,
            take_profit_pct=args.take_profit,
        )
        result = Backtester(store, strategy, config).run()
        save_backtest_result(result, args.out_dir)
        print(f"Backtest report written to {Path(args.out_dir).resolve()}")
        print(f"Total return: {result.summary['total_return']:.2%}")
        print(f"Max drawdown: {result.summary['max_drawdown']:.2%}")
        return

    if args.command == "plan":
        store = DataStore.load(args.data_dir)
        strategy = _build_strategy(args.strategy, args)
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
