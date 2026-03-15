# Trading Ideas

Write new ideas below as numbered entries. The factory reads this file every ~30 minutes
and sends each new idea to GPT-5.4 to design a real trading model. You can be brief
("exploit oil price spikes from Middle East tensions") or detailed with venue/instruments.

1. Binance Funding Rate Mean-Reversion
When 8-hour funding rate exceeds 0.05%, short the perp. When below -0.05%, go long.
Funding rates mean-revert within 1-3 periods. Instruments: BTCUSDT, ETHUSDT, SOLUSDT.

2. Polymarket-Binance Price Lag Arbitrage
Polymarket BTC prediction markets lag Binance spot by 30-120 seconds. When BTC moves
>0.12% and Polymarket hasn't adjusted, buy the stale side priced above $0.70.
70.9% win rate, 12.4 cents average edge per backtest.

3. VIX Regime-Switching Stock Allocation
Use VIX level to classify market regime. Bull (VIX<18): full equity. Transition (18-25):
half equity half bonds. Crisis (>25): short equity or all bonds. Rebalance daily.
Instruments: SPY, QQQ, TLT, GLD.

4. Binance Liquidation Cascade Detection
Monitor open interest decline rate and funding rate convergence to zero. When OI drops >5%
in 4 hours while funding flattens, a cascade may be forming. Short with tight stops.

5. Cross-Venue Prediction Market Arbitrage
When the same event is priced differently on Polymarket vs Betfair by >5%, take opposing
positions. The implied probability difference is the edge after accounting for fees.

6. Oil Supply Shock Momentum
When geopolitical events cause oil to gap >3%, momentum persists 2-5 days. Go long
energy ETFs (USO, XLE) on gap day, exit after 5 trading days or reversal signal.

7. Crypto Basis Trade
When annualized basis between BTC spot and perpetual exceeds 15%, sell perp buy spot.
Unwind when basis normalizes below 5%. Delta-neutral carry trade.

8. Betfair In-Play Football Momentum
After a goal in football, scoring team's odds overcorrect by 8-15%. Lay immediately,
back once odds stabilize (2-5 minutes).

9. Earnings Season Mean-Reversion
Large-cap stocks (AAPL, MSFT, GOOGL, AMZN, META, NVDA) overcorrect on earnings.
Sell momentum into earnings, buy the dip T+1 when volatility settles.

10. Altcoin Funding Rate Divergence
When altcoin funding rate diverges from BTC funding rate, altcoin mean-reverts within 24h.
Trade the divergence. Instruments: SOLUSDT, DOGEUSDT, AVAXUSDT, 1000PEPEUSDT.
