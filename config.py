from __future__ import annotations

import os
from decimal import Decimal


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
PORTFOLIO_STATE_ROOT: str = os.getenv("PORTFOLIO_STATE_ROOT", "data/portfolios")
PREDICTION_MODEL_KINDS: str = os.getenv("PREDICTION_MODEL_KINDS", "hybrid_logit,market_calibrated")

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
