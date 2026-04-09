"""Tests for IMMED-002: concurrent position cap.

Verifies that:
- At most MAX_CONCURRENT_POSITIONS longs open simultaneously.
- A 6th position is skipped when 5 are already open.
- Closing a position allows a new one to open on the next tick.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from src.backtesting.engine import BacktestEngine, MAX_CONCURRENT_POSITIONS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candles(
    close_prices: list[float],
    start_ts: int = 1_700_000_000_000,
    interval_ms: int = 3_600_000,
) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a list of close prices."""
    n = len(close_prices)
    timestamps = [start_ts + i * interval_ms for i in range(n)]
    # Last candle has 1.5x volume so vol_ratio >= MIN_VOLUME_RATIO (1.2) for entry tests.
    volumes = [1_000_000.0] * (n - 1) + [1_500_000.0]
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": close_prices,
            "high": [p * 1.001 for p in close_prices],
            "low": [p * 0.999 for p in close_prices],
            "close": close_prices,
            "volume": volumes,
        }
    )


def _make_engine(pairs: list[str], scorer_confidence: float = 0.9) -> BacktestEngine:
    """Return a BacktestEngine with mocked scorer, regime, and risk."""
    scorer = MagicMock()
    scorer.score.return_value = scorer_confidence

    regime = MagicMock()
    regime.detect.return_value = "risk_on"

    risk = MagicMock()
    risk.should_trade.return_value = True
    risk.position_size.return_value = 500.0

    return BacktestEngine(
        pairs=pairs,
        initial_capital=20_000.0,
        regime_detector=regime,
        scorer=scorer,
        risk=risk,
    )


def _build_candles(pairs: list[str], start_ts: int = 1_700_000_000_000) -> dict[str, pd.DataFrame]:
    """Build a candle dict for each pair with 60 candles (no BTC, neutral gate)."""
    candles: dict[str, pd.DataFrame] = {}
    for pair in pairs:
        candles[pair] = _make_candles([100.0] * 60, start_ts=start_ts)
    return candles


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPositionCap:
    def test_constant_exists_and_equals_five(self) -> None:
        assert MAX_CONCURRENT_POSITIONS == 5

    def test_exactly_five_open_sixth_skipped(self) -> None:
        """With 6 pairs scored above threshold, only 5 positions should open."""
        pairs = [f"TOKEN{i}/USDT" for i in range(6)]
        engine = _make_engine(pairs=pairs)

        # BTC_BULL_GATE: without BTC candles _last_btc_4h_change=0.0, so not blocked.
        candles = _build_candles(pairs)
        ts = int(next(iter(candles.values()))["timestamp"].iloc[-1])

        engine._step(ts, candles)

        assert len(engine._positions) == 5, (
            f"Expected exactly 5 open positions, got {len(engine._positions)}"
        )

    def test_closing_one_allows_new_entry(self) -> None:
        """After closing a position, the next tick can open a new one."""
        pairs = [f"TOKEN{i}/USDT" for i in range(6)]
        engine = _make_engine(pairs=pairs)

        start_ts = 1_700_000_000_000
        interval = 3_600_000

        # Tick 1 — fill up to cap (5 open)
        candles = _build_candles(pairs, start_ts=start_ts)
        ts1 = int(next(iter(candles.values()))["timestamp"].iloc[-1])
        engine._step(ts1, candles)
        assert len(engine._positions) == 5

        # Force-close one position directly (simulates stop-loss / take-profit)
        closed_pair = next(iter(engine._positions))
        closed_pos = engine._positions.pop(closed_pair)
        engine._portfolio += closed_pos.size_usd  # return capital
        assert len(engine._positions) == 4

        # Tick 2 — one slot now free; skipped_pair should open
        ts2 = ts1 + interval
        # Extend all candles by one row so the window is valid at ts2
        candles2: dict[str, pd.DataFrame] = {}
        for pair, df in candles.items():
            extra = pd.DataFrame(
                [
                    {
                        "timestamp": ts2,
                        "open": 100.0,
                        "high": 100.1,
                        "low": 99.9,
                        "close": 100.0,
                        "volume": 1_500_000.0,  # 1.5x for vol filter
                    }
                ]
            )
            candles2[pair] = pd.concat([df, extra], ignore_index=True)

        engine._step(ts2, candles2)

        assert len(engine._positions) == 5, (
            f"Expected 5 positions after re-fill, got {len(engine._positions)}"
        )
