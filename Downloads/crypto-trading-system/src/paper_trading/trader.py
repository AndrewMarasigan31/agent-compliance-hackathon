"""Paper trading tick engine — US-004.

PaperTrader wraps BacktestEngine for live hourly ticks, persisting state
between runs and returning trade events for Telegram alerts.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.backtesting.engine import (
    BTCRegime,
    BacktestEngine,
    MLScorer,
    _Position,
    _utc_date_str,
)
from src.paper_trading.fetcher import LiveCandleFetcher
from src.paper_trading.state import DEFAULT_STATE_PATH, PaperState, load_state, save_state

logger = logging.getLogger(__name__)


class PaperTrader:
    """Runs one hourly tick of the paper trading engine.

    On each call to :meth:`tick`:
    1. Loads (or creates) persisted state.
    2. Restores engine internals from state.
    3. Fetches live candles.
    4. Calls ``engine._step``.
    5. Diffs positions to find newly opened / closed events.
    6. Saves updated state.
    7. Returns ``(newly_opened, newly_closed)``.

    Parameters
    ----------
    pairs:
        Symbols to trade, e.g. ``['BTC/USDT', 'ETH/USDT']``.
    initial_capital:
        Starting portfolio value in USD (used only when no saved state exists).
    glm_regime:
        If *True*, instantiate ``GLMClient`` + sentiment/news fetchers for
        regime detection.  If *False*, use rule-based ``BTCRegime``.
    state_path:
        Path to the JSON state file.
    """

    def __init__(
        self,
        pairs: list[str],
        initial_capital: float = 10_000.0,
        glm_regime: bool = True,
        state_path: Path = DEFAULT_STATE_PATH,
    ) -> None:
        self._pairs = pairs
        self._initial_capital = initial_capital
        self._glm_regime = glm_regime
        self._state_path = state_path

        self._ml_scorer = MLScorer()
        self._btc_regime = BTCRegime()
        self._fetcher = LiveCandleFetcher()

        self._glm_client = None
        self._news_fetcher = None
        self._funding_fetcher = None
        self._sentiment_fetcher = None
        self._onchain_fetcher = None

        if glm_regime:
            from src.data.funding import FundingRateFetcher
            from src.data.news import CryptoPanicFetcher
            from src.data.onchain import ExchangeFlowFetcher
            from src.data.sentiment import FearGreedFetcher
            from src.llm.glm_client import GLMClient

            self._glm_client = GLMClient()
            self._news_fetcher = CryptoPanicFetcher()
            self._funding_fetcher = FundingRateFetcher()
            self._sentiment_fetcher = FearGreedFetcher()
            self._onchain_fetcher = ExchangeFlowFetcher()

        # Tracks date of the last tick for journal boundary detection
        self._prev_date_str: str | None = None

    def tick(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Run one engine tick and return (newly_opened, newly_closed).

        Each element of *newly_opened* is a dict with keys:
        ``pair, direction, entry_price, size_usd, ml_score, regime, confidence``.

        Each element of *newly_closed* is a trade dict from ``engine._trades``
        (same schema as ``BacktestEngine._trades``).
        """
        # 1. Load or create fresh state
        state = load_state(self._state_path)
        if state is None:
            state = PaperState(
                run_id=uuid.uuid4().hex[:12],
                initial_capital=self._initial_capital,
                portfolio=self._initial_capital,
                positions={},
                closed_trades=[],
                last_glm_regime_ts=0,
                last_glm_regime_str="neutral",
                last_btc_4h_change=0.0,
                last_fear_greed=0.0,
                last_regime_confidence=100,
            )

        # 2. Instantiate BacktestEngine and restore persisted state
        engine = BacktestEngine(
            pairs=self._pairs,
            initial_capital=state.initial_capital,
            regime_detector=self._btc_regime,
            scorer=self._ml_scorer,
            run_id=state.run_id,
            glm_client=self._glm_client,
            news_fetcher=self._news_fetcher,
            funding_fetcher=self._funding_fetcher,
            sentiment_fetcher=self._sentiment_fetcher,
            onchain_fetcher=self._onchain_fetcher,
        )
        engine._portfolio = state.portfolio
        engine._positions = {
            pair: _Position(**d) for pair, d in state.positions.items()
        }
        engine._last_glm_regime_ts = state.last_glm_regime_ts
        engine._last_glm_regime_str = state.last_glm_regime_str
        engine._last_btc_4h_change = state.last_btc_4h_change
        engine._last_fear_greed = state.last_fear_greed
        engine._last_regime_confidence = state.last_regime_confidence
        engine._cooldowns = dict(state.stop_loss_cooldowns)

        # 3. Fetch live candles
        candles = self._fetcher.fetch(self._pairs)

        # 4. Get current UTC timestamp in ms
        ts = int(datetime.now(timezone.utc).timestamp() * 1000)

        # Journal boundary check: write journal for previous day when date rolls over
        curr_date_str = _utc_date_str(ts)
        if self._prev_date_str is not None and curr_date_str != self._prev_date_str:
            engine._trades = [
                t for t in state.closed_trades
                if t.get("exit_date_utc") == self._prev_date_str
            ]
            engine._write_journal(self._prev_date_str)
            engine._trades = []
        self._prev_date_str = curr_date_str

        # 5. Record positions before the step
        positions_before = set(engine._positions.keys())

        # 6. Run the engine tick
        engine._step(ts, candles)

        # 7. Diff positions
        newly_opened: list[dict[str, Any]] = []
        for pair, pos in engine._positions.items():
            if pair not in positions_before:
                newly_opened.append(
                    {
                        "pair": pair,
                        "direction": pos.direction,
                        "entry_price": pos.entry_price,
                        "size_usd": pos.size_usd,
                        "ml_score": pos.ml_score_at_entry,
                        "regime": pos.regime_at_entry,
                        "confidence": pos.regime_confidence,
                    }
                )

        newly_closed: list[dict[str, Any]] = []
        for pair in positions_before:
            if pair not in engine._positions:
                # Take the last trade record for this pair from this tick
                pair_trades = [t for t in engine._trades if t["pair"] == pair]
                if pair_trades:
                    newly_closed.append(pair_trades[-1])

        # 8. Append closed trades to persistent state
        state.closed_trades.extend(newly_closed)

        # 9. Flush engine state back to PaperState and save
        state.portfolio = engine._portfolio
        state.positions = {
            pair: dataclasses.asdict(pos) for pair, pos in engine._positions.items()
        }
        state.last_glm_regime_ts = engine._last_glm_regime_ts
        state.last_glm_regime_str = engine._last_glm_regime_str
        state.last_btc_4h_change = engine._last_btc_4h_change
        state.last_fear_greed = engine._last_fear_greed
        state.last_regime_confidence = engine._last_regime_confidence
        state.stop_loss_cooldowns = engine._cooldowns

        save_state(state, self._state_path)
        logger.info(
            "tick complete: opened=%d closed=%d portfolio=%.2f",
            len(newly_opened),
            len(newly_closed),
            state.portfolio,
        )

        return newly_opened, newly_closed
