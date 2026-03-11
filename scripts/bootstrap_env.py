#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


DEFAULTS = {
    "AGENTIC_FACTORY_MODE": "full",
    "FACTORY_ROOT": "data/factory",
    "FACTORY_GOLDFISH_ROOT": "research/goldfish",
    "FACTORY_PAPER_GATE_MONTHLY_ROI_PCT": "5.0",
    "FACTORY_PAPER_GATE_MAX_DRAWDOWN_PCT": "8.0",
    "FACTORY_PAPER_GATE_MIN_DAYS": "30",
    "FACTORY_PAPER_GATE_MIN_FAST_TRADES": "50",
    "FACTORY_PAPER_GATE_MIN_SLOW_SETTLED": "10",
    "FACTORY_EXECUTION_AUTOSTART_ENABLED": "false",
    "RESEARCH_FACTORY_PORTFOLIO_ID": "research_factory",
    "PORTFOLIO_STATE_ROOT": "data/portfolios",
    "PREDICTION_MODEL_KINDS": "hybrid_logit,market_calibrated",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a local .env for AgenticTrading extraction mode.")
    parser.add_argument("--execution-repo-root", required=True, help="Absolute path to the execution repo.")
    parser.add_argument("--output", default=".env", help="Target .env path.")
    args = parser.parse_args()

    execution_root = Path(args.execution_repo_root).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    rows = [f"{key}={value}" for key, value in DEFAULTS.items()]
    rows.append(f"EXECUTION_REPO_ROOT={execution_root}")
    rows.append(f"EXECUTION_PORTFOLIO_STATE_ROOT={execution_root / 'data' / 'portfolios'}")
    output.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
