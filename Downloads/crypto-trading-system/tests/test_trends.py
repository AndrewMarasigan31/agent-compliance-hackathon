"""Tests for MED-004: Google Trends social velocity signal.

Verifies that:
- Successful response returns dict with bitcoin and crypto keys.
- Exception returns None without propagating.
- Cache prevents second API call within 6h.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data.trends import GoogleTrendsFetcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pytrends_df(bitcoin: int = 72, crypto: int = 45) -> pd.DataFrame:
    """Build a fake pytrends interest_over_time() DataFrame."""
    return pd.DataFrame(
        {
            "bitcoin": [bitcoin - 5, bitcoin],
            "crypto": [crypto - 3, crypto],
            "isPartial": [False, False],
        }
    )


def _mock_trendreq_cls(df: pd.DataFrame | None = None) -> MagicMock:
    """Return a MagicMock that acts as the TrendReq class.

    When called as ``TrendReq(...)``, it returns a mock instance whose
    ``interest_over_time()`` returns ``df``.
    """
    cls_mock = MagicMock()
    instance_mock = cls_mock.return_value  # what TrendReq(...) returns
    instance_mock.interest_over_time.return_value = df if df is not None else _make_pytrends_df()
    return cls_mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGoogleTrendsFetcher:
    def test_successful_response_returns_dict_with_bitcoin_and_crypto_keys(self) -> None:
        fetcher = GoogleTrendsFetcher()
        cls_mock = _mock_trendreq_cls(_make_pytrends_df(bitcoin=72, crypto=45))

        with patch("src.data.trends.TrendReq", cls_mock):
            result = fetcher.fetch()

        assert isinstance(result, dict), "Should return a dict"
        assert "bitcoin" in result, "Dict must have 'bitcoin' key"
        assert "crypto" in result, "Dict must have 'crypto' key"
        assert result["bitcoin"] == 72
        assert result["crypto"] == 45

    def test_exception_returns_none_without_propagating(self) -> None:
        fetcher = GoogleTrendsFetcher()
        # Make TrendReq(...) raise immediately
        exploding_cls = MagicMock(side_effect=Exception("rate limited"))

        with patch("src.data.trends.TrendReq", exploding_cls):
            result = fetcher.fetch()

        assert result is None, "Exception should be caught and None returned"

    def test_empty_dataframe_returns_none(self) -> None:
        fetcher = GoogleTrendsFetcher()
        cls_mock = _mock_trendreq_cls(pd.DataFrame())

        with patch("src.data.trends.TrendReq", cls_mock):
            result = fetcher.fetch()

        assert result is None

    def test_cache_prevents_second_api_call_within_6h(self) -> None:
        """Within the 6h TTL, the second fetch must not instantiate TrendReq again."""
        fetcher = GoogleTrendsFetcher()
        cls_mock = _mock_trendreq_cls()

        with patch("src.data.trends.TrendReq", cls_mock):
            first = fetcher.fetch()
            second = fetcher.fetch()

        # TrendReq class should only be called (instantiated) once
        assert cls_mock.call_count == 1, "TrendReq should only be instantiated once within cache window"
        assert first == second

    def test_cache_expires_after_6h(self) -> None:
        """After TTL, TrendReq is instantiated again."""
        fetcher = GoogleTrendsFetcher()
        cls_mock = _mock_trendreq_cls()

        with patch("src.data.trends.TrendReq", cls_mock):
            fetcher.fetch()

        # Age the cache past TTL
        fetcher._cache_ts = time.monotonic() - (6 * 3600 + 1)

        cls_mock2 = _mock_trendreq_cls()
        with patch("src.data.trends.TrendReq", cls_mock2):
            fetcher.fetch()

        assert cls_mock2.call_count == 1, "TrendReq should be instantiated again after TTL"


class TestGLMTrendsIntegration:
    def test_glm_context_includes_google_trends_when_fetcher_present(self) -> None:
        from src.backtesting.engine import BacktestEngine

        captured_context: dict = {}

        def fake_get_regime(ctx: dict):
            captured_context.update(ctx)
            resp = MagicMock()
            resp.regime = "risk_on"
            resp.confidence = 80
            resp.reasoning = "test"
            return resp

        glm_client = MagicMock()
        glm_client.get_regime.side_effect = fake_get_regime

        trends_fetcher = MagicMock()
        trends_fetcher.fetch.return_value = {"bitcoin": 65, "crypto": 40}

        engine = BacktestEngine(
            pairs=["BTC/USDT"],
            glm_client=glm_client,
            trends_fetcher=trends_fetcher,
        )
        engine._get_glm_regime(ts=1_700_000_000_000, btc_window=None)

        assert "google_trends" in captured_context
        assert captured_context["google_trends"] == {"bitcoin": 65, "crypto": 40}
