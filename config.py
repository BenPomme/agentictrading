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
    os.getenv("FACTORY_EXECUTION_AUTOSTART_ENABLED", "true").lower() == "true"
)
FACTORY_EMBEDDED_EXECUTION_ENABLED: bool = (
    os.getenv("FACTORY_EMBEDDED_EXECUTION_ENABLED", "true").lower() == "true"
)
AGENTIC_FACTORY_MODE: str = os.getenv("AGENTIC_FACTORY_MODE", "full").strip().lower()
RESEARCH_FACTORY_PORTFOLIO_ID: str = os.getenv("RESEARCH_FACTORY_PORTFOLIO_ID", "research_factory")
PREDICTION_POLICY_GATE_PATH: str = os.getenv("PREDICTION_POLICY_GATE_PATH", "data/prediction_policy_gate.json")
EXECUTION_TRACKED_PORTFOLIOS: str = os.getenv(
    "EXECUTION_TRACKED_PORTFOLIOS",
    "liquidation_rebound_absorption,funding_term_structure_dislocation,vol_surface_dispersion_rotation,cross_venue_probability_elasticity",
)
PORTFOLIO_STATE_ROOT: str = os.getenv("PORTFOLIO_STATE_ROOT", "data/portfolios")
PREDICTION_MODEL_KINDS: str = os.getenv("PREDICTION_MODEL_KINDS", "hybrid_logit,market_calibrated")
FUNDING_MODE: str = os.getenv("FUNDING_MODE", "paper").strip().lower()
FACTORY_REAL_AGENTS_ENABLED: bool = os.getenv("FACTORY_REAL_AGENTS_ENABLED", "true").lower() == "true"
FACTORY_AGENT_PROVIDER_ORDER: str = os.getenv("FACTORY_AGENT_PROVIDER_ORDER", "codex,openai_api,deterministic")
FACTORY_AGENT_OLLAMA_FALLBACK_ENABLED: bool = (
    os.getenv("FACTORY_AGENT_OLLAMA_FALLBACK_ENABLED", "false").lower() == "true"
)
FACTORY_AGENT_ENABLED_FAMILIES: str = os.getenv(
    "FACTORY_AGENT_ENABLED_FAMILIES",
    "binance_funding_contrarian,binance_cascade_regime,polymarket_cross_venue",
)
FACTORY_AGENT_DEMO_FAMILY: str = os.getenv("FACTORY_AGENT_DEMO_FAMILY", "binance_funding_contrarian")
FACTORY_AGENT_CODEX_MODEL_CHEAP: str = os.getenv("FACTORY_AGENT_CODEX_MODEL_CHEAP", "gpt-5.1-codex-mini")
FACTORY_AGENT_CODEX_MODEL_PROPOSAL: str = os.getenv("FACTORY_AGENT_CODEX_MODEL_PROPOSAL", "gpt-5.2-codex")
FACTORY_AGENT_CODEX_MODEL_STANDARD: str = os.getenv("FACTORY_AGENT_CODEX_MODEL_STANDARD", "gpt-5.1-codex")
FACTORY_AGENT_CODEX_MODEL_HARD: str = os.getenv("FACTORY_AGENT_CODEX_MODEL_HARD", "gpt-5.2-codex")
FACTORY_AGENT_CODEX_MODEL_FRONTIER: str = os.getenv("FACTORY_AGENT_CODEX_MODEL_FRONTIER", "gpt-5.3-codex")
FACTORY_AGENT_CODEX_MODEL_DEEP: str = os.getenv("FACTORY_AGENT_CODEX_MODEL_DEEP", "gpt-5.4")
FACTORY_AGENT_CODEX_MODEL_SPARK: str = os.getenv("FACTORY_AGENT_CODEX_MODEL_SPARK", "gpt-5.3-codex-spark")
FACTORY_AGENT_CODEX_MULTI_AGENT_ENABLED: bool = (
    os.getenv("FACTORY_AGENT_CODEX_MULTI_AGENT_ENABLED", "true").lower() == "true"
)
FACTORY_AGENT_CODEX_MULTI_AGENT_TASKS: str = os.getenv(
    "FACTORY_AGENT_CODEX_MULTI_AGENT_TASKS",
    "proposal_generation,post_eval_critique,runtime_debug_review,family_bootstrap_generation,maintenance_resolution_review",
)
FACTORY_AGENT_REASONING_CHEAP: str = os.getenv("FACTORY_AGENT_REASONING_CHEAP", "medium")
FACTORY_AGENT_REASONING_PROPOSAL: str = os.getenv("FACTORY_AGENT_REASONING_PROPOSAL", "high")
FACTORY_AGENT_REASONING_STANDARD: str = os.getenv("FACTORY_AGENT_REASONING_STANDARD", "medium")
FACTORY_AGENT_REASONING_HARD: str = os.getenv("FACTORY_AGENT_REASONING_HARD", "high")
FACTORY_AGENT_REASONING_FRONTIER: str = os.getenv("FACTORY_AGENT_REASONING_FRONTIER", "high")
FACTORY_AGENT_REASONING_DEEP: str = os.getenv("FACTORY_AGENT_REASONING_DEEP", "high")
FACTORY_AGENT_OLLAMA_MODEL: str = os.getenv("FACTORY_AGENT_OLLAMA_MODEL", "qwen2.5:32b")
FACTORY_AGENT_OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
FACTORY_AGENT_OPENAI_MODEL_CHEAP: str = os.getenv("FACTORY_AGENT_OPENAI_MODEL_CHEAP", "gpt-4.1-nano")
FACTORY_AGENT_OPENAI_MODEL_STANDARD: str = os.getenv("FACTORY_AGENT_OPENAI_MODEL_STANDARD", "gpt-4.1-mini")
FACTORY_AGENT_OPENAI_MODEL_HARD: str = os.getenv("FACTORY_AGENT_OPENAI_MODEL_HARD", "gpt-5-mini")
FACTORY_AGENT_OPENAI_MODEL_FRONTIER: str = os.getenv("FACTORY_AGENT_OPENAI_MODEL_FRONTIER", "gpt-5-mini")
FACTORY_AGENT_OPENAI_MODEL_DEEP: str = os.getenv("FACTORY_AGENT_OPENAI_MODEL_DEEP", "gpt-5.4")
FACTORY_AGENT_EXPENSIVE_CAP_PCT: float = float(os.getenv("FACTORY_AGENT_EXPENSIVE_CAP_PCT", "10"))
FACTORY_AGENT_COST_WINDOW: int = int(os.getenv("FACTORY_AGENT_COST_WINDOW", "50"))
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
FACTORY_FIRST_ASSESSMENT_DAYS: int = int(os.getenv("FACTORY_FIRST_ASSESSMENT_DAYS", "2"))
FACTORY_FIRST_ASSESSMENT_FAST_TRADES: int = int(os.getenv("FACTORY_FIRST_ASSESSMENT_FAST_TRADES", "10"))
FACTORY_FIRST_ASSESSMENT_SLOW_SETTLED: int = int(os.getenv("FACTORY_FIRST_ASSESSMENT_SLOW_SETTLED", "4"))
FACTORY_DEBUG_AGENT_ENABLED: bool = os.getenv("FACTORY_DEBUG_AGENT_ENABLED", "true").lower() == "true"
FACTORY_DEBUG_AGENT_REVIEW_INTERVAL_HOURS: int = int(os.getenv("FACTORY_DEBUG_AGENT_REVIEW_INTERVAL_HOURS", "12"))
FACTORY_MAINTENANCE_AGENT_MAX_ITEMS_PER_CYCLE: int = int(os.getenv("FACTORY_MAINTENANCE_AGENT_MAX_ITEMS_PER_CYCLE", "3"))
FACTORY_MAINTENANCE_AGENT_REVIEW_INTERVAL_HOURS: int = int(os.getenv("FACTORY_MAINTENANCE_AGENT_REVIEW_INTERVAL_HOURS", "12"))
FACTORY_MAINTENANCE_QUEUE_REVIEW_COOLDOWN_HOURS: int = int(os.getenv("FACTORY_MAINTENANCE_QUEUE_REVIEW_COOLDOWN_HOURS", "12"))
FACTORY_MAINTENANCE_QUEUE_MAX_PER_FAMILY: int = int(os.getenv("FACTORY_MAINTENANCE_QUEUE_MAX_PER_FAMILY", "3"))
FACTORY_STALLED_MODEL_HOURS: int = int(os.getenv("FACTORY_STALLED_MODEL_HOURS", "8"))
FACTORY_STALLED_MODEL_MIN_SCANS: int = int(os.getenv("FACTORY_STALLED_MODEL_MIN_SCANS", "25"))
FACTORY_MAX_LOSS_STREAK: int = int(os.getenv("FACTORY_MAX_LOSS_STREAK", "3"))
FACTORY_BACKTEST_TTL_HOURS: float = float(os.getenv("FACTORY_BACKTEST_TTL_HOURS", "48"))
FACTORY_MAX_FAMILY_RETIREMENTS: int = int(os.getenv("FACTORY_MAX_FAMILY_RETIREMENTS", "8"))
FACTORY_TRAINABILITY_GRACE_HOURS: float = float(os.getenv("FACTORY_TRAINABILITY_GRACE_HOURS", "6.0"))
FACTORY_RUNTIME_LANE_MIN_SCORE_GAP: float = float(os.getenv("FACTORY_RUNTIME_LANE_MIN_SCORE_GAP", "3.0"))
FACTORY_RUNTIME_LANE_MIN_PAPER_DAYS: int = int(os.getenv("FACTORY_RUNTIME_LANE_MIN_PAPER_DAYS", "7"))
FACTORY_RUNTIME_LANE_MIN_TRADE_COUNT: int = int(os.getenv("FACTORY_RUNTIME_LANE_MIN_TRADE_COUNT", "10"))
FACTORY_RUNTIME_FIRST_READ_MIN_ROI_PCT: float = float(os.getenv("FACTORY_RUNTIME_FIRST_READ_MIN_ROI_PCT", "1.0"))
FACTORY_RUNTIME_FIRST_READ_MIN_RESEARCH_TRADES: int = int(os.getenv("FACTORY_RUNTIME_FIRST_READ_MIN_RESEARCH_TRADES", "25"))
FACTORY_RUNTIME_ALIAS_STALE_HOURS: float = float(os.getenv("FACTORY_RUNTIME_ALIAS_STALE_HOURS", "4.0"))
FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES: int = int(os.getenv("FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES", "10"))
FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES_PER_FAMILY: int = int(
    os.getenv("FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES_PER_FAMILY", "2")
)
FACTORY_ISOLATED_CHALLENGER_FIRST_ASSESSMENT_MIN_ROI_PCT: float = float(
    os.getenv("FACTORY_ISOLATED_CHALLENGER_FIRST_ASSESSMENT_MIN_ROI_PCT", "0.0")
)
FACTORY_IDEA_SCOUT_ENABLED: bool = os.getenv("FACTORY_IDEA_SCOUT_ENABLED", "true").lower() == "true"
FACTORY_IDEA_SCOUT_INTERVAL_HOURS: int = int(os.getenv("FACTORY_IDEA_SCOUT_INTERVAL_HOURS", "48"))
FACTORY_IDEA_SCOUT_MAX_NEW_PER_RUN: int = int(os.getenv("FACTORY_IDEA_SCOUT_MAX_NEW_PER_RUN", "3"))
FACTORY_NEW_FAMILY_ENABLED: bool = os.getenv("FACTORY_NEW_FAMILY_ENABLED", "true").lower() == "true"
FACTORY_NEW_FAMILY_INTERVAL_CYCLES: int = int(os.getenv("FACTORY_NEW_FAMILY_INTERVAL_CYCLES", "2"))
FACTORY_NEW_FAMILY_PROPOSALS_PER_CYCLE: int = int(os.getenv("FACTORY_NEW_FAMILY_PROPOSALS_PER_CYCLE", "1"))
FACTORY_NEW_FAMILY_MAX_ACTIVE_INCUBATIONS: int = int(os.getenv("FACTORY_NEW_FAMILY_MAX_ACTIVE_INCUBATIONS", "3"))
FACTORY_NEW_FAMILY_FIRST_ASSESSMENT_MIN_ROI_PCT: float = float(
    os.getenv("FACTORY_NEW_FAMILY_FIRST_ASSESSMENT_MIN_ROI_PCT", "0.0")
)
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

