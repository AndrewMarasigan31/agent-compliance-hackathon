"""Tests for IMMED-003: token cooldown tracker.

Verifies that:
- A pair is blocked from re-entry after a stop-loss exit.
- A pair unblocks after 48 hours have elapsed.
- Take-profit exits do NOT trigger a cooldown.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.backtesting.engine import BacktestEngine, _Position
from src.risk.cooldown import CooldownTracker


# ---------------------------------------------------------------------------
# CooldownTracker unit tests
# ---------------------------------------------------------------------------


class TestCooldownTracker:
    def test_pair_not_cooling_when_no_stop_recorded(self) -> None:
        tracker = CooldownTracker()
        cooldowns: dict[str, int] = {}
        assert not tracker.is_cooling(cooldowns, "ETH/USDT", 1_000_000)

    def test_pair_cooling_immediately_after_stop(self) -> None:
        tracker = CooldownTracker()
        cooldowns: dict[str, int] = {}
        ts = 1_700_000_000_000
        tracker.record_stop(cooldowns, "ETH/USDT", ts)
        # 1 second later — still cooling
        assert tracker.is_cooling(cooldowns, "ETH/USDT", ts + 1_000)

    def test_pair_unblocks_after_48h(self) -> None:
        tracker = CooldownTracker()
        cooldowns: dict[str, int] = {}
        ts = 1_700_000_000_000
        tracker.record_stop(cooldowns, "ETH/USDT", ts)
        # Exactly at 48h boundary: (48 * 3600 * 1000) = 172_800_000 ms
        assert not tracker.is_cooling(cooldowns, "ETH/USDT", ts + 172_800_000)

    def test_cooldown_is_per_pair(self) -> None:
        tracker = CooldownTracker()
        cooldowns: dict[str, int] = {}
        ts = 1_700_000_000_000
        tracker.record_stop(cooldowns, "ETH/USDT", ts)
        # BTC/USDT was not stopped — should not be cooling
        assert not tracker.is_cooling(cooldowns, "BTC/USDT", ts + 1_000)


# ---------------------------------------------------------------------------
# Engine integration tests
# ---------------------------------------------------------------------------


def _make_candles(
    close_prices: list[float],
    start_ts: int = 1_700_000_000_000,
    interval_ms: int = 3_600_000,
) -> pd.DataFrame:
    n = len(close_prices)
    timestamps = [start_ts + i * interval_ms for i in range(n)]
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": close_prices,
            "high": [p * 1.001 for p in close_prices],
            "low": [p * 0.999 for p in close_prices],
            "close": close_prices,
            "volume": [1_000_000.0] * n,
        }
    )


def _make_engine(scorer_confidence: float = 0.9) -> BacktestEngine:
    scorer = MagicMock()
    scorer.score.return_value = scorer_confidence

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


class TestEngineCooldown:
    def test_stop_loss_blocks_re_entry(self) -> None:
        """After a stop-loss, the pair cannot re-enter within 48h."""
        engine = _make_engine()
        start_ts = 1_700_000_000_000
        interval = 3_600_000

        # Build candles: 60 candles at 1000, then 1 candle at 960 (triggers -4% SL)
        prices_up = [1_000.0] * 60
        prices_down = [960.0]  # below -3% stop-loss
        all_prices = prices_up + prices_down

        eth_df = _make_candles(all_prices, start_ts=start_ts)
        candles = {"ETH/USDT": eth_df}

        # Tick 1: open position at ts=60th candle
        ts_open = start_ts + 59 * interval
        engine._step(ts_open, candles)
        assert len(engine._positions) == 1, "position should open"

        # Tick 2: price drops to 960 — triggers stop-loss
        ts_stop = start_ts + 60 * interval
        engine._step(ts_stop, candles)
        assert len(engine._positions) == 0, "stop-loss should close position"
        assert "ETH/USDT" in engine._cooldowns, "cooldown should be recorded"

        # Tick 3 (1h later): same pair should not re-open
        ts_next = ts_stop + interval
        extra = pd.DataFrame(
            [{"timestamp": ts_next, "open": 1_000.0, "high": 1_001.0, "low": 999.0, "close": 1_000.0, "volume": 1_000_000.0}]
        )
        candles2 = {"ETH/USDT": pd.concat([eth_df, extra], ignore_index=True)}
        engine._step(ts_next, candles2)
        assert len(engine._positions) == 0, "pair should still be in cooldown"

    def test_take_profit_does_not_trigger_cooldown(self) -> None:
        """A take-profit exit should NOT put the pair in cooldown."""
        engine = _make_engine()
        start_ts = 1_700_000_000_000
        interval = 3_600_000

        # Open at 1000, take-profit is set to fill_price + ATR*2.4
        # We'll close via _close_position directly with reason='take_profit'
        engine._cooldowns.clear()
        engine._close_position("ETH/USDT", 1_100.0, start_ts + interval, "take_profit")

        assert "ETH/USDT" not in engine._cooldowns, (
            "take_profit exit must not trigger cooldown"
        )

    def test_stop_loss_close_records_cooldown(self) -> None:
        """_close_position with reason='stop_loss' must record cooldown."""
        engine = _make_engine()
        # Inject a fake position so _close_position can pop it
        ts = 1_700_000_000_000
        engine._positions["ETH/USDT"] = _Position(
            pair="ETH/USDT",
            entry_price=1_000.0,
            size_usd=500.0,
            quantity=0.5,
            entry_ts=ts,
            tp_price=1_100.0,
        )
        engine._close_position("ETH/USDT", 970.0, ts + 3_600_000, "stop_loss")

        assert "ETH/USDT" in engine._cooldowns
        assert engine._cooldowns["ETH/USDT"] == ts + 3_600_000
