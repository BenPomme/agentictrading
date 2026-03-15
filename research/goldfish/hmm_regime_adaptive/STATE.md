# HMM Regime-Adaptive Strategy

**Family**: hmm_regime_adaptive  
**Status**: scaffolded  
**Created**: 2026-03-13  

## Overview

Instrument-agnostic strategy using Hidden Markov Models for market regime detection.

## Files

- `model.py` - Core HMM model with feature engineering, fitting, signal generation, and backtesting
- `../../data/factory/families/hmm_regime_adaptive/hypothesis.json` - Family hypothesis
- `../../data/factory/families/hmm_regime_adaptive/genome.json` - Default hyperparameters

## Next Steps

1. Run initial backtest on SPY 5yr data
2. Validate on crypto (BTCUSDT)  
3. Optimize hyperparameters via goldfish mutations
4. Promote to paper trading