FACTORY_VALIDATION_PROFILE: str = os.getenv(
    "FACTORY_VALIDATION_PROFILE",
    "paper",
).strip().lower()

FACTORY_STOCK_MARKET_TZ: str = os.getenv("FACTORY_STOCK_MARKET_TZ", "America/New_York")
FACTORY_STOCK_MARKET_OPEN: str = os.getenv("FACTORY_STOCK_MARKET_OPEN", "09:30")
FACTORY_STOCK_MARKET_CLOSE: str = os.getenv("FACTORY_STOCK_MARKET_CLOSE", "16:00")

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

# ---------------------------------------------------------------------------
# Task 01: Runtime adapter feature flags
# ---------------------------------------------------------------------------
# FACTORY_RUNTIME_BACKEND: active runtime backend. "legacy" (default) uses the
# existing Codex/OpenAI provider chain. "mobkit" will be wired in Task 03.
FACTORY_RUNTIME_BACKEND: str = os.getenv("FACTORY_RUNTIME_BACKEND", "legacy").strip().lower()

# FACTORY_ENABLE_MOBKIT: must be true *and* FACTORY_RUNTIME_BACKEND=mobkit to
# activate the mobkit backend. Double-flag prevents accidental activation.
FACTORY_ENABLE_MOBKIT: bool = os.getenv("FACTORY_ENABLE_MOBKIT", "false").lower() == "true"

