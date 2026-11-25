"""Backtester événementiel multi-stratégies / multi-symboles.

* Signal à la clôture de la barre M15 i ; exécution à l'ouverture suivante (spread ajouté à l'achat).
* Chemin du prix rejoué sur les bougies M1 réelles : SL, TP, break-even, durée maximale.
* Si SL et TP sont touchés dans la même minute : SL (hypothèse conservatrice).
* Coûts : spread de l'instrument + commission par lot (aller-retour).
* Le même Risk Engine que le trading réel dimensionne et filtre les ordres.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable

import numpy as np
import pandas as pd

from ..analysis.features import TF_MINUTES
from ..brain.decision import DecisionEngine
from ..models import AccountInfo, Candidate, Position, Tick
from ..risk.engine import RiskEngine, RiskState
from ..strategies.base import FeatureView, Strategy
from ..symbols import SymbolSpec, get_spec, value_per_price_unit


FALLBACK_RATES = {"USDJPY": 150.0, "USDCAD": 1.36, "USDCHF": 0.88, "GBPUSD": 1.27, "EURUSD": 1.08}


@dataclass
class ExecParams:
    be_trigger_r: float = 1.0
    be_lock_r: float = 0.1
    max_hold_min: int = 2880
    spread_mult: float = 1.0


class PricePath:
    """Bougies M1 d'un symbole pour rejouer l'évolution d'une position."""

    def __init__(self, m1: pd.DataFrame):
        self.t = m1.index.as_unit("ns").asi8  # entiers ns UTC
        self.o, self.h, self.l, self.c = (m1[k].to_numpy(float) for k in ("open", "high", "low", "close"))

    def start_index(self, ts: pd.Timestamp) -> int:
        ts = pd.Timestamp(ts)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
        return int(np.searchsorted(self.t, ts.as_unit("ns").value, side="left"))

    def simulate(self, start: int, direction: str, sl: float, tp: float, spread: float,
                 ex: ExecParams, trail: float = 0.0) -> dict | None:
        if start >= len(self.t):
            return None
        buy = direction == "BUY"
        fill = self.o[start] + spread if buy else self.o[start]
        risk = (fill - sl) if buy else (sl - fill)
        if risk <= 0:
            return None
        end = min(len(self.t), start + ex.max_hold_min)
        t0 = self.t[start]
        # limite de durée en temps réel (les week-ends créent des trous dans les M1)
        limit_t = t0 + ex.max_hold_min * 60_000_000_000
        end = min(end, int(np.searchsorted(self.t, limit_t, side="right")))
        if end <= start:
            end = start + 1
        h, l = self.h[start:end], self.l[start:end]
        if buy:
            hit_sl, hit_tp = l <= sl, h >= tp
            fav = h - fill
        else:
            hit_sl, hit_tp = h + spread >= sl, l + spread <= tp
            fav = fill - (l + spread)
        first = lambda m: int(np.argmax(m)) if m.any() else None  # noqa: E731
        if trail > 0:
            # Stop suiveur : plus haut atteint (minute précédente) - trail, jamais en dessous du SL initial
            if buy:
                ref = np.maximum.accumulate(h)
                stop = np.maximum(sl, np.concatenate([[sl], ref[:-1] - trail]))
                hit = l <= stop
            else:
                ref = np.minimum.accumulate(l + spread)
                stop = np.minimum(sl, np.concatenate([[sl], ref[:-1] + trail]))
                hit = h + spread >= stop
            i_s, i_t = first(hit), first(hit_tp)
            if i_s is not None and (i_t is None or i_s <= i_t):
                exit_i, exit_px = i_s, float(stop[i_s])
                reason = "SL" if abs(exit_px - sl) < 1e-12 else "TRAIL"
            elif i_t is not None:
                exit_i, exit_px, reason = i_t, tp, "TP"
            else:
                exit_i = end - 1 - start
                c = self.c[start + exit_i]
                exit_px, reason = (c if buy else c + spread), "TIME"
            pnl_price = (exit_px - fill) if buy else (fill - exit_px)
            return {"fill": fill, "exit": exit_px, "risk": risk, "r": pnl_price / risk, "reason": reason,
                    "entry_time": pd.Timestamp(int(self.t[start]), unit="ns", tz="UTC"),
                    "exit_time": pd.Timestamp(int(self.t[start + exit_i]), unit="ns", tz="UTC")}
        be_hit = fav >= ex.be_trigger_r * risk if ex.be_trigger_r > 0 else np.zeros_like(hit_sl)
        i_sl, i_tp, i_be = first(hit_sl), first(hit_tp), first(be_hit)
        exit_i, exit_px, reason = None, None, None
        cands = [(i, r) for i, r in ((i_sl, "SL"), (i_tp, "TP")) if i is not None]
        if i_be is not None and (not cands or i_be < min(i for i, _ in cands)):
            # Break-even activé : nouveau stop à l'entrée + lock, appliqué à partir de la minute suivante
            new_sl = fill + ex.be_lock_r * risk if buy else fill - ex.be_lock_r * risk
            if i_tp is not None and i_tp == i_be:
                exit_i, exit_px, reason = i_tp, tp, "TP"
            else:
                h2, l2 = h[i_be + 1:], l[i_be + 1:]
                s2 = (l2 <= new_sl) if buy else (h2 + spread >= new_sl)
                t2 = (h2 >= tp) if buy else (l2 + spread <= tp)
                j_sl, j_tp = first(s2), first(t2)
                opts = [(j, r) for j, r in ((j_sl, "BE"), (j_tp, "TP")) if j is not None]
                if opts:
                    j, reason = min(opts, key=lambda x: (x[0], 0 if x[1] == "BE" else 1))
                    exit_i = i_be + 1 + j
                    exit_px = new_sl if reason == "BE" else tp
        elif cands:
            exit_i, reason = min(cands, key=lambda x: (x[0], 0 if x[1] == "SL" else 1))
            exit_px = sl if reason == "SL" else tp
        if exit_i is None:
            exit_i = end - 1 - start if end - 1 >= start else 0
            c = self.c[start + exit_i]
            exit_px, reason = (c if buy else c + spread), "TIME"
        pnl_price = (exit_px - fill) if buy else (fill - exit_px)
        return {"fill": fill, "exit": exit_px, "risk": risk, "r": pnl_price / risk, "reason": reason,
                "entry_time": pd.Timestamp(int(self.t[start]), unit="ns", tz="UTC"),
                "exit_time": pd.Timestamp(int(self.t[start + exit_i]), unit="ns", tz="UTC")}


