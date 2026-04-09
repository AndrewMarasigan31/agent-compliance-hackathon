"""Paper trading state persistence — atomic JSON save/load."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = Path("data/paper_trading/state.json")


@dataclass
class PaperState:
    run_id: str
    initial_capital: float
    portfolio: float
    positions: dict[str, dict]  # keyed by pair; value = dataclasses.asdict(_Position)
    closed_trades: list[dict]
    last_glm_regime_ts: int
    last_glm_regime_str: str
    last_btc_4h_change: float
    last_fear_greed: float
    last_regime_confidence: int
    stop_loss_cooldowns: dict = field(default_factory=dict)  # pair -> stop-loss ts_ms


def save_state(state: PaperState, path: Path = DEFAULT_STATE_PATH) -> None:
    """Write state to disk atomically via a .tmp file then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    data = {
        "run_id": state.run_id,
        "initial_capital": state.initial_capital,
        "portfolio": state.portfolio,
        "positions": state.positions,
        "closed_trades": state.closed_trades,
        "last_glm_regime_ts": state.last_glm_regime_ts,
        "last_glm_regime_str": state.last_glm_regime_str,
        "last_btc_4h_change": state.last_btc_4h_change,
        "last_fear_greed": state.last_fear_greed,
        "last_regime_confidence": state.last_regime_confidence,
        "stop_loss_cooldowns": state.stop_loss_cooldowns,
    }
    tmp_path.write_text(json.dumps(data, indent=2))
    os.replace(tmp_path, path)
    logger.debug("State saved to %s", path)


def load_state(path: Path = DEFAULT_STATE_PATH) -> PaperState | None:
    """Load state from disk; returns None if missing or corrupt."""
    try:
        text = path.read_text()
        data = json.loads(text)
        return PaperState(
            run_id=data["run_id"],
            initial_capital=data["initial_capital"],
            portfolio=data["portfolio"],
            positions=data["positions"],
            closed_trades=data["closed_trades"],
            last_glm_regime_ts=data["last_glm_regime_ts"],
            last_glm_regime_str=data["last_glm_regime_str"],
            last_btc_4h_change=data["last_btc_4h_change"],
            last_fear_greed=data["last_fear_greed"],
            last_regime_confidence=data["last_regime_confidence"],
            stop_loss_cooldowns=data.get("stop_loss_cooldowns", {}),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Failed to load state from %s: %s", path, exc)
        return None
