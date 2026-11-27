"""Test sur données futures inédites (forward test) d'une version déjà entraînée.

La version est figée (paramètres, matrice stratégie x paire x régime, réglages du cerveau et du risque) ;
on la rejoue telle quelle sur une période POSTÉRIEURE à toutes ses données d'entraînement, avec l'historique
réel du broker (MT5). Aucun paramètre n'est ajusté : c'est la preuve la plus honnête disponible.
Si les critères du registre sont remplis, la version est validée et activée.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from ..analysis.features import HTF_FOR, build_features
from ..brain.factory import load_engine
from ..performance.metrics import compute
from ..risk.engine import RiskConfig, RiskEngine
from ..strategies.base import FeatureView
from . import registry
from .backtest import ExecParams, PricePath, RateBook, run_portfolio

ROOT = Path(__file__).resolve().parents[2]
WARMUP_DAYS = 420  # contexte D1 : EMA200 journalière + rang de volatilité sur 500 barres
PATH_TF = "M5"     # rejeu du prix : 100 000 bougies M5 ≈ 16 mois (limite « Max bars » du terminal MT5)
MIN_COVERAGE = 0.85  # part minimale des heures de marché présentes dans l'historique testé


def coverage(m1: pd.DataFrame, start: datetime, end: datetime, symbol: str = "") -> float:
    """Part des heures de marché (lun-ven ; or : 23 h/j) pour lesquelles l'historique contient des bougies."""
    seg = m1[(m1.index >= pd.Timestamp(start)) & (m1.index < pd.Timestamp(end))]
    if seg.empty:
        return 0.0
    hours = pd.date_range(pd.Timestamp(start).floor("h"), seg.index[-1].floor("h"), freq="h")
    # Forex ouvert du dimanche 22:00 au vendredi 22:00 UTC (approximation, à 1 h près selon l'heure d'été)
    open_mask = ~(((hours.day_of_week == 4) & (hours.hour >= 22)) | (hours.day_of_week == 5) |
                  ((hours.day_of_week == 6) & (hours.hour < 22)))
    expected = int(open_mask.sum()) * (23 / 24 if symbol.startswith("XA") else 1.0)
    have = seg.index.floor("h").nunique()
    return min(1.0, have / expected) if expected else 0.0

# fonction (symbole, timeframe, début, fin) -> bougies OHLCV (index UTC = heure d'ouverture)
Loader = Callable[[str, str, datetime, datetime], pd.DataFrame]


