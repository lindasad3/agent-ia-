"""Objets métier partagés entre les modules."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Candidate:
    """Proposition brute d'une stratégie (avant arbitrage du cerveau et contrôle du risque)."""
    strategy: str
    direction: str  # BUY | SELL
    entry: float
    sl: float
    tp: float
    base_score: float
    reasons: list[str] = field(default_factory=list)
    trail: float = 0.0  # distance du stop suiveur (prix) ; 0 = pas de trailing

    @property
    def risk(self) -> float:
        return abs(self.entry - self.sl)

    @property
    def rr(self) -> float:
        return abs(self.tp - self.entry) / self.risk if self.risk > 0 else 0.0


@dataclass
class Signal:
    symbol: str
    timeframe: str
    direction: str
    strategy: str
    entry: float
    sl: float
    tp: float
    score: float
    probability: float
    rr: float
    ev_r: float
    regime: str
    reasons: list[str]
    explanation: str
    confluences: list[str] = field(default_factory=list)
    analysis: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    valid_minutes: int = 30
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    # Rempli par le Risk Engine
    volume: float = 0.0
    risk_amount: float = 0.0
    risk_pct: float = 0.0
    status: str = "new"
    trail: float = 0.0

    @property
    def valid_until(self) -> datetime:
        return self.created_at + timedelta(minutes=self.valid_minutes)

    def expired(self, now: datetime | None = None) -> bool:
        return (now or utcnow()) > self.valid_until

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        d["valid_until"] = self.valid_until.isoformat()
        return d


@dataclass
class RiskCheck:
    name: str
    ok: bool
    detail: str


@dataclass
class RiskDecision:
    approved: bool
    volume: float = 0.0
    risk_amount: float = 0.0
    risk_pct: float = 0.0
    checks: list[RiskCheck] = field(default_factory=list)

    @property
    def rejections(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.ok]


@dataclass
class AccountInfo:
    balance: float
    equity: float
    margin_free: float
    currency: str = "USD"
    leverage: int = 100
    login: int | None = None
    server: str = ""
    is_demo: bool = True
    name: str = ""


@dataclass
class Tick:
    symbol: str
    bid: float
    ask: float
    time: datetime

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass
class Position:
    ticket: int
    symbol: str
    direction: str
    volume: float
    entry_price: float
    sl: float
    tp: float
    open_time: datetime
    profit: float = 0.0
    swap: float = 0.0
    comment: str = ""
    magic: int = 0
    price_current: float = 0.0


@dataclass
class OrderRequest:
    symbol: str
    direction: str
    volume: float
    sl: float
    tp: float
    price: float | None = None
    deviation_points: int = 10
    comment: str = ""
    magic: int = 770077
    signal_id: str = ""


@dataclass
class OrderResult:
    ok: bool
    verified: bool = False
    ticket: int | None = None
    deal: int | None = None
    price: float = 0.0
    requested_price: float = 0.0
    volume: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    slippage_pips: float = 0.0
    retcode: int | None = None
    message: str = ""
    attempts: int = 1


@dataclass
class ClosedDeal:
    ticket: int
    symbol: str
    direction: str
    volume: float
    entry_price: float
    exit_price: float
    open_time: datetime
    close_time: datetime
    profit: float
    commission: float = 0.0
    swap: float = 0.0
    reason: str = ""
