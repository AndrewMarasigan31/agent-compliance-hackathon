"""Tests for IMMED-004: CryptoPanic news fetcher replacement.

Verifies that:
- Happy path returns a list of strings.
- HTTP 404 returns empty list without raising.
- Fallback is attempted when primary source fails.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.data.news import CryptoPanicFetcher, _fetch_coindesk_rss, _fetch_cryptocompare


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CRYPTOCOMPARE_OK = {
    "Data": [
        {"title": "Bitcoin hits $100k", "url": "https://example.com/1"},
        {"title": "Ethereum upgrade live", "url": "https://example.com/2"},
    ]
}

_COINDESK_RSS_OK = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>CoinDesk</title>
    <item><title>BTC rally continues</title></item>
    <item><title>Fed keeps rates unchanged</title></item>
  </channel>
</rss>"""


def _mock_response(status_code: int, json_data=None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# _fetch_cryptocompare unit tests
# ---------------------------------------------------------------------------


class TestFetchCryptoCompare:
    def test_happy_path_returns_list_of_strings(self) -> None:
        with patch("src.data.news.requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, json_data=_CRYPTOCOMPARE_OK)
            result = _fetch_cryptocompare()
        assert isinstance(result, list)
        assert len(result) == 2
        assert all(isinstance(h, str) for h in result)

    def test_http_404_returns_empty_list(self) -> None:
        with patch("src.data.news.requests.get") as mock_get:
            mock_get.return_value = _mock_response(404)
            result = _fetch_cryptocompare()
        assert result == []

    def test_connection_error_returns_empty_list(self) -> None:
        with patch("src.data.news.requests.get", side_effect=Exception("timeout")):
            result = _fetch_cryptocompare()
        assert result == []


# ---------------------------------------------------------------------------
# _fetch_coindesk_rss unit tests
# ---------------------------------------------------------------------------


class TestFetchCoindeskRss:
    def test_happy_path_returns_list_of_strings(self) -> None:
        with patch("src.data.news.requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, text=_COINDESK_RSS_OK)
            result = _fetch_coindesk_rss()
        assert isinstance(result, list)
        assert len(result) == 2
        assert "BTC rally continues" in result

    def test_http_error_returns_empty_list(self) -> None:
        with patch("src.data.news.requests.get") as mock_get:
            mock_get.return_value = _mock_response(500)
            result = _fetch_coindesk_rss()
        assert result == []

    def test_exception_returns_empty_list(self) -> None:
        with patch("src.data.news.requests.get", side_effect=Exception("network down")):
            result = _fetch_coindesk_rss()
        assert result == []


# ---------------------------------------------------------------------------
# CryptoPanicFetcher integration tests
# ---------------------------------------------------------------------------


class TestCryptoPanicFetcher:
    def test_happy_path_returns_list_of_strings(self) -> None:
        """Primary source succeeds — returns its headlines."""
        with patch("src.data.news.requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, json_data=_CRYPTOCOMPARE_OK)
            result = CryptoPanicFetcher().fetch()
        assert isinstance(result, list)
        assert len(result) > 0
        assert all(isinstance(h, str) for h in result)

    def test_404_returns_empty_list_without_raising(self) -> None:
        """Both sources fail → empty list, no exception."""
        with patch("src.data.news.requests.get") as mock_get:
            mock_get.return_value = _mock_response(404)
            result = CryptoPanicFetcher().fetch()
        assert result == []

    def test_fallback_attempted_on_primary_failure(self) -> None:
        """Primary returns empty; fallback (CoinDesk) is tried."""
        primary_resp = _mock_response(200, json_data={"Data": []})  # empty Data
        fallback_resp = _mock_response(200, text=_COINDESK_RSS_OK)

        with patch("src.data.news.requests.get") as mock_get:
            mock_get.side_effect = [primary_resp, fallback_resp]
            result = CryptoPanicFetcher().fetch()

        # Two calls were made (primary + fallback)
        assert mock_get.call_count == 2
        assert isinstance(result, list)
        assert len(result) > 0
