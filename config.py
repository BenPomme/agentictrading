from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass


FACTORY_ROOT: str = os.getenv("FACTORY_ROOT", "data/factory")
FACTORY_GOLDFISH_ROOT: str = os.getenv("FACTORY_GOLDFISH_ROOT", "research/goldfish")
FACTORY_PAPER_GATE_MONTHLY_ROI_PCT: Decimal = Decimal(
    os.getenv("FACTORY_PAPER_GATE_MONTHLY_ROI_PCT", "5.0")
)
FACTORY_PAPER_GATE_MAX_DRAWDOWN_PCT: Decimal = Decimal(
    os.getenv("FACTORY_PAPER_GATE_MAX_DRAWDOWN_PCT", "8.0")
)
FACTORY_PAPER_GATE_MIN_DAYS: int = int(os.getenv("FACTORY_PAPER_GATE_MIN_DAYS", "30"))
FACTORY_PAPER_GATE_MIN_FAST_TRADES: int = int(os.getenv("FACTORY_PAPER_GATE_MIN_FAST_TRADES", "50"))
FACTORY_PAPER_GATE_MIN_SLOW_SETTLED: int = int(os.getenv("FACTORY_PAPER_GATE_MIN_SLOW_SETTLED", "10"))
FACTORY_EXECUTION_AUTOSTART_ENABLED: bool = (
    os.getenv("FACTORY_EXECUTION_AUTOSTART_ENABLED", "false").lower() == "true"
)
AGENTIC_FACTORY_MODE: str = os.getenv("AGENTIC_FACTORY_MODE", "full").strip().lower()
RESEARCH_FACTORY_PORTFOLIO_ID: str = os.getenv("RESEARCH_FACTORY_PORTFOLIO_ID", "research_factory")
PREDICTION_POLICY_GATE_PATH: str = os.getenv("PREDICTION_POLICY_GATE_PATH", "data/prediction_policy_gate.json")
EXECUTION_REPO_ROOT: str = os.getenv("EXECUTION_REPO_ROOT", "")
EXECUTION_PORTFOLIO_STATE_ROOT: str = os.getenv("EXECUTION_PORTFOLIO_STATE_ROOT", "")
EXECUTION_TRACKED_PORTFOLIOS: str = os.getenv(
    "EXECUTION_TRACKED_PORTFOLIOS",
    "betfair_core,hedge_validation,hedge_research,cascade_alpha,contrarian_legacy,research_factory,polymarket_quantum_fold,mev_scout_sol",
)
PORTFOLIO_STATE_ROOT: str = os.getenv("PORTFOLIO_STATE_ROOT", "data/portfolios")
PREDICTION_MODEL_KINDS: str = os.getenv("PREDICTION_MODEL_KINDS", "hybrid_logit,market_calibrated")
FUNDING_MODE: str = os.getenv("FUNDING_MODE", "paper").strip().lower()
FACTORY_REAL_AGENTS_ENABLED: bool = os.getenv("FACTORY_REAL_AGENTS_ENABLED", "true").lower() == "true"
FACTORY_AGENT_PROVIDER_ORDER: str = os.getenv("FACTORY_AGENT_PROVIDER_ORDER", "codex,deterministic")
FACTORY_AGENT_OLLAMA_FALLBACK_ENABLED: bool = (
    os.getenv("FACTORY_AGENT_OLLAMA_FALLBACK_ENABLED", "false").lower() == "true"
)
FACTORY_AGENT_ENABLED_FAMILIES: str = os.getenv(
    "FACTORY_AGENT_ENABLED_FAMILIES",
    "binance_funding_contrarian,binance_cascade_regime,polymarket_cross_venue",
)
FACTORY_AGENT_DEMO_FAMILY: str = os.getenv("FACTORY_AGENT_DEMO_FAMILY", "binance_funding_contrarian")
FACTORY_AGENT_CODEX_MODEL_CHEAP: str = os.getenv("FACTORY_AGENT_CODEX_MODEL_CHEAP", "gpt-5.1-codex-mini")
FACTORY_AGENT_CODEX_MODEL_PROPOSAL: str = os.getenv("FACTORY_AGENT_CODEX_MODEL_PROPOSAL", "gpt-5.4")
FACTORY_AGENT_CODEX_MODEL_STANDARD: str = os.getenv("FACTORY_AGENT_CODEX_MODEL_STANDARD", "gpt-5.1-codex")
FACTORY_AGENT_CODEX_MODEL_HARD: str = os.getenv("FACTORY_AGENT_CODEX_MODEL_HARD", "gpt-5.2-codex")
FACTORY_AGENT_CODEX_MODEL_FRONTIER: str = os.getenv("FACTORY_AGENT_CODEX_MODEL_FRONTIER", "gpt-5.3-codex")
FACTORY_AGENT_CODEX_MODEL_DEEP: str = os.getenv("FACTORY_AGENT_CODEX_MODEL_DEEP", "gpt-5.4")
FACTORY_AGENT_CODEX_MODEL_SPARK: str = os.getenv("FACTORY_AGENT_CODEX_MODEL_SPARK", "gpt-5.3-codex-spark")
FACTORY_AGENT_REASONING_CHEAP: str = os.getenv("FACTORY_AGENT_REASONING_CHEAP", "medium")
FACTORY_AGENT_REASONING_PROPOSAL: str = os.getenv("FACTORY_AGENT_REASONING_PROPOSAL", "high")
FACTORY_AGENT_REASONING_STANDARD: str = os.getenv("FACTORY_AGENT_REASONING_STANDARD", "medium")
FACTORY_AGENT_REASONING_HARD: str = os.getenv("FACTORY_AGENT_REASONING_HARD", "high")
FACTORY_AGENT_REASONING_FRONTIER: str = os.getenv("FACTORY_AGENT_REASONING_FRONTIER", "high")
FACTORY_AGENT_REASONING_DEEP: str = os.getenv("FACTORY_AGENT_REASONING_DEEP", "high")
FACTORY_AGENT_OLLAMA_MODEL: str = os.getenv("FACTORY_AGENT_OLLAMA_MODEL", "qwen2.5:32b")
FACTORY_AGENT_LOG_DIR: str = os.getenv("FACTORY_AGENT_LOG_DIR", "data/factory/agent_runs")
FACTORY_AGENT_POST_EVAL_CRITIQUE_ENABLED: bool = (
    os.getenv("FACTORY_AGENT_POST_EVAL_CRITIQUE_ENABLED", "false").lower() == "true"
)
FACTORY_CHALLENGER_MUTATION_PCT: int = int(os.getenv("FACTORY_CHALLENGER_MUTATION_PCT", "80"))
FACTORY_CHALLENGER_NEW_MODEL_PCT: int = int(os.getenv("FACTORY_CHALLENGER_NEW_MODEL_PCT", "20"))
FACTORY_AGENT_REVIEW_ENABLED: bool = os.getenv("FACTORY_AGENT_REVIEW_ENABLED", "true").lower() == "true"
FACTORY_AGENT_REVIEW_MIN_FAST_DAYS: int = int(os.getenv("FACTORY_AGENT_REVIEW_MIN_FAST_DAYS", "14"))
FACTORY_AGENT_REVIEW_MIN_FAST_TRADES: int = int(os.getenv("FACTORY_AGENT_REVIEW_MIN_FAST_TRADES", "50"))
FACTORY_AGENT_REVIEW_MIN_SLOW_DAYS: int = int(os.getenv("FACTORY_AGENT_REVIEW_MIN_SLOW_DAYS", "21"))
FACTORY_AGENT_REVIEW_MIN_SLOW_SETTLED: int = int(os.getenv("FACTORY_AGENT_REVIEW_MIN_SLOW_SETTLED", "10"))
FACTORY_AGENT_REVIEW_INTERVAL_DAYS: int = int(os.getenv("FACTORY_AGENT_REVIEW_INTERVAL_DAYS", "7"))
FACTORY_AGENT_REVIEW_INCREMENTAL_TRADES: int = int(os.getenv("FACTORY_AGENT_REVIEW_INCREMENTAL_TRADES", "25"))
FACTORY_DEBUG_AGENT_ENABLED: bool = os.getenv("FACTORY_DEBUG_AGENT_ENABLED", "true").lower() == "true"
FACTORY_DEBUG_AGENT_REVIEW_INTERVAL_HOURS: int = int(os.getenv("FACTORY_DEBUG_AGENT_REVIEW_INTERVAL_HOURS", "12"))
FACTORY_STALLED_MODEL_HOURS: int = int(os.getenv("FACTORY_STALLED_MODEL_HOURS", "8"))
FACTORY_STALLED_MODEL_MIN_SCANS: int = int(os.getenv("FACTORY_STALLED_MODEL_MIN_SCANS", "25"))
FACTORY_IDEA_SCOUT_ENABLED: bool = os.getenv("FACTORY_IDEA_SCOUT_ENABLED", "true").lower() == "true"
FACTORY_IDEA_SCOUT_INTERVAL_HOURS: int = int(os.getenv("FACTORY_IDEA_SCOUT_INTERVAL_HOURS", "48"))
FACTORY_IDEA_SCOUT_MAX_NEW_PER_RUN: int = int(os.getenv("FACTORY_IDEA_SCOUT_MAX_NEW_PER_RUN", "3"))
FACTORY_EXECUTION_REFRESH_ENABLED: bool = (
    os.getenv("FACTORY_EXECUTION_REFRESH_ENABLED", "true").lower() == "true"
)
FACTORY_EXECUTION_REFRESH_FAMILIES: str = os.getenv(
    "FACTORY_EXECUTION_REFRESH_FAMILIES",
    "binance_funding_contrarian,binance_cascade_regime,polymarket_cross_venue",
)
FACTORY_EXECUTION_REFRESH_TIMEOUT_SECONDS: int = int(
    os.getenv("FACTORY_EXECUTION_REFRESH_TIMEOUT_SECONDS", "900")
)
FACTORY_EXECUTION_REFRESH_PYTHON_BIN: str = os.getenv(
    "FACTORY_EXECUTION_REFRESH_PYTHON_BIN",
    "python3",
)
FACTORY_LOOP_INTERVAL_SECONDS: int = int(os.getenv("FACTORY_LOOP_INTERVAL_SECONDS", "900"))
FACTORY_LOOP_LOG_PATH: str = os.getenv("FACTORY_LOOP_LOG_PATH", "data/factory/factory_loop.log")

