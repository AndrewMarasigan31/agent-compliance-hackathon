"""Tests for IMMED-001: BTC directional gate.

Verifies that:
- No long positions open when BTC 4h return is negative.
- Long positions do open when BTC 4h return is positive.
- Existing open positions are NOT closed/affected by the gate.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.backtesting.engine import BacktestEngine, BTC_BULL_GATE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candles(close_prices: list[float], start_ts: int = 1_700_000_000_000, interval_ms: int = 3_600_000) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a list of close prices."""
    n = len(close_prices)
    timestamps = [start_ts + i * interval_ms for i in range(n)]
    # Last candle has 1.5x volume so vol_ratio >= MIN_VOLUME_RATIO (1.2) for entry tests.
    volumes = [1_000_000.0] * (n - 1) + [1_500_000.0]
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": close_prices,
        "high": [p * 1.001 for p in close_prices],
        "low": [p * 0.999 for p in close_prices],
        "close": close_prices,
        "volume": volumes,
    })


def _make_engine(scorer_confidence: float = 0.9) -> BacktestEngine:
    """Return a BacktestEngine with injectable scorer and deterministic regime."""
    scorer = MagicMock()
    scorer.score.return_value = scorer_confidence

    regime = MagicMock()
    regime.detect.return_value = "risk_on"

    risk = MagicMock()
    risk.should_trade.return_value = True
    risk.position_size.return_value = 500.0

    engine = BacktestEngine(
        pairs=["ETH/USDT"],
        initial_capital=10_000.0,
        regime_detector=regime,
        scorer=scorer,
        risk=risk,
    )
    return engine


def _build_candle_dict(btc_4h_return: float, pair: str = "ETH/USDT") -> dict[str, pd.DataFrame]:
    """Build a candles dict with a BTC series producing the desired 4h return.

    BTC: 55 candles — first 50 at base price, last 5 at current price.
    This ensures _last_btc_4h_change = (current - base) / base = btc_4h_return.
    Pair: 55 candles aligned to the same timestamps.
    At the final timestamp, the pair window has all 55 candles (>= 50).
    """
    btc_base = 60_000.0
    btc_current = btc_base * (1 + btc_4h_return)

    # 55 candles: first 54 at base, last 1 at current.
    # engine uses iloc[-1] (current) and iloc[-5] (index 50 = base), so
    # _last_btc_4h_change = (btc_current - btc_base) / btc_base = btc_4h_return
    btc_prices = [btc_base] * 54 + [btc_current] * 1
    ts_start = 1_700_000_000_000
    btc_df = _make_candles(btc_prices, start_ts=ts_start)

    # Pair candles: same 55 timestamps so the window has >= 50 rows at the final ts
    eth_prices = [2_000.0] * 55  # noqa: unchanged count
    eth_df = _make_candles(eth_prices, start_ts=ts_start)

    return {"BTC/USDT": btc_df, pair: eth_df}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBTCBullGate:
    def test_constant_exists_and_is_zero(self):
        assert BTC_BULL_GATE == 0.0

    def test_no_longs_when_btc_4h_negative(self):
        """Gate blocks entry when BTC 4h return is -1% (< 0.0)."""
        engine = _make_engine(scorer_confidence=0.9)
        candles = _build_candle_dict(btc_4h_return=-0.01)

        ts = int(candles["BTC/USDT"]["timestamp"].iloc[-1])
        engine._step(ts, candles)

        assert len(engine._positions) == 0, (
            "No positions should open when BTC 4h return is negative"
        )

    def test_longs_open_when_btc_4h_positive(self):
        """Gate allows entry when BTC 4h return is +1% (> 0.0)."""
        engine = _make_engine(scorer_confidence=0.9)
        candles = _build_candle_dict(btc_4h_return=0.01)

        ts = int(candles["BTC/USDT"]["timestamp"].iloc[-1])
        engine._step(ts, candles)

        assert len(engine._positions) == 1, (
            "A long position should open when BTC 4h return is positive"
        )

    def test_existing_positions_not_closed_by_gate(self):
        """Positions already open when the gate is active remain open."""
        engine = _make_engine(scorer_confidence=0.9)
        candles = _build_candle_dict(btc_4h_return=0.01)
        ts = int(candles["BTC/USDT"]["timestamp"].iloc[-1])

        # Open a position while BTC is positive
        engine._step(ts, candles)
        assert len(engine._positions) == 1, "Precondition: position opened"

        # Now simulate BTC going negative at the next tick
        next_candles = _build_candle_dict(btc_4h_return=-0.01)
        # Keep the same position in the engine; advance one candle
        next_ts = ts + 3_600_000
        # Add a row to the ETH candles so exit logic has a price
        existing_eth = next_candles["ETH/USDT"]
        extra_row = pd.DataFrame([{
            "timestamp": next_ts,
            "open": 2_000.0,
            "high": 2_002.0,
            "low": 1_998.0,
            "close": 2_000.0,
            "volume": 1_000_000.0,
        }])
        next_candles["ETH/USDT"] = pd.concat([existing_eth, extra_row], ignore_index=True)

        extra_btc = pd.DataFrame([{
            "timestamp": next_ts,
            "open": 58_000.0,
            "high": 58_100.0,
            "low": 57_900.0,
            "close": 58_000.0,  # still negative vs the base of 60_000
            "volume": 1_000_000.0,
        }])
        next_candles["BTC/USDT"] = pd.concat([next_candles["BTC/USDT"], extra_btc], ignore_index=True)

        engine._step(next_ts, next_candles)

        # The existing ETH/USDT position must still be open (price hasn't hit SL/TP)
        assert "ETH/USDT" in engine._positions, (
            "Existing position must not be closed by the BTC directional gate"
        )

    def test_gate_noop_when_btc_unavailable(self):
        """When BTC candles are absent, _last_btc_4h_change defaults to 0.0 — entry not blocked."""
        engine = _make_engine(scorer_confidence=0.9)

        eth_prices = [2_000.0] * 50
        ts_start = 1_700_000_000_000
        eth_df = _make_candles(eth_prices, start_ts=ts_start)
        candles = {"ETH/USDT": eth_df}  # no BTC candles

        ts = int(eth_df["timestamp"].iloc[-1])
        engine._step(ts, candles)

        # 0.0 is not < BTC_BULL_GATE (0.0), so entry should be allowed
        assert len(engine._positions) == 1, (
            "Entry should be allowed when BTC data is unavailable (treated as 0.0)"
        )
