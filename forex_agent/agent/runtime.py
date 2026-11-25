"""Orchestrateur de l'agent : boucle continue, modes manuel/automatique, exécution, suivi, sécurité.

Cycle : connexion -> état du compte -> limites de risque -> gestion des positions -> analyse des paires
-> décision -> Risk Engine -> notification / validation / exécution vérifiée -> journalisation.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ..analysis.features import HTF_FOR, build_features
from ..analysis.orderflow import OrderFlowStore
from ..brain.decision import DecisionEngine
from ..data.calendar import EconomicCalendar
from ..execution.broker import Broker
from ..models import OrderRequest, Signal
from ..notify import formatter as fmt
from ..notify.telegram import TelegramBot
from ..performance.metrics import compute
from ..risk.engine import RiskConfig, RiskEngine, RiskState
from ..storage.db import Store
from ..strategies.base import FeatureView

log = logging.getLogger("agent")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AgentRuntime:
    def __init__(self, broker: Broker, store: Store, engine: DecisionEngine, model_info: dict,
                 risk: RiskEngine | None = None, telegram: TelegramBot | None = None,
                 orderflow: OrderFlowStore | None = None, calendar: EconomicCalendar | None = None,
                 symbols: list[str] | None = None, entry_tf: str = "H1", loop_seconds: float = 20.0,
                 mode: str = "manual", auto_trading: bool = False, magic: int = 770077,
                 be_trigger_r: float = 1.0, be_lock_r: float = 0.1, sim_minutes_per_cycle: int = 0,
                 backup_dir: str = "backups", backup_every_hours: float = 24.0):
        self.broker, self.store, self.engine, self.model_info = broker, store, engine, model_info
        self.risk = risk or RiskEngine(RiskConfig(**(store.get("risk_config") or {})))
        self.telegram = telegram or TelegramBot(None, [])
        self.orderflow = orderflow or OrderFlowStore()
        self.calendar = calendar or EconomicCalendar(None)
        self.symbols, self.entry_tf, self.loop_seconds = symbols or ["EURUSD"], entry_tf, loop_seconds
        self.magic, self.be_trigger_r, self.be_lock_r = magic, be_trigger_r, be_lock_r
        self.sim_minutes = sim_minutes_per_cycle
        self.backup_dir, self.backup_every = Path(backup_dir), backup_every_hours * 3600
        st = store.get("runtime") or {}
        self.mode = st.get("mode", mode)
        self.auto_trading = st.get("auto_trading", auto_trading)
        self.paused = st.get("paused", False)
        self.halted = st.get("halted", False)
        self.halt_reason = st.get("halt_reason", "")
        self.running = False
        self.lock = threading.RLock()
        self.pending: dict[str, Signal] = {}
        self.decisions: dict[str, dict] = {}
        self.features_cache: dict[str, pd.DataFrame] = {}
        self.last_bar: dict[str, Any] = {}
        self.last_cycle: datetime | None = None
        self.cycle_errors = 0
        self.broker_was_connected = False
        self._last_equity_snap = 0.0
        self._last_backup = time.time()
        self._block_notified: dict[tuple, datetime] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.started_at = _now()
        self._wire_telegram()

    @property
    def simulation(self) -> bool:
        return bool(getattr(self.broker, "is_simulation", False))

    def clock(self) -> datetime:
        m = getattr(self.broker, "market", None)
        return m.now.to_pydatetime() if m is not None else _now()

    # ---------- persistance de l'état ----------
    def _persist(self) -> None:
        self.store.set("runtime", {"mode": self.mode, "auto_trading": self.auto_trading, "paused": self.paused,
                                   "halted": self.halted, "halt_reason": self.halt_reason})

    def _event(self, cat: str, msg: str, level: str = "info", data: Any = None, notify: bool = False) -> None:
        log.log(logging.WARNING if level in ("warning", "error") else logging.INFO, "[%s] %s", cat, msg)
        self.store.log(cat, msg, level, data)
        if notify:
            self.telegram.send(fmt.alert_msg(cat.upper(), msg))

    # ---------- démarrage / arrêt ----------
    def start(self, background: bool = True) -> None:
        self.running = True
        self._stop.clear()
        self.connect()
        self.telegram.start()
        self._event("system", f"Agent démarré (broker {self.broker.name}, mode {self.mode}, auto {'ON' if self.auto_trading else 'OFF'}, "
                              f"modèle {self.model_info.get('version') or 'aucun'})", notify=True)
        if background:
            self._thread = threading.Thread(target=self._loop, name="agent-loop", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self.running = False
        self._stop.set()
        self._event("system", "Agent arrêté", notify=True)

    def connect(self) -> bool:
        for attempt in range(3):
            try:
                if self.broker.connect():
                    if not self.broker_was_connected:
                        self._event("connexion", f"Connecté à {self.broker.name}", notify=attempt > 0 or self.cycle_errors > 0)
                    self.broker_was_connected = True
                    return True
            except Exception as e:  # noqa: BLE001
                self._event("connexion", f"Échec de connexion {self.broker.name} : {e}", "error")
            time.sleep(min(2 ** attempt, 10) if not self.simulation else 0)
        err = getattr(self.broker, "last_error", "")
        if self.broker_was_connected:
            self._event("connexion", f"Déconnexion {self.broker.name} — reconnexion en échec. {err}", "error", notify=True)
        self.broker_was_connected = False
        return False

    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self.cycle()
                self.cycle_errors = 0
            except Exception as e:  # noqa: BLE001 - la boucle ne doit jamais mourir
                self.cycle_errors += 1
                self._event("erreur", f"Cycle en erreur : {e}", "error", {"trace": traceback.format_exc()[-1500:]},
                            notify=self.cycle_errors in (1, 5, 20))
            # Rejeu : l'horloge simulée se fige tant qu'un signal attend la validation humaine
            if self.sim_minutes and hasattr(self.broker, "market") and not self.pending:
                self.broker.market.advance(self.sim_minutes)
            self._stop.wait(max(0.2, self.loop_seconds - (time.time() - t0)))

    # ---------- cycle ----------
    def cycle(self) -> None:
        with self.lock:
            if not self.broker.is_connected() and not self.connect():
                return
            acct = self.broker.account()
            self._risk_limits(acct)
            self.manage_positions(acct)
            self._expire_pending()
            self.calendar.refresh()
            for sym in self.symbols:
                try:
                    self.analyze_symbol(sym)
                except Exception as e:  # noqa: BLE001
                    self._event("analyse", f"{sym} : {e}", "warning")
            if time.time() - self._last_equity_snap > (60 if not self.simulation else 0):
                a = self.broker.account()
                self.store.snapshot_equity(a.balance, a.equity)
                self._last_equity_snap = time.time()
            if time.time() - self._last_backup > self.backup_every and "sqlite" in str(self.store.engine.url):
                try:
                    self.store.backup(self.backup_dir / f"agent-{_now():%Y%m%d-%H%M}.db")
                    self._last_backup = time.time()
                except Exception as e:  # noqa: BLE001
                    self._event("sauvegarde", f"Sauvegarde impossible : {e}", "warning")
            self.last_cycle = _now()
            self.store.set("heartbeat", {"ts": self.last_cycle.isoformat(), "sim_time": self.clock().isoformat()})

    def _risk_limits(self, acct) -> None:
        now = self.clock()
        rs = self.store.get("risk_state") or {}
        day, week = now.strftime("%Y-%m-%d"), "%d-W%02d" % now.isocalendar()[:2]
        if rs.get("day") != day:
            rs["day"], rs["day_start_equity"] = day, acct.equity
            if self.paused and self.halt_reason.startswith("Limite journalière"):
                self.paused, self.halted, self.halt_reason = False, False, ""
                self._event("risque", "Nouvelle journée : reprise des entrées après limite journalière", notify=True)
        if rs.get("week") != week:
            rs["week"], rs["week_start_equity"] = week, acct.equity
            if self.halted and self.halt_reason.startswith("Limite hebdomadaire"):
                self.halted, self.halt_reason = False, ""
        rs["peak_equity"] = max(rs.get("peak_equity", acct.equity), acct.equity)
        self.store.set("risk_state", rs)
        c = self.risk.cfg
        d = 100 * (rs["day_start_equity"] - acct.equity) / rs["day_start_equity"]
        w = 100 * (rs["week_start_equity"] - acct.equity) / rs["week_start_equity"]
        dd = 100 * (rs["peak_equity"] - acct.equity) / rs["peak_equity"]
        if not self.halted:
            if dd >= c.max_drawdown_pct:
                self._halt(f"Drawdown maximal atteint ({dd:.2f}%) : intervention manuelle requise")
            elif w >= c.max_weekly_loss_pct:
                self._halt(f"Limite hebdomadaire atteinte ({w:.2f}%)")
            elif d >= c.max_daily_loss_pct:
                self._halt(f"Limite journalière atteinte ({d:.2f}%)")

    def risk_state(self) -> RiskState:
        rs = self.store.get("risk_state") or {}
        eq = self.broker.account().equity
        return RiskState(rs.get("day_start_equity", eq), rs.get("week_start_equity", eq), rs.get("peak_equity", eq),
                         self.halted, self.halt_reason)

    def _halt(self, reason: str) -> None:
        self.halted, self.paused, self.halt_reason = True, True, reason
        self._persist()
        self._event("sécurité", f"Nouvelles entrées bloquées : {reason}", "warning", notify=True)

    # ---------- analyse ----------
    def analyze_symbol(self, sym: str) -> dict | None:
        htf = HTF_FOR[self.entry_tf]
        entry = self.broker.bars(sym, self.entry_tf, 700)
        if len(entry) < 300:
            return None
        if self.last_bar.get(sym) == entry.index[-1] and sym in self.decisions:
            return self.decisions[sym]  # bougie déjà analysée : rien de nouveau
        frames = {tf: self.broker.bars(sym, tf, 400) for tf in htf}
        f = build_features(entry, frames, self.entry_tf)
        self.features_cache[sym] = f
        F = FeatureView(f)
        i = F.n - 1
        bar_time = f.index[i]
        news = self.calendar.block_reason(sym, self.clock())
        dec = self.engine.decide(sym, F, i, self.entry_tf, orderflow=self.orderflow.get(sym), news_block=news,
                                 now=self.clock())
        d = dec.to_dict()
        d["bar_time"] = bar_time.isoformat()
        self.decisions[sym] = d
        if self.last_bar.get(sym) == bar_time:
            return d  # une seule décision par bougie clôturée
        self.last_bar[sym] = bar_time
        if dec.signal is not None:
            self.store.log("décision", f"{sym} : opportunité {dec.signal.strategy} {dec.signal.direction} "
                                       f"(score {dec.signal.score:.0f})", data={"explanation": dec.explanation})
            if not self.paused and not self.halted:
                self.process_signal(dec.signal)
            else:
                self.store.save_signal(dec.signal, "ignored", "pause", mode=self.mode)
        return d

    def process_signal(self, sig: Signal) -> dict:
        tick = self.broker.tick(sig.symbol)
        acct = self.broker.account()
        positions = self.broker.positions(self.magic)
        recent = list(self.pending.values()) + [self._sig_obj(r) for r in self.store.signals(30) if r["status"] in ("executed", "approved")]
        news = self.calendar.block_reason(sig.symbol, self.clock())
        rd = self.risk.evaluate(sig, acct, positions, tick, self.risk_state(), self.broker.value_per_price_unit,
                                recent=recent, spec=self.broker.symbol_spec(sig.symbol), news_block=news,
                                now=self.clock())
        sig.volume, sig.risk_amount, sig.risk_pct = rd.volume, rd.risk_amount, rd.risk_pct
        if not rd.approved:
            self.store.save_signal(sig, "blocked", "risk_engine", rd.checks, self.mode)
            self._event("risque", f"{sig.symbol} {sig.direction} refusé : " + " | ".join(rd.rejections), "warning")
            key = (sig.symbol, sig.direction, sig.strategy)
            last = self._block_notified.get(key)
            if sig.score >= 70 and (last is None or (self.clock() - last) > timedelta(hours=6)):
                self._block_notified[key] = self.clock()
                self.telegram.send(fmt.rejected_msg(sig.symbol, sig.direction, rd.rejections, self.simulation))
            return {"status": "blocked", "reasons": rd.rejections}
        if self.mode == "auto" and self.auto_trading:
            self.store.save_signal(sig, "approved", "auto", rd.checks, self.mode)
            self.telegram.send(fmt.signal_msg(sig, acct.currency, "auto", self.simulation))
            return self.execute(sig)
        sig.status = "pending"
        self.pending[sig.id] = sig
        self.store.save_signal(sig, "pending", "agent", rd.checks, self.mode)
        self.telegram.send(fmt.signal_msg(sig, acct.currency, "manual", self.simulation),
                           buttons=[[("✅ Valider", f"approve:{sig.id}"), ("❌ Refuser", f"reject:{sig.id}")]])
        self._event("signal", f"{sig.symbol} {sig.direction} {sig.strategy} en attente de validation")
        return {"status": "pending", "id": sig.id}

    @staticmethod
    def _sig_obj(r: dict) -> Signal:
        s = Signal(r["symbol"], r["timeframe"], r["direction"], r["strategy"], r["entry"], r["sl"], r["tp"],
                   r["score"] or 0, r["probability"] or 0, r["rr"] or 0, r["ev_r"] or 0, r["regime"] or "",
                   r["reasons"] or [], r["explanation"] or "", id=r["id"],
                   created_at=datetime.fromisoformat(r["created_at"]))
        s.status = r["status"]
        return s

    # ---------- validation humaine ----------
    def approve(self, sid: str, by: str = "user") -> dict:
        with self.lock:
            sig = self.pending.pop(sid, None)
            if sig is None:
                return {"ok": False, "message": "Signal introuvable ou déjà traité"}
            if sig.expired(self.clock()):
                self.store.update_signal(sid, status="expired")
                return {"ok": False, "message": "Signal expiré"}
            # Re-contrôle complet du risque au prix courant
            tick = self.broker.tick(sig.symbol)
            rd = self.risk.evaluate(sig, self.broker.account(), self.broker.positions(self.magic), tick,
                                    self.risk_state(), self.broker.value_per_price_unit,
                                    spec=self.broker.symbol_spec(sig.symbol), now=self.clock(),
                                    news_block=self.calendar.block_reason(sig.symbol, self.clock()))
            if not rd.approved:
                self.store.save_signal(sig, "blocked", f"risk_engine (validation {by})", rd.checks, self.mode)
                return {"ok": False, "message": "Refusé par le Risk Engine : " + " | ".join(rd.rejections)}
            sig.volume, sig.risk_amount, sig.risk_pct = rd.volume, rd.risk_amount, rd.risk_pct
            self.store.save_signal(sig, "approved", by, rd.checks, self.mode)
            self._event("signal", f"{sig.symbol} {sig.direction} validé par {by}")
            res = self.execute(sig)
            return {"ok": res.get("status") == "executed", **res}

    def reject(self, sid: str, by: str = "user") -> dict:
        with self.lock:
            sig = self.pending.pop(sid, None)
            if sig is None:
                return {"ok": False, "message": "Signal introuvable ou déjà traité"}
            self.store.update_signal(sid, status="rejected", decided_by=by)
            self._event("signal", f"{sig.symbol} {sig.direction} refusé par {by}")
            return {"ok": True, "message": "Signal refusé"}

    def _expire_pending(self) -> None:
        now = self.clock()
        for sid, sig in list(self.pending.items()):
            if sig.expired(now):
                self.pending.pop(sid, None)
                self.store.update_signal(sid, status="expired")
                self._event("signal", f"{sig.symbol} {sig.direction} expiré sans validation")

    # ---------- exécution ----------
    def execute(self, sig: Signal) -> dict:
        req = OrderRequest(sig.symbol, sig.direction, sig.volume, sig.sl, sig.tp, magic=self.magic,
                           comment=f"fxa {sig.strategy[:12]} {sig.id[:6]}", signal_id=sig.id)
        res = self.broker.place_market(req)
        if not res.ok or not res.verified:
            self.store.update_signal(sig.id, status="failed")
            self._event("exécution", f"{sig.symbol} {sig.direction} échec : {res.message} (code {res.retcode})", "error")
            self.telegram.send(fmt.error_msg(sig.symbol, f"{res.message} (code {res.retcode})"))
            return {"status": "failed", "message": res.message}
        spec = self.broker.symbol_spec(sig.symbol)
        vpu = self.broker.value_per_price_unit(spec, res.price)
        risk_amt = abs(res.price - res.sl) * res.volume * vpu
        acct = self.broker.account()
        self.store.open_trade(ticket=res.ticket, signal_id=sig.id, symbol=sig.symbol, direction=sig.direction,
                              strategy=sig.strategy, timeframe=sig.timeframe, regime=sig.regime, volume=res.volume,
                              entry_price=res.price, requested_price=res.requested_price, slippage_pips=res.slippage_pips,
                              sl_initial=res.sl, tp_initial=res.tp, sl=res.sl, tp=res.tp, open_time=self.clock(),
                              risk_amount=risk_amt, risk_pct=100 * risk_amt / acct.equity if acct.equity else 0,
                              mode=self.mode, broker=self.broker.name, analysis={"explanation": sig.explanation,
                              "reasons": sig.reasons, "score": sig.score, "probability": sig.probability},
                              market_conditions=sig.analysis, trail=sig.trail, events=[])
        self.store.update_signal(sig.id, status="executed", trade_ticket=res.ticket)
        self._event("exécution", f"{sig.symbol} {sig.direction} {res.volume:.2f} lot exécuté @ {res.price} "
                                 f"(ticket {res.ticket}, vérifié)")
        self.telegram.send(fmt.execution_msg(sig.symbol, sig.direction, res, self.simulation))
        chk = self.risk.check_slippage(spec, res.slippage_pips)
        if not chk.ok:
            self._event("sécurité", f"Slippage excessif sur {sig.symbol} : {chk.detail}", "warning", notify=True)
        return {"status": "executed", "ticket": res.ticket, "price": res.price, "volume": res.volume}

    # ---------- suivi des positions ----------
    def manage_positions(self, acct=None) -> None:
        positions = {p.ticket: p for p in self.broker.positions(self.magic)}
        for t in self.store.trades(status="open"):
            tk = t["ticket"]
            if tk not in positions:
                self._record_close(t)
                continue
            p = positions[tk]
            risk0 = abs(t["entry_price"] - t["sl_initial"])
            if risk0 <= 0:
                continue
            tick = self.broker.tick(p.symbol)
            px = tick.bid if p.direction == "BUY" else tick.ask
            prog = ((px - p.entry_price) if p.direction == "BUY" else (p.entry_price - px)) / risk0
            new_sl, what = None, ""
            if not t.get("be_done") and self.be_trigger_r > 0 and prog >= self.be_trigger_r and not t.get("trail"):
                lock = self.be_lock_r * risk0
                new_sl = p.entry_price + lock if p.direction == "BUY" else p.entry_price - lock
                what = "Break-even activé"
            trail = t.get("trail") or 0
            if trail > 0:
                cand = px - trail if p.direction == "BUY" else px + trail
                better = (cand > p.sl + 0.1 * risk0) if p.direction == "BUY" else (cand < p.sl - 0.1 * risk0)
                if better:
                    new_sl, what = cand, "Trailing stop"
            if new_sl is not None:
                new_sl = round(new_sl, self.broker.symbol_spec(p.symbol).digits)
                res = self.broker.modify(tk, new_sl, None)
                if res.ok:
                    self.store.update_trade(tk, sl=res.sl, be_done=1 if what.startswith("Break") else t.get("be_done", 0))
                    self.store.add_trade_event(tk, f"{what} : SL {res.sl}")
                    self._event("gestion", f"{p.symbol} #{tk} {what} -> SL {res.sl}")
                    self.telegram.send(fmt.modify_msg(p.symbol, tk, what, res.sl, res.tp, self.simulation))
                else:
                    self._event("gestion", f"{p.symbol} #{tk} modification refusée : {res.message}", "warning")

    def _record_close(self, t: dict) -> None:
        deal = self.broker.closed_deal(t["ticket"])
        if deal is None:
            return  # l'historique n'est pas encore disponible : nouvel essai au prochain cycle
        pnl = deal.profit + deal.commission + deal.swap
        risk_amt = t.get("risk_amount") or 0
        dur = deal.close_time - deal.open_time
        self.store.update_trade(t["ticket"], status="closed", exit_price=deal.exit_price, close_time=deal.close_time,
                                pnl=round(pnl, 2), pnl_r=round(pnl / risk_amt, 3) if risk_amt else None,
                                commission=deal.commission, swap=deal.swap, close_reason=deal.reason)
        t2 = {**t, "exit_price": deal.exit_price, "pnl": pnl, "pnl_r": pnl / risk_amt if risk_amt else 0,
              "close_reason": deal.reason, "duration": str(dur).split(".")[0]}
        self._event("clôture", f"{t['symbol']} #{t['ticket']} clôturée ({deal.reason}) : {pnl:+.2f}")
        self.telegram.send(fmt.close_msg(t2, self.broker.account().currency, self.simulation))

    # ---------- commandes ----------
    def set_mode(self, mode: str, by: str = "user") -> str:
        if mode not in ("manual", "auto"):
            return "Mode inconnu (manual|auto)"
        self.mode = mode
        self._persist()
        self._event("contrôle", f"Mode {mode.upper()} activé par {by}", notify=True)
        return f"Mode {mode.upper()} actif"

    def set_auto(self, on: bool, by: str = "user") -> str:
        if on and self.halted:
            return f"Impossible : arrêt de sécurité actif ({self.halt_reason})"
        self.auto_trading = on
        self._persist()
        self._event("contrôle", f"Exécution automatique {'ON' if on else 'OFF'} ({by})", notify=True)
        return f"Exécution automatique {'ON' if on else 'OFF'}"

    def pause(self, by: str = "user") -> str:
        self.paused = True
        self._persist()
        self._event("contrôle", f"Nouvelles entrées suspendues ({by})", notify=True)
        return "Nouvelles entrées suspendues (les positions restent gérées)"

    def resume(self, by: str = "user") -> str:
        if self.halted and self.halt_reason.startswith("Drawdown"):
            return f"Reprise refusée : {self.halt_reason}. Utilisez la réinitialisation de sécurité dans l'application."
        self.paused, self.halted, self.halt_reason = False, False, ""
        self._persist()
        self._event("contrôle", f"Reprise des entrées ({by})", notify=True)
        return "Fonctionnement repris"

    def reset_safety(self, by: str = "user") -> str:
        rs = self.store.get("risk_state") or {}
        eq = self.broker.account().equity
        rs.update(peak_equity=eq, day_start_equity=eq, week_start_equity=eq)
        self.store.set("risk_state", rs)
        self.halted, self.halt_reason = False, ""
        self._persist()
        self._event("sécurité", f"Réinitialisation des compteurs de risque par {by}", "warning", notify=True)
        return "Compteurs de risque réinitialisés"

    def emergency_stop(self, close_all: bool = True, by: str = "user") -> str:
        with self.lock:
            self.auto_trading = False
            self.paused = True
            self.halted, self.halt_reason = True, f"Arrêt d'urgence déclenché par {by}"
            for sid in list(self.pending):
                self.pending.pop(sid)
                self.store.update_signal(sid, status="cancelled")
            closed = []
            if close_all:
                for p in self.broker.positions(self.magic):
                    r = self.broker.close(p.ticket)
                    closed.append(f"#{p.ticket} {'OK' if r.ok else r.message}")
                self.manage_positions()
            self._persist()
        self._event("sécurité", f"ARRÊT D'URGENCE ({by}) — positions clôturées : {', '.join(closed) or 'aucune'}",
                    "error", notify=True)
        return "Arrêt d'urgence exécuté : exécution auto OFF, entrées bloquées" + (f", {len(closed)} position(s) clôturée(s)" if closed else "")

    def close_position(self, ticket: int, by: str = "user") -> str:
        r = self.broker.close(int(ticket))
        if r.ok:
            self.store.add_trade_event(int(ticket), f"Clôture manuelle par {by}")
            self.manage_positions()
            return f"Position #{ticket} clôturée"
        return f"Échec : {r.message}"

    # ---------- état ----------
    def performance(self) -> dict:
        closed = [t for t in self.store.trades(status="closed", limit=5000) if t.get("pnl") is not None]
        df = pd.DataFrame(closed)
        if len(df):
            df["close_time"] = pd.to_datetime(df["close_time"])
        acct = None
        try:
            acct = self.broker.account()
        except Exception:  # noqa: BLE001
            pass
        initial = (acct.balance - df["pnl"].sum()) if (acct and len(df)) else (acct.balance if acct else None)
        return compute(df, initial)

    def status(self) -> dict:
        acct = None
        try:
            a = self.broker.account()
            acct = {"balance": a.balance, "equity": a.equity, "margin_free": a.margin_free, "currency": a.currency,
                    "login": a.login, "server": a.server, "is_demo": a.is_demo, "leverage": a.leverage}
        except Exception as e:  # noqa: BLE001
            acct = {"error": str(e)}
        rs = self.store.get("risk_state") or {}
        eq = (acct or {}).get("equity")
        pnl_today = (eq - rs["day_start_equity"]) if eq is not None and rs.get("day_start_equity") else 0.0
        try:
            n_open = len(self.broker.positions(self.magic))
        except Exception:  # noqa: BLE001
            n_open = 0
        return {"running": self.running, "paused": self.paused, "halted": self.halted, "halt_reason": self.halt_reason,
                "mode": self.mode, "auto_trading": self.auto_trading, "simulation": self.simulation,
                "broker": {"name": self.broker.name, "connected": self.broker.is_connected(),
                           "error": getattr(self.broker, "last_error", "")},
                "quantower": {"connected": self.orderflow.connected,
                              "symbols": list(self.orderflow.all().keys())},
                "telegram": {"enabled": self.telegram.enabled, "connected": self.telegram.connected,
                             "sent": self.telegram.sent, "error": self.telegram.last_error},
                "calendar": {"events": len(self.calendar.events), "error": self.calendar.error},
                "model": self.model_info, "account": acct, "pnl_today": round(pnl_today, 2), "open_positions": n_open,
                "pending_signals": len(self.pending), "symbols": self.symbols, "entry_tf": self.entry_tf,
                "last_cycle": self.last_cycle.isoformat() if self.last_cycle else None,
                "clock": self.clock().isoformat(), "started_at": self.started_at.isoformat(),
                "cycle_errors": self.cycle_errors}

    # ---------- Telegram ----------
    def _wire_telegram(self) -> None:
        tg = self.telegram
        cur = lambda: (self.broker.account().currency if self.broker.is_connected() else "")  # noqa: E731
        tg.handlers = {
            "/start": lambda a: fmt.status_msg(self.status()),
            "/help": lambda a: "Agent IA Forex — " + "/status /positions /performance /signals /pause /resume /mode /auto /stop /close",
            "/status": lambda a: fmt.status_msg(self.status()),
            "/positions": lambda a: fmt.positions_msg(self.broker.positions(self.magic), cur()),
            "/performance": lambda a: fmt.performance_msg(self.performance(), cur()),
            "/signals": lambda a: fmt.signals_msg(self.store.signals(10)),
            "/pause": lambda a: self.pause("telegram"),
            "/resume": lambda a: self.resume("telegram"),
            "/mode": lambda a: self.set_mode(a[0].lower(), "telegram") if a else f"Mode actif : {self.mode.upper()} · auto {'ON' if self.auto_trading else 'OFF'}",
            "/auto": lambda a: self.set_auto(a[0].lower() == "on", "telegram") if a else f"Exécution auto : {'ON' if self.auto_trading else 'OFF'}",
            "/stop": lambda a: self.emergency_stop(True, "telegram"),
            "/close": lambda a: self.close_position(int(a[0]), "telegram") if a else "Usage : /close <ticket>",
        }

        def cb(action: str, sid: str) -> str:
            r = self.approve(sid, "telegram") if action == "approve" else self.reject(sid, "telegram")
            return r.get("message") or ("Ordre exécuté" if r.get("ok") else "Échec")
        tg.callback_handler = cb
        tg.on_security = lambda m: self._event("sécurité", m, "warning")
