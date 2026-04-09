"""Token cooldown tracker — blocks re-entry after a stop-loss exit."""

from __future__ import annotations


class CooldownTracker:
    """Stateless helper that checks whether a pair is in its cooldown window.

    Usage::

        tracker = CooldownTracker()
        tracker.record_stop(cooldowns, "ETH/USDT", ts_ms)
        if tracker.is_cooling(cooldowns, "ETH/USDT", current_ts_ms):
            skip_entry()

    The cooldown dict (``dict[str, int]``) lives on the engine/state so it
    persists across ticks without requiring a singleton instance.
    """

    _WINDOW_MS: int = 172_800_000  # 48 hours in milliseconds

    def record_stop(self, cooldowns: dict[str, int], pair: str, ts_ms: int) -> None:
        """Record a stop-loss exit for *pair* at *ts_ms*."""
        cooldowns[pair] = ts_ms

    def is_cooling(
        self,
        cooldowns: dict[str, int],
        pair: str,
        ts_ms: int,
        window_ms: int = _WINDOW_MS,
    ) -> bool:
        """Return True if *pair* is still within its cooldown window."""
        stop_ts = cooldowns.get(pair)
        if stop_ts is None:
            return False
        return (ts_ms - stop_ts) < window_ms
