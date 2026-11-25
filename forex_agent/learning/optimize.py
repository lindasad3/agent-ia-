"""Optimisation bayésienne (Optuna TPE + MedianPruner) des paramètres de stratégie — IS uniquement, imbriquée.

L'IS est coupé en IS-A (recherche, 70 %) et IS-B (validation, 30 %). Un jeu de paramètres n'est adopté que
s'il bat la référence sur IS-A ET ne la dégrade pas sur IS-B (jamais vu par Optuna). L'OOS final reste intact.
"""
from __future__ import annotations

import math

import optuna
import pandas as pd

from ..strategies.base import FeatureView, Strategy
from .backtest import ExecParams, PricePath, evaluate_raw

optuna.logging.set_verbosity(optuna.logging.WARNING)


def objective_value(r: pd.Series, min_trades: int = 30) -> float:
    """Espérance x racine(n) : récompense un avantage robuste, pénalise les petits échantillons."""
    if len(r) < min_trades:
        return -10.0 + len(r) / min_trades
    return float(r.mean() * math.sqrt(len(r)))


def optimize_strategy(proto: Strategy, data: dict[str, tuple[FeatureView, PricePath]], is_end: dict[str, int],
                      ex: ExecParams, n_trials: int = 15, entry_tf: str = "H1", seed: int = 42,
                      inner_frac: float = 0.7) -> dict:
    space = proto.search_space
    symbols = list(data)
    a_end = {s: 250 + int((is_end[s] - 250) * inner_frac) for s in symbols}

    def run(params: dict, lo: dict[str, int], hi: dict[str, int], trial: optuna.Trial | None = None) -> float:
        st = proto.with_params(**params)
        rs = []
        for k, sym in enumerate(symbols):
            F, path = data[sym]
            raw = evaluate_raw(sym, F, path, [st], ex, lo[sym], hi[sym], entry_tf)
            if len(raw):
                rs.append(raw["r"])
            if trial is not None:
                cur = pd.concat(rs) if rs else pd.Series(dtype=float)
                trial.report(objective_value(cur, 10), k)
                if trial.should_prune():
                    raise optuna.TrialPruned()
        return objective_value(pd.concat(rs) if rs else pd.Series(dtype=float))

    start = {s: 250 for s in symbols}

    def objective(trial: optuna.Trial) -> float:
        params = {}
        for name, (lo, hi) in space.items():
            if name == "window":
                params[name] = trial.suggest_int(name, int(lo), int(hi))
            else:
                params[name] = trial.suggest_float(name, lo, hi)
        return run(params, start, a_end, trial)

    base_a, base_b = run({}, start, a_end), run({}, a_end, is_end)
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed),
                                pruner=optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=3))
    study.enqueue_trial({k: proto.params[k] for k in space if k in proto.params})
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    best_a = study.best_value
    best_b = run(study.best_params, a_end, is_end) if study.best_params else base_b
    adopt = best_a > base_a + 0.5 and best_b >= base_b
    params = {**proto.params, **(study.best_params if adopt else {})}
    return {"strategy": proto.name, "baseline": round(base_a, 3), "best": round(best_a, 3),
            "baseline_val": round(base_b, 3), "best_val": round(best_b, 3), "adopted": adopt, "params": params,
            "n_trials": len(study.trials),
            "pruned": sum(1 for t in study.trials if t.state == optuna.trial.TrialState.PRUNED)}