class RateBook:
    """Taux de conversion historiques (ex. USDJPY) pour valoriser les paires croisées."""

    def __init__(self, closes: dict[str, pd.Series]):
        self.closes = {k: v.sort_index() for k, v in closes.items()}

    def rates_at(self, ts: pd.Timestamp) -> dict[str, float]:
        out = {}
        for k, s in self.closes.items():
            i = s.index.searchsorted(ts, side="right") - 1
            if i >= 0:
                out[k] = float(s.iloc[i])
        return out


def candidate_stream(F: FeatureView, strategies: Iterable[Strategy], start: int = 250,
                     stop: int | None = None) -> dict[int, list[Candidate]]:
    out: dict[int, list[Candidate]] = {}
    stop = stop or F.n
    strategies = [s for s in strategies if s.enabled]
    for i in range(start, stop):
        lst = []
        for st in strategies:
            c = st.evaluate(F, i)
            if c is not None:
                lst.append(c)
        if lst:
            out[i] = lst
    return out


def evaluate_raw(symbol: str, F: FeatureView, path: PricePath, strategies: list[Strategy], ex: ExecParams,
                 start: int = 250, stop: int | None = None, entry_tf: str = "M15",
                 cands: dict[int, list[Candidate]] | None = None) -> pd.DataFrame:
    """Chaque stratégie isolément, une position à la fois : base de la matrice stratégie x régime."""
    spec = get_spec(symbol)
    spread = spec.typical_spread_pips * spec.pip_size * ex.spread_mult
    cands = cands if cands is not None else candidate_stream(F, strategies, start, stop)
    busy_until: dict[str, pd.Timestamp] = {}
    rows = []
    tfm = pd.Timedelta(minutes=TF_MINUTES[entry_tf])
    for i in sorted(cands):
        bar_close = pd.Timestamp(F.index[i]) + tfm
        for c in cands[i]:
            if c.strategy in busy_until and bar_close < busy_until[c.strategy]:
                continue
            res = path.simulate(path.start_index(bar_close), c.direction, c.sl, c.tp, spread, ex, c.trail)
            if res is None:
                continue
            busy_until[c.strategy] = res["exit_time"]
            ex_time = res["exit_time"]
            # commission en R : commission / (risque par lot)
            vpu = value_per_price_unit(spec, res["fill"], "USD", {**FALLBACK_RATES, spec.name: res["fill"]})
            comm_r = spec.commission_per_lot / (res["risk"] * vpu)
            rows.append({"symbol": symbol, "strategy": c.strategy, "direction": c.direction,
                         "signal_time": F.index[i], "entry_time": res["entry_time"], "close_time": res["exit_time"],
                         "regime": F["regime"][i], "vol_state": F["vol_state"][i], "session": F["session"][i],
                         "score": c.base_score, "rr": c.rr, "r": res["r"] - comm_r, "reason": res["reason"],
                         "bar": i})
    df = pd.DataFrame(rows)
    if len(df):
        df["pnl_r"] = df["r"]
        df["pnl"] = df["r"]  # en R pour les métriques brutes
        df["win"] = df["r"] > 0
    return df


