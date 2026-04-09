"""Historical OHLCV fetcher using CCXT — paginates through exchange API limits."""

from __future__ import annotations

import logging
import time

import ccxt
import pandas as pd

from src.utils.config import ExchangeConfig

logger = logging.getLogger(__name__)

_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
_LIMIT = 1000      # candles per request (Gate.io max)
_SLEEP_S = 0.3     # polite delay between paginated requests


class ExchangeFetcher:
    """Fetches historical OHLCV data from Gate.io via CCXT with pagination."""

    def __init__(self, config: ExchangeConfig | None = None) -> None:
        self._config = config or ExchangeConfig()
        self._exchange: ccxt.Exchange = ccxt.gate(
            {"options": {"defaultType": "spot"}}
        )

    def fetch_historical_ohlcv(
        self,
        pair: str,
        timeframe: str,
        since_ms: int,
        until_ms: int,
    ) -> pd.DataFrame:
        """Paginate through Gate.io OHLCV data between since_ms and until_ms.

        Returns a DataFrame with columns [timestamp, open, high, low, close, volume].
        Returns an empty DataFrame on any error.
        """
        all_rows: list[list] = []
        fetch_since = since_ms

        while fetch_since < until_ms:
            try:
                batch = self._exchange.fetch_ohlcv(
                    pair,
                    timeframe=timeframe,
                    since=fetch_since,
                    limit=_LIMIT,
                )
            except Exception as exc:
                logger.warning("fetch_ohlcv error for %s at since=%d: %s", pair, fetch_since, exc)
                break

            if not batch:
                break

            # Filter candles beyond until_ms
            filtered = [c for c in batch if c[0] < until_ms]
            all_rows.extend(filtered)

            last_ts = batch[-1][0]
            if last_ts >= until_ms or len(filtered) < len(batch):
                break  # reached the end of requested range

            # Advance past the last received candle
            fetch_since = last_ts + 1
            time.sleep(_SLEEP_S)

        if not all_rows:
            return pd.DataFrame(columns=_COLUMNS)

        df = pd.DataFrame(all_rows, columns=_COLUMNS)
        df["timestamp"] = df["timestamp"].astype("int64")
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype("float64")
        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        return df
