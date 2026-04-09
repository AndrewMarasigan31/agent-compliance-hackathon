"""On-chain exchange flow fetcher via CoinGlass public API.

BTC exchange netflow (positive = inflow / bearish, negative = outflow / bullish)
is cached in-memory for 4 hours to avoid hammering the free endpoint.

The value is injected into the GLM regime context so the LLM can reason about
whale positioning.
"""

from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 10  # seconds
_CACHE_TTL_S = 4 * 3600  # 4 hours

# CoinGlass free public API — no auth required
_COINGLASS_URL = "https://open-api.coinglass.com/public/v2/indicator/exchange_netflow"


class ExchangeFlowFetcher:
    """Fetches BTC exchange netflow from CoinGlass and caches it for 4 hours.

    Returns:
        float | None — BTC exchange netflow in BTC units.
            Positive = net inflow to exchanges (bearish signal).
            Negative = net outflow from exchanges (bullish signal).
            None if the fetch fails for any reason.
    """

    def __init__(self) -> None:
        self._cached_value: float | None = None
        self._cache_ts: float = 0.0  # wall-clock time of last successful fetch

    def fetch(self) -> float | None:
        """Return BTC exchange netflow, using cache if within 4h window."""
        now = time.monotonic()
        if self._cached_value is not None and (now - self._cache_ts) < _CACHE_TTL_S:
            logger.debug("ExchangeFlowFetcher: returning cached value %.4f", self._cached_value)
            return self._cached_value

        value = self._fetch_from_api()
        if value is not None:
            self._cached_value = value
            self._cache_ts = now
        return value

    def _fetch_from_api(self) -> float | None:
        """Call CoinGlass API and extract the latest BTC netflow value."""
        try:
            resp = requests.get(
                _COINGLASS_URL,
                params={"symbol": "BTC", "interval": "h4"},
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                logger.warning("CoinGlass API returned HTTP %d", resp.status_code)
                return None
            data = resp.json()
            # Response shape: {"code": "0", "data": {"list": [{"netflow": 123.4, ...}]}}
            netflow_list = data.get("data", {}).get("list", [])
            if not netflow_list:
                logger.warning("CoinGlass API returned empty netflow list")
                return None
            # Use the most recent entry (last element)
            latest = netflow_list[-1]
            value = latest.get("netflow")
            if value is None:
                logger.warning("CoinGlass API response missing 'netflow' key")
                return None
            return float(value)
        except Exception as exc:
            logger.warning("ExchangeFlowFetcher: fetch failed: %s", exc)
            return None
