"""Broker papier + marché rejoué (données historiques réelles M1).

Sert aux tests, à la démonstration sans compte MT5 et au mode « paper ». Toujours étiqueté SIMULATION :
aucune opération réalisée ici n'est présentée comme un trade réel.
"""
from __future__ import annotations

import itertools
import threading
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from ..analysis.features import TF_MINUTES, resample
from ..models import AccountInfo, ClosedDeal, OrderRequest, OrderResult, Position, Tick
from ..symbols import SymbolSpec, get_spec, value_per_price_unit
from .broker import Broker


class ReplayMarket:
    """Horloge simulée qui avance sur l'historique M1 réel."""

    def __init__(self, m1: dict[str, pd.DataFrame], start: datetime, tfs: tuple[str, ...] = ("H1", "H4", "D1")):
        self.m1 = {s: d.sort_index() for s, d in m1.items()}
        self.now = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz="UTC")
        self.frames = {s: {tf: resample(d, tf) for tf in tfs} for s, d in self.m1.items()}
        self.lock = threading.Lock()

    def advance(self, minutes: int) -> None:
        with self.lock:
            self.now = self.now + pd.Timedelta(minutes=minutes)

    def last_m1(self, symbol: str) -> pd.Series:
        d = self.m1[symbol]
        i = d.index.searchsorted(self.now, side="right") - 1
        return d.iloc[max(i, 0)]

    def m1_between(self, symbol: str, a: pd.Timestamp, b: pd.Timestamp) -> pd.DataFrame:
        d = self.m1[symbol]
        return d[(d.index >= a) & (d.index < b)]

    def bars(self, symbol: str, tf: str, count: int) -> pd.DataFrame:
        f = self.frames[symbol].get(tf)
        if f is None:
            f = self.frames[symbol][tf] = resample(self.m1[symbol], tf)
        closed = f[f.index + pd.Timedelta(minutes=TF_MINUTES[tf]) <= self.now]
        return closed.tail(count)