def run_forward(version: str | None, loader: Loader, start: datetime, end: datetime | None = None,
                capital: float = 10_000.0, symbols: list[str] | None = None, register: bool = True,
                log: Callable[[str], None] = print, path_tf: str = PATH_TF) -> dict:
    reg = registry.load()
    v = next((x for x in reg["versions"] if x["version"] == version), None) if version else registry.latest_version()
    if v is None:
        raise ValueError("Aucune version entraînée dans models/registry.json")
    engine, _ = load_engine(v["version"], allow_unvalidated=True)
    entry_tf = v.get("entry_tf", "H1")
    end = end or datetime.now(timezone.utc)
    symbols = symbols or v["symbols"]
    data, closes, skipped, coverages = {}, {}, {}, {}
    warm = start - timedelta(days=WARMUP_DAYS)
    for s in symbols:
        try:
            # Analyse : bougies du broker H1/H4/D1 (comme l'agent en direct) ; rejeu du prix : bougies fines
            entry = loader(s, entry_tf, warm, end)
            htfs = {tf: loader(s, tf, warm, end) for tf in HTF_FOR[entry_tf]}
            path = loader(s, path_tf, start - timedelta(days=3), end)
        except Exception as e:  # noqa: BLE001
            skipped[s] = str(e)
            log(f"  {s}: EXCLU — {e}")
            continue
        if path.empty or path.index[-1] <= pd.Timestamp(start):
            skipped[s] = f"aucune bougie {path_tf} après le début du test"
            continue
        cov = min(coverage(path, start, end, s), coverage(entry, start, end, s))
        coverages[s] = round(cov, 3)
        warm_ok = len(entry[entry.index < pd.Timestamp(start)]) >= 300 and len(htfs[HTF_FOR[entry_tf][-1]]) >= 250
        if cov < MIN_COVERAGE or not warm_ok:
            # Un historique troué ou trop court fausserait le test : paire exclue plutôt que jugée sur des données partielles
            skipped[s] = (f"historique incomplet ({cov:.0%} des heures de marché, minimum {MIN_COVERAGE:.0%})"
                          if cov < MIN_COVERAGE else "contexte d'avant-test insuffisant")
            log(f"  {s}: EXCLU — {skipped[s]}")
            continue
        log(f"  {s}: {path_tf} {len(path):,} bougies ({path.index[0]:%Y-%m-%d} → {path.index[-1]:%Y-%m-%d}), "
            f"{entry_tf} depuis {entry.index[0]:%Y-%m-%d}, couverture {cov:.0%}")
        data[s] = (FeatureView(build_features(entry, htfs, entry_tf)), PricePath(path))
        closes[s] = entry["close"]
    if not data:
        raise RuntimeError(f"Aucune donnée exploitable : {skipped}")
    ratebook = RateBook({k: closes[k] for k in ("USDJPY", "USDCAD", "USDCHF", "EURUSD", "GBPUSD") if k in closes})
    risk = RiskEngine(RiskConfig(**v.get("risk_config", {})))
    log("Rejeu de l'agent complet (cerveau + Risk Engine + coûts) sur la période inédite...")
    trades = run_portfolio(data, engine, risk, ExecParams(), capital, start_time=pd.Timestamp(start),
                           end_time=pd.Timestamp(end), ratebook=ratebook, entry_tf=entry_tf)
    m = compute(trades, capital)
    passed, reasons = registry.gate({"oos_metrics": m}, None)
    result = {"version": v["version"], "run_at": datetime.now(timezone.utc).isoformat(),
              "period": [pd.Timestamp(start).isoformat(), pd.Timestamp(end).isoformat()],
              "symbols": list(data), "skipped": skipped, "coverage": coverages, "metrics": {k: x for k, x in m.items() if k != "equity_curve"},
              "equity_curve": m.get("equity_curve", []), "passed": passed, "gate": reasons}
    out = ROOT / "reports" / "forward" / f"{v['version']}_{datetime.now(timezone.utc):%Y%m%d-%H%M}"
    out.mkdir(parents=True, exist_ok=True)
    if len(trades):
        trades.to_csv(out / "trades.csv", index=False)
    (out / "result.json").write_text(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    write_report(result, out / "REPORT.md")
    result["report"] = str(out.relative_to(ROOT))
    if register:
        registry.record_forward(v["version"], result)
    return result


def write_report(r: dict, path: Path) -> None:
    m = r["metrics"]

    def f(x, pct=False):
        return "-" if x is None else (f"{100 * x:.1f} %" if pct else (f"{x:,.2f}" if isinstance(x, float) else str(x)))
    L = [f"# Test sur données inédites — version {r['version']}", "",
         f"Période : {r['period'][0][:10]} → {r['period'][1][:10]} (postérieure à toutes les données d'entraînement) · "
         f"{len(r['symbols'])} instruments · historique réel du broker", "",
         f"**{'VALIDÉE ✅' if r['passed'] else 'NON VALIDÉE ❌'}** — {'; '.join(r['gate'])}", "",
         "| Trades | Profit net | Rendement | Win rate | Profit factor | Espérance (R) | Drawdown max |",
         "|---|---|---|---|---|---|---|",
         f"| {m['n_trades']} | {f(m['net_profit'])} | {f(m.get('return_pct'))} % | {f(m['win_rate'], True)} | "
         f"{f(m['profit_factor'])} | {f(m['expectancy_r'])} | {f(m['max_drawdown_pct'])} % |", "",
         "| Stratégie | Trades | Net | Win rate | PF | Esp. R |", "|---|---|---|---|---|---|"]
    for k, x in (m.get("by_strategy") or {}).items():
        L.append(f"| {k} | {x['n']} | {f(x['net'])} | {f(x['win_rate'], True)} | {f(x['profit_factor'])} | {f(x['expectancy_r'])} |")
    L += ["", "| Paire | Trades | Net | Win rate | PF |", "|---|---|---|---|---|"]
    for k, x in (m.get("by_symbol") or {}).items():
        L.append(f"| {k} | {x['n']} | {f(x['net'])} | {f(x['win_rate'], True)} | {f(x['profit_factor'])} |")
    if r["skipped"]:
        L += ["", "Instruments exclus : " + "; ".join(f"{k} ({v})" for k, v in r["skipped"].items())]
    L += ["", "Méthode : version figée (aucun réglage) ; analyse sur les bougies H1/H4/D1 du broker (comme en direct) ; "
          "exécution à l'ouverture suivant le signal ; chemin du prix rejoué sur les bougies M5 réelles, SL prioritaire si SL "
          "et TP dans la même bougie ; spread typique + 7 $/lot de commission. Couverture des données par paire : "
          + ", ".join(f"{k} {100 * v:.0f} %" for k, v in (r.get("coverage") or {}).items())]
    path.write_text("\n".join(L), encoding="utf-8")
