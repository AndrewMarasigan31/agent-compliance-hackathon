"""Tests for MED-001: volume confirmation filter.

Verifies that:
- Entry is blocked when volume is 1.0x the 20-period average (below MIN_VOLUME_RATIO=1.2).
- Entry is allowed when volume is 1.5x the 20-period average.
- No crash when all volume data is zero (ratio treated as 1.0 → entry blocked).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from src.backtesting.engine import BacktestEngine, MIN_VOLUME_RATIO


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine() -> BacktestEngine:
    scorer = MagicMock()
    scorer.score.return_value = 0.9

    regime = MagicMock()
    regime.detect.return_value = "risk_on"

    risk = MagicMock()
    risk.should_trade.return_value = True
    risk.position_size.return_value = 500.0

    return BacktestEngine(
        pairs=["ETH/USDT"],
        initial_capital=10_000.0,
        regime_detector=regime,
        scorer=scorer,
        risk=risk,
    )


def _make_candles(
    avg_volume: float,
    last_volume: float,
    n_candles: int = 60,
    start_ts: int = 1_700_000_000_000,
    interval_ms: int = 3_600_000,
) -> pd.DataFrame:
    """Build candles where the first n-1 candles have avg_volume and the last has last_volume."""
    n = n_candles
    timestamps = [start_ts + i * interval_ms for i in range(n)]
    close = 1_000.0
    volumes = [avg_volume] * (n - 1) + [last_volume]
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [close] * n,
            "high": [close * 1.001] * n,
            "low": [close * 0.999] * n,
            "close": [close] * n,
            "volume": volumes,
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVolumeFilter:
    def test_constant_exists_and_is_1_2(self) -> None:
        assert MIN_VOLUME_RATIO == 1.2

    def test_entry_blocked_when_volume_is_1x_average(self) -> None:
        """vol_ratio = 1.0 < 1.2 → entry skipped."""
        engine = _make_engine()
        avg_vol = 1_000_000.0
        eth_df = _make_candles(avg_volume=avg_vol, last_volume=avg_vol * 1.0)
        candles = {"ETH/USDT": eth_df}
        ts = int(eth_df["timestamp"].iloc[-1])

        engine._step(ts, candles)

        assert len(engine._positions) == 0, (
            "Entry should be blocked when volume ratio is below MIN_VOLUME_RATIO"
        )

    def test_entry_allowed_when_volume_is_1_5x_average(self) -> None:
        """vol_ratio = 1.5 >= 1.2 → entry opens."""
        engine = _make_engine()
        avg_vol = 1_000_000.0
        eth_df = _make_candles(avg_volume=avg_vol, last_volume=avg_vol * 1.5)
        candles = {"ETH/USDT": eth_df}
        ts = int(eth_df["timestamp"].iloc[-1])

        engine._step(ts, candles)

        assert len(engine._positions) == 1, (
            "Entry should open when volume ratio is above MIN_VOLUME_RATIO"
        )

    def test_no_crash_when_volume_data_all_zeros(self) -> None:
        """All volumes = 0 → mean=0 → ratio treated as 1.0 → blocked, no exception."""
        engine = _make_engine()
        eth_df = _make_candles(avg_volume=0.0, last_volume=0.0)
        candles = {"ETH/USDT": eth_df}
        ts = int(eth_df["timestamp"].iloc[-1])

        # Should not raise
        engine._step(ts, candles)

        # ratio defaults to 1.0, which is < 1.2, so no position opens
        assert len(engine._positions) == 0
