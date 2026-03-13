# AgenticTrading - ideas for building trading strategies

Some of them may be for stock option trading, which we can also build, but use this as some ideas worth considering, or not, when generating trading models.
·
14h
1. The Tastytrade 0DTE SPX Credit Spread Scanner

"You are a senior options trader at Tastytrade who specializes in 0DTE (zero days to expiration) SPX credit spreads — the strategy professional theta traders use to generate daily income from time decay on the S&P 500 index.

I
Voir plus
Clara Bennett
@CodeswithClara
·
14h
2. The Citadel Market Regime Classifier

"You are a senior quantitative strategist at Citadel who classifies market conditions into specific regimes before placing any options trade — because the #1 reason theta traders lose is selling premium in the wrong environment.

I need a
Voir plus
Clara Bennett
@CodeswithClara
·
14h
3. The SIG Daily Theta Decay Calculator

"You are a senior options market maker at Susquehanna International Group who quantifies exact theta decay profits on short premium positions hour by hour throughout the trading day.

I need a complete theta decay analysis showing exactly
Voir plus
Clara Bennett
@CodeswithClara
·
14h
4. The Two Sigma Probability-Based Strike Selection

"You are a senior quantitative researcher at Two Sigma who selects option strikes based purely on statistical probability models — removing emotion and replacing gut feeling with math.

I need a probability-based framework for
Voir plus
Clara Bennett
@CodeswithClara
·
14h
5. The D.E. Shaw Iron Condor Income Machine

"You are a senior portfolio manager at D.E. Shaw who runs systematic iron condor strategies on indexes and ETFs, collecting premium from both sides of the market when the underlying stays within a predictable range.

I need a complete
Voir plus
Clara Bennett
@CodeswithClara
·
14h
6. The Jane Street Pre-Market Edge Analyzer

"You are a senior volatility trader at Jane Street who analyzes pre-market conditions every morning at 8 AM to determine the optimal theta strategy before the opening bell — because the best trades are planned before the market opens.
Voir plus
Clara Bennett
@CodeswithClara
·
14h
7. The Wolverine Trading Risk Management System

"You are a senior risk manager at Wolverine Trading who monitors options portfolios in real-time and enforces strict risk rules that prevent catastrophic losses — because surviving bad days is more important than maximizing good
Voir plus
Clara Bennett
@CodeswithClara
·
14h
8. The Akuna Capital Volatility Skew Exploiter

"You are a senior options trader at Akuna Capital who profits from volatility skew — the phenomenon where out-of-the-money puts are priced more expensively than equivalent calls, creating systematic edges for traders who know how to
Voir plus
Clara Bennett
@CodeswithClara
·
14h
9. The Peak6 SPY Weekly Income Calendar

"You are a senior income portfolio manager at Peak6 who runs a systematic weekly options income calendar on SPY — opening and closing positions on a fixed schedule that compounds premium income week after week.

I need a complete weekly
Voir plus
Clara Bennett
@CodeswithClara
·
14h
10. The IMC Trading Earnings Theta Crusher

"You are a senior volatility trader at IMC Trading who systematically sells options before earnings announcements to profit from the predictable IV crush that occurs after every single earnings report — regardless of whether the stock
Voir plus

10: new idea: Polymarket Prices lag Binance

500 resolved 5-minute BTC markets. 1,090,031 snapshots. Every price tick. Every BTC movement. Cross-referenced against Binance.

Result: Polymarket prices do lag Binance.

247 stale-price moments detected. 70.9% win rate. Average edge: 12.4 cents.

DeepSeek said 11.3 cents. Our backtest says 12.4 cents. Nearly identical.

When BTC moves >0.12% and Polymarket hasn't adjusted: 100% win rate in our sample.

Sweet spot: buying stale sides priced above $0.70. Win rate jumps to 90%.

What the thread doesn't tell you:

The edge is 9 cents per dollar risked. To make $2M you need $22 million of volume through the book. That means mass-firing $10–50 orders hundreds of times per day with subsecond execution.

The window exists. The math checks out.  

If you want to run your own backtests, polybacktest has subsecond historical data going back a month.


11: new idea: war in iran
Should we find a way to trade oil because of the war

---

## Hidden Markov Model Regime-Adaptive Trading

**Status**: proposed  
**Priority**: high  
**Venue**: multi (stocks via Yahoo/Alpaca, crypto via Binance, potentially sports via Betfair)  
**Author**: factory-agent  

### Hypothesis

Markets exhibit distinct regimes (bull, bear, sideways, turbulent) that can be detected using Hidden Markov Models. By inferring the current regime from observable features (returns, volatility, volume), we can adaptively size positions and select direction:
- **Bull regime**: full long exposure
- **Bear regime**: flat or short (where allowed)
- **Sideways regime**: mean-reversion micro-trades
- **Turbulent regime**: reduced size, wider stops

### Approach

1. **Feature engineering**: Normalized log returns, rolling volatility ratio (20d/60d), volume Z-score, VIX level (for stocks)
2. **Model**: `hmmlearn.GaussianHMM` with 3-4 hidden states, full covariance
3. **State ordering**: Sort states by mean return to assign semantic labels
4. **Signal generation**: Map current state to position sizing and direction
5. **Instrument-agnostic**: Same model architecture applied across stocks (SPY, QQQ, individual), crypto (BTC, ETH), and potentially other venues
6. **Backtest**: Walk-forward with expanding training window, minimum 2yr train / 6mo test

### Key References

- Christensen et al. 2020 (arXiv:2006.08307) - HMM for regime detection
- Hamilton 1989 - Markov switching models
- Bulla & Bulla 2006 - Stylized facts of financial time series and HMMs

### Expected Edge

Regime-aware position sizing should reduce drawdowns during bear/turbulent periods while capturing upside during bull regimes. The key advantage over static strategies is adaptivity.
