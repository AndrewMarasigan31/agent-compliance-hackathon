"""Tests for v0.3.0 strategy improvements:

1. ML_THRESHOLD raised to 0.695 (was 0.65)
2. BTC_BULL_GATE raised to +0.35% (was 0.0)
3. EXCLUDED_TOKENS blocks ADA/USDT entries
4. Intermediate 6h stop: exit if down >1.5% at or after 6h hold
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from src.backtesting.engine import (
    BacktestEngine,
    BTC_BULL_GATE,
    EXCLUDED_TOKENS,
    INTERMEDIATE_STOP_HOURS,
    INTERMEDIATE_STOP_PCT,
    ML_THRESHOLD,
    _Position,
)

_TS0 = 1_700_000_000_000
_INTERVAL = 3_600_000  # 1h in ms


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_candles(
    close_prices: list[float],
    start_ts: int = _TS0,
    interval_ms: int = _INTERVAL,
) -> pd.DataFrame:
    n = len(close_prices)
    timestamps = [start_ts + i * interval_ms for i in range(n)]
    volumes = [1_000_000.0] * (n - 1) + [1_500_000.0]  # last candle 1.5x for vol filter
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": close_prices,
        "high": [p * 1.001 for p in close_prices],
        "low": [p * 0.999 for p in close_prices],
        "close": close_prices,
        "volume": volumes,
    })


def _make_engine(
    pairs: list[str] | None = None,
    scorer_confidence: float = 0.9,
) -> BacktestEngine:
    scorer = MagicMock()
    scorer.score.return_value = scorer_confidence

    regime = MagicMock()
    regime.detect.return_value = "risk_on"

    risk = MagicMock()
    risk.should_trade.return_value = True
    risk.position_size.return_value = 500.0

    return BacktestEngine(
        pairs=pairs or ["ETH/USDT"],
        initial_capital=10_000.0,
        regime_detector=regime,
        scorer=scorer,
        risk=risk,
    )


def _candle_dict(
    btc_4h_return: float = 0.005,
    pair: str = "ETH/USDT",
    pair_4h_return: float = 0.02,
    n: int = 56,
) -> dict[str, pd.DataFrame]:
    """Build candles dict with BTC and a pair, designed to pass all entry filters."""
    btc_base = 60_000.0
    btc_prices = [btc_base] * (n - 1) + [btc_base * (1 + btc_4h_return)]
    btc_df = _make_candles(btc_prices)

    # pair: last 4 candles at +pair_4h_return so RS filter passes
    pair_base = 1_000.0
    pair_prices = [pair_base] * (n - 4) + [pair_base * (1 + pair_4h_return)] * 4
    pair_df = _make_candles(pair_prices)

    return {"BTC/USDT": btc_df, pair: pair_df}


# ---------------------------------------------------------------------------
# 1. ML_THRESHOLD = 0.695
# ---------------------------------------------------------------------------


class TestMLThreshold:
    def test_constant_value(self) -> None:
        assert ML_THRESHOLD == 0.695

    def test_entry_blocked_when_confidence_just_below_threshold(self) -> None:
        engine = _make_engine(scorer_confidence=0.694)
        candles = _candle_dict(btc_4h_return=0.005)
        ts = _TS0 + 55 * _INTERVAL
        engine._step(ts, candles)
        assert len(engine._positions) == 0, "score 0.694 should be blocked by ML_THRESHOLD 0.695"

    def test_entry_allowed_at_threshold(self) -> None:
        engine = _make_engine(scorer_confidence=0.695)
        candles = _candle_dict(btc_4h_return=0.005)
        ts = _TS0 + 55 * _INTERVAL
        engine._step(ts, candles)
        assert len(engine._positions) == 1, "score 0.695 should be allowed"

    def test_entry_allowed_above_threshold(self) -> None:
        engine = _make_engine(scorer_confidence=0.75)
        candles = _candle_dict(btc_4h_return=0.005)
        ts = _TS0 + 55 * _INTERVAL
        engine._step(ts, candles)
        assert len(engine._positions) == 1, "score 0.75 should be allowed"


# ---------------------------------------------------------------------------
# 2. BTC_BULL_GATE = 0.0035
# ---------------------------------------------------------------------------


class TestBTCBullGate:
    def test_constant_value(self) -> None:
        assert BTC_BULL_GATE == 0.0035

    def test_entry_blocked_when_btc_4h_below_gate(self) -> None:
        engine = _make_engine(scorer_confidence=0.75)
        # btc4h = +0.003 (0.3%) < gate 0.35%
        candles = _candle_dict(btc_4h_return=0.003)
        ts = _TS0 + 55 * _INTERVAL
        engine._step(ts, candles)
        assert len(engine._positions) == 0, "btc4h=+0.3% should be blocked by BTC_BULL_GATE"

    def test_entry_allowed_at_gate(self) -> None:
        engine = _make_engine(scorer_confidence=0.75)
        candles = _candle_dict(btc_4h_return=0.0035)
        ts = _TS0 + 55 * _INTERVAL
        engine._step(ts, candles)
        assert len(engine._positions) == 1, "btc4h=+0.35% should be allowed"

    def test_entry_allowed_well_above_gate(self) -> None:
        engine = _make_engine(scorer_confidence=0.75)
        candles = _candle_dict(btc_4h_return=0.01)
        ts = _TS0 + 55 * _INTERVAL
        engine._step(ts, candles)
        assert len(engine._positions) == 1, "btc4h=+1.0% should be allowed"

    def test_entry_still_blocked_when_btc_4h_negative(self) -> None:
        engine = _make_engine(scorer_confidence=0.75)
        candles = _candle_dict(btc_4h_return=-0.01)
        ts = _TS0 + 55 * _INTERVAL
        engine._step(ts, candles)
        assert len(engine._positions) == 0, "negative btc4h should still be blocked"


# ---------------------------------------------------------------------------
# 3. EXCLUDED_TOKENS = {"ADA/USDT"}
# ---------------------------------------------------------------------------


class TestExcludedTokens:
    def test_ada_in_excluded_tokens(self) -> None:
        assert "ADA/USDT" in EXCLUDED_TOKENS

    def test_ada_entry_blocked(self) -> None:
        engine = _make_engine(pairs=["ADA/USDT"], scorer_confidence=0.75)
        candles = _candle_dict(btc_4h_return=0.005, pair="ADA/USDT")
        ts = _TS0 + 55 * _INTERVAL
        engine._step(ts, candles)
        assert len(engine._positions) == 0, "ADA/USDT should be excluded from entries"

    def test_non_excluded_token_still_enters(self) -> None:
        engine = _make_engine(pairs=["ETH/USDT"], scorer_confidence=0.75)
        candles = _candle_dict(btc_4h_return=0.005, pair="ETH/USDT")
        ts = _TS0 + 55 * _INTERVAL
        engine._step(ts, candles)
        assert len(engine._positions) == 1, "ETH/USDT should not be excluded"


# ---------------------------------------------------------------------------
# 4. Intermediate 6h stop: exit if down >1.5% at or after 6h hold
# ---------------------------------------------------------------------------


class TestIntermediateStop:
    def test_constants_defined(self) -> None:
        assert INTERMEDIATE_STOP_HOURS == 6
        assert INTERMEDIATE_STOP_PCT == -0.015

    def test_position_closed_at_6h_when_down_more_than_1_5pct(self) -> None:
        """Position down -1.6% at 6h should trigger intermediate_stop exit."""
        engine = _make_engine()
        entry_ts = _TS0
        entry_price = 1_000.0
        current_price = 984.0  # -1.6%

        # Inject open position at entry_ts
        pos = _Position(
            pair="ETH/USDT",
            direction="long",
            entry_price=entry_price,
            entry_ts=entry_ts,
            quantity=0.5,
            size_usd=500.0,
            tp_price=entry_price * 1.05,
        )
        engine._positions["ETH/USDT"] = pos

        # 6h candle at -1.6%
        ts_6h = entry_ts + 6 * _INTERVAL
        candles = {
            "ETH/USDT": pd.DataFrame([{
                "timestamp": ts_6h,
                "open": current_price,
                "high": current_price * 1.001,
                "low": current_price * 0.999,
                "close": current_price,
                "volume": 1_000_000.0,
            }])
        }
        engine._step(ts_6h, candles)

        assert len(engine._positions) == 0, "position should be closed by intermediate stop"
        assert engine._trades[-1]["reason"] == "intermediate_stop"

    def test_position_held_at_6h_when_down_less_than_1_5pct(self) -> None:
        """Position down only -1.0% at 6h should NOT trigger intermediate stop."""
        engine = _make_engine()
        entry_ts = _TS0
        entry_price = 1_000.0
        current_price = 990.0  # -1.0%

        pos = _Position(
            pair="ETH/USDT",
            direction="long",
            entry_price=entry_price,
            entry_ts=entry_ts,
            quantity=0.5,
            size_usd=500.0,
            tp_price=entry_price * 1.05,
        )
        engine._positions["ETH/USDT"] = pos

        ts_6h = entry_ts + 6 * _INTERVAL
        candles = {
            "ETH/USDT": pd.DataFrame([{
                "timestamp": ts_6h,
                "open": current_price,
                "high": current_price * 1.001,
                "low": current_price * 0.999,
                "close": current_price,
                "volume": 1_000_000.0,
            }])
        }
        engine._step(ts_6h, candles)

        assert len(engine._positions) == 1, "position should stay open when only down 1%"

    def test_position_not_closed_before_6h_even_if_losing(self) -> None:
        """Intermediate stop should not fire before 6h hold."""
        engine = _make_engine()
        entry_ts = _TS0
        entry_price = 1_000.0
        current_price = 984.0  # -1.6%, but only 3h in

        pos = _Position(
            pair="ETH/USDT",
            direction="long",
            entry_price=entry_price,
            entry_ts=entry_ts,
            quantity=0.5,
            size_usd=500.0,
            tp_price=entry_price * 1.05,
        )
        engine._positions["ETH/USDT"] = pos

        ts_3h = entry_ts + 3 * _INTERVAL  # only 3h
        candles = {
            "ETH/USDT": pd.DataFrame([{
                "timestamp": ts_3h,
                "open": current_price,
                "high": current_price * 1.001,
                "low": current_price * 0.999,
                "close": current_price,
                "volume": 1_000_000.0,
            }])
        }
        engine._step(ts_3h, candles)

        assert len(engine._positions) == 1, "should not close before 6h hold"

    def test_hard_stop_loss_still_fires_before_6h(self) -> None:
        """Hard stop-loss (-3%) still overrides intermediate stop timing."""
        engine = _make_engine()
        entry_ts = _TS0
        entry_price = 1_000.0
        current_price = 965.0  # -3.5%, triggers hard stop

        pos = _Position(
            pair="ETH/USDT",
            direction="long",
            entry_price=entry_price,
            entry_ts=entry_ts,
            quantity=0.5,
            size_usd=500.0,
            tp_price=entry_price * 1.05,
        )
        engine._positions["ETH/USDT"] = pos

        ts_2h = entry_ts + 2 * _INTERVAL  # only 2h
        candles = {
            "ETH/USDT": pd.DataFrame([{
                "timestamp": ts_2h,
                "open": current_price,
                "high": current_price * 1.001,
                "low": current_price * 0.999,
                "close": current_price,
                "volume": 1_000_000.0,
            }])
        }
        engine._step(ts_2h, candles)

        assert len(engine._positions) == 0, "hard stop-loss should still close position"
        assert engine._trades[-1]["reason"] == "stop_loss"
