# Changelog

## [v0.3.0] — 2026-04-09

Four strategy improvements derived from trade journal analysis of 75 live paper trades
(Mar 2 – Apr 8, 2026: 41.3% win rate, +10.2% return, profit factor 1.48).

### Strategy Changes

**ML_THRESHOLD raised to 0.695** (was 0.65)
Analysis showed trades with ML confidence 0.65–0.694 had a 29% win rate vs 51% for ≥0.695.
Cutting low-confidence entries reduces churn without significantly limiting opportunity.

**BTC_BULL_GATE raised to +0.35%** (was 0.0)
The original gate blocked negative BTC 4h moves. Extending it to require +0.35% minimum
momentum eliminates entries during BTC consolidation/drift — the regime most correlated with
our stop-loss outcomes. Gate is no-op when BTC candle data is unavailable.

**EXCLUDED_TOKENS = {"ADA/USDT"}**
ADA/USDT recorded 0W/3L across the analysis period with no structural edge. Permanently
blocked from long entries. Additional tokens can be added as evidence accumulates.

**Intermediate 6h stop: exit if down >1.5% at or after 6h hold**
Slow-bleed losers (down 1–3% after 6h) were consuming capital and expiring at -3% stop.
New rule cuts them earlier at -1.5%, freeing capital for better setups. Hard stop-loss
(-3%) still fires immediately regardless of hold time.

### Backtest Results (3-month, Jan 9 – Apr 9, 2026)
- Max drawdown corrected to 3.09% (was reported as 96.3% due to cash-only equity tracking bug)
- MTM equity tracking now used for all performance metrics

### Test Coverage
- 136 tests passing (1 pre-existing skip: test_paper_trading_runner args)
- New test file: `tests/test_strategy_v3.py` (12 tests for all v0.3.0 changes)

---

## [v0.2.0] — 2026-04-09

Eight improvements to the live paper trading engine based on 38-day performance analysis
(Mar 2 – Apr 8, 2026: 75 trades, 41.3% win rate, +10.2% return, profit factor 1.48).

### Immediate Fixes

**IMMED-001 — BTC directional gate**
Skip long entries when BTC's 4h return is negative. Win rate drops from 48% → 36% when
BTC 4h is negative. Adds `BTC_BULL_GATE = 0.0` constant and a guard in `_step`.

**IMMED-002 — Concurrent position cap**
Cap simultaneous open longs at 5 to prevent correlated cascade failures (Mar 18 disaster:
15 simultaneous longs, 10 stop-losses in one BTC dip). Adds `MAX_CONCURRENT_POSITIONS = 5`.

**IMMED-003 — Token cooldown tracker (48h after stop-loss)**
Block re-entry on any token that hit a stop-loss within the last 48 hours. Prevents
repeatedly buying failing tokens (POWER: 4 entries, PIPPIN: 3 entries). New module:
`src/risk/cooldown.py` with `CooldownTracker`.

**IMMED-004 — Fix news fetcher**
CryptoPanic deprecated their free API endpoint on 2026-03-01, causing 404 on every call
since launch. Replaced with CryptoCompare News API (primary) + CoinDesk RSS (fallback).
New module: `src/data/news.py`.

### Medium-Term Improvements

**MED-001 — Volume confirmation filter**
Skip entries when current candle volume is below 1.2× the 20-period average. Prevents
being trapped in low-liquidity fakeouts (PIPPIN, POWER, MYX losses on thin volume).
Adds `MIN_VOLUME_RATIO = 1.2`.

**MED-002 — Relative strength filter**
Only go long on tokens outperforming BTC over the last 4 hours. Tokens lagging BTC on
up moves get hit harder on reversals. Adds `RS_FILTER_ENABLED = True`.

**MED-003 — On-chain exchange flow data source**
New `src/data/onchain.py` with `ExchangeFlowFetcher` pulling BTC exchange netflow from
CoinGlass free API (no auth). 4-hour in-memory cache. Netflow value added to GLM regime
context so the LLM can reason about whale movements.

**MED-004 — Google Trends social velocity signal**
New `src/data/trends.py` with `GoogleTrendsFetcher` using `pytrends` (free, no API key).
Fetches 7-day interest scores for "bitcoin" and "crypto". 6-hour cache. Scores added to
GLM regime context to detect retail sentiment spikes and collapses.

### Test Coverage
- 45 tests total, all passing
- New test files: `test_btc_gate.py`, `test_position_cap.py`, `test_cooldown.py`,
  `test_news.py`, `test_volume_filter.py`, `test_rs_filter.py`, `test_onchain.py`,
  `test_trends.py`

---

## [v0.1.0] — 2026-02-28

Initial paper trading system deployment.
- CCXT exchange data ingestion
- LLM regime detection (risk_on / risk_off / choppy / uncertain)
- LightGBM ML ensemble for token scoring
- Kelly Criterion position sizing + circuit breakers
- Telegram alerts + trade journal
