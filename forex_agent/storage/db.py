"""Persistance SQLite (WAL) : signaux, trades, événements, equity, état d'exécution."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (Column, DateTime, Float, Integer, MetaData, String, Table, Text, create_engine, event, insert,
                        select, update)

meta = MetaData()

signals = Table(
    "signals", meta,
    Column("id", String(16), primary_key=True), Column("created_at", DateTime(timezone=True), index=True),
    Column("symbol", String(12), index=True), Column("timeframe", String(4)), Column("direction", String(4)),
    Column("strategy", String(40)), Column("entry", Float), Column("sl", Float), Column("tp", Float),
    Column("volume", Float), Column("risk_pct", Float), Column("risk_amount", Float), Column("rr", Float),
    Column("score", Float), Column("probability", Float), Column("ev_r", Float), Column("regime", String(16)),
    Column("status", String(16), index=True), Column("valid_until", DateTime(timezone=True)),
    Column("decided_by", String(40)), Column("explanation", Text), Column("reasons", Text), Column("analysis", Text),
    Column("risk_checks", Text), Column("trade_ticket", Integer), Column("trail", Float), Column("mode", String(8)),
)

trades = Table(
    "trades", meta,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("ticket", Integer, index=True),
    Column("signal_id", String(16)), Column("symbol", String(12), index=True), Column("direction", String(4)),
    Column("strategy", String(40)), Column("timeframe", String(4)), Column("regime", String(16)),
    Column("volume", Float), Column("entry_price", Float), Column("exit_price", Float),
    Column("requested_price", Float), Column("slippage_pips", Float),
    Column("sl_initial", Float), Column("tp_initial", Float), Column("sl", Float), Column("tp", Float),
    Column("open_time", DateTime(timezone=True)), Column("close_time", DateTime(timezone=True)),
    Column("risk_amount", Float), Column("risk_pct", Float), Column("pnl", Float), Column("pnl_r", Float),
    Column("commission", Float), Column("swap", Float), Column("close_reason", String(40)),
    Column("status", String(8), index=True), Column("mode", String(8)), Column("broker", String(12)),
    Column("analysis", Text), Column("market_conditions", Text), Column("events", Text), Column("trail", Float),
    Column("be_done", Integer, default=0),
)

events = Table(
    "events", meta,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("ts", DateTime(timezone=True), index=True),
    Column("level", String(8)), Column("category", String(20)), Column("message", Text), Column("data", Text),
)

equity = Table(
    "equity", meta,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("ts", DateTime(timezone=True), index=True),
    Column("balance", Float), Column("equity", Float),
)

kv = Table("kv", meta, Column("key", String(64), primary_key=True), Column("value", Text))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _j(v: Any) -> str:
    return json.dumps(v, default=str, ensure_ascii=False)


def _aware(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=timezone.utc) if d is not None and d.tzinfo is None else d


class Store:
    def __init__(self, url: str = "sqlite:///data/agent.db"):
        if url.startswith("sqlite:///") and not url.startswith("sqlite:////") and ":memory:" not in url:
            Path(url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, future=True, connect_args={"check_same_thread": False} if "sqlite" in url else {})
        if "sqlite" in url:
            @event.listens_for(self.engine, "connect")
            def _pragma(conn, _):  # noqa: ANN001
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()
        meta.create_all(self.engine)
        self.lock = threading.RLock()

    # ---------- clé/valeur ----------
    def get(self, key: str, default: Any = None) -> Any:
        with self.engine.connect() as c:
            r = c.execute(select(kv.c.value).where(kv.c.key == key)).first()
        return json.loads(r[0]) if r else default

    def set(self, key: str, value: Any) -> None:
        with self.lock, self.engine.begin() as c:
            if c.execute(select(kv.c.key).where(kv.c.key == key)).first():
                c.execute(update(kv).where(kv.c.key == key).values(value=_j(value)))
            else:
                c.execute(insert(kv).values(key=key, value=_j(value)))

    # ---------- événements ----------
    def log(self, category: str, message: str, level: str = "info", data: Any = None) -> None:
        with self.lock, self.engine.begin() as c:
            c.execute(insert(events).values(ts=_now(), level=level, category=category, message=message,
                                            data=_j(data) if data is not None else None))

    def events(self, limit: int = 100, category: str | None = None) -> list[dict]:
        q = select(events).order_by(events.c.id.desc()).limit(limit)
        if category:
            q = q.where(events.c.category == category)
        with self.engine.connect() as c:
            rows = [dict(r._mapping) for r in c.execute(q)]
        for r in rows:
            r["ts"] = _aware(r["ts"]).isoformat()
            r["data"] = json.loads(r["data"]) if r["data"] else None
        return rows

    # ---------- signaux ----------
    def save_signal(self, sig, status: str, decided_by: str = "agent", risk_checks: list | None = None,
                    mode: str = "manual") -> None:
        row = dict(id=sig.id, created_at=sig.created_at, symbol=sig.symbol, timeframe=sig.timeframe,
                   direction=sig.direction, strategy=sig.strategy, entry=sig.entry, sl=sig.sl, tp=sig.tp,
                   volume=sig.volume, risk_pct=sig.risk_pct, risk_amount=sig.risk_amount, rr=sig.rr, score=sig.score,
                   probability=sig.probability, ev_r=sig.ev_r, regime=sig.regime, status=status,
                   valid_until=sig.valid_until, decided_by=decided_by, explanation=sig.explanation,
                   reasons=_j(sig.reasons), analysis=_j(sig.analysis),
                   risk_checks=_j([vars(c) for c in (risk_checks or [])]), trail=sig.trail, mode=mode)
        with self.lock, self.engine.begin() as c:
            if c.execute(select(signals.c.id).where(signals.c.id == sig.id)).first():
                c.execute(update(signals).where(signals.c.id == sig.id).values(**row))
            else:
                c.execute(insert(signals).values(**row))

    def update_signal(self, sid: str, **values: Any) -> None:
        with self.lock, self.engine.begin() as c:
            c.execute(update(signals).where(signals.c.id == sid).values(**values))

    def signal(self, sid: str) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(signals).where(signals.c.id == sid)).first()
        return self._sig(dict(r._mapping)) if r else None

    def signals(self, limit: int = 50, status: str | None = None) -> list[dict]:
        q = select(signals).order_by(signals.c.created_at.desc()).limit(limit)
        if status:
            q = q.where(signals.c.status == status)
        with self.engine.connect() as c:
            return [self._sig(dict(r._mapping)) for r in c.execute(q)]

    @staticmethod
    def _sig(r: dict) -> dict:
        for k in ("created_at", "valid_until"):
            r[k] = _aware(r[k]).isoformat() if r[k] else None
        for k in ("reasons", "analysis", "risk_checks"):
            r[k] = json.loads(r[k]) if r[k] else None
        return r

    # ---------- trades ----------
    def open_trade(self, **values: Any) -> int:
        values.setdefault("status", "open")
        for k in ("analysis", "market_conditions", "events"):
            if k in values and not isinstance(values[k], str):
                values[k] = _j(values[k])
        with self.lock, self.engine.begin() as c:
            return int(c.execute(insert(trades).values(**values)).inserted_primary_key[0])

    def update_trade(self, ticket: int, **values: Any) -> None:
        for k in ("analysis", "market_conditions", "events"):
            if k in values and not isinstance(values[k], str):
                values[k] = _j(values[k])
        with self.lock, self.engine.begin() as c:
            c.execute(update(trades).where(trades.c.ticket == ticket).values(**values))

    def trade_by_ticket(self, ticket: int) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(trades).where(trades.c.ticket == ticket).order_by(trades.c.id.desc())).first()
        return self._tr(dict(r._mapping)) if r else None

    def trades(self, status: str | None = None, limit: int = 500, symbol: str | None = None,
               strategy: str | None = None) -> list[dict]:
        q = select(trades).order_by(trades.c.id.desc()).limit(limit)
        if status:
            q = q.where(trades.c.status == status)
        if symbol:
            q = q.where(trades.c.symbol == symbol)
        if strategy:
            q = q.where(trades.c.strategy == strategy)
        with self.engine.connect() as c:
            return [self._tr(dict(r._mapping)) for r in c.execute(q)]

    @staticmethod
    def _tr(r: dict) -> dict:
        for k in ("open_time", "close_time"):
            r[k] = _aware(r[k]).isoformat() if r[k] else None
        for k in ("analysis", "market_conditions", "events"):
            r[k] = json.loads(r[k]) if r[k] else None
        return r

    def add_trade_event(self, ticket: int, text: str) -> None:
        t = self.trade_by_ticket(ticket)
        if not t:
            return
        evs = t.get("events") or []
        evs.append({"ts": _now().isoformat(), "event": text})
        self.update_trade(ticket, events=evs)

    # ---------- equity ----------
    def snapshot_equity(self, balance: float, eq: float) -> None:
        with self.lock, self.engine.begin() as c:
            c.execute(insert(equity).values(ts=_now(), balance=balance, equity=eq))

    def equity_curve(self, limit: int = 2000) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(equity).order_by(equity.c.id.desc()).limit(limit)).fetchall()
        return [{"t": _aware(r.ts).isoformat(), "balance": r.balance, "equity": r.equity} for r in reversed(rows)]

    def backup(self, dest: Path) -> Path:
        """Copie à chaud de la base (API backup SQLite)."""
        import sqlite3
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(str(self.engine.url).replace("sqlite:///", ""))
        dst = sqlite3.connect(str(dest))
        with dst:
            src.backup(dst)
        src.close(); dst.close()
        return dest
