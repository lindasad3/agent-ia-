"""Flux complet : signal -> validation manuelle -> exécution vérifiée -> suivi -> clôture -> historique -> API."""
from datetime import timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from forex_agent.agent.runtime import AgentRuntime
from forex_agent.api.app import create_app
from forex_agent.api.auth import Auth, hash_password
from forex_agent.brain.decision import Decision
from forex_agent.execution.paper import PaperBroker, ReplayMarket
from forex_agent.models import Signal
from forex_agent.storage.db import Store
from forex_agent.strategies.library import default_strategies
from tests.conftest import make_m1


class ScriptedEngine:
    """Moteur de décision factice : émet un BUY sur la première bougie analysée."""

    def __init__(self):
        self.strategies, self.perf, self.done = default_strategies(), {}, False

    def decide(self, symbol, F, i, tf, orderflow=None, news_block=None, now=None, precomputed=None):
        ts = pd.Timestamp(F.index[i]).to_pydatetime()
        d = Decision(symbol, ts, str(F["regime"][i]), str(F["vol_state"][i]), str(F["session"][i]))
        if not self.done:
            self.done = True
            c, a = float(F["close"][i]), float(F["atr"][i])
            d.signal = Signal(symbol, tf, "BUY", "trend_pullback", c, c - 1.5 * a, c + 3 * a, 75, 0.5, 2.0, 0.3,
                              d.regime, ["test"], "Explication de test", created_at=now, valid_minutes=600)
        return d


@pytest.fixture()
def rt(tmp_path):
    m1 = make_m1(days=60, seed=3)
    start = m1.index[0] + pd.Timedelta(days=45)
    broker = PaperBroker(ReplayMarket({"EURUSD": m1}, start.to_pydatetime(), tfs=("H1", "H4", "D1")), 10_000)
    store = Store(f"sqlite:///{tmp_path}/t.db")
    r = AgentRuntime(broker, store, ScriptedEngine(), {"version": "test", "validated": True}, symbols=["EURUSD"],
                     entry_tf="H1", mode="manual", sim_minutes_per_cycle=60)
    r.running = True
    r.connect()
    return r


def test_manual_flow_end_to_end(rt):
    rt.cycle()
    assert len(rt.pending) == 1
    sid = next(iter(rt.pending))
    assert rt.store.signal(sid)["status"] == "pending"
    res = rt.approve(sid, "test")
    assert res["ok"] and res["status"] == "executed"
    pos = rt.broker.positions(rt.magic)
    assert len(pos) == 1 and pos[0].volume > 0
    t = rt.store.trade_by_ticket(pos[0].ticket)
    assert t["status"] == "open" and t["signal_id"] == sid
    # Avance jusqu'à la clôture (SL ou TP) sur les données rejouées
    for _ in range(24 * 14):
        rt.broker.market.advance(60)
        rt.cycle()
        if not rt.broker.positions(rt.magic):
            break
    rt.cycle()
    closed = rt.store.trades(status="closed")
    assert len(closed) == 1 and closed[0]["pnl"] is not None and closed[0]["close_reason"]
    perf = rt.performance()
    assert perf["n_trades"] == 1


def test_risk_limits_halt(rt):
    rt.store.set("risk_state", {"day": rt.clock().strftime("%Y-%m-%d"), "day_start_equity": 11_000,
                                "week": "%d-W%02d" % rt.clock().isocalendar()[:2], "week_start_equity": 11_000,
                                "peak_equity": 11_000})
    rt.cycle()
    assert rt.halted and rt.paused


def test_emergency_stop_closes_positions(rt):
    rt.cycle()
    sid = next(iter(rt.pending))
    rt.approve(sid)
    assert rt.broker.positions(rt.magic)
    msg = rt.emergency_stop(True, "test")
    assert "urgence" in msg and not rt.broker.positions(rt.magic) and not rt.auto_trading and rt.halted


def test_api_auth_and_endpoints(rt):
    app = create_app(rt, Auth("admin", hash_password("pw"), "secret"), quantower_token="qt")
    c = TestClient(app)
    assert c.get("/api/status").status_code == 401
    assert c.post("/api/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401
    tok = c.post("/api/auth/login", json={"username": "admin", "password": "pw"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    rt.cycle()
    assert c.get("/api/status", headers=h).json()["pending_signals"] == 1
    sid = next(iter(rt.pending))
    assert c.post(f"/api/signals/{sid}/approve", headers=h).json()["ok"]
    assert len(c.get("/api/positions", headers=h).json()["positions"]) == 1
    for path in ["/api/dashboard", "/api/analysis/EURUSD", "/api/strategies", "/api/risk", "/api/learning", "/api/trades",
                 "/api/performance", "/api/events", "/api/research"]:
        assert c.get(path, headers=h).status_code == 200, path
    assert c.put("/api/risk", headers=h, json={"risk_per_trade_pct": 50}).status_code == 400
    assert c.put("/api/risk", headers=h, json={"risk_per_trade_pct": 0.5}).json()["config"]["risk_per_trade_pct"] == 0.5
    assert c.post("/api/quantower/ingest", json={"symbol": "6E", "delta": 10}).status_code == 401
    rt.quantower_map = {"EURUSD": "6E"}
    ok = c.post("/api/quantower/ingest", headers={"X-Bridge-Token": "qt"},
                json={"items": [{"symbol": "6E", "delta": 120, "dom_bid": 300, "dom_ask": 100}]})
    assert ok.json()["received"] == 1 and rt.orderflow.get("EURUSD").dom_imbalance == pytest.approx(0.5)
    assert c.post("/api/control/emergency_stop", headers=h, json={"close_all": True}).json()["ok"]
    assert c.get("/").status_code == 200 and c.get("/manifest.webmanifest").status_code == 200
