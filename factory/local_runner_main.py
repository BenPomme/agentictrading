"""CLI entrypoint for embedded portfolio runners: python -m factory.local_runner_main --portfolio <id>."""

from __future__ import annotations

import argparse
import sys

from factory.local_runner_base import LocalPortfolioRunner, StubLocalRunner


def get_runner(portfolio_id: str) -> LocalPortfolioRunner:
    """Return the runner implementation for this portfolio; default to stub."""
    return StubLocalRunner(portfolio_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run embedded paper portfolio runner")
    parser.add_argument("--portfolio", required=True, help="Portfolio ID")
    parser.add_argument("--interval", type=float, default=60.0, help="Cycle interval seconds")
    args = parser.parse_args()
    runner = get_runner(args.portfolio)
    runner.run(cycle_interval_sec=args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
