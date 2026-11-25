"""API REST de l'agent + service de l'application Web (PWA)."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..agent.runtime import AgentRuntime
from ..analysis.orderflow import OrderFlowSnapshot
from ..analysis.price_action import sr_levels
from ..analysis.regime import REGIME_LABELS
from ..learning import registry
from ..risk.engine import RiskConfig
from ..strategies.library import default_strategies
from .auth import Auth

ROOT = Path(__file__).resolve().parents[2]
WEB = Path(__file__).resolve().parents[1] / "web"


def _clean(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if (np.isnan(o) or np.isinf(o)) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return o


def create_app(rt: AgentRuntime, auth: Auth, quantower_token: str | None = None) -> FastAPI:
    app = FastAPI(title="Agent IA Forex", version="1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    user = auth.dependency()
    J = lambda o: JSONResponse(_clean(o))  # noqa: E731

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    # ---------- public ----------
    @app.get("/api/health")
    def health():
        return {"ok": True, "running": rt.running, "last_cycle": rt.last_cycle.isoformat() if rt.last_cycle else None,
                "broker_connected": rt.broker.is_connected()}

    @app.post("/api/auth/login")
    def login(request: Request, body: dict = Body(...)):
        tok = auth.login(str(body.get("username", "")), str(body.get("password", "")),
                         request.client.host if request.client else "-")
        rt.store.log("sécurité", f"Connexion à l'application ({body.get('username')})")
        return {"token": tok}

    @app.post("/api/quantower/ingest")
    def quantower_ingest(payload: dict = Body(...), x_bridge_token: str | None = Header(default=None)):
        if not quantower_token or x_bridge_token != quantower_token:
            raise HTTPException(401, "Jeton du bridge Quantower invalide")
        items = payload.get("items") or [payload]
        rev = {v: k for k, v in getattr(rt, "quantower_map", {}).items()}
        for it in items:
            sym = str(it.get("symbol", "")).upper()
            sym = rev.get(sym, rev.get(sym[:2], sym))
            rt.orderflow.update(OrderFlowSnapshot(
                symbol=sym, ts=time.time(), source="quantower", last=float(it.get("last", 0)),
                delta=float(it.get("delta", 0)), cum_delta=float(it.get("cum_delta", 0)),
                buy_volume=float(it.get("buy_volume", 0)), sell_volume=float(it.get("sell_volume", 0)),
                poc=it.get("poc"), vah=it.get("vah"), val=it.get("val"), dom_bid=float(it.get("dom_bid", 0)),
                dom_ask=float(it.get("dom_ask", 0)), imbalances=it.get("imbalances", []), absorption=it.get("absorption")))
        return {"ok": True, "received": len(items)}

    # ---------- état ----------
    @app.get("/api/status")
    def status(u: str = Depends(user)):
        return J(rt.status())

    @app.get("/api/dashboard")
    def dashboard(u: str = Depends(user)):
        perf = rt.performance()
        return J({"status": rt.status(), "positions": [vars(p) for p in rt.broker.positions(rt.magic)],
                  "pending": [s.to_dict() for s in rt.pending.values()], "signals": rt.store.signals(8),
                  "events": rt.store.events(15), "decisions": list(rt.decisions.values()),
                  "performance": {k: v for k, v in perf.items() if k != "equity_curve"},
                  "equity": rt.store.equity_curve(500)})

    @app.get("/api/positions")
    def positions(u: str = Depends(user)):
        out = []
        trades = {t["ticket"]: t for t in rt.store.trades(status="open")}
        for p in rt.broker.positions(rt.magic):
            d = vars(p).copy()
            t = trades.get(p.ticket, {})
            d.update(strategy=t.get("strategy"), sl_initial=t.get("sl_initial"), risk_amount=t.get("risk_amount"),
                     events=t.get("events"), trail=t.get("trail"))
            out.append(d)
        return J({"positions": out, "orders": rt.broker.pending_orders(),
                  "pending_signals": [s.to_dict() for s in rt.pending.values()]})

    @app.get("/api/signals")
    def signals(status: str | None = None, limit: int = 50, u: str = Depends(user)):
        return J(rt.store.signals(limit, status))

    @app.post("/api/signals/{sid}/approve")
    def approve(sid: str, u: str = Depends(user)):
        return J(rt.approve(sid, f"app:{u}"))

    @app.post("/api/signals/{sid}/reject")
    def reject(sid: str, u: str = Depends(user)):
        return J(rt.reject(sid, f"app:{u}"))

    @app.get("/api/trades")
    def trades(status: str | None = None, symbol: str | None = None, strategy: str | None = None, limit: int = 300,
               u: str = Depends(user)):
        return J(rt.store.trades(status, limit, symbol, strategy))

    @app.get("/api/performance")
    def performance(u: str = Depends(user)):
        return J({"live": rt.performance(), "equity": rt.store.equity_curve(2000)})

    @app.get("/api/events")
    def events(limit: int = 100, category: str | None = None, u: str = Depends(user)):
        return J(rt.store.events(limit, category))

    # ---------- analyse de marché ----------
    @app.get("/api/analysis/{symbol}")
    def analysis(symbol: str, u: str = Depends(user)):
        symbol = symbol.upper()
        f = rt.features_cache.get(symbol)
        if f is None:
            try:
                rt.analyze_symbol(symbol)
                f = rt.features_cache.get(symbol)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(503, f"Données indisponibles : {e}") from e
        if f is None:
            raise HTTPException(404, "Pas encore de données pour ce symbole")
        tail = f.tail(120)
        bars = [{"t": i.isoformat(), "o": r.open, "h": r.high, "l": r.low, "c": r.close, "v": r.volume}
                for i, r in tail[["open", "high", "low", "close", "volume"]].iterrows()]
        last = f.iloc[-1]
        of = rt.orderflow.get(symbol)
        return J({"symbol": symbol, "timeframe": rt.entry_tf, "bars": bars, "decision": rt.decisions.get(symbol),
                  "levels": sr_levels(f, float(last["atr"])),
                  "regime": {"value": last["regime"], "label": REGIME_LABELS.get(last["regime"]), "adx": last["adx"],
                             "er": last["er"], "vol_state": last["vol_state"], "session": last["session"],
                             "atr": last["atr"], "rsi": last["rsi"], "htf1_trend": last["htf1_trend"],
                             "htf2_trend": last["htf2_trend"], "structure": last["structure"]},
                  "smc": {k: last.get(k) for k in ("fvg_bull_top", "fvg_bull_bot", "fvg_bear_top", "fvg_bear_bot",
                                                   "ob_bull_top", "ob_bull_bot", "ob_bear_top", "ob_bear_bot", "bsl", "ssl",
                                                   "pd_pos", "last_sh", "last_sl", "since_sweep_bull", "since_sweep_bear")},
                  "orderflow": {"source": "quantower" if of else "proxy (volume des bougies)",
                                "delta": of.delta if of else last["delta_proxy"],
                                "cum_delta": of.cum_delta if of else last["cum_delta_50"],
                                "dom_imbalance": of.dom_imbalance if of else None,
                                "poc": (of.poc if of and of.poc else last["vp_poc"]), "vah": (of.vah if of and of.vah else last["vp_vah"]),
                                "val": (of.val if of and of.val else last["vp_val"]), "vol_z": last["vol_z"],
                                "absorption": bool(last["absorption"]), "imbalances": of.imbalances if of else []},
                  "calendar": [e for e in rt.calendar.upcoming() if e["currency"] in (symbol[:3], symbol[3:6])]})

    # ---------- stratégies ----------
    @app.get("/api/strategies")
    def strategies(u: str = Depends(user)):
        live = {s.name: s for s in rt.engine.strategies}
        perf = rt.performance().get("by_strategy", {})
        out = []
        for s in default_strategies():
            cur = live.get(s.name, s)
            mat = rt.engine.perf.get(s.name, {})
            out.append({**cur.info(), "learned": {"all": mat.get("all"), "by_regime": mat.get("by_regime", {}),
                                                  "by_symbol": mat.get("by_symbol_all", {})},
                        "live": perf.get(s.name)})
        return J(out)

    @app.put("/api/strategies/{name}")
    def toggle_strategy(name: str, body: dict = Body(...), u: str = Depends(user)):
        for s in rt.engine.strategies:
            if s.name == name:
                s.enabled = bool(body.get("enabled", True))
                ov = rt.store.get("strategy_overrides") or {}
                ov[name] = s.enabled
                rt.store.set("strategy_overrides", ov)
                rt.store.log("contrôle", f"Stratégie {name} {'activée' if s.enabled else 'désactivée'} par {u}")
                return {"ok": True, "enabled": s.enabled}
        raise HTTPException(404, "Stratégie inconnue")

    # ---------- risque ----------
    @app.get("/api/risk")
    def risk(u: str = Depends(user)):
        rs = rt.store.get("risk_state") or {}
        acct = rt.broker.account()
        pos = rt.broker.positions(rt.magic)
        exposure: dict[str, float] = {}
        total = 0.0
        for p in pos:
            r = rt.risk.position_risk(p, rt.broker.value_per_price_unit)
            pct = 100 * r / acct.equity if acct.equity else 0
            total += pct
            sgn = 1 if p.direction == "BUY" else -1
            exposure[p.symbol[:3]] = exposure.get(p.symbol[:3], 0) + sgn * pct
            exposure[p.symbol[3:6]] = exposure.get(p.symbol[3:6], 0) - sgn * pct
        cfg = rt.risk.cfg
        day = 100 * (rs.get("day_start_equity", acct.equity) - acct.equity) / rs.get("day_start_equity", acct.equity)
        week = 100 * (rs.get("week_start_equity", acct.equity) - acct.equity) / rs.get("week_start_equity", acct.equity)
        dd = 100 * (rs.get("peak_equity", acct.equity) - acct.equity) / rs.get("peak_equity", acct.equity)
        blocked = [s for s in rt.store.signals(40) if s["status"] == "blocked"][:10]
        return J({"config": cfg.to_dict(), "usage": {"daily_loss_pct": day, "weekly_loss_pct": week, "drawdown_pct": dd,
                                                     "open_positions": len(pos), "open_risk_pct": total, "exposure": exposure},
                  "halted": rt.halted, "halt_reason": rt.halt_reason, "recent_rejections": blocked})

    @app.put("/api/risk")
    def update_risk(body: dict = Body(...), u: str = Depends(user)):
        cur = rt.risk.cfg.to_dict()
        allowed = {k for k in cur if k not in ("max_spread_pips", "max_sl_pips")}
        bounds = {"risk_per_trade_pct": (0.05, 3.0), "max_daily_loss_pct": (0.5, 10), "max_weekly_loss_pct": (1, 20),
                  "max_drawdown_pct": (2, 30), "max_open_positions": (1, 10), "max_total_risk_pct": (0.1, 10),
                  "max_currency_exposure_pct": (0.1, 10), "min_rr": (1.0, 5.0)}
        for k, v in body.items():
            if k in allowed:
                lo, hi = bounds.get(k, (-1e9, 1e9))
                v = type(cur[k])(v)
                if not lo <= v <= hi:
                    raise HTTPException(400, f"{k} hors bornes [{lo}, {hi}]")
                cur[k] = v
        rt.risk.cfg = RiskConfig(**cur)
        rt.store.set("risk_config", cur)
        rt.store.log("contrôle", f"Paramètres de risque modifiés par {u}", data=body)
        return J({"ok": True, "config": cur})

    # ---------- contrôle ----------
    @app.post("/api/control/{action}")
    def control(action: str, body: dict = Body(default={}), u: str = Depends(user)):
        by = f"app:{u}"
        acts = {"auto_on": lambda: rt.set_auto(True, by), "auto_off": lambda: rt.set_auto(False, by),
                "pause": lambda: rt.pause(by), "resume": lambda: rt.resume(by),
                "mode_manual": lambda: rt.set_mode("manual", by), "mode_auto": lambda: rt.set_mode("auto", by),
                "emergency_stop": lambda: rt.emergency_stop(bool(body.get("close_all", True)), by),
                "reset_safety": lambda: rt.reset_safety(by),
                "close": lambda: rt.close_position(int(body["ticket"]), by),
                "telegram_test": lambda: (rt.telegram.send("✅ Test de notification depuis l'application") or
                                          ("Message envoyé" if rt.telegram.enabled else "Telegram non configuré")),
                "analyze_now": lambda: (rt.cycle() or "Cycle d'analyse exécuté")}
        if action not in acts:
            raise HTTPException(404, "Action inconnue")
        return {"ok": True, "message": acts[action](), "status": _clean(rt.status())}

    # ---------- apprentissage / recherche ----------
    @app.get("/api/learning")
    def learning(u: str = Depends(user)):
        reg = registry.load()
        versions = []
        for v in reg.get("versions", []):
            versions.append({k: v.get(k) for k in ("version", "status", "created_at", "entry_tf", "symbols", "is_period",
                                                     "oos_period", "oos_metrics", "gate", "meta_model", "meta_model_active",
                                                     "optimization", "raw_is", "raw_oos", "validation", "duration_s")}
                            | {"walkforward": v.get("walkforward"), "oos_equity_curve": v.get("oos_equity_curve")})
        review = rt.store.get("journal_review")
        return J({"active": reg.get("active"), "loaded": rt.model_info, "versions": versions, "journal_review": review,
                  "running": getattr(app.state, "training", False)})

    @app.post("/api/learning/run")
    def learning_run(u: str = Depends(user)):
        if getattr(app.state, "training", False):
            return {"ok": False, "message": "Apprentissage déjà en cours"}

        def job():
            app.state.training = True
            try:
                from ..learning.trainer import run
                c = run(rt.symbols, rt.entry_tf, 12)
                rt._event("apprentissage", f"Version {c['version']} : {c['status']} ({'; '.join(c['gate'])})", notify=True)
            except Exception as e:  # noqa: BLE001
                rt.store.log("apprentissage", f"Échec : {e}", "error")
            finally:
                app.state.training = False
        threading.Thread(target=job, daemon=True).start()
        return {"ok": True, "message": "Apprentissage offline lancé (backtest, Optuna, walk-forward, validation)"}

    @app.post("/api/learning/review")
    def learning_review(u: str = Depends(user)):
        from ..learning.journal_review import review
        r = review(rt.store)
        rt.store.set("journal_review", r)
        return J(r)

    @app.get("/api/research")
    def research(u: str = Depends(user)):
        p = ROOT / "research_lab" / "output" / "catalog.json"
        cat = json.loads(p.read_text()) if p.exists() else []
        sel = ROOT / "research_lab" / "output" / "selection.json"
        return J({"count": len(cat), "top": [{k: r[k] for k in ("rank", "full_name", "url", "stars", "language", "license",
                                                                  "categories", "scores", "reusability", "technologies",
                                                                  "strengths", "weaknesses")} for r in cat[:40]],
                  "selection": json.loads(sel.read_text()) if sel.exists() else None})

    @app.get("/api/calendar")
    def calendar(u: str = Depends(user)):
        return J({"events": rt.calendar.upcoming(72), "error": rt.calendar.error})

    # ---------- application Web ----------
    if WEB.exists():
        app.mount("/static", StaticFiles(directory=WEB), name="static")

        @app.get("/manifest.webmanifest")
        def manifest():
            return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json")

        @app.get("/sw.js")
        def sw():
            return FileResponse(WEB / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})

        @app.get("/")
        def index():
            return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    return app
