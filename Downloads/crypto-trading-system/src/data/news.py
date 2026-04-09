"""Crypto news headline fetcher.

Primary source: CryptoCompare News API (free, no API key required).
Fallback source: CoinDesk RSS feed (public, no auth).

The original CryptoPanic fetcher used endpoint
``https://cryptopanic.com/api/v1/posts/`` which began returning HTTP 404
on March 1, 2026 (the free-tier public endpoint was deprecated). This
module replaces it with two alternative sources that require no paid key.

Both fetchers return a ``list[str]`` of headline strings and degrade
gracefully to ``[]`` on any error so callers never receive exceptions.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 10  # seconds

# ---------------------------------------------------------------------------
# Primary source — CryptoCompare News API (free, no auth)
# ---------------------------------------------------------------------------

_CRYPTOCOMPARE_URL = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN"


def _fetch_cryptocompare() -> list[str]:
    """Return up to 20 headlines from CryptoCompare News API."""
    try:
        resp = requests.get(_CRYPTOCOMPARE_URL, timeout=_TIMEOUT)
        if resp.status_code != 200:
            logger.warning("CryptoCompare news returned HTTP %d", resp.status_code)
            return []
        data: dict[str, Any] = resp.json()
        articles = data.get("Data", [])
        return [a["title"] for a in articles if isinstance(a.get("title"), str)][:20]
    except Exception as exc:
        logger.warning("CryptoCompare news fetch failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Fallback source — CoinDesk RSS feed (public, no auth)
# ---------------------------------------------------------------------------

_COINDESK_RSS_URL = "https://www.coindesk.com/arc/outboundfeeds/rss/"


def _fetch_coindesk_rss() -> list[str]:
    """Return up to 20 headlines from the CoinDesk RSS feed."""
    try:
        resp = requests.get(_COINDESK_RSS_URL, timeout=_TIMEOUT)
        if resp.status_code != 200:
            logger.warning("CoinDesk RSS returned HTTP %d", resp.status_code)
            return []
        root = ET.fromstring(resp.text)
        titles: list[str] = []
        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                titles.append(title_el.text.strip())
            if len(titles) >= 20:
                break
        return titles
    except Exception as exc:
        logger.warning("CoinDesk RSS fetch failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Public fetcher class — drop-in replacement for CryptoPanicFetcher
# ---------------------------------------------------------------------------


class CryptoPanicFetcher:
    """Crypto news headline fetcher.

    Tries the CryptoCompare News API first; falls back to CoinDesk RSS if the
    primary source returns nothing or fails.  Always returns ``list[str]``.

    .. note::
        The original CryptoPanic ``/api/v1/posts/`` endpoint was deprecated
        on 2026-03-01 and now returns HTTP 404 on every call.
    """

    def fetch(self) -> list[str]:
        """Return a list of recent crypto news headlines.

        Returns an empty list if both sources fail.
        """
        headlines = _fetch_cryptocompare()
        if headlines:
            logger.debug("News: %d headlines from CryptoCompare", len(headlines))
            return headlines

        logger.info("Primary news source returned nothing; trying CoinDesk RSS fallback")
        headlines = _fetch_coindesk_rss()
        logger.debug("News: %d headlines from CoinDesk RSS", len(headlines))
        return headlines
