"""Backtesting engine — replays candles through the full pipeline.

Usage (CLI):
    python -m src.backtesting.backtest_runner \\
        --pairs BTC/USDT \\
        --timerange 20240101-20250101
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol

import pandas as pd

if TYPE_CHECKING:
    from src.data.funding import FundingRateFetcher
    from src.data.news import CryptoPanicFetcher
    from src.data.onchain import ExchangeFlowFetcher
    from src.data.sentiment import FearGreedFetcher
    from src.llm.glm_client import GLMClient

from src.risk.cooldown import CooldownTracker

_cooldown_tracker = CooldownTracker()

logger = logging.getLogger(__name__)

_REPORTS_DIR = Path("data/backtests")
_JOURNALS_DIR = Path("data/journals")

# Gate.io taker fee (0.10%) and slippage (0.05%)
TAKER_FEE = 0.001
SLIPPAGE = 0.0005

ML_THRESHOLD = 0.65        # minimum confidence score for LONG entry
ATR_MULT = 2.4             # ATR multiplier for take-profit distance
BTC_MOMENTUM_CAP = 0.025      # reject longs when |btc_4h_change| > 2.5% (chaotic regimes)
BTC_BULL_GATE = 0.0           # skip long entries when BTC 4h return is negative
MAX_CONCURRENT_POSITIONS = 5  # max open long positions at once (prevents correlated cascades)
MIN_VOLUME_RATIO = 1.2        # minimum vol/avg20 ratio to confirm an entry signal
RS_FILTER_ENABLED = True      # skip longs on tokens underperforming BTC over last 4h


# ---------------------------------------------------------------------------
# Protocols / interfaces  (allow injection of any regime/model/risk)
# ---------------------------------------------------------------------------


class RegimeProtocol(Protocol):
    def detect(self, market_data: dict[str, Any]) -> str:
        """Return regime string: risk_on | risk_off | choppy | uncertain."""
        ...


class ScorerProtocol(Protocol):
    def score(self, pair: str, df: pd.DataFrame, regime: str) -> float:
        """Return confidence [0, 1] for going long on *pair*."""
        ...


class RiskProtocol(Protocol):
    def should_trade(self) -> bool:
        """Return False when any circuit breaker is active."""
        ...

    def position_size(self, confidence: float, price: float, portfolio: float) -> float:
        """Return USD notional to trade."""
        ...


# ---------------------------------------------------------------------------
# Default (pass-through) implementations for standalone backtesting
# ---------------------------------------------------------------------------


class _AlwaysRiskOnRegime:
    def detect(self, market_data: dict[str, Any]) -> str:
        return "risk_on"


class BTCRegime:
    """Rule-based regime from BTC price action — mimics what Claude Haiku would say.

    Logic:
      risk_on  — BTC above 200-EMA AND 24h return > +0.5%
      risk_off — BTC below 200-EMA AND 24h return < -1%
      choppy   — everything else
    """

    def detect(self, market_data: dict[str, Any]) -> str:
        btc_window: pd.DataFrame | None = market_data.get("btc_window")
        if btc_window is None or len(btc_window) < 200:
            return "uncertain"

        close = btc_window["close"]
        ema200 = float(close.ewm(span=200, min_periods=200).mean().iloc[-1])
        current = float(close.iloc[-1])
        prev24 = float(close.iloc[-25]) if len(close) >= 25 else current
        ret24 = (current - prev24) / prev24

        above_ema = current > ema200
        if above_ema and ret24 > 0.005:
            return "risk_on"
        if not above_ema and ret24 < -0.01:
            return "risk_off"
        return "choppy"


class _RandomScorer:
    """Deterministic scorer based on a price-hash — good for smoke tests."""

    def score(self, pair: str, df: pd.DataFrame, regime: str) -> float:
        if regime in ("risk_off", "uncertain"):
            return 0.0
        last_close = float(df["close"].iloc[-1])
        # Deterministic pseudo-random from the price value
        h = hash(f"{pair}{last_close:.4f}") & 0xFFFF
        return h / 0xFFFF


class MLScorer:
    """Scorer backed by a trained TradingModel + FeatureEngineer."""

    def __init__(self, model_path: str | Path = "models/trading_model.joblib") -> None:
        from src.ml.features import FeatureEngineer
        from src.ml.model import TradingModel

        self._fe = FeatureEngineer()
        self._model = TradingModel()
        self._model.load_model(model_path)

    def score(self, pair: str, df: pd.DataFrame, regime: str, btc_4h_change: float = 0.0) -> float:
        if regime in ("risk_off", "uncertain"):
            return 0.0
        try:
            features = self._fe.build_features(df, regime_signal=regime, btc_4h_change=btc_4h_change)
            if len(features) == 0:
                return 0.0
            probs = self._model.predict(features.tail(1))
            return float(probs[0])
        except Exception:
            return 0.0


class _SimpleRisk:
    def should_trade(self) -> bool:
        return True

    def position_size(self, confidence: float, price: float, portfolio: float) -> float:
        # Fixed fractional risk: risk 2% of portfolio per trade, sized by 3% stop-loss.
        # At $10K: risk=$200, position=$667. If stop hits, loss = exactly $200.
        _RISK_PER_TRADE = 0.02   # 2% of portfolio at risk per trade
        _STOP_LOSS_PCT  = 0.03   # hard stop-loss in engine (_step)
        return portfolio * _RISK_PER_TRADE / _STOP_LOSS_PCT


# ---------------------------------------------------------------------------
# Position tracking
# ---------------------------------------------------------------------------


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    """Return the current ATR value from an OHLCV window."""
    if len(df) < period + 1:
        return float(df["close"].iloc[-1]) * 0.01  # fallback: 1% of price
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"],
         (df["high"] - prev_close).abs(),
         (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    val = float(tr.ewm(com=period - 1, min_periods=period).mean().iloc[-1])
    return val if val == val else float(df["close"].iloc[-1]) * 0.01  # NaN guard


def _utc_date_str(ts_ms: int) -> str:
    """Return YYYYMMDD string for a millisecond timestamp in UTC."""
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%Y%m%d")


@dataclass
class _Position:
    pair: str
    entry_price: float
    size_usd: float    # notional in USD
    quantity: float    # base quantity
    entry_ts: int      # milliseconds
    tp_price: float    # ATR-based take-profit level
    direction: str = "long"      # 'long' or 'short'
    trail_active: bool = False   # trailing stop engaged after 12h
    trail_high: float = 0.0     # highest price seen (LONG trailing)
    trail_low: float = 0.0      # lowest price seen (SHORT trailing)
    trail_stop: float = 0.0     # current trailing stop level
    # US-007: journal context fields populated at entry time
    regime_at_entry: str = "unknown"
    regime_confidence: int = 0
    ml_score_at_entry: float = 0.0
    btc_4h_at_entry: float = 0.0
    fear_greed_at_entry: float = 0.0


# ---------------------------------------------------------------------------
# BacktestEngine
# ---------------------------------------------------------------------------


class BacktestEngine:
    """Event-driven backtester.

    Args:
        pairs: List of pairs to backtest.
        timeframe: Candle timeframe string.
        initial_capital: Starting portfolio value in USD.
        data_dir: Root directory for parquet candle files.
        regime_detector: Injectable regime detector.
        scorer: Injectable token scorer.
        risk: Injectable risk manager.
        run_id: Optional run ID (auto-generated if None).
    """

    def __init__(
        self,
        pairs: list[str],
        timeframe: str = "1h",
        initial_capital: float = 10_000.0,
        data_dir: Path | None = None,
        regime_detector: Any = None,
        scorer: Any = None,
        risk: Any = None,
        run_id: str | None = None,
        glm_client: GLMClient | None = None,
        news_fetcher: CryptoPanicFetcher | None = None,
        funding_fetcher: FundingRateFetcher | None = None,
        sentiment_fetcher: FearGreedFetcher | None = None,
        onchain_fetcher: ExchangeFlowFetcher | None = None,
    ) -> None:
        self.pairs = pairs
        self.timeframe = timeframe
        self.initial_capital = initial_capital
        self._data_dir = data_dir or Path("data/candles")
        self._regime = regime_detector or _AlwaysRiskOnRegime()
        self._scorer = scorer or _RandomScorer()
        self._risk = risk or _SimpleRisk()
        self.run_id = run_id or uuid.uuid4().hex[:12]

        # Optional GLM regime detection
        self._glm_client = glm_client
        self._news_fetcher = news_fetcher
        self._funding_fetcher = funding_fetcher
        self._sentiment_fetcher = sentiment_fetcher
        self._onchain_fetcher = onchain_fetcher

        # State (reset on each run)
        self._portfolio: float = initial_capital
        self._positions: dict[str, _Position] = {}
        self._equity_curve: list[tuple[int, float]] = []
        self._trades: list[dict[str, Any]] = []
        # GLM regime cache (reset on each run)
        self._last_glm_regime_ts: int = 0
        self._last_glm_regime_str: str = "neutral"
        # Context tracking for journal population (reset on each run)
        self._last_btc_4h_change: float = 0.0
        self._last_fear_greed: float = 0.0
        self._last_regime_confidence: int = 100
        # Cooldown tracker: pair -> stop-loss exit ts_ms (reset on each run)
        self._cooldowns: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        start: str | datetime,
        end: str | datetime,
        candles: dict[str, pd.DataFrame] | None = None,
    ) -> dict[str, Any]:
        """Replay candles through the pipeline and return summary metrics."""
        # Reset state for determinism
        self._portfolio = self.initial_capital
        self._positions = {}
        self._equity_curve = []
        self._trades = []
        self._last_glm_regime_ts = 0
        self._last_glm_regime_str = "neutral"
        self._last_btc_4h_change = 0.0
        self._last_fear_greed = 0.0
        self._last_regime_confidence = 100
        self._cooldowns = {}

        start_ms = self._to_ms(start)
        end_ms = self._to_ms(end)

        # Load candles
        if candles is None:
            candles = self._load_candles(start_ms, end_ms)

        # Build a merged sorted list of timestamps present in any pair
        all_timestamps = sorted(
            {int(ts) for df in candles.values() for ts in df["timestamp"]}
        )
        all_timestamps = [ts for ts in all_timestamps if start_ms <= ts <= end_ms]

        if not all_timestamps:
            logger.warning("No candles in range %d – %d", start_ms, end_ms)
            return self.generate_report()

        # Walk forward through time
        prev_date_str: str | None = None
        for ts in all_timestamps:
            curr_date_str = _utc_date_str(ts)
            if prev_date_str is not None and curr_date_str != prev_date_str:
                self._write_journal(prev_date_str)
            self._step(ts, candles)
            self._equity_curve.append((ts, self._portfolio))
            prev_date_str = curr_date_str

        # Close any remaining positions at last price
        for pair, pos in list(self._positions.items()):
            last_df = candles.get(pair)
            if last_df is not None and len(last_df) > 0:
                last_price = float(last_df.iloc[-1]["close"])
                self._close_position(pair, last_price, ts, "end_of_backtest")

        # Write journal for the last processed day
        if prev_date_str is not None:
            self._write_journal(prev_date_str)

        self._equity_curve.append((all_timestamps[-1], self._portfolio))
        return self.generate_report()

    def generate_report(self, save: bool = True) -> dict[str, Any]:
        """Compute and optionally save performance metrics as JSON."""
        metrics = self._compute_metrics()
        if save:
            self._save_report(metrics)
        return metrics

    def plot_equity_curve(self, save: bool = True) -> None:
        """Save a matplotlib PNG of the equity curve."""
        if not self._equity_curve:
            logger.warning("No equity curve data to plot")
            return
        try:
            import matplotlib.pyplot as plt

            timestamps, values = zip(*self._equity_curve)
            dates_numeric = [ts / 1000.0 for ts in timestamps]
            fig, ax = plt.subplots(figsize=(12, 5))
            ax.plot(dates_numeric, values)
            ax.set_title(f"Equity Curve — {self.run_id}")
            ax.set_xlabel("Date")
            ax.set_ylabel("Portfolio Value (USD)")
            ax.grid(True)
            plt.tight_layout()
            if save:
                out_path = _REPORTS_DIR / f"{self.run_id}_equity.png"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                plt.savefig(out_path, dpi=100)
                logger.info("Equity curve saved to %s", out_path)
            plt.close(fig)
        except Exception as exc:
            logger.error("Could not plot equity curve: %s", exc)

    # ------------------------------------------------------------------
    # Internal step logic
    # ------------------------------------------------------------------

    def _step(self, ts: int, candles: dict[str, pd.DataFrame]) -> None:
        """Process one timestamp tick."""
        # Check existing positions for exits
        for pair in list(self._positions.keys()):
            pair_df = candles.get(pair)
            if pair_df is None:
                continue
            row = pair_df[pair_df["timestamp"] <= ts]
            if row.empty:
                continue
            price = float(row.iloc[-1]["close"])
            pos = self._positions[pair]
            hold_hours = (ts - pos.entry_ts) / 3_600_000

            if pos.direction == "long":
                pnl_pct = (price - pos.entry_price) / pos.entry_price

                # 1. Hard stop-loss
                if pnl_pct <= -0.03:
                    self._close_position(pair, price, ts, "stop_loss")

                # 2. ATR-based take-profit
                elif price >= pos.tp_price:
                    self._close_position(pair, price, ts, "take_profit")

                # 3. Trailing stop (once activated)
                elif pos.trail_active:
                    pos.trail_high = max(pos.trail_high, price)
                    # Trail distance: 0.5% of the high-water mark
                    pos.trail_stop = max(pos.trail_stop, pos.trail_high * 0.995)
                    if price <= pos.trail_stop:
                        self._close_position(pair, price, ts, "trail_stop")
                    elif hold_hours >= 72:
                        self._close_position(pair, price, ts, "time_exit")

                # 4. 12h barrier: activate trailing if in profit, force-close if not
                elif hold_hours >= 12:
                    if pnl_pct > 0:
                        # Engage trailing stop; floor at entry + 0.5%
                        pos.trail_active = True
                        pos.trail_high = price
                        pos.trail_stop = pos.entry_price * 1.005
                    else:
                        self._close_position(pair, price, ts, "time_exit")

                # 5. Hard max-hold safety valve
                elif hold_hours >= 72:
                    self._close_position(pair, price, ts, "time_exit")

            else:  # short
                pnl_pct = (pos.entry_price - price) / pos.entry_price

                # 1. Hard stop-loss (price moved up against short)
                if pnl_pct <= -0.03:
                    self._close_position(pair, price, ts, "stop_loss")

                # 2. ATR-based take-profit (price fell to TP)
                elif price <= pos.tp_price:
                    self._close_position(pair, price, ts, "take_profit")

                # 3. Trailing stop (once activated)
                elif pos.trail_active:
                    pos.trail_low = min(pos.trail_low, price)
                    # Trail distance: 0.5% above the low-water mark
                    pos.trail_stop = min(pos.trail_stop, pos.trail_low * 1.005)
                    if price >= pos.trail_stop:
                        self._close_position(pair, price, ts, "trail_stop")
                    elif hold_hours >= 72:
                        self._close_position(pair, price, ts, "time_exit")

                # 4. 12h barrier: activate trailing if in profit, force-close if not
                elif hold_hours >= 12:
                    if pnl_pct > 0:
                        # Engage trailing stop; floor at entry − 0.5%
                        pos.trail_active = True
                        pos.trail_low = price
                        pos.trail_stop = pos.entry_price * 0.995
                    else:
                        self._close_position(pair, price, ts, "time_exit")

                # 5. Hard max-hold safety valve
                elif hold_hours >= 72:
                    self._close_position(pair, price, ts, "time_exit")

        # Build BTC window for regime detection
        btc_df = candles.get("BTC/USDT")
        btc_window = btc_df[btc_df["timestamp"] <= ts].tail(250) if btc_df is not None else None

        # Update BTC 4h change for journal context
        if btc_window is not None and len(btc_window) >= 5:
            _btc_cur = float(btc_window["close"].iloc[-1])
            _btc_prev = float(btc_window["close"].iloc[-5])
            self._last_btc_4h_change = (_btc_cur - _btc_prev) / _btc_prev if _btc_prev > 0 else 0.0
        else:
            self._last_btc_4h_change = 0.0

        # Entry logic: score each pair and open new positions
        if not self._risk.should_trade():
            return

        if self._glm_client is not None:
            regime = self._get_glm_regime(ts, btc_window)
        else:
            regime = self._regime.detect({"ts": ts, "btc_window": btc_window})

        if regime == "neutral":
            return

        # BTC momentum cap: skip entries during chaotic BTC moves (GLM-suggested filter)
        if abs(self._last_btc_4h_change) > BTC_MOMENTUM_CAP:
            return

        # BTC directional gate: skip long entries when BTC 4h return is negative.
        # Treats missing BTC data (0.0) as neutral — does not block entry.
        if self._last_btc_4h_change < BTC_BULL_GATE:
            return

        for pair in self.pairs:
            if pair in self._positions:
                continue  # already in position

            # Concurrent position cap: prevent correlated cascade failures
            if len(self._positions) >= MAX_CONCURRENT_POSITIONS:
                break

            # Token cooldown: skip pairs that hit a stop-loss within 48h
            if _cooldown_tracker.is_cooling(self._cooldowns, pair, ts):
                continue

            pair_df = candles.get(pair)
            if pair_df is None:
                continue

            window = pair_df[pair_df["timestamp"] <= ts].tail(200)
            if len(window) < 50:
                continue

            # Volume confirmation: skip entries on low-volume moves
            current_volume = float(window["volume"].iloc[-1])
            mean_volume_20 = float(window["volume"].tail(20).mean())
            vol_ratio = current_volume / mean_volume_20 if mean_volume_20 > 0 else 1.0
            if vol_ratio < MIN_VOLUME_RATIO:
                continue

            # Relative strength filter: only enter tokens outperforming BTC on 4h return
            if RS_FILTER_ENABLED and len(window) >= 5:
                current_price = float(window["close"].iloc[-1])
                price_4h_ago = float(window["close"].iloc[-5])
                token_4h_return = (current_price - price_4h_ago) / price_4h_ago if price_4h_ago > 0 else 0.0
                if token_4h_return <= self._last_btc_4h_change:
                    continue

            confidence = self._scorer.score(pair, window, regime, btc_4h_change=self._last_btc_4h_change)

            if confidence < ML_THRESHOLD:
                continue

            # Use the most recent candle at or before ts (paper trading: ts is wall-clock,
            # not a candle boundary, so exact match would always miss)
            current_row = pair_df[pair_df["timestamp"] <= ts]
            if current_row.empty:
                continue

            price = float(current_row.iloc[-1]["close"])
            fill_price = price * (1 + SLIPPAGE)
            size_usd = self._risk.position_size(confidence, fill_price, self._portfolio)

            if size_usd <= 0 or size_usd > self._portfolio:
                continue

            fee = size_usd * TAKER_FEE
            quantity = (size_usd - fee) / fill_price
            self._portfolio -= size_usd  # commit full capital; entry fee embedded in quantity

            # ATR-based take-profit: ATR_MULT × ATR above fill price
            current_atr = _atr(window)
            tp_price = fill_price + ATR_MULT * current_atr

            self._positions[pair] = _Position(
                pair=pair,
                entry_price=fill_price,
                size_usd=size_usd,
                quantity=quantity,
                entry_ts=ts,
                tp_price=tp_price,
                direction="long",
                regime_at_entry=regime,
                regime_confidence=self._last_regime_confidence,
                ml_score_at_entry=confidence,
                btc_4h_at_entry=self._last_btc_4h_change,
                fear_greed_at_entry=self._last_fear_greed,
            )
            logger.debug("OPEN LONG %s @ %.4f qty=%.6f conf=%.3f", pair, fill_price, quantity, confidence)

        # SHORT entries disabled: MLScorer returns 0.0 in risk_off regime so all pairs
        # qualify as shorts, producing lossy entries with no real edge.
        # Re-enable once a dedicated short-side model is trained.

    def _get_glm_regime(self, ts: int, btc_window: pd.DataFrame | None) -> str:
        """Call GLM-5 for regime classification every 4h; return cached result between calls."""
        _4H_MS = 4 * 3_600_000
        if self._last_glm_regime_ts > 0 and (ts - self._last_glm_regime_ts) < _4H_MS:
            return self._last_glm_regime_str

        # Build BTC context
        btc_price = 0.0
        btc_4h_change = 0.0
        btc_24h_change = 0.0
        btc_atr_val = 0.0
        if btc_window is not None and len(btc_window) > 0:
            btc_price = float(btc_window["close"].iloc[-1])
            btc_atr_val = _atr(btc_window)
            if len(btc_window) >= 5:
                prev_4h = float(btc_window["close"].iloc[-5])
                btc_4h_change = (btc_price - prev_4h) / prev_4h if prev_4h > 0 else 0.0
            if len(btc_window) >= 25:
                prev_24h = float(btc_window["close"].iloc[-25])
                btc_24h_change = (btc_price - prev_24h) / prev_24h if prev_24h > 0 else 0.0

        headlines: list[str] = []
        if self._news_fetcher is not None:
            headlines = self._news_fetcher.fetch()

        funding_rates: dict[str, float] = {}
        if self._funding_fetcher is not None:
            funding_rates = self._funding_fetcher.fetch(self.pairs)

        fear_greed_score = 0.0
        fear_greed_label = "unknown"
        if self._sentiment_fetcher is not None:
            dt = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
            result = self._sentiment_fetcher.fetch(dt)
            fear_greed_score = result.score
            fear_greed_label = result.label

        exchange_netflow_btc: float | None = None
        if self._onchain_fetcher is not None:
            exchange_netflow_btc = self._onchain_fetcher.fetch()

        context = {
            "btc_price": btc_price,
            "btc_4h_change_pct": btc_4h_change,
            "btc_24h_change_pct": btc_24h_change,
            "btc_atr": btc_atr_val,
            "headlines": headlines,
            "funding_rates": funding_rates,
            "fear_greed_score": fear_greed_score,
            "fear_greed_label": fear_greed_label,
            "exchange_netflow_btc": exchange_netflow_btc,
        }

        response = self._glm_client.get_regime(context)  # type: ignore[union-attr]
        self._last_fear_greed = fear_greed_score
        self._last_regime_confidence = response.confidence
        effective_regime = response.regime if response.confidence >= 60 else "neutral"
        logger.info(
            "GLM regime @ ts=%d: %s (confidence=%d) — %s",
            ts, response.regime, response.confidence, response.reasoning,
        )

        self._last_glm_regime_ts = ts
        self._last_glm_regime_str = effective_regime
        return effective_regime

    def _close_position(self, pair: str, price: float, ts: int, reason: str) -> None:
        pos = self._positions.pop(pair, None)
        if pos is None:
            return
        if reason == "stop_loss":
            _cooldown_tracker.record_stop(self._cooldowns, pair, ts)
        if pos.direction == "long":
            fill_price = price * (1.0 - SLIPPAGE)
            proceeds = pos.quantity * fill_price
            fee = proceeds * TAKER_FEE
            pnl = proceeds - fee - pos.size_usd
        else:  # short: buy back at worse (higher) price
            fill_price = price * (1.0 + SLIPPAGE)
            pnl = pos.size_usd - pos.quantity * fill_price * (1.0 + TAKER_FEE)
        self._portfolio += pos.size_usd + pnl
        hold_hours = (ts - pos.entry_ts) / 3_600_000
        self._trades.append(
            {
                "pair": pair,
                "direction": pos.direction,
                "entry_price": pos.entry_price,
                "exit_price": fill_price,
                "pnl_usd": pnl,
                "pnl_pct": pnl / pos.size_usd,
                "reason": reason,
                "hold_hours": hold_hours,
                "exit_date_utc": _utc_date_str(ts),
                "regime_at_entry": pos.regime_at_entry,
                "regime_confidence": pos.regime_confidence,
                "ml_score_at_entry": pos.ml_score_at_entry,
                "btc_4h_change_at_entry": pos.btc_4h_at_entry,
                "fear_greed_at_entry": pos.fear_greed_at_entry,
            }
        )
        logger.debug(
            "CLOSE %s %s @ %.4f pnl=%.2f reason=%s",
            pos.direction.upper(), pair, fill_price, pnl, reason,
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _compute_metrics(self) -> dict[str, Any]:
        trades = self._trades
        equity = self._equity_curve

        total_return_pct = ((self._portfolio - self.initial_capital) / self.initial_capital) * 100

        # Sharpe (annualised, hourly returns)
        annualised_sharpe = 0.0
        if len(equity) > 1:
            values = [v for _, v in equity]
            hourly_returns = [
                (values[i] - values[i - 1]) / values[i - 1]
                for i in range(1, len(values))
                if values[i - 1] > 0
            ]
            if len(hourly_returns) > 1:
                mean_r = sum(hourly_returns) / len(hourly_returns)
                variance = sum((r - mean_r) ** 2 for r in hourly_returns) / len(hourly_returns)
                std_r = math.sqrt(variance) if variance > 0 else 0.0
                if std_r > 0:
                    annualised_sharpe = (mean_r / std_r) * math.sqrt(8760)  # sqrt(hours/year)

        # Max drawdown
        max_drawdown_pct = 0.0
        if equity:
            peak = equity[0][1]
            for _, v in equity:
                peak = max(peak, v)
                dd = (peak - v) / peak * 100 if peak > 0 else 0.0
                max_drawdown_pct = max(max_drawdown_pct, dd)

        # Win rate / profit factor
        wins = [t for t in trades if t["pnl_usd"] > 0]
        losses = [t for t in trades if t["pnl_usd"] <= 0]
        win_rate = len(wins) / len(trades) if trades else 0.0
        gross_profit = sum(t["pnl_usd"] for t in wins)
        gross_loss = abs(sum(t["pnl_usd"] for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
        avg_hold = sum(t["hold_hours"] for t in trades) / len(trades) if trades else 0.0

        return {
            "run_id": self.run_id,
            "total_return_pct": round(total_return_pct, 4),
            "annualised_sharpe": round(annualised_sharpe, 4),
            "max_drawdown_pct": round(max_drawdown_pct, 4),
            "win_rate": round(win_rate, 4),
            "total_trades": len(trades),
            "avg_hold_hours": round(avg_hold, 2),
            "profit_factor": round(profit_factor, 4),
        }

    def _save_report(self, metrics: dict[str, Any]) -> None:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = _REPORTS_DIR / f"{self.run_id}.json"
        with path.open("w") as f:
            json.dump(metrics, f, indent=2)
        logger.info("Backtest report saved to %s", path)

    def _write_journal(self, date_str: str) -> None:
        """Write daily trade journal for date_str (YYYYMMDD) if ≥1 trade closed that day."""
        day_trades = [t for t in self._trades if t.get("exit_date_utc") == date_str]
        if not day_trades:
            return
        wins = [t for t in day_trades if t["pnl_usd"] > 0]
        losses = [t for t in day_trades if t["pnl_usd"] <= 0]
        total_pnl = sum(t["pnl_usd"] for t in day_trades)
        win_rate_pct = round(len(wins) / len(day_trades) * 100, 2)
        journal: dict[str, Any] = {
            "date": date_str,
            "run_id": self.run_id,
            "trades": [
                {
                    "pair": t["pair"],
                    "direction": t["direction"],
                    "entry_price": t["entry_price"],
                    "exit_price": t["exit_price"],
                    "pnl_pct": t["pnl_pct"],
                    "pnl_usd": t["pnl_usd"],
                    "hold_hours": t["hold_hours"],
                    "reason": t["reason"],
                    "regime_at_entry": t.get("regime_at_entry", "unknown"),
                    "regime_confidence": t.get("regime_confidence", 0),
                    "ml_score_at_entry": t.get("ml_score_at_entry", 0.0),
                    "btc_4h_change_at_entry": t.get("btc_4h_change_at_entry", 0.0),
                    "fear_greed_at_entry": t.get("fear_greed_at_entry", 0.0),
                }
                for t in day_trades
            ],
            "summary": {
                "total_trades": len(day_trades),
                "wins": len(wins),
                "losses": len(losses),
                "total_pnl_usd": round(total_pnl, 4),
                "win_rate_pct": win_rate_pct,
            },
        }
        _JOURNALS_DIR.mkdir(parents=True, exist_ok=True)
        path = _JOURNALS_DIR / f"{self.run_id}_{date_str}.json"
        with path.open("w") as f:
            json.dump(journal, f, indent=2)
        logger.info("Trade journal saved to %s", path)

    def _load_candles(self, start_ms: int, end_ms: int) -> dict[str, pd.DataFrame]:
        result: dict[str, pd.DataFrame] = {}
        for pair in self.pairs:
            pair_dir = pair.replace("/", "-")
            path = self._data_dir / pair_dir / f"{self.timeframe}.parquet"
            if not path.exists():
                logger.warning("No candle data for %s at %s", pair, path)
                continue
            df = pd.read_parquet(path)
            df = df[(df["timestamp"] >= start_ms) & (df["timestamp"] <= end_ms)]
            result[pair] = df.reset_index(drop=True)
        return result

    @staticmethod
    def _to_ms(dt: str | datetime) -> int:
        if isinstance(dt, datetime):
            d = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        else:
            d = datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
        return int(d.timestamp() * 1000)
