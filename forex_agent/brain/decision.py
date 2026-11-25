"""Cerveau IA : lit le contexte, interroge toutes les stratégies et sélectionne la meilleure opportunité.

Principes :
  * Aucune dépendance à une stratégie unique : chaque stratégie propose, le cerveau arbitre.
  * Le poids d'une stratégie dépend de sa performance mesurée hors ligne dans le régime courant
    (matrice stratégie x régime issue du walk-forward). Une case négative avec n >= 30 désactive la stratégie.
  * L'espérance (en R) est estimée avec rétrécissement vers 0 (pas de trade sans preuve d'avantage).
  * Un méta-modèle ML validé hors échantillon peut affiner la probabilité de gain.
  * Signaux contradictoires de force comparable => pas de trade.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from ..analysis.orderflow import OrderFlowSnapshot
from ..analysis.regime import REGIME_LABELS, VOL_LABELS
from ..models import Candidate, Signal
from ..strategies.base import FeatureView, Strategy, ok
from ..symbols import get_spec


@dataclass
class BrainConfig:
    min_score: float = 62.0
    min_ev_r: float = 0.05
    min_trades_cell: int = 30
    shrink_k: float = 20.0
    cost_r: float = 0.0  # les R appris sont déjà nets de spread + commission
    conflict_margin: float = 10.0
    allow_unproven: bool = False  # True = autorise des cases sans historique (démo uniquement)
    signal_valid_minutes: int = 30


@dataclass
class Decision:
    symbol: str
    time: datetime
    regime: str
    vol_state: str
    session: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    signal: Signal | None = None
    explanation: str = ""
    blocked: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "time": self.time.isoformat(), "regime": self.regime,
                "regime_label": REGIME_LABELS.get(self.regime, self.regime), "vol_state": self.vol_state,
                "session": self.session, "candidates": self.candidates,
                "signal": self.signal.to_dict() if self.signal else None, "explanation": self.explanation,
                "blocked": self.blocked}


class DecisionEngine:
    def __init__(self, strategies: list[Strategy], perf_matrix: dict | None = None, meta_model: Any = None,
                 config: BrainConfig | None = None):
        self.strategies = strategies
        self.perf = perf_matrix or {}
        self.meta = meta_model
        self.cfg = config or BrainConfig()

    # ---------- statistiques apprises ----------
    def cell(self, strategy: str, regime: str, symbol: str | None = None) -> dict[str, float]:
        """Statistique la plus spécifique disposant d'assez de trades : (paire, régime) > paire > régime."""
        s = self.perf.get(strategy, {})
        m = self.cfg.min_trades_cell
        if symbol:
            c = s.get("by_symbol", {}).get(symbol, {}).get(regime)
            if c and c.get("n", 0) >= m:
                return c
            c = s.get("by_symbol_all", {}).get(symbol)
            if c and c.get("n", 0) >= m:
                return c
        return s.get("by_regime", {}).get(regime, {"n": 0, "wins": 0, "sum_r": 0.0})

    def estimate(self, cand: Candidate, regime: str, symbol: str, features: dict[str, Any] | None) -> tuple[float, float, str]:
        c = self.cell(cand.strategy, regime, symbol)
        n, wins, sum_r = c.get("n", 0), c.get("wins", 0), c.get("sum_r", 0.0)
        k = self.cfg.shrink_k
        p0 = 1.0 / (1.0 + max(cand.rr, 0.1))
        p = (wins + p0 * k) / (n + k)
        ev = sum_r / (n + k) - self.cfg.cost_r
        source = f"historique {n} trades" if n else "aucun historique"
        if self.meta is not None and features is not None:
            try:
                pm = float(self.meta.predict_proba_one(cand, regime, features))
                ev_meta = pm * cand.rr - (1 - pm) - self.cfg.cost_r
                p, ev = 0.5 * p + 0.5 * pm, 0.5 * ev + 0.5 * ev_meta
                source += " + méta-modèle ML"
            except Exception:  # noqa: BLE001 - le méta-modèle est optionnel
                pass
        if n == 0 and self.cfg.allow_unproven:
            ev = max(ev, p * cand.rr - (1 - p))
        return p, ev, source

    def disabled_in(self, strategy: str, regime: str, symbol: str) -> bool:
        c = self.cell(strategy, regime, symbol)
        return c.get("n", 0) >= self.cfg.min_trades_cell and c.get("sum_r", 0.0) <= 0

    # ---------- décision ----------
    def decide(self, symbol: str, F: FeatureView, i: int, timeframe: str = "M15",
               orderflow: OrderFlowSnapshot | None = None, news_block: str | None = None,
               now: datetime | None = None, precomputed: list[Candidate] | None = None) -> Decision:
        regime, vol, session = str(F["regime"][i]), str(F["vol_state"][i]), str(F["session"][i])
        ts = pd.Timestamp(F.index[i]).to_pydatetime()
        dec = Decision(symbol, ts, regime, vol, session)
        raw: list[tuple[Candidate, float, float, float, str]] = []
        feats = None
        by_name = {c.strategy: c for c in (precomputed or [])}
        for st in self.strategies:
            if not st.enabled:
                continue
            if precomputed is not None:
                cand = by_name.get(st.name)
                if cand is not None:  # copie : le cerveau ajoute des raisons sans modifier le cache
                    cand = Candidate(cand.strategy, cand.direction, cand.entry, cand.sl, cand.tp, cand.base_score,
                                     list(cand.reasons), cand.trail)
            else:
                try:
                    cand = st.evaluate(F, i)
                except Exception as e:  # noqa: BLE001 - une stratégie défaillante ne bloque pas les autres
                    dec.blocked.append(f"{st.name}: erreur {e}")
                    continue
            if cand is None:
                continue
            if self.disabled_in(st.name, regime, symbol):
                dec.blocked.append(f"{st.label}: désactivée en {REGIME_LABELS.get(regime, regime)} (espérance négative)")
                continue
            if feats is None:
                feats = F.row(i)
            p, ev, src = self.estimate(cand, regime, symbol, feats)
            fit = 1.0 if regime in st.regimes else 0.85
            score = cand.base_score * fit + float(np.clip(ev * 25, -15, 15))
            if orderflow is not None:
                sgn = 1 if cand.direction == "BUY" else -1
                if orderflow.dom_imbalance * sgn > 0.2:
                    score += 5; cand.reasons.append(f"DOM Quantower favorable (déséquilibre {orderflow.dom_imbalance:+.2f})")
                if orderflow.delta * sgn > 0:
                    score += 5; cand.reasons.append(f"Delta réel Quantower {orderflow.delta:+.0f}")
                elif orderflow.delta * sgn < 0:
                    score -= 8; cand.reasons.append(f"Delta réel Quantower opposé ({orderflow.delta:+.0f})")
                if orderflow.absorption == ("bull" if sgn > 0 else "bear"):
                    score += 6; cand.reasons.append("Absorption confirmée par Quantower")
            raw.append((cand, score, p, ev, src))

        # Confluences / conflits
        scored = []
        for cand, score, p, ev, src in raw:
            agree = [c.strategy for c, *_ in raw if c is not cand and c.direction == cand.direction]
            score += 6 * len(agree)
            scored.append((cand, min(score, 99.0), p, ev, src, agree))
        for cand, score, p, ev, src, agree in scored:
            dec.candidates.append({"strategy": cand.strategy, "direction": cand.direction, "entry": cand.entry,
                                   "sl": cand.sl, "tp": cand.tp, "rr": round(cand.rr, 2), "score": round(score, 1),
                                   "probability": round(p, 3), "ev_r": round(ev, 3), "source": src,
                                   "confluences": agree, "reasons": cand.reasons})
        if not scored:
            dec.explanation = self._context_text(F, i) + " Aucune configuration exploitable sur cette bougie."
            return dec
        scored.sort(key=lambda x: (x[1] + 20 * x[3]), reverse=True)
        best = scored[0]
        opp = [s for s in scored if s[0].direction != best[0].direction]
        if opp and opp[0][1] >= best[1] - self.cfg.conflict_margin:
            dec.blocked.append(f"Signaux contradictoires ({best[0].strategy} {best[0].direction} vs "
                               f"{opp[0][0].strategy} {opp[0][0].direction})")
            dec.explanation = self._context_text(F, i) + " Signaux contradictoires de force comparable : abstention."
            return dec
        cand, score, p, ev, src, agree = best
        if news_block:
            dec.blocked.append(news_block)
            dec.explanation = self._context_text(F, i) + f" Opportunité {cand.strategy} ignorée : {news_block}."
            return dec
        if score < self.cfg.min_score or ev < self.cfg.min_ev_r:
            dec.blocked.append(f"{cand.strategy}: score {score:.0f} / EV {ev:+.2f}R sous les seuils "
                               f"({self.cfg.min_score:.0f} / {self.cfg.min_ev_r:+.2f}R)")
            dec.explanation = self._context_text(F, i) + (f" Meilleure opportunité ({cand.strategy} {cand.direction}) "
                                                          f"jugée insuffisante.")
            return dec
        st = next(s for s in self.strategies if s.name == cand.strategy)
        dg = get_spec(symbol).digits
        sig = Signal(symbol=symbol, timeframe=timeframe, direction=cand.direction, strategy=cand.strategy,
                     entry=round(float(cand.entry), dg), sl=round(float(cand.sl), dg), tp=round(float(cand.tp), dg), score=round(score, 1),
                     probability=round(p, 3), rr=round(cand.rr, 2), ev_r=round(ev, 3), regime=regime,
                     reasons=cand.reasons, explanation="", confluences=agree,
                     analysis=self.snapshot(F, i), created_at=now or datetime.now(timezone.utc),
                     valid_minutes=self.cfg.signal_valid_minutes, trail=float(cand.trail))
        sig.explanation = (self._context_text(F, i) + f" Stratégie retenue : {st.label} ({cand.direction}). "
                           + " ; ".join(cand.reasons) + f". Score {score:.0f}/100, probabilité estimée {p:.0%} "
                           f"({src}), R/R {cand.rr:.2f}, espérance {ev:+.2f}R."
                           + (f" Confluence avec : {', '.join(agree)}." if agree else ""))
        dec.signal, dec.explanation = sig, sig.explanation
        return dec

    @staticmethod
    def _context_text(F: FeatureView, i: int) -> str:
        r = str(F["regime"][i])
        return (f"Contexte : {REGIME_LABELS.get(r, r)} (ADX {F['adx'][i]:.0f}, efficacité {F['er'][i]:.2f}), "
                f"volatilité {VOL_LABELS.get(str(F['vol_state'][i]), '?')}, session {F['session'][i]}, "
                f"H4 {'haussier' if F["htf2_trend"][i] > 0 else 'baissier' if F["htf2_trend"][i] < 0 else 'neutre'}.")

    @staticmethod
    def snapshot(F: FeatureView, i: int) -> dict[str, Any]:
        keys = ["close", "atr", "adx", "er", "rsi", "regime", "vol_state", "session", "liquidity", "structure",
                "htf1_trend", "htf2_trend", "htf1_regime", "htf2_regime", "htf1_structure", "htf2_structure", "pd_pos", "near_res", "near_sup", "last_sh",
                "last_sl", "fvg_bull_top", "fvg_bull_bot", "fvg_bear_top", "fvg_bear_bot", "ob_bull_top", "ob_bull_bot",
                "ob_bear_top", "ob_bear_bot", "bsl", "ssl", "vp_poc", "vp_vah", "vp_val", "vol_z", "delta_z",
                "absorption", "since_bull_break", "since_bear_break", "since_sweep_bull", "since_sweep_bear"]
        out = {}
        for k in keys:
            if k in F:
                v = F[k][i]
                if isinstance(v, (np.floating, float)):
                    out[k] = None if np.isnan(v) else round(float(v), 6)
                elif isinstance(v, (np.integer, int, np.bool_, bool)):
                    out[k] = v.item() if hasattr(v, "item") else v
                else:
                    out[k] = str(v)
        return out