def perf_matrix(raw: pd.DataFrame) -> dict:
    """{strategy: {by_regime: {regime: {n, wins, sum_r, exp_r, pf}}, by_symbol: {...}}}"""
    out: dict = {}
    if raw is None or not len(raw):
        return out

    def cell(g: pd.DataFrame) -> dict:
        r = g["r"]
        gl = -r[r <= 0].sum()
        return {"n": int(len(g)), "wins": int((r > 0).sum()), "sum_r": round(float(r.sum()), 3),
                "exp_r": round(float(r.mean()), 4), "pf": round(float(r[r > 0].sum() / gl), 3) if gl > 0 else None}

    for st, g in raw.groupby("strategy"):
        d = {"by_regime": {str(k): cell(x) for k, x in g.groupby("regime")},
             "by_symbol": {str(s): {str(k): cell(x) for k, x in gs.groupby("regime")} for s, gs in g.groupby("symbol")},
             "by_symbol_all": {str(s): cell(gs) for s, gs in g.groupby("symbol")},
             "all": cell(g)}
        out[str(st)] = d
    return out


def run_portfolio(data: dict[str, tuple[FeatureView, PricePath]], engine: DecisionEngine, risk: RiskEngine,
                  ex: ExecParams, capital: float = 10_000.0, start_time: pd.Timestamp | None = None,
                  end_time: pd.Timestamp | None = None, ratebook: RateBook | None = None,
                  entry_tf: str = "M15", cands_cache: dict[str, dict[int, list[Candidate]]] | None = None) -> pd.DataFrame:
    """Agent complet (cerveau + risk engine) sur plusieurs symboles, dans l'ordre chronologique."""
    tfm = pd.Timedelta(minutes=TF_MINUTES[entry_tf])
    events = []
    for sym, (F, _) in data.items():
        idx = F.index
        lo = 250 if start_time is None else max(250, int(idx.searchsorted(start_time)))
        hi = F.n if end_time is None else int(idx.searchsorted(end_time))
        cc = cands_cache.get(sym) if cands_cache else None
        if cc is None:
            cc = candidate_stream(F, engine.strategies, lo, hi)
        for i, cl in cc.items():
            if lo <= i < hi:
                events.append((pd.Timestamp(idx[i]) + tfm, sym, i, cl))
    events.sort(key=lambda e: e[0])
    equity = capital
    state = RiskState(capital, capital, capital)
    open_trades: list[dict] = []
    closed: list[dict] = []
    cur_day = cur_week = None
    for ts, sym, i, cl in events:
        # Clôtures intervenues avant ce signal
        for tr in sorted([t for t in open_trades if t["close_time"] <= ts], key=lambda t: t["close_time"]):
            equity += tr["pnl"]
            open_trades.remove(tr); closed.append(tr)
            state.peak_equity = max(state.peak_equity, equity)
        day, week = ts.date(), ts.isocalendar()[:2]
        if day != cur_day:
            cur_day, state.day_start_equity = day, equity
        if week != cur_week:
            cur_week, state.week_start_equity = week, equity
        if any(t["symbol"] == sym for t in open_trades):
            continue
        F, path = data[sym]
        dec = engine.decide(sym, F, i, entry_tf, now=ts.to_pydatetime(), precomputed=cl)
        sig = dec.signal
        if sig is None:
            continue
        spec = get_spec(sym)
        spread = spec.typical_spread_pips * spec.pip_size * ex.spread_mult
        k = path.start_index(ts)
        if k >= len(path.t):
            continue
        bid = path.o[k]
        tick = Tick(sym, bid, bid + spread, ts.to_pydatetime())
        rates = ratebook.rates_at(ts) if ratebook else {}

        def vfn(sp: SymbolSpec, px: float, _r=rates) -> float:
            return value_per_price_unit(sp, px, "USD", _r)

        positions = [Position(0, t["symbol"], t["direction"], t["volume"], t["entry_price"],
                              t["sl"] if not t.get("be_done") else t["entry_price"], t["tp"], t["entry_time"])
                     for t in open_trades if t["entry_time"] <= ts]
        acct = AccountInfo(balance=equity, equity=equity, margin_free=equity)
        rd = risk.evaluate(sig, acct, positions, tick, state, vfn, now=ts.to_pydatetime())
        if not rd.approved:
            continue
        res = path.simulate(k, sig.direction, sig.sl, sig.tp, spread, ex, sig.trail)
        if res is None:
            continue
        vpu = vfn(spec, res["fill"])
        gross = (res["exit"] - res["fill"] if sig.direction == "BUY" else res["fill"] - res["exit"]) * rd.volume * vpu
        commission = spec.commission_per_lot * rd.volume
        risk_amt = res["risk"] * rd.volume * vpu
        pnl = gross - commission
        open_trades.append({"symbol": sym, "strategy": sig.strategy, "direction": sig.direction, "timeframe": entry_tf,
                            "regime": sig.regime, "score": sig.score, "probability": sig.probability,
                            "entry_time": res["entry_time"], "close_time": res["exit_time"],
                            "entry_price": res["fill"], "exit_price": res["exit"], "sl": sig.sl, "tp": sig.tp,
                            "volume": rd.volume, "risk_amount": risk_amt, "pnl": pnl,
                            "pnl_r": pnl / risk_amt if risk_amt else 0.0, "close_reason": res["reason"],
                            "commission": commission})
    for tr in sorted(open_trades, key=lambda t: t["close_time"]):
        closed.append(tr)
    return pd.DataFrame(closed)
