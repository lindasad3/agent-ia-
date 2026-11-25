"""Interface broker commune (MT5 réel, papier/simulation)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from ..models import AccountInfo, ClosedDeal, OrderRequest, OrderResult, Position, Tick
from ..symbols import SymbolSpec


class Broker(ABC):
    name: str = "broker"
    is_simulation: bool = False

    @abstractmethod
    def connect(self) -> bool: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    def disconnect(self) -> None:  # pragma: no cover - optionnel
        pass

    @abstractmethod
    def account(self) -> AccountInfo: ...

    @abstractmethod
    def tick(self, symbol: str) -> Tick: ...

    @abstractmethod
    def bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        """Bougies OHLCV (index UTC = heure d'ouverture), barre en cours exclue."""

    @abstractmethod
    def value_per_price_unit(self, spec: SymbolSpec, price: float) -> float: ...

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        from ..symbols import get_spec
        return get_spec(symbol)

    @abstractmethod
    def positions(self, magic: int | None = None) -> list[Position]: ...

    def pending_orders(self) -> list[dict]:
        return []

    @abstractmethod
    def place_market(self, req: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def modify(self, ticket: int, sl: float | None, tp: float | None) -> OrderResult: ...

    @abstractmethod
    def close(self, ticket: int, volume: float | None = None) -> OrderResult: ...

    @abstractmethod
    def closed_deal(self, ticket: int) -> ClosedDeal | None:
        """Résultat réel d'une position clôturée (prix de sortie, profit, commission, swap)."""

    def history(self, since: datetime) -> list[ClosedDeal]:
        return []
