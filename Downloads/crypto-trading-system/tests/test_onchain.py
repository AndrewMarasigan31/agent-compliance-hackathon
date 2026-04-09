"""Tests for MED-003: on-chain exchange flow data source.

Verifies that:
- Successful fetch returns a float.
- HTTP error returns None without raising.
- Cached value is returned within 4h.
- GLM context includes exchange_netflow_btc key when fetcher is present.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from src.data.onchain import ExchangeFlowFetcher


# ---------------------------------------------------------------------------
# ExchangeFlowFetcher unit tests
# ---------------------------------------------------------------------------

_GOOD_RESPONSE = {
    "code": "0",
    "data": {
        "list": [
            {"netflow": -1234.56, "timestamp": 1700000000},
            {"netflow": 789.01,   "timestamp": 1700014400},
        ]
    },
}


def _mock_response(status_code: int, json_data=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


class TestExchangeFlowFetcher:
    def test_successful_fetch_returns_float(self) -> None:
        fetcher = ExchangeFlowFetcher()
        with patch("src.data.onchain.requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, json_data=_GOOD_RESPONSE)
            result = fetcher.fetch()
        assert isinstance(result, float)
        # Should return the last entry's netflow
        assert result == pytest.approx(789.01)

    def test_http_error_returns_none(self) -> None:
        fetcher = ExchangeFlowFetcher()
        with patch("src.data.onchain.requests.get") as mock_get:
            mock_get.return_value = _mock_response(429)
            result = fetcher.fetch()
        assert result is None

    def test_exception_returns_none(self) -> None:
        fetcher = ExchangeFlowFetcher()
        with patch("src.data.onchain.requests.get", side_effect=Exception("timeout")):
            result = fetcher.fetch()
        assert result is None

    def test_cached_value_returned_within_4h(self) -> None:
        """Second call within cache TTL must not hit the API."""
        fetcher = ExchangeFlowFetcher()
        with patch("src.data.onchain.requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, json_data=_GOOD_RESPONSE)
            first = fetcher.fetch()
            second = fetcher.fetch()

        assert mock_get.call_count == 1, "API should only be called once within cache window"
        assert first == second

    def test_cache_expires_after_ttl(self) -> None:
        """After TTL has elapsed, the API is called again."""
        fetcher = ExchangeFlowFetcher()
        with patch("src.data.onchain.requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, json_data=_GOOD_RESPONSE)
            fetcher.fetch()

        # Artificially age the cache beyond TTL
        fetcher._cache_ts = time.monotonic() - (4 * 3600 + 1)

        with patch("src.data.onchain.requests.get") as mock_get2:
            mock_get2.return_value = _mock_response(200, json_data=_GOOD_RESPONSE)
            fetcher.fetch()

        assert mock_get2.call_count == 1, "API should be called again after TTL expires"


# ---------------------------------------------------------------------------
# GLM context integration test
# ---------------------------------------------------------------------------


class TestGLMContextIntegration:
    def test_glm_context_includes_netflow_key_when_fetcher_present(self) -> None:
        """When onchain_fetcher is provided, _get_glm_regime adds exchange_netflow_btc to context."""
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

        onchain_fetcher = MagicMock()
        onchain_fetcher.fetch.return_value = -500.0

        engine = BacktestEngine(
            pairs=["BTC/USDT"],
            glm_client=glm_client,
            onchain_fetcher=onchain_fetcher,
        )

        # Call _get_glm_regime directly with no BTC window to keep it simple
        engine._get_glm_regime(ts=1_700_000_000_000, btc_window=None)

        assert "exchange_netflow_btc" in captured_context, (
            "GLM context must include exchange_netflow_btc when onchain_fetcher is present"
        )
        assert captured_context["exchange_netflow_btc"] == -500.0

    def test_glm_context_netflow_is_none_when_fetcher_absent(self) -> None:
        """When no onchain_fetcher, exchange_netflow_btc is still in context but None."""
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

        engine = BacktestEngine(
            pairs=["BTC/USDT"],
            glm_client=glm_client,
        )

        engine._get_glm_regime(ts=1_700_000_000_000, btc_window=None)

        assert "exchange_netflow_btc" in captured_context
        assert captured_context["exchange_netflow_btc"] is None
