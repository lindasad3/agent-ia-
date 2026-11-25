"""Méta-modèle ML (meta-labeling, López de Prado) : probabilité qu'un signal proposé soit gagnant.

Checklist anti-fuite appliquée :
  [x] Label = issue future du trade (R > 0), calculée après l'entrée — jamais présente dans les features.
  [x] Features = valeurs connues à la clôture de la barre de signal (pivots confirmés, HTF clôturés).
  [x] Validation croisée walk-forward chronologique avec purge (trades chevauchant le test) et embargo.
  [x] Aucun scaler global : HistGradientBoosting (invariant d'échelle, NaN natifs).
  [x] Activation uniquement si le modèle bat la base hors échantillon (AUC, Brier, gain d'espérance).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score

from ..analysis.regime import REGIMES

NUM = ["adx", "er", "rsi", "atr_rank", "bb_width_rank", "vol_z", "liquidity", "htf1_adx", "htf2_adx", "hour", "dow"]
DIRECTIONAL = ["structure", "htf1_trend", "htf2_trend", "htf1_structure", "htf2_structure", "delta_z", "macd_hist"]
STRATEGIES = ["trend_pullback", "breakout", "mean_reversion", "smc_sweep", "orderflow_absorption", "momentum",
              "trend_trail", "london_breakout"]


def make_row(strategy: str, direction: str, rr: float, regime: str, f: dict[str, Any]) -> list[float]:
    sgn = 1.0 if direction == "BUY" else -1.0
    atr = f.get("atr") or np.nan

    def g(k: str) -> float:
        v = f.get(k)
        try:
            return float(v)
        except (TypeError, ValueError):
            return np.nan

    row = [g(k) for k in NUM]
    row += [sgn * g(k) for k in DIRECTIONAL]
    row += [sgn * (g("close") - g("ema50")) / atr if atr else np.nan,
            sgn * (g("close") - g("ema200")) / atr if atr else np.nan,
            sgn * (g("pd_pos") - 0.5),
            min(g("since_bull_break") if sgn > 0 else g("since_bear_break"), 200),
            min(g("since_sweep_bull") if sgn > 0 else g("since_sweep_bear"), 200),
            float(rr), sgn]
    row += [1.0 if strategy == s else 0.0 for s in STRATEGIES]
    row += [1.0 if regime == r else 0.0 for r in REGIMES]
    return row


FEATURE_NAMES = (NUM + [f"dir_{k}" for k in DIRECTIONAL] +
                 ["dist_ema50_atr", "dist_ema200_atr", "pd_centered", "bars_since_break", "bars_since_sweep", "rr",
                  "direction"] + [f"strat_{s}" for s in STRATEGIES] + [f"regime_{r}" for r in REGIMES])


@dataclass
class MetaModel:
    model: Any = None
    threshold: float = 0.5
    report: dict = field(default_factory=dict)
    active: bool = False

    def predict_proba_one(self, cand, regime: str, features: dict[str, Any]) -> float:
        x = np.array([make_row(cand.strategy, cand.direction, cand.rr, regime, features)])
        return float(self.model.predict_proba(x)[0, 1])

    def save(self, path: Path) -> None:
        joblib.dump({"model": self.model, "threshold": self.threshold, "report": self.report, "active": self.active},
                    path)

    @classmethod
    def load(cls, path: Path) -> "MetaModel":
        d = joblib.load(path)
        return cls(d["model"], d["threshold"], d["report"], d["active"])


def build_dataset(trades: pd.DataFrame, features_by_symbol: dict[str, pd.DataFrame]) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    rows, keep = [], []
    for idx, t in trades.iterrows():
        f = features_by_symbol[t["symbol"]]
        frow = f.iloc[int(t["bar"])].to_dict()
        rows.append(make_row(t["strategy"], t["direction"], t["rr"], t["regime"], frow))
        keep.append(idx)
    X = np.array(rows, dtype=float)
    y = (trades.loc[keep, "r"].to_numpy() > 0).astype(int)
    return X, y, trades.loc[keep]


def _new_model() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200, min_samples_leaf=40,
                                          l2_regularization=1.0, random_state=42)


def purged_walk_forward(trades: pd.DataFrame, X: np.ndarray, y: np.ndarray, n_folds: int = 5,
                        embargo: pd.Timedelta = pd.Timedelta(days=2)) -> dict:
    order = np.argsort(trades["signal_time"].to_numpy())
    X, y, t = X[order], y[order], trades.iloc[order].reset_index(drop=True)
    n = len(t)
    bounds = np.linspace(int(n * 0.3), n, n_folds + 1, dtype=int)
    oof_p = np.full(n, np.nan)
    folds = []
    for k in range(n_folds):
        a, b = bounds[k], bounds[k + 1]
        test_start = t["signal_time"].iloc[a]
        # Purge : on retire du train les trades encore ouverts au début du test (+ embargo)
        train_mask = (t["close_time"] < test_start - embargo).to_numpy().copy()
        train_mask[a:] = False
        if train_mask.sum() < 200 or b - a < 30:
            continue
        m = _new_model().fit(X[train_mask], y[train_mask])
        p = m.predict_proba(X[a:b])[:, 1]
        oof_p[a:b] = p
        auc = roc_auc_score(y[a:b], p) if len(set(y[a:b])) > 1 else np.nan
        folds.append({"fold": k, "train": int(train_mask.sum()), "test": int(b - a), "auc": round(float(auc), 4),
                      "test_start": str(test_start)})
    mask = ~np.isnan(oof_p)
    res: dict[str, Any] = {"folds": folds, "n_oof": int(mask.sum())}
    if mask.sum() >= 100 and len(set(y[mask])) > 1:
        r = t["r"].to_numpy()[mask]
        p = oof_p[mask]
        base = y[mask].mean()
        res["auc"] = round(float(roc_auc_score(y[mask], p)), 4)
        res["brier"] = round(float(brier_score_loss(y[mask], p)), 4)
        res["brier_base"] = round(float(brier_score_loss(y[mask], np.full(mask.sum(), base))), 4)
        best = None
        for th in np.arange(0.40, 0.66, 0.02):
            sel = p >= th
            if sel.sum() >= 30:
                cand = {"threshold": round(float(th), 2), "n": int(sel.sum()), "exp_r": round(float(r[sel].mean()), 4)}
                if best is None or cand["exp_r"] > best["exp_r"]:
                    best = cand
        res["all_exp_r"] = round(float(r.mean()), 4)
        res["best_filter"] = best
    return res


def train(trades: pd.DataFrame, features_by_symbol: dict[str, pd.DataFrame], min_auc: float = 0.53) -> MetaModel:
    X, y, t = build_dataset(trades, features_by_symbol)
    if len(t) < 300:
        return MetaModel(report={"status": "données insuffisantes", "n": int(len(t))}, active=False)
    rep = purged_walk_forward(t, X, y)
    model = _new_model().fit(X, y)
    bf = rep.get("best_filter") or {}
    active = bool(rep.get("auc", 0) >= min_auc and rep.get("brier", 1) <= rep.get("brier_base", 0) + 1e-4
                  and bf.get("exp_r", -1) > rep.get("all_exp_r", 0))
    rep["status"] = "activé" if active else "rejeté (pas d'avantage hors échantillon)"
    rep["n"] = int(len(t))
    rep["features"] = FEATURE_NAMES
    return MetaModel(model, bf.get("threshold", 0.5), rep, active)


def report_json(mm: MetaModel) -> str:
    return json.dumps(mm.report, indent=2, ensure_ascii=False, default=str)
