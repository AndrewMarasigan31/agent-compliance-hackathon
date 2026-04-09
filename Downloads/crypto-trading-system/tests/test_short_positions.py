"""Tests for SHORT position support in BacktestEngine (US-002)."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from src.backtesting.engine import (
    ATR_MULT,
    ML_THRESHOLD,
    SLIPPAGE,
    TAKER_FEE,
    BacktestEngine,
    _Position,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

START_TS = 1_700_000_000_000
INTERVAL_MS = 3_600_000


def _make_candles(
    prices: list[float],
    start_ts: int = START_TS,
    interval_ms: int = INTERVAL_MS,
    pct_range: float = 0.01,
) -> pd.DataFrame:
    """Build OHLCV DataFrame with ±pct_range high/low around each close."""
    timestamps = [start_ts + i * interval_ms for i in range(len(prices))]
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": prices,
            "high": [p * (1.0 + pct_range) for p in prices],
            "low": [p * (1.0 - pct_range) for p in prices],
            "close": prices,
            "volume": [1_000_000.0] * max(0, len(prices) - 7) + [1_500_000.0] * min(7, len(prices)),
        }
    )


def _dt(ts: int) -> datetime:
    return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)


class _FixedRegime:
    def __init__(self, regime: str) -> None:
        self._regime = regime

    def detect(self, market_data: dict) -> str:
        return self._regime


class _FixedScorer:
    def __init__(self, score: float) -> None:
        self._score = score

    def score(self, pair: str, df: pd.DataFrame, regime: str, btc_4h_change: float = 0.0) -> float:
        return self._score


class _FixedRisk:
    def __init__(self, size_usd: float = 100.0) -> None:
        self._size = size_usd

    def should_trade(self) -> bool:
        return True

    def position_size(self, confidence: float, price: float, portfolio: float) -> float:
        return self._size


def _run(
    regime: str,
    score: float,
    prices: list[float],
    pair: str = "ETH/USDT",
) -> BacktestEngine:
    candles = {pair: _make_candles(prices)}
    engine = BacktestEngine(
        pairs=[pair],
        regime_detector=_FixedRegime(regime),
        scorer=_FixedScorer(score),
        risk=_FixedRisk(100.0),
        run_id="test",
    )
    n = len(prices)
    engine.run(
        _dt(START_TS),
        _dt(START_TS + (n - 1) * INTERVAL_MS),
        candles=candles,
    )
    return engine


# ---------------------------------------------------------------------------
# SHORT TP tests
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="SHORT positions intentionally disabled in engine")
def test_short_tp_fires_when_price_falls_to_tp_level() -> None:
    """SHORT TP fires when price drops to ≤ tp_price.

    ATR ≈ 2.0 (±1% range on price=100), so tp_price ≈ 99.95 − 4.8 = 95.15.
    Price held at 97 for 9 candles (above TP), then drops to 94 (below TP).
    """
    # 50 warmup candles → entry at tick 49; then 9 at 97 (hold < 12h); then 1 at 94
    prices = [100.0] * 50 + [97.0] * 9 + [94.0]

    engine = _run("risk_off", 0.1, prices)  # score 0.1 ≤ 0.35 → qualifies

    short_trades = [t for t in engine._trades if t.get("direction") == "short"]
    assert len(short_trades) >= 1, "Expected at least one SHORT trade"
    assert short_trades[0]["reason"] == "take_profit"
    assert short_trades[0]["pnl_usd"] > 0


# ---------------------------------------------------------------------------
# SHORT trailing-stop tests
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="SHORT positions intentionally disabled in engine")
def test_short_trailing_stop_triggers_on_bounce() -> None:
    """SHORT trailing stop fires when price bounces after falling.

    Entry at tick 49 (price=100).
    Ticks 50-61 (12h): price=97 — in profit, trail activates at 12h.
      trail_low=97, trail_stop=entry*0.995=99.45
    Tick 62: price=96 → trail_low=96, trail_stop=min(99.45, 96.48)=96.48
    Tick 63: price=97 → 97 ≥ 96.48 → trail_stop fires.
    """
    prices = [100.0] * 50 + [97.0] * 12 + [96.0] + [97.0]

    engine = _run("risk_off", 0.1, prices)

    short_trades = [t for t in engine._trades if t.get("direction") == "short"]
    assert len(short_trades) >= 1, "Expected at least one SHORT trade"
    assert short_trades[0]["reason"] == "trail_stop"


# ---------------------------------------------------------------------------
# SHORT PnL tests
# ---------------------------------------------------------------------------


def test_short_pnl_positive_when_price_falls_2pct() -> None:
    """SHORT PnL is positive when price falls 2% from entry."""
    engine = BacktestEngine(pairs=["ETH/USDT"], run_id="pnl_pos")
    engine._portfolio = 10_000.0

    entry_price = 100.0 * (1.0 - SLIPPAGE)
    size_usd = 100.0
    quantity = (size_usd * (1.0 - TAKER_FEE)) / entry_price

    engine._positions["ETH/USDT"] = _Position(
        pair="ETH/USDT",
        entry_price=entry_price,
        size_usd=size_usd,
        quantity=quantity,
        entry_ts=0,
        tp_price=95.0,
        direction="short",
    )

    engine._close_position("ETH/USDT", 100.0 * 0.98, 3_600_000, "take_profit")

    assert len(engine._trades) == 1
    trade = engine._trades[0]
    assert trade["direction"] == "short"
    assert trade["pnl_usd"] > 0


def test_short_pnl_negative_when_price_rises_2pct() -> None:
    """SHORT PnL is negative when price rises 2% from entry."""
    engine = BacktestEngine(pairs=["ETH/USDT"], run_id="pnl_neg")
    engine._portfolio = 10_000.0

    entry_price = 100.0 * (1.0 - SLIPPAGE)
    size_usd = 100.0
    quantity = (size_usd * (1.0 - TAKER_FEE)) / entry_price

    engine._positions["ETH/USDT"] = _Position(
        pair="ETH/USDT",
        entry_price=entry_price,
        size_usd=size_usd,
        quantity=quantity,
        entry_ts=0,
        tp_price=95.0,
        direction="short",
    )

    engine._close_position("ETH/USDT", 100.0 * 1.02, 3_600_000, "stop_loss")

    assert len(engine._trades) == 1
    trade = engine._trades[0]
    assert trade["direction"] == "short"
    assert trade["pnl_usd"] < 0


# ---------------------------------------------------------------------------
# Regression: LONG logic unchanged
# ---------------------------------------------------------------------------


def test_long_tp_fires_when_price_rises() -> None:
    """Existing LONG TP logic unchanged — fires when price exceeds tp_price.

    ATR ≈ 2.0, fill_price ≈ 100.05, tp_price ≈ 100.05 + 4.8 = 104.85.
    Price at 102 for 5 ticks (below TP), then 106 (above TP).
    """
    prices = [100.0] * 50 + [102.0] * 4 + [110.0]

    engine = _run("risk_on", 0.9, prices)  # score 0.9 ≥ ML_THRESHOLD → LONG

    long_trades = [t for t in engine._trades if t.get("direction") == "long"]
    assert len(long_trades) >= 1, "Expected at least one LONG trade"
    assert long_trades[0]["reason"] == "take_profit"
    assert long_trades[0]["pnl_usd"] > 0


def test_long_pnl_direct() -> None:
    """LONG PnL calculation: positive when price rises."""
    engine = BacktestEngine(pairs=["ETH/USDT"], run_id="long_pnl")
    engine._portfolio = 10_000.0

    fill_price = 100.0 * (1.0 + SLIPPAGE)
    size_usd = 100.0
    fee = size_usd * TAKER_FEE
    quantity = (size_usd - fee) / fill_price

    engine._positions["ETH/USDT"] = _Position(
        pair="ETH/USDT",
        entry_price=fill_price,
        size_usd=size_usd,
        quantity=quantity,
        entry_ts=0,
        tp_price=110.0,
        direction="long",
    )

    engine._close_position("ETH/USDT", 105.0, 3_600_000, "take_profit")

    assert len(engine._trades) == 1
    trade = engine._trades[0]
    assert trade["direction"] == "long"
    assert trade["pnl_usd"] > 0
