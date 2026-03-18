# Fast Multi-Venue Readiness Status

**Date:** 2026-03-18

## Summary

| Venue | Status | One-line Reason |
|---|---|---|
| **Binance** | READY NOW | Testnet keys configured, funding/OI/klines data for BTC/ETH/SOL, 3 active families |
| **Polymarket** | READY NOW | 19 historical parquet files, cross_venue family active, portfolio dir exists |
| **Yahoo/Alpaca** | READY NOW | Yahoo OHLCV parquets for S&P500, Alpaca paper API key configured, vol_surface family active |
| **Betfair** | BLOCKED NOW | `./certs/` directory missing — Betfair requires X.509 client certificates for API auth |

---

## Venue Details

### Binance — READY NOW
- Data: `data/funding_history/funding_rates/` and `klines/` with BTCUSDT, ETHUSDT, SOLUSDT, + 200+ symbols
- Auth: Testnet API keys configured in .env.staging (`BINANCE_FUTURES_TESTNET_API_KEY`)
- Paper broker: `https://demo-fapi.binance.com` (testnet)
- Active families: `funding_term_structure_dislocation`, `liquidation_rebound_absorption`, `cross_venue_probability_elasticity`

### Polymarket — READY NOW
- Data: `data/polymarket/prices_history/` — 19 parquet files
- Auth: No API key required for read-only data
- Paper mode: Simulated fills against live market prices (factory's existing model)
- Active families: `cross_venue_probability_elasticity` (Polymarket+Binance cross-venue)
- Portfolio: `data/portfolios/polymarket_quantum_fold/` exists

### Yahoo/Alpaca — READY NOW
- Data: `data/yahoo/ohlcv/` — S&P 500 components as parquet files (AAPL, ADBE, ... full basket)
- Auth: `ALPACA_PAPER_API_KEY=PKUCZVPCYWYQPSY7AOUDCBZ6UI` configured
- Paper broker: `https://paper-api.alpaca.markets` (Alpaca native paper mode)
- Active families: `vol_surface_dispersion_rotation` (yahoo data, alpaca execution)
- Portfolio: `data/portfolios/alpaca_paper/` exists

### Betfair — BLOCKED NOW
- **Hard blocker:** `./certs/` directory does not exist
- Betfair requires X.509 client certificate for API authentication
- These certificates are issued by Betfair and cannot be generated programmatically
- No active families (all Betfair families are retired or in backup)
- **To unblock:** Install client cert from Betfair account settings → `./certs/`
