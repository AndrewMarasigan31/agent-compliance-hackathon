"""Google Trends social velocity fetcher.

Uses `pytrends` (free, no API key) to fetch 7-day interest scores for
'bitcoin' and 'crypto' as a proxy for retail sentiment. Scores are 0-100
(relative search interest). Results are cached in-memory for 6 hours since
trends data changes slowly.

The fetcher is injected into BacktestEngine._get_glm_regime so the LLM can
reason about retail sentiment spikes (often precede tops) or collapses.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_CACHE_TTL_S = 6 * 3600  # 6 hours

_KEYWORDS = ["bitcoin", "crypto"]

# Module-level reference so tests can patch `src.data.trends.TrendReq`.
try:
    from pytrends.request import TrendReq
except ImportError:  # pytrends not installed in test environments without it
    TrendReq = None  # type: ignore[assignment,misc]


class GoogleTrendsFetcher:
    """Fetch 7-day Google Trends interest scores for bitcoin and crypto.

    Returns:
        dict | None — ``{'bitcoin': int, 'crypto': int}`` with 0-100 scores,
            or None if the fetch fails.
    """

    def __init__(self) -> None:
        self._cached_value: dict[str, int] | None = None
        self._cache_ts: float = 0.0

    def fetch(self) -> dict[str, int] | None:
        """Return Google Trends scores, using cache if within 6h window."""
        now = time.monotonic()
        if self._cached_value is not None and (now - self._cache_ts) < _CACHE_TTL_S:
            logger.debug("GoogleTrendsFetcher: returning cached value %s", self._cached_value)
            return self._cached_value

        value = self._fetch_from_api()
        if value is not None:
            self._cached_value = value
            self._cache_ts = now
        return value

    def _fetch_from_api(self) -> dict[str, int] | None:
        """Call pytrends and return the current interest scores."""
        if TrendReq is None:
            logger.warning("GoogleTrendsFetcher: pytrends is not installed")
            return None
        try:
            pytrends = TrendReq(hl="en-US", tz=0)
            pytrends.build_payload(_KEYWORDS, cat=0, timeframe="now 7-d", geo="", gprop="")
            df = pytrends.interest_over_time()

            if df is None or df.empty:
                logger.warning("GoogleTrendsFetcher: empty response from pytrends")
                return None

            # Use the most recent row's values
            latest = df.iloc[-1]
            result: dict[str, int] = {}
            for keyword in _KEYWORDS:
                if keyword in latest.index:
                    result[keyword] = int(latest[keyword])
                else:
                    result[keyword] = 0

            return result
        except Exception as exc:
            logger.warning("GoogleTrendsFetcher: fetch failed: %s", exc)
            return None
