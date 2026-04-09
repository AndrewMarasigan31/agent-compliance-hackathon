"""Integration tests for GLM-5 regime wiring in BacktestEngine (US-006)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.backtesting.engine import BacktestEngine, _Position
from src.llm.glm_client import RegimeResponse


# ---------------------------------------------------------------------------
# Helpers (mirrors test_short_positions.py helpers)
# ---------------------------------------------------------------------------

START_TS = 1_700_000_000_000
INTERVAL_MS = 3_600_000


def _make_candles(
    prices: list[float],
    start_ts: int = START_TS,
    interval_ms: int = INTERVAL_MS,
    pct_range: float = 0.01,
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


@pytest.mark.skip(reason="SHORT positions intentionally disabled in engine")
def test_glm_regime_risk_off_opens_short() -> None:
    """Mock GLMClient.get_regime returning risk_off → SHORT positions opened."""
    mock_glm = MagicMock()
    mock_glm.get_regime.return_value = RegimeResponse(
        regime="risk_off", confidence=80, reasoning="bearish test"
    )

    # 50 warmup + 10 more — SHORT should fire once warmup completes
    prices = [100.0] * 60
    candles = {"ETH/USDT": _make_candles(prices)}

    engine = BacktestEngine(
        pairs=["ETH/USDT"],
        glm_client=mock_glm,
        scorer=_FixedScorer(0.1),  # score ≤ 0.35 → qualifies for SHORT
        risk=_FixedRisk(100.0),
        run_id="glm_short_test",
    )
    engine.run(
        _dt(START_TS),
        _dt(START_TS + 59 * INTERVAL_MS),
        candles=candles,
    )

    short_trades = [t for t in engine._trades if t.get("direction") == "short"]
    assert len(short_trades) >= 1, "Expected at least one SHORT trade via GLM risk_off"


def test_glm_regime_risk_on_opens_long() -> None:
    """Mock GLMClient.get_regime returning risk_on → LONG positions opened."""
    mock_glm = MagicMock()
    mock_glm.get_regime.return_value = RegimeResponse(
        regime="risk_on", confidence=85, reasoning="bullish test"
    )

    # Last 4 candles at +2% so RS filter (token_4h_return > btc_4h_change=0.0) passes
    prices = [100.0] * 56 + [102.0] * 4
    candles = {"ETH/USDT": _make_candles(prices)}

    engine = BacktestEngine(
        pairs=["ETH/USDT"],
        glm_client=mock_glm,
        scorer=_FixedScorer(0.9),  # score ≥ ML_THRESHOLD → qualifies for LONG
        risk=_FixedRisk(100.0),
        run_id="glm_long_test",
    )
    engine.run(
        _dt(START_TS),
        _dt(START_TS + 59 * INTERVAL_MS),
        candles=candles,
    )

    long_trades = [t for t in engine._trades if t.get("direction") == "long"]
    assert len(long_trades) >= 1, "Expected at least one LONG trade via GLM risk_on"


def test_glm_regime_neutral_no_entries() -> None:
    """Mock GLMClient.get_regime returning neutral → no new positions opened."""
    mock_glm = MagicMock()
    mock_glm.get_regime.return_value = RegimeResponse(
        regime="neutral", confidence=75, reasoning="sideways test"
    )

    prices = [100.0] * 60
    candles = {"ETH/USDT": _make_candles(prices)}

    engine = BacktestEngine(
        pairs=["ETH/USDT"],
        glm_client=mock_glm,
        scorer=_FixedScorer(0.9),
        risk=_FixedRisk(100.0),
        run_id="glm_neutral_test",
    )
    engine.run(
        _dt(START_TS),
        _dt(START_TS + 59 * INTERVAL_MS),
        candles=candles,
    )

    # No trades opened in neutral regime
    assert len(engine._trades) == 0, "Expected no trades in neutral GLM regime"


def test_glm_low_confidence_forces_neutral() -> None:
    """Confidence < 60 overrides regime to neutral — no entries."""
    mock_glm = MagicMock()
    mock_glm.get_regime.return_value = RegimeResponse(
        regime="risk_on", confidence=55, reasoning="low confidence"
    )

    prices = [100.0] * 60
    candles = {"ETH/USDT": _make_candles(prices)}

    engine = BacktestEngine(
        pairs=["ETH/USDT"],
        glm_client=mock_glm,
        scorer=_FixedScorer(0.9),
        risk=_FixedRisk(100.0),
        run_id="glm_lowconf_test",
    )
    engine.run(
        _dt(START_TS),
        _dt(START_TS + 59 * INTERVAL_MS),
        candles=candles,
    )

    assert len(engine._trades) == 0, "Expected no trades when GLM confidence < 60"


def test_glm_4h_cache_limits_api_calls() -> None:
    """GLM is called at most once per 4h window."""
    mock_glm = MagicMock()
    mock_glm.get_regime.return_value = RegimeResponse(
        regime="neutral", confidence=75, reasoning="cached"
    )

    # 24 hourly ticks → at most 6 GLM calls (one per 4h block)
    prices = [100.0] * 24
    candles = {"ETH/USDT": _make_candles(prices)}

    engine = BacktestEngine(
        pairs=["ETH/USDT"],
        glm_client=mock_glm,
        scorer=_FixedScorer(0.5),
        risk=_FixedRisk(100.0),
        run_id="glm_cache_test",
    )
    engine.run(
        _dt(START_TS),
        _dt(START_TS + 23 * INTERVAL_MS),
        candles=candles,
    )

    # 24 ticks / 4h = 6 GLM calls (first call at tick 0, then ticks 4, 8, 12, 16, 20)
    assert mock_glm.get_regime.call_count <= 6, (
        f"Expected ≤6 GLM calls for 24 ticks, got {mock_glm.get_regime.call_count}"
    )


def test_existing_btcregime_unchanged_without_glm_client() -> None:
    """When glm_client=None, engine uses existing BTCRegime; no GLM calls."""
    # Last 4 candles at +2% so RS filter passes
    prices = [100.0] * 56 + [102.0] * 4
    candles = {"ETH/USDT": _make_candles(prices)}

    class _FixedRegime:
        def detect(self, market_data: dict) -> str:
            return "risk_on"

    engine = BacktestEngine(
        pairs=["ETH/USDT"],
        regime_detector=_FixedRegime(),
        scorer=_FixedScorer(0.9),
        risk=_FixedRisk(100.0),
        run_id="btcregime_test",
        glm_client=None,  # explicit None
    )
    engine.run(
        _dt(START_TS),
        _dt(START_TS + 59 * INTERVAL_MS),
        candles=candles,
    )

    long_trades = [t for t in engine._trades if t.get("direction") == "long"]
    assert len(long_trades) >= 1, "Expected LONG trades from rule-based BTCRegime"
