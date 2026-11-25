"""Tests des composants prometteurs identifiés par le Research Lab, sur données EURUSD H1 réelles.

1. `ta` (bukosabino/ta, MIT) : validation croisée de nos indicateurs (RSI, ATR, ADX, Bollinger, MACD).
2. `smartmoneyconcepts` (joshyattridge/smart-money-concepts) : comparaison de la détection des Fair Value Gaps.
3. Optuna (TPE + MedianPruner) : déjà intégré (learning/optimize.py) — vérification du déterminisme (graine fixe).
Résultats -> research_lab/output/component_tests.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forex_agent.analysis import indicators as ind  # noqa: E402
from forex_agent.analysis import smc as our_smc  # noqa: E402
from forex_agent.analysis.features import resample  # noqa: E402
from forex_agent.data.hf_dataset import load_m1  # noqa: E402

OUT = Path(__file__).resolve().parent / "output"


def corr_maxdiff(a: pd.Series, b: pd.Series, skip: int = 300) -> dict:
    x, y = a.iloc[skip:], b.iloc[skip:]
    m = x.notna() & y.notna()
    return {"corr": round(float(np.corrcoef(x[m], y[m])[0, 1]), 6), "max_abs_diff": float((x[m] - y[m]).abs().max()),
            "n": int(m.sum())}


def test_ta(h1: pd.DataFrame) -> dict:
    import ta
    c = h1["close"]
    res = {"package": "ta", "version": getattr(ta, "__version__", "0.11.0"), "license": "MIT"}
    res["rsi14"] = corr_maxdiff(ind.rsi(c, 14), ta.momentum.RSIIndicator(c, 14).rsi())
    res["atr14"] = corr_maxdiff(ind.atr(h1, 14), ta.volatility.AverageTrueRange(h1["high"], h1["low"], c, 14).average_true_range())
    adx_ours, _, _ = ind.adx(h1, 14)
    res["adx14"] = corr_maxdiff(adx_ours, ta.trend.ADXIndicator(h1["high"], h1["low"], c, 14).adx())
    _, up, lo, _ = ind.bollinger(c, 20, 2.0)
    bb = ta.volatility.BollingerBands(c, 20, 2)
    res["bb_upper"] = corr_maxdiff(up, bb.bollinger_hband())
    m, _, _ = ind.macd(c)
    res["macd"] = corr_maxdiff(m, ta.trend.MACD(c).macd())
    res["verdict"] = ("Nos indicateurs concordent avec la référence (corrélation > 0.99)"
                      if all(v["corr"] > 0.99 for k, v in res.items() if isinstance(v, dict)) else
                      "Écarts significatifs : à investiguer")
    return res


def test_smc(h1: pd.DataFrame) -> dict:
    from smartmoneyconcepts import smc as lib
    df = h1.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].copy()
    t0 = time.time()
    lib_fvg = lib.fvg(df, join_consecutive=False)
    t_lib = time.time() - t0
    atr = ind.atr(h1).to_numpy()
    t0 = time.time()
    ours = our_smc.fvg(h1["high"].to_numpy(), h1["low"].to_numpy(), h1["close"].to_numpy(), np.nan_to_num(atr),
                       min_gap_atr=0.0)
    t_ours = time.time() - t0
    # La librairie marque le FVG sur la bougie du milieu (i-1) ; nous le confirmons à la clôture de la 3e (i)
    lib_bull = set(np.where(lib_fvg["FVG"].to_numpy() == 1)[0] + 1)
    lib_bear = set(np.where(lib_fvg["FVG"].to_numpy() == -1)[0] + 1)
    our_bull = set(np.where(ours["fvg_bull_new"])[0])
    our_bear = set(np.where(ours["fvg_bear_new"])[0])
    j = lambda a, b: round(len(a & b) / max(1, len(a | b)), 4)  # noqa: E731
    return {"package": "smartmoneyconcepts", "version": "0.0.27", "bars": int(len(df)),
            "lib_bull": len(lib_bull), "ours_bull": len(our_bull), "jaccard_bull": j(lib_bull, our_bull),
            "lib_bear": len(lib_bear), "ours_bear": len(our_bear), "jaccard_bear": j(lib_bear, our_bear),
            "time_lib_s": round(t_lib, 3), "time_ours_s": round(t_ours, 3),
            "note": ("Même définition (gap entre la bougie i-2 et i). La librairie horodate le FVG sur la bougie centrale, "
                     "ce qui n'est connu qu'une bougie plus tard : notre module l'horodate à sa confirmation (pas de look-ahead). "
                     "Décision : garder notre module (sans dépendance, anti-fuite), utiliser la librairie comme référence de test.")}


def test_optuna_determinism() -> dict:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def obj(t):
        x = t.suggest_float("x", -5, 5)
        return -(x - 1.7) ** 2

    vals = []
    for _ in range(2):
        st = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42),
                                 pruner=optuna.pruners.MedianPruner())
        st.optimize(obj, n_trials=40)
        vals.append(round(st.best_params["x"], 6))
    return {"package": "optuna", "version": optuna.__version__, "runs": vals, "deterministic": vals[0] == vals[1],
            "found_x": vals[0], "target_x": 1.7}


if __name__ == "__main__":
    h1 = resample(load_m1("EURUSD"), "H1")
    results = {"data": f"EURUSD H1 réel, {len(h1)} bougies, {h1.index[0]:%Y-%m-%d} → {h1.index[-1]:%Y-%m-%d}",
               "ta": test_ta(h1), "smartmoneyconcepts": test_smc(h1), "optuna": test_optuna_determinism()}
    OUT.mkdir(exist_ok=True)
    (OUT / "component_tests.json").write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
