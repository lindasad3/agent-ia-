"""Pipeline d'apprentissage offline complet.

1. Données réelles M1 -> features MTF par symbole.
2. Pré-validation (données, MTF, absence de look-ahead).
3. Découpage chronologique : 70 % in-sample (IS) / 30 % hors échantillon (OOS) jamais optimisé.
4. Optuna (TPE + MedianPruner) des paramètres de chaque stratégie sur l'IS.
5. Matrice stratégie x paire x régime + méta-modèle ML appris sur l'IS.
6. Test OOS de l'agent complet (cerveau + Risk Engine + coûts) — chiffre de référence.
7. Walk-forward glissant (train 12 mois / test 3 mois) pour mesurer la stabilité.
8. Version candidate enregistrée ; activée seulement si elle passe les critères OOS.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..analysis.features import features_from_m1, resample
from ..brain.decision import BrainConfig, DecisionEngine
from ..brain.meta_model import MetaModel, train as train_meta
from ..data.hf_dataset import DEFAULT_PAIRS, load_m1
from ..performance.metrics import compute
from ..risk.engine import RiskConfig, RiskEngine
from ..strategies.base import FeatureView
from ..strategies.library import default_strategies
from . import registry
from .backtest import ExecParams, PricePath, RateBook, candidate_stream, evaluate_raw, perf_matrix, run_portfolio
from .optimize import optimize_strategy
from .validation import check_data, check_mtf, check_no_lookahead, check_trades

ROOT = Path(__file__).resolve().parents[2]


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _summary_table(raw: pd.DataFrame) -> pd.DataFrame:
    g = raw.groupby("strategy")["r"]
    return pd.DataFrame({"trades": g.size(), "win_rate": g.apply(lambda r: (r > 0).mean()).round(3),
                         "exp_r": g.mean().round(3), "sum_r": g.sum().round(1)})


def run(symbols: list[str] | None = None, entry_tf: str = "H1", n_trials: int = 12, capital: float = 10_000.0,
        oos_frac: float = 0.3, version: str | None = None, optimize: bool = True) -> dict:
    t0 = time.time()
    symbols = symbols or DEFAULT_PAIRS
    version = version or datetime.now(timezone.utc).strftime("v%Y%m%d-%H%M")
    out = ROOT / "reports" / "learning" / version
    mdir = ROOT / "models" / version
    out.mkdir(parents=True, exist_ok=True); mdir.mkdir(parents=True, exist_ok=True)
    ex = ExecParams()

    # 1-2. Données, features, validation
    log("Chargement des données et construction des features")
    m1s = {s: load_m1(s) for s in symbols}
    validation = {"data": [check_data(s, m) for s, m in m1s.items()]}
    ref = "EURUSD" if "EURUSD" in m1s else symbols[0]
    validation["mtf"] = check_mtf(m1s[ref])
    log("Test d'absence de look-ahead")
    validation["no_lookahead"] = check_no_lookahead(m1s[ref], entry_tf)
    feats = {s: features_from_m1(m, entry_tf) for s, m in m1s.items()}
    data = {s: (FeatureView(feats[s]), PricePath(m1s[s])) for s in symbols}
    conv = {k: resample(m1s[k], "H1")["close"] for k in ("USDJPY", "USDCAD", "USDCHF", "EURUSD", "GBPUSD") if k in m1s}
    ratebook = RateBook(conv)

    # 3. Découpage IS / OOS
    start = max(f.index[250] for f in feats.values())
    end_common = min(f.index[-1] for f in feats.values())
    cut = start + (end_common - start) * (1 - oos_frac)
    is_end = {s: int(feats[s].index.searchsorted(cut)) for s in symbols}
    log(f"IS: {start:%Y-%m-%d} -> {cut:%Y-%m-%d} | OOS: {cut:%Y-%m-%d} -> fin")

    # 4. Optimisation (IS uniquement)
    strategies = default_strategies()
    optim = []
    if optimize:
        for st in strategies:
            if not st.search_space:
                continue
            log(f"Optuna : {st.name}")
            res = optimize_strategy(st, data, is_end, ex, n_trials=n_trials, entry_tf=entry_tf)
            optim.append(res)
            st.params = res["params"]
    # 5. Trades bruts sur tout l'historique avec les paramètres retenus
    log("Évaluation brute des stratégies")
    cands = {s: candidate_stream(data[s][0], strategies) for s in symbols}
    raw = pd.concat([evaluate_raw(s, data[s][0], data[s][1], strategies, ex, entry_tf=entry_tf, cands=cands[s])
                     for s in symbols], ignore_index=True)
    raw_is = raw[raw["signal_time"] < cut]
    raw_oos = raw[raw["signal_time"] >= cut]
    validation["trades"] = check_trades(raw_is)
    matrix_is = perf_matrix(raw_is)
    log("Méta-modèle ML (purged walk-forward CV)")
    meta_is = train_meta(raw_is, feats)
    brain_cfg = BrainConfig()
    risk_cfg = RiskConfig()

    # 6. Test OOS de l'agent complet
    log("Test hors échantillon de l'agent complet")
    eng = DecisionEngine(strategies, matrix_is, meta_is if meta_is.active else None, brain_cfg)
    oos_trades = run_portfolio(data, eng, RiskEngine(risk_cfg), ex, capital, start_time=cut, ratebook=ratebook,
                               entry_tf=entry_tf, cands_cache=cands)
    oos_metrics = compute(oos_trades, capital)

    # 7. Walk-forward glissant
    log("Walk-forward (train 12 mois / test 3 mois)")
    wf_rows, wf_windows = [], []
    w_start = start + pd.DateOffset(months=12)
    while w_start < end_common:
        w_end = w_start + pd.DateOffset(months=3)
        tr = raw[(raw["signal_time"] >= w_start - pd.DateOffset(months=12)) & (raw["signal_time"] < w_start)
                 & (raw["close_time"] < w_start)]
        e = DecisionEngine(strategies, perf_matrix(tr), None, brain_cfg)
        tt = run_portfolio(data, e, RiskEngine(risk_cfg), ex, capital, start_time=w_start, end_time=w_end,
                           ratebook=ratebook, entry_tf=entry_tf, cands_cache=cands)
        m = compute(tt, capital)
        wf_windows.append({"test_start": f"{w_start:%Y-%m-%d}", "test_end": f"{w_end:%Y-%m-%d}", "train_trades": int(len(tr)),
                           "trades": m["n_trades"], "net": m["net_profit"], "return_pct": m.get("return_pct"),
                           "win_rate": m["win_rate"], "profit_factor": m["profit_factor"], "max_dd_pct": m["max_drawdown_pct"]})
        if len(tt):
            tt["window"] = f"{w_start:%Y-%m}"
            wf_rows.append(tt)
        w_start = w_end
    wf_trades = pd.concat(wf_rows, ignore_index=True) if wf_rows else pd.DataFrame()
    wf_metrics = compute(wf_trades, capital)

    # 8. Modèle de production : matrice et méta-modèle réappris sur tout l'historique validé
    matrix_all = perf_matrix(raw)
    meta_all = train_meta(raw, feats) if meta_is.active else MetaModel(report=meta_is.report, active=False)
    (mdir / "strategies.json").write_text(json.dumps({s.name: {"params": s.params, "enabled": s.enabled} for s in strategies}, indent=2))
    (mdir / "perf_matrix.json").write_text(json.dumps(matrix_all, indent=1))
    (mdir / "perf_matrix_is.json").write_text(json.dumps(matrix_is, indent=1))
    if meta_all.model is not None:
        meta_all.save(mdir / "meta_model.joblib")
    oos_trades.to_csv(out / "oos_trades.csv", index=False)
    if len(wf_trades):
        wf_trades.to_csv(out / "walkforward_trades.csv", index=False)
    raw.drop(columns=["bar"]).to_csv(out / "raw_strategy_trades.csv.gz", index=False)

    summary_is, summary_oos = _summary_table(raw_is), _summary_table(raw_oos)
    candidate = {
        "version": version, "created_at": datetime.now(timezone.utc).isoformat(), "entry_tf": entry_tf,
        "symbols": symbols, "capital": capital, "is_period": [str(start), str(cut)], "oos_period": [str(cut), str(end_common)],
        "optimization": optim, "validation": validation, "meta_model": meta_is.report, "meta_model_active": meta_is.active,
        "oos_metrics": {k: v for k, v in oos_metrics.items() if k != "equity_curve"},
        "oos_equity_curve": oos_metrics.get("equity_curve", []),
        "walkforward": {"windows": wf_windows, "metrics": {k: v for k, v in wf_metrics.items() if k != "equity_curve"},
                        "equity_curve": wf_metrics.get("equity_curve", [])},
        "raw_is": summary_is.reset_index().to_dict("records"), "raw_oos": summary_oos.reset_index().to_dict("records"),
        "brain_config": asdict(brain_cfg), "risk_config": risk_cfg.to_dict(),
        "artifacts": str(mdir.relative_to(ROOT)), "duration_s": round(time.time() - t0, 1),
    }
    reg = registry.register(candidate)
    (out / "metrics.json").write_text(json.dumps(candidate, indent=2, default=str, ensure_ascii=False))
    write_report(candidate, out / "REPORT.md")
    log(f"Version {version} : {reg['status']} — {'; '.join(reg['gate'])}")
    return candidate


def _fmt(v, pct: bool = False) -> str:
    if v is None:
        return "-"
    return f"{100 * v:.1f} %" if pct else (f"{v:,.2f}" if isinstance(v, float) else str(v))


def write_report(c: dict, path: Path) -> None:
    m, w = c["oos_metrics"], c["walkforward"]["metrics"]
    L = [f"# Rapport d'apprentissage offline — {c['version']}", "",
         f"Timeframe d'entrée **{c['entry_tf']}** · {len(c['symbols'])} instruments · capital {c['capital']:,.0f} USD · "
         f"IS {c['is_period'][0][:10]} → {c['is_period'][1][:10]} · OOS {c['oos_period'][0][:10]} → {c['oos_period'][1][:10]}",
         "", f"**Statut : {c['status'].upper()}** — {'; '.join(c['gate'])}", "",
         "## Pré-validation", "",
         f"- Données : {sum(d['ok'] for d in c['validation']['data'])}/{len(c['validation']['data'])} instruments valides "
         f"(≥ 90 jours, sans NaN, OHLC cohérents)",
         f"- Agrégation MTF : {'OK' if c['validation']['mtf']['ok'] else 'ÉCHEC'}",
         f"- Absence de look-ahead : {'OK' if c['validation']['no_lookahead']['ok'] else 'ÉCHEC'} "
         f"({c['validation']['no_lookahead']['probes']} sondages)",
         f"- Stratégies < 30 trades IS : {', '.join(c['validation']['trades']['insufficient']) or 'aucune'}", "",
         "## Agent complet — hors échantillon (jamais optimisé)", "",
         "| Trades | Profit net | Rendement | Win rate | Profit factor | Espérance (R) | Drawdown max |", "|---|---|---|---|---|---|---|",
         f"| {m['n_trades']} | {_fmt(m['net_profit'])} $ | {_fmt(m.get('return_pct'))} % | {_fmt(m['win_rate'], True)} | "
         f"{_fmt(m['profit_factor'])} | {_fmt(m['expectancy_r'])} | {_fmt(m['max_drawdown_pct'])} % |", "",
         "### Par stratégie (OOS)", "", "| Stratégie | Trades | Net $ | Win rate | PF | Esp. R |", "|---|---|---|---|---|---|"]
    for k, v in m.get("by_strategy", {}).items():
        L.append(f"| {k} | {v['n']} | {v['net']:,.2f} | {_fmt(v['win_rate'], True)} | {_fmt(v['profit_factor'])} | {_fmt(v['expectancy_r'])} |")
    L += ["", "### Par paire (OOS)", "", "| Paire | Trades | Net $ | Win rate | PF |", "|---|---|---|---|---|"]
    for k, v in m.get("by_symbol", {}).items():
        L.append(f"| {k} | {v['n']} | {v['net']:,.2f} | {_fmt(v['win_rate'], True)} | {_fmt(v['profit_factor'])} |")
    L += ["", "## Walk-forward (fenêtres de test de 3 mois)", "",
          f"Global : {w['n_trades']} trades, net {_fmt(w['net_profit'])} $, PF {_fmt(w['profit_factor'])}, "
          f"win rate {_fmt(w['win_rate'], True)}, espérance {_fmt(w['expectancy_r'])} R", "",
          "| Fenêtre | Trades train | Trades test | Net $ | PF | Win rate | DD max |", "|---|---|---|---|---|---|---|"]
    for x in c["walkforward"]["windows"]:
        L.append(f"| {x['test_start']} → {x['test_end']} | {x['train_trades']} | {x['trades']} | {_fmt(x['net'])} | "
                 f"{_fmt(x['profit_factor'])} | {_fmt(x['win_rate'], True)} | {_fmt(x['max_dd_pct'])} % |")
    L += ["", "## Stratégies isolées (avant arbitrage du cerveau)", "", "| Stratégie | IS trades | IS esp. R | OOS trades | OOS esp. R |",
          "|---|---|---|---|---|"]
    oos = {r["strategy"]: r for r in c["raw_oos"]}
    for r in c["raw_is"]:
        o = oos.get(r["strategy"], {})
        L.append(f"| {r['strategy']} | {r['trades']} | {r['exp_r']:+.3f} | {o.get('trades', 0)} | {o.get('exp_r', 0):+.3f} |")
    L += ["", "## Optimisation Optuna imbriquée (IS-A recherche / IS-B validation)", "", "| Stratégie | Réf. IS-A | Meilleur IS-A | Réf. IS-B | Meilleur IS-B | Adopté | Essais (élagués) |", "|---|---|---|---|---|---|---|"]
    for o in c["optimization"]:
        L.append(f"| {o['strategy']} | {o['baseline']} | {o['best']} | {o.get('baseline_val')} | {o.get('best_val')} | {'oui' if o['adopted'] else 'non'} | {o['n_trials']} ({o['pruned']}) |")
    mm = c["meta_model"]
    L += ["", "## Méta-modèle ML", "", f"- Statut : **{mm.get('status')}** (n={mm.get('n')})",
          f"- AUC OOS : {mm.get('auc')} · Brier {mm.get('brier')} vs base {mm.get('brier_base')}",
          f"- Filtre optimal : {mm.get('best_filter')} · espérance sans filtre {mm.get('all_exp_r')}", "",
          "## Méthodologie et limites", "",
          "- Exécution à l'ouverture suivant le signal, chemin du prix rejoué sur M1 réelles, SL prioritaire si SL et TP "
          "dans la même minute, spread typique + commission 7 $/lot.",
          "- Les performances passées ne garantissent pas les performances futures ; le slippage réel et les spreads "
          "variables (news) peuvent dégrader les résultats.",
          "- Le modèle de production est réappris sur tout l'historique après validation ; le chiffre de référence reste "
          "le test OOS ci-dessus."]
    path.write_text("\n".join(L), encoding="utf-8")
