"""Tests for MED-002: relative strength filter.

Verifies that:
- Token with lower 4h return than BTC is skipped.
- Token with higher 4h return than BTC is allowed.
- Filter is bypassed when window has fewer than 5 candles (no crash, no block).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.backtesting.engine import BacktestEngine, RS_FILTER_ENABLED


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


def _build_candles(
    token_4h_return: float,
    btc_4h_return: float,
    n: int = 60,
    start_ts: int = 1_700_000_000_000,
    interval_ms: int = 3_600_000,
) -> dict[str, pd.DataFrame]:
    """Build candles for ETH and BTC with specified 4h returns.

    The engine uses window.iloc[-5] for the token's 4h prior price.
    We put the price change in the last 5 candles so that:
      token_4h_return = (current - price_4h_ago) / price_4h_ago
    """
    timestamps = [start_ts + i * interval_ms for i in range(n)]
    # Last-candle volume 1.5x to pass the volume filter
    volumes = [1_000_000.0] * (n - 1) + [1_500_000.0]

    # Token (ETH): engine uses window.iloc[-5] as 4h-ago price and iloc[-1] as current.
    # For iloc[-5]=eth_base and iloc[-1]=eth_current, keep last 4 candles at eth_current.
    eth_base = 2_000.0
    eth_current = eth_base * (1 + token_4h_return)
    eth_prices = [eth_base] * (n - 4) + [eth_current] * 4
    eth_df = pd.DataFrame({
        "timestamp": timestamps,
        "open": eth_prices,
        "high": [p * 1.001 for p in eth_prices],
        "low": [p * 0.999 for p in eth_prices],
        "close": eth_prices,
        "volume": volumes,
    })

    # BTC: first n-1 at base, last 1 at current (so _last_btc_4h_change is correct)
    btc_base = 60_000.0
    btc_current = btc_base * (1 + btc_4h_return)
    btc_prices = [btc_base] * (n - 1) + [btc_current]
    btc_df = pd.DataFrame({
        "timestamp": timestamps,
        "open": btc_prices,
        "high": [p * 1.001 for p in btc_prices],
        "low": [p * 0.999 for p in btc_prices],
        "close": btc_prices,
        "volume": [1_000_000.0] * n,
    })

    return {"ETH/USDT": eth_df, "BTC/USDT": btc_df}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRSFilter:
    def test_constant_exists_and_is_true(self) -> None:
        assert RS_FILTER_ENABLED is True

    def test_token_underperforming_btc_is_skipped(self) -> None:
        """Token 4h return (+0.5%) <= BTC 4h return (+1%) → entry skipped."""
        engine = _make_engine()
        candles = _build_candles(token_4h_return=0.005, btc_4h_return=0.01)
        ts = int(candles["ETH/USDT"]["timestamp"].iloc[-1])

        engine._step(ts, candles)

        assert len(engine._positions) == 0, (
            "Token underperforming BTC should be blocked by RS filter"
        )

    def test_token_outperforming_btc_is_allowed(self) -> None:
        """Token 4h return (+2%) > BTC 4h return (+1%) → entry allowed."""
        engine = _make_engine()
        candles = _build_candles(token_4h_return=0.02, btc_4h_return=0.01)
        ts = int(candles["ETH/USDT"]["timestamp"].iloc[-1])

        engine._step(ts, candles)

        assert len(engine._positions) == 1, (
            "Token outperforming BTC should be allowed by RS filter"
        )

    def test_filter_bypassed_when_window_too_short(self) -> None:
        """Window with < 5 rows → RS check skipped, no crash.

        In practice the engine's ``len(window) < 50`` guard fires first and
        continues before reaching the RS check.  This test confirms no exception
        is raised when candle history is minimal.
        """
        engine = _make_engine()
        # Set BTC 4h change very high so RS would normally block everything
        engine._last_btc_4h_change = 0.99

        n = 4
        start_ts = 1_700_000_000_000
        interval = 3_600_000
        timestamps = [start_ts + i * interval for i in range(n)]
        prices = [2_000.0] * n
        volumes = [1_000_000.0] * (n - 1) + [1_500_000.0]
        eth_df = pd.DataFrame({
            "timestamp": timestamps,
            "open": prices,
            "high": [p * 1.001 for p in prices],
            "low": [p * 0.999 for p in prices],
            "close": prices,
            "volume": volumes,
        })
        candles = {"ETH/USDT": eth_df}
        ts = int(eth_df["timestamp"].iloc[-1])

        # Should not raise; len(window)=4 < 50 → early continue before RS check
        try:
            engine._step(ts, candles)
        except Exception as exc:
            pytest.fail(f"_step raised unexpectedly with short window: {exc}")
        # Position stays closed (len < 50 guard skips entry entirely)
        assert len(engine._positions) == 0