# FACTORY_ENABLE_GOLDFISH_PROVENANCE: will gate Goldfish write path (Task 02).
FACTORY_ENABLE_GOLDFISH_PROVENANCE: bool = (
    os.getenv("FACTORY_ENABLE_GOLDFISH_PROVENANCE", "false").lower() == "true"
)

# FACTORY_ENABLE_STRICT_BUDGETS: will gate hard budget enforcement (Task 04).
FACTORY_ENABLE_STRICT_BUDGETS: bool = (
    os.getenv("FACTORY_ENABLE_STRICT_BUDGETS", "false").lower() == "true"
)

# FACTORY_FALLBACK_TO_LEGACY: when true, a failing new backend falls back to
# legacy rather than hard-failing. Default true for safety during migration.
FACTORY_FALLBACK_TO_LEGACY: bool = (
    os.getenv("FACTORY_FALLBACK_TO_LEGACY", "true").lower() == "true"
)

# ---------------------------------------------------------------------------
# Task 03: mobkit backend config keys
# ---------------------------------------------------------------------------
# Path to the mobkit-rpc gateway binary. Required when FACTORY_RUNTIME_BACKEND=mobkit.
# Set via MOBKIT_RPC_GATEWAY_BIN (conventional name) or FACTORY_MOBKIT_GATEWAY_BIN.
FACTORY_MOBKIT_GATEWAY_BIN: str = os.getenv(
    "FACTORY_MOBKIT_GATEWAY_BIN",
    os.getenv("MOBKIT_RPC_GATEWAY_BIN", ""),
).strip()

# Optional path to a mob.toml config file for the mobkit runtime.
# Leave empty to use convention-based defaults (config/mob.toml if present).
FACTORY_MOBKIT_CONFIG_PATH: str = os.getenv("FACTORY_MOBKIT_CONFIG_PATH", "").strip()

# Per-call timeout for mobkit RPC operations, in seconds.
FACTORY_MOBKIT_TIMEOUT_SECONDS: int = int(os.getenv("FACTORY_MOBKIT_TIMEOUT_SECONDS", "120"))

# ---------------------------------------------------------------------------
# Task 02: Goldfish provenance config keys
# ---------------------------------------------------------------------------
# Optional override for the goldfish project root directory.
# If empty, falls back to FACTORY_GOLDFISH_ROOT.
FACTORY_GOLDFISH_PROJECT_ROOT: str = os.getenv("FACTORY_GOLDFISH_PROJECT_ROOT", "").strip()

# When true, a Goldfish write failure causes the operation to raise rather than
# log-and-continue. Default false (warn-only) during migration.
FACTORY_GOLDFISH_FAIL_ON_ERROR: bool = (
    os.getenv("FACTORY_GOLDFISH_FAIL_ON_ERROR", "false").lower() == "true"
)
