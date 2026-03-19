#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


DEFAULTS = {
    "AGENTIC_FACTORY_MODE": "full",
    "FACTORY_REAL_AGENTS_ENABLED": "true",
    "FACTORY_AGENT_PROVIDER_ORDER": "codex,deterministic",
    "FACTORY_AGENT_OLLAMA_FALLBACK_ENABLED": "false",
    "FACTORY_AGENT_ENABLED_FAMILIES": "",
    "FACTORY_AGENT_DEMO_FAMILY": "",
    "FACTORY_AGENT_OLLAMA_MODEL": "qwen2.5:32b",
    "FACTORY_AGENT_LOG_DIR": "data/factory/agent_runs",
    "FACTORY_AGENT_POST_EVAL_CRITIQUE_ENABLED": "false",
    "FACTORY_CHALLENGER_MUTATION_PCT": "80",
    "FACTORY_CHALLENGER_NEW_MODEL_PCT": "20",
    "FACTORY_AGENT_REVIEW_ENABLED": "true",
    "FACTORY_AGENT_REVIEW_MIN_FAST_DAYS": "14",
    "FACTORY_AGENT_REVIEW_MIN_FAST_TRADES": "50",
    "FACTORY_AGENT_REVIEW_MIN_SLOW_DAYS": "21",
    "FACTORY_AGENT_REVIEW_MIN_SLOW_SETTLED": "10",
    "FACTORY_AGENT_REVIEW_INTERVAL_DAYS": "7",
    "FACTORY_AGENT_REVIEW_INCREMENTAL_TRADES": "25",
    "FACTORY_FIRST_ASSESSMENT_DAYS": "2",
    "FACTORY_FIRST_ASSESSMENT_FAST_TRADES": "10",
    "FACTORY_FIRST_ASSESSMENT_SLOW_SETTLED": "4",
    "FACTORY_DEBUG_AGENT_ENABLED": "true",
    "FACTORY_DEBUG_AGENT_REVIEW_INTERVAL_HOURS": "12",
    "FACTORY_MAINTENANCE_AGENT_MAX_ITEMS_PER_CYCLE": "3",
    "FACTORY_MAINTENANCE_AGENT_REVIEW_INTERVAL_HOURS": "12",
    "FACTORY_MAINTENANCE_QUEUE_REVIEW_COOLDOWN_HOURS": "12",
    "FACTORY_MAINTENANCE_QUEUE_MAX_PER_FAMILY": "3",
    "FACTORY_STALLED_MODEL_HOURS": "8",
    "FACTORY_STALLED_MODEL_MIN_SCANS": "25",
    "FACTORY_TRAINABILITY_GRACE_HOURS": "6.0",
    "FACTORY_RUNTIME_LANE_MIN_SCORE_GAP": "3.0",
    "FACTORY_RUNTIME_LANE_MIN_PAPER_DAYS": "7",
    "FACTORY_RUNTIME_LANE_MIN_TRADE_COUNT": "10",
    "FACTORY_RUNTIME_FIRST_READ_MIN_ROI_PCT": "1.0",
    "FACTORY_RUNTIME_FIRST_READ_MIN_RESEARCH_TRADES": "25",
    "FACTORY_RUNTIME_ALIAS_STALE_HOURS": "4.0",
    "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES": "10",
    "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES_PER_FAMILY": "1",
    "FACTORY_ISOLATED_CHALLENGER_FIRST_ASSESSMENT_MIN_ROI_PCT": "0.0",
    "FACTORY_IDEA_SCOUT_ENABLED": "true",
    "FACTORY_IDEA_SCOUT_INTERVAL_HOURS": "48",
    "FACTORY_IDEA_SCOUT_MAX_NEW_PER_RUN": "3",
    "FACTORY_NEW_FAMILY_ENABLED": "true",
    "FACTORY_NEW_FAMILY_INTERVAL_CYCLES": "2",
    "FACTORY_NEW_FAMILY_PROPOSALS_PER_CYCLE": "1",
    "FACTORY_NEW_FAMILY_MAX_ACTIVE_INCUBATIONS": "8",
    "FACTORY_NEW_FAMILY_FIRST_ASSESSMENT_MIN_ROI_PCT": "0.0",
    "FACTORY_EXECUTION_REFRESH_ENABLED": "true",
    "FACTORY_EXECUTION_REFRESH_FAMILIES": "binance_funding_contrarian,binance_cascade_regime,polymarket_cross_venue",
    "FACTORY_EXECUTION_REFRESH_TIMEOUT_SECONDS": "900",
    "FACTORY_EXECUTION_REFRESH_PYTHON_BIN": "python3",
    "FACTORY_LOOP_INTERVAL_SECONDS": "900",
    "FACTORY_LOOP_LOG_PATH": "data/factory/factory_loop.log",
    "FACTORY_ROOT": "data/factory",
    "FACTORY_GOLDFISH_ROOT": "research/goldfish",
    "FACTORY_PAPER_GATE_MONTHLY_ROI_PCT": "5.0",
    "FACTORY_PAPER_GATE_MAX_DRAWDOWN_PCT": "8.0",
    "FACTORY_PAPER_GATE_MIN_DAYS": "30",
    "FACTORY_PAPER_GATE_MIN_FAST_TRADES": "50",
    "FACTORY_PAPER_GATE_MIN_SLOW_SETTLED": "10",
    "FACTORY_EXECUTION_AUTOSTART_ENABLED": "false",
    "FACTORY_EMBEDDED_EXECUTION_ENABLED": "true",
    "FACTORY_VALIDATION_PROFILE": "paper",
    "CASCADE_ALPHA_ENABLED": "true",
    "CONTRARIAN_LEGACY_ENABLED": "true",
    "POLYMARKET_QF_ENABLED": "true",
    "RESEARCH_FACTORY_PORTFOLIO_ID": "research_factory",
    "PREDICTION_POLICY_GATE_PATH": "data/prediction_policy_gate.json",
    "PORTFOLIO_STATE_ROOT": "data/portfolios",
    "EXECUTION_TRACKED_PORTFOLIOS": "betfair_core,hedge_validation,hedge_research,cascade_alpha,contrarian_legacy,research_factory,polymarket_quantum_fold,mev_scout_sol",
    "PREDICTION_MODEL_KINDS": "hybrid_logit,market_calibrated",
    "BINANCE_SPOT_PROD_URL": "https://api.binance.com",
    "BINANCE_FUTURES_PROD_URL": "https://fapi.binance.com",
    "BINANCE_SPOT_TESTNET_URL": "https://testnet.binance.vision",
    "BINANCE_FUTURES_TESTNET_URL": "https://demo-fapi.binance.com",
    "BF_USERNAME": "",
    "BF_PASSWORD": "",
    "BF_APP_KEY": "",
    "BF_CERTS_PATH": "./certs",
    "BF_LOCALE": "spain",
    "PAPER_TRADING": "true",
    "INITIAL_BALANCE_EUR": "1000.00",
    "STAKE_FRACTION": "0.10",
    "PAPER_STATE_PATH": "data/state/paper_executor_state.json",
    "PAPER_TRADES_LOG_PATH": "data/state/paper_trades.jsonl",
    "REDIS_URL": "redis://localhost:6379",
    "DATABASE_URL": "postgresql://user:pass@localhost/betfair_arb",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",
    "POLYMARKET_ENABLED": "true",
    "POLYMARKET_SPORTS_ONLY": "true",
    "POLYMARKET_ROLE": "confirmation",
    "POLYMARKET_HTTP_BASE_URL": "https://gamma-api.polymarket.com",
    "POLYMARKET_WS_URL": "wss://ws-subscriptions-clob.polymarket.com/ws",
    "POLYMARKET_MAX_EVENTS": "250",
    "POLYMARKET_QF_CLOB_HTTP_BASE_URL": "https://clob.polymarket.com",
    "POLYMARKET_QF_CLOB_WS_URL": "wss://ws-subscriptions-clob.polymarket.com/ws",
    "POLYMARKET_QF_HTTP_TIMEOUT_SECONDS": "10.0",
    "FUNDING_MODE": "paper",
    "BINANCE_SPOT_API_KEY": "",
    "BINANCE_SPOT_API_SECRET": "",
    "BINANCE_FUTURES_API_KEY": "",
    "BINANCE_FUTURES_API_SECRET": "",
    "BINANCE_SPOT_TESTNET_API_KEY": "",
    "BINANCE_SPOT_TESTNET_API_SECRET": "",
    "BINANCE_FUTURES_TESTNET_API_KEY": "",
    "BINANCE_FUTURES_TESTNET_API_SECRET": "",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a local .env for AgenticTrading extraction mode.")
    parser.add_argument("--output", default=".env", help="Target .env path.")
    args = parser.parse_args()

    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    rows = [f"{key}={value}" for key, value in DEFAULTS.items()]
    output.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