class PaperBroker(Broker):
    name = "PAPER"
    is_simulation = True

    def __init__(self, market: ReplayMarket, balance: float = 10_000.0, currency: str = "USD",
                 spread_mult: float = 1.0, commission_per_lot: float = 7.0):
        self.market = market
        self.balance = balance
        self.currency = currency
        self.spread_mult = spread_mult
        self.commission = commission_per_lot
        self._pos: dict[int, Position] = {}
        self._closed: dict[int, ClosedDeal] = {}
        self._tickets = itertools.count(100001)
        self._last_check = market.now
        self._connected = False
        self.lock = threading.RLock()

    def connect(self) -> bool:
        self._connected = True
        return True

    def is_connected(self) -> bool:
        return self._connected

    def disconnect(self) -> None:
        self._connected = False

    def _spread(self, symbol: str) -> float:
        s = get_spec(symbol)
        return s.typical_spread_pips * s.pip_size * self.spread_mult

    def tick(self, symbol: str) -> Tick:
        bar = self.market.last_m1(symbol)
        dg = get_spec(symbol).digits
        bid = round(float(bar["close"]), dg)
        return Tick(symbol, bid, round(bid + self._spread(symbol), dg), self.market.now.to_pydatetime())

    def bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        return self.market.bars(symbol, timeframe, count)

    def _rates(self) -> dict[str, float]:
        out = {}
        for k in ("USDJPY", "USDCAD", "USDCHF", "EURUSD", "GBPUSD"):
            if k in self.market.m1:
                out[k] = float(self.market.last_m1(k)["close"])
        return out

    def value_per_price_unit(self, spec: SymbolSpec, price: float) -> float:
        return value_per_price_unit(spec, price, self.currency, {**{"USDJPY": 150.0, "USDCAD": 1.36, "USDCHF": 0.88},
                                                                 **self._rates()})

    def _floating(self, p: Position) -> float:
        t = self.tick(p.symbol)
        px = t.bid if p.direction == "BUY" else t.ask
        diff = (px - p.entry_price) if p.direction == "BUY" else (p.entry_price - px)
        p.price_current = px
        return diff * p.volume * self.value_per_price_unit(get_spec(p.symbol), px)

    def account(self) -> AccountInfo:
        with self.lock:
            self.update()
            fl = sum(self._floating(p) for p in self._pos.values())
            eq = self.balance + fl
            return AccountInfo(balance=round(self.balance, 2), equity=round(eq, 2), margin_free=round(eq, 2),
                               currency=self.currency, leverage=100, login=0, server="PAPER-REPLAY", is_demo=True,
                               name="Simulation (données historiques réelles)")

    def positions(self, magic: int | None = None) -> list[Position]:
        with self.lock:
            self.update()
            for p in self._pos.values():
                p.profit = round(self._floating(p), 2)
            return [Position(**vars(p)) for p in self._pos.values() if magic is None or p.magic == magic]

    def place_market(self, req: OrderRequest) -> OrderResult:
        with self.lock:
            t = self.tick(req.symbol)
            buy = req.direction == "BUY"
            price = t.ask if buy else t.bid
            if (buy and not (req.sl < price < req.tp)) or (not buy and not (req.tp < price < req.sl)):
                return OrderResult(False, retcode=10016, message="Stops invalides", requested_price=price)
            ticket = next(self._tickets)
            self._pos[ticket] = Position(ticket, req.symbol, req.direction, req.volume, price, req.sl, req.tp,
                                         t.time, comment=req.comment, magic=req.magic, price_current=price)
            self.balance -= self.commission * req.volume / 2
            return OrderResult(True, verified=True, ticket=ticket, deal=ticket, price=price, requested_price=price,
                               volume=req.volume, sl=req.sl, tp=req.tp, slippage_pips=0.0, retcode=10009,
                               message="SIMULATION : exécuté sur le marché rejoué")

    def modify(self, ticket: int, sl: float | None, tp: float | None) -> OrderResult:
        with self.lock:
            p = self._pos.get(ticket)
            if not p:
                return OrderResult(False, ticket=ticket, message="Position introuvable")
            if sl is not None:
                p.sl = sl
            if tp is not None:
                p.tp = tp
            return OrderResult(True, verified=True, ticket=ticket, sl=p.sl, tp=p.tp, retcode=10009, message="SIMULATION : modifié")

    def _close_at(self, p: Position, price: float, when: datetime, reason: str) -> None:
        spec = get_spec(p.symbol)
        diff = (price - p.entry_price) if p.direction == "BUY" else (p.entry_price - price)
        profit = diff * p.volume * self.value_per_price_unit(spec, price)
        comm = self.commission * p.volume
        self.balance += profit - comm / 2
        self._closed[p.ticket] = ClosedDeal(p.ticket, p.symbol, p.direction, p.volume, p.entry_price, price,
                                            p.open_time, when, round(profit, 2), -round(comm, 2), 0.0, reason)
        self._pos.pop(p.ticket, None)

    def close(self, ticket: int, volume: float | None = None) -> OrderResult:
        with self.lock:
            p = self._pos.get(ticket)
            if not p:
                return OrderResult(False, ticket=ticket, message="Position introuvable")
            t = self.tick(p.symbol)
            px = t.bid if p.direction == "BUY" else t.ask
            self._close_at(p, px, t.time, "Clôture manuelle")
            return OrderResult(True, verified=True, ticket=ticket, price=px, volume=p.volume, retcode=10009,
                               message="SIMULATION : position clôturée")

    def update(self) -> None:
        """Déclenche SL/TP sur les bougies M1 écoulées depuis le dernier contrôle (SL prioritaire)."""
        now = self.market.now
        a = self._last_check
        if now <= a:
            return
        for p in list(self._pos.values()):
            seg = self.market.m1_between(p.symbol, max(a, pd.Timestamp(p.open_time)), now)
            if seg.empty:
                continue
            sp = self._spread(p.symbol)
            hi, lo = seg["high"].to_numpy(), seg["low"].to_numpy()
            if p.direction == "BUY":
                s_hit, t_hit = lo <= p.sl, hi >= p.tp
            else:
                s_hit, t_hit = hi + sp >= p.sl, lo + sp <= p.tp
            i_s = int(np.argmax(s_hit)) if s_hit.any() else None
            i_t = int(np.argmax(t_hit)) if t_hit.any() else None
            if i_s is not None and (i_t is None or i_s <= i_t):
                self._close_at(p, p.sl, seg.index[i_s].to_pydatetime(), "SL")
            elif i_t is not None:
                self._close_at(p, p.tp, seg.index[i_t].to_pydatetime(), "TP")
        self._last_check = now

    def closed_deal(self, ticket: int) -> ClosedDeal | None:
        with self.lock:
            self.update()
            return self._closed.get(ticket)

    def history(self, since: datetime) -> list[ClosedDeal]:
        return [d for d in self._closed.values() if d.close_time >= since]


def utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


__all__ = ["ReplayMarket", "PaperBroker", "utc", "timedelta"]