BF_USERNAME: str = os.getenv("BF_USERNAME", "")
BF_PASSWORD: str = os.getenv("BF_PASSWORD", "")
BF_APP_KEY: str = os.getenv("BF_APP_KEY", "")
BF_CERTS_PATH: str = os.getenv("BF_CERTS_PATH", "./certs")
BF_LOCALE: str = os.getenv("BF_LOCALE", "spain")

PAPER_TRADING: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"
INITIAL_BALANCE_EUR: Decimal = Decimal(os.getenv("INITIAL_BALANCE_EUR", "1000.00"))
STAKE_FRACTION: Decimal = Decimal(os.getenv("STAKE_FRACTION", "0.10"))
PAPER_STATE_PATH: str = os.getenv("PAPER_STATE_PATH", "data/state/paper_executor_state.json")
PAPER_TRADES_LOG_PATH: str = os.getenv("PAPER_TRADES_LOG_PATH", "data/state/paper_trades.jsonl")

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/betfair_arb")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

POLYMARKET_ENABLED: bool = os.getenv("POLYMARKET_ENABLED", "true").lower() == "true"
POLYMARKET_SPORTS_ONLY: bool = os.getenv("POLYMARKET_SPORTS_ONLY", "true").lower() == "true"
POLYMARKET_ROLE: str = os.getenv("POLYMARKET_ROLE", "confirmation")
POLYMARKET_HTTP_BASE_URL: str = os.getenv("POLYMARKET_HTTP_BASE_URL", "https://gamma-api.polymarket.com")
POLYMARKET_WS_URL: str = os.getenv("POLYMARKET_WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws")
POLYMARKET_MAX_EVENTS: int = int(os.getenv("POLYMARKET_MAX_EVENTS", "250"))
POLYMARKET_QF_CLOB_HTTP_BASE_URL: str = os.getenv("POLYMARKET_QF_CLOB_HTTP_BASE_URL", "https://clob.polymarket.com")
POLYMARKET_QF_CLOB_WS_URL: str = os.getenv("POLYMARKET_QF_CLOB_WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws")
POLYMARKET_QF_HTTP_TIMEOUT_SECONDS: float = float(os.getenv("POLYMARKET_QF_HTTP_TIMEOUT_SECONDS", "10.0"))

