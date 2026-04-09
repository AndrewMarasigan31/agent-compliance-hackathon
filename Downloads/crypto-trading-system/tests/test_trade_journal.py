"""Tests for daily trade journal writing in BacktestEngine (US-007)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.backtesting.engine import BacktestEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

START_TS = 1_700_000_000_000  # 2023-11-14 22:13:20 UTC
INTERVAL_MS = 3_600_000  # 1 hour


def _make_candles(
    prices: list[float],
    start_ts: int = START_TS,
    interval_ms: int = INTERVAL_MS,
    pct_range: float = 0.005,
) -> pd.DataFrame:
    timestamps = [start_ts + i * interval_ms for i in range(len(prices))]
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": prices,
            "high": [p * (1.0 + pct_range) for p in prices],
            "low": [p * (1.0 - pct_range) for p in prices],
            "close": prices,
            "volume": [1_000_000.0] * (len(prices) - 1) + [1_500_000.0],
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_journal_two_days_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Journal files created for each UTC day that has at least 1 closed trade.

    Timeline (START_TS = Nov 14 22:13 UTC):
      warmup candles: 60h before START_TS (all price=100.0)
      tick 0 (Nov 14 22:13): entry opens at 100.0
      tick 1 (Nov 14 23:13): price=95.0 → stop_loss → trade closed on "20231114"
                              new entry opens at 95.0
      midnight crosses between tick 1 and tick 2 → journal for "20231114" written
      tick 2 (Nov 15 00:13): position holds (price=95.0, pnl ≈ 0)
      tick 3 (Nov 15 01:13): price=90.0 → stop_loss → trade closed on "20231115"
                              new entry opens at 90.0
      end of run → remaining position closed, journal for "20231115" written
    """
    import src.backtesting.engine as engine_mod

    monkeypatch.setattr(engine_mod, "_JOURNALS_DIR", tmp_path / "journals")
    # Disable entry filters that are not under test here (RS filter, volume ratio)
    monkeypatch.setattr(engine_mod, "RS_FILTER_ENABLED", False)
    monkeypatch.setattr(engine_mod, "MIN_VOLUME_RATIO", 0.5)
    # Bypass cooldown: after a stop-loss, allow immediate re-entry for this test
    monkeypatch.setattr(engine_mod._cooldown_tracker, "is_cooling", lambda *a: False)

    warmup = [100.0] * 60
    run_prices = [100.0, 95.0, 95.0, 90.0]
    all_prices = warmup + run_prices

    warmup_start = START_TS - 60 * INTERVAL_MS
    candles_df = _make_candles(all_prices, start_ts=warmup_start)
    candles = {"ETH/USDT": candles_df}

    run_start = START_TS
    run_end = START_TS + 3 * INTERVAL_MS

    engine = BacktestEngine(
        pairs=["ETH/USDT"],
        regime_detector=_FixedRegime("risk_on"),
        scorer=_FixedScorer(0.9),
        risk=_FixedRisk(100.0),
        run_id="journal_2day_test",
    )
    engine.run(_dt(run_start), _dt(run_end), candles=candles)

    journal_dir = tmp_path / "journals"
    files = sorted(journal_dir.glob("journal_2day_test_*.json"))
    assert len(files) == 2, (
        f"Expected 2 journal files, found {len(files)}: {[f.name for f in files]}"
    )


