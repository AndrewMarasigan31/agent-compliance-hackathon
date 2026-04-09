"""Exchange configuration dataclass."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExchangeConfig:
    id: str = "gateio"
    testnet: bool = False