BINANCE_SPOT_PROD_URL: str = os.getenv("BINANCE_SPOT_PROD_URL", "https://api.binance.com")
BINANCE_FUTURES_PROD_URL: str = os.getenv("BINANCE_FUTURES_PROD_URL", "https://fapi.binance.com")
BINANCE_SPOT_TESTNET_URL: str = os.getenv("BINANCE_SPOT_TESTNET_URL", "https://testnet.binance.vision")
BINANCE_FUTURES_TESTNET_URL: str = os.getenv("BINANCE_FUTURES_TESTNET_URL", "https://demo-fapi.binance.com")

BINANCE_SPOT_API_KEY: str = os.getenv("BINANCE_SPOT_API_KEY", "")
BINANCE_SPOT_API_SECRET: str = os.getenv("BINANCE_SPOT_API_SECRET", "")
BINANCE_FUTURES_API_KEY: str = os.getenv("BINANCE_FUTURES_API_KEY", "")
BINANCE_FUTURES_API_SECRET: str = os.getenv("BINANCE_FUTURES_API_SECRET", "")
BINANCE_SPOT_TESTNET_API_KEY: str = os.getenv("BINANCE_SPOT_TESTNET_API_KEY", "")
BINANCE_SPOT_TESTNET_API_SECRET: str = os.getenv("BINANCE_SPOT_TESTNET_API_SECRET", "")
BINANCE_FUTURES_TESTNET_API_KEY: str = os.getenv("BINANCE_FUTURES_TESTNET_API_KEY", "")
BINANCE_FUTURES_TESTNET_API_SECRET: str = os.getenv("BINANCE_FUTURES_TESTNET_API_SECRET", "")