def test_journal_win_rate_calculation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Journal summary win_rate_pct = wins / total_trades * 100."""
    import src.backtesting.engine as engine_mod

    monkeypatch.setattr(engine_mod, "_JOURNALS_DIR", tmp_path / "journals")

    engine = BacktestEngine(pairs=["ETH/USDT"], run_id="winrate_test")
    date_str = "20231115"

    # 2 trades on the same day: 1 win, 1 loss
    engine._trades = [
        {
            "pair": "ETH/USDT",
            "direction": "long",
            "entry_price": 100.0,
            "exit_price": 105.0,
            "pnl_pct": 0.05,
            "pnl_usd": 50.0,
            "hold_hours": 4.0,
            "reason": "take_profit",
            "exit_date_utc": date_str,
            "regime_at_entry": "risk_on",
            "regime_confidence": 80,
            "ml_score_at_entry": 0.9,
            "btc_4h_change_at_entry": 0.01,
            "fear_greed_at_entry": 65.0,
        },
        {
            "pair": "BTC/USDT",
            "direction": "long",
            "entry_price": 30000.0,
            "exit_price": 29100.0,
            "pnl_pct": -0.03,
            "pnl_usd": -30.0,
            "hold_hours": 2.0,
            "reason": "stop_loss",
            "exit_date_utc": date_str,
            "regime_at_entry": "risk_on",
            "regime_confidence": 75,
            "ml_score_at_entry": 0.7,
            "btc_4h_change_at_entry": 0.005,
            "fear_greed_at_entry": 60.0,
        },
    ]

    engine._write_journal(date_str)

    journal_path = tmp_path / "journals" / f"winrate_test_{date_str}.json"
    assert journal_path.exists(), "Journal file not created"

    with journal_path.open() as f:
        journal = json.load(f)

    assert journal["date"] == date_str
    assert journal["run_id"] == "winrate_test"
    assert len(journal["trades"]) == 2

    summary = journal["summary"]
    assert summary["total_trades"] == 2
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["win_rate_pct"] == pytest.approx(50.0)
    assert summary["total_pnl_usd"] == pytest.approx(20.0)


def test_journal_not_written_when_no_trades(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No journal file is created for a day with no closed trades."""
    import src.backtesting.engine as engine_mod

    monkeypatch.setattr(engine_mod, "_JOURNALS_DIR", tmp_path / "journals")

    engine = BacktestEngine(pairs=["ETH/USDT"], run_id="no_trades_test")
    engine._trades = []  # no trades

    engine._write_journal("20231115")

    journal_dir = tmp_path / "journals"
    assert not journal_dir.exists() or not any(journal_dir.iterdir()), (
        "Expected no journal file for a day with no trades"
    )


def test_journal_schema_has_required_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Journal JSON contains all required schema fields."""
    import src.backtesting.engine as engine_mod

    monkeypatch.setattr(engine_mod, "_JOURNALS_DIR", tmp_path / "journals")

    engine = BacktestEngine(pairs=["ETH/USDT"], run_id="schema_test")
    date_str = "20231114"
    engine._trades = [
        {
            "pair": "ETH/USDT",
            "direction": "long",
            "entry_price": 100.0,
            "exit_price": 103.0,
            "pnl_pct": 0.03,
            "pnl_usd": 30.0,
            "hold_hours": 6.0,
            "reason": "take_profit",
            "exit_date_utc": date_str,
            "regime_at_entry": "risk_on",
            "regime_confidence": 82,
            "ml_score_at_entry": 0.8,
            "btc_4h_change_at_entry": 0.015,
            "fear_greed_at_entry": 70.0,
        },
    ]

    engine._write_journal(date_str)

    journal_path = tmp_path / "journals" / f"schema_test_{date_str}.json"
    with journal_path.open() as f:
        journal = json.load(f)

    # Top-level keys
    assert "date" in journal
    assert "run_id" in journal
    assert "trades" in journal
    assert "summary" in journal

    # Trade keys
    trade = journal["trades"][0]
    required_trade_keys = {
        "pair", "direction", "entry_price", "exit_price", "pnl_pct", "pnl_usd",
        "hold_hours", "reason", "regime_at_entry", "regime_confidence",
        "ml_score_at_entry", "btc_4h_change_at_entry", "fear_greed_at_entry",
    }
    assert required_trade_keys.issubset(trade.keys()), (
        f"Missing trade keys: {required_trade_keys - trade.keys()}"
    )

    # Summary keys
    summary_keys = {"total_trades", "wins", "losses", "total_pnl_usd", "win_rate_pct"}
    assert summary_keys.issubset(journal["summary"].keys())
