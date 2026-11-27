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

from ..analysis.features import features_from_m1, resample
from ..brain.factory import load_engine
from ..performance.metrics import compute
from ..risk.engine import RiskConfig, RiskEngine
from ..strategies.base import FeatureView
from . import registry
from .backtest import ExecParams, PricePath, RateBook, run_portfolio

ROOT = Path(__file__).resolve().parents[2]
WARMUP_DAYS = 90  # historique nécessaire avant le début du test (indicateurs, contexte D1)

# fonction (symbole, début, fin) -> bougies M1 (index UTC)
Loader = Callable[[str, datetime, datetime], pd.DataFrame]


def run_forward(version: str | None, loader: Loader, start: datetime, end: datetime | None = None,
                capital: float = 10_000.0, symbols: list[str] | None = None, register: bool = True,
                log: Callable[[str], None] = print) -> dict:
    reg = registry.load()
    v = next((x for x in reg["versions"] if x["version"] == version), None) if version else registry.latest_version()
    if v is None:
        raise ValueError("Aucune version entraînée dans models/registry.json")
    engine, _ = load_engine(v["version"], allow_unvalidated=True)
    entry_tf = v.get("entry_tf", "H1")
    end = end or datetime.now(timezone.utc)
    symbols = symbols or v["symbols"]
    data, closes, skipped = {}, {}, {}
    for s in symbols:
        try:
            m1 = loader(s, start - timedelta(days=WARMUP_DAYS), end)
        except Exception as e:  # noqa: BLE001
            skipped[s] = str(e)
            continue
        if len(m1) < 20_000 or m1.index[-1] <= pd.Timestamp(start):
            skipped[s] = f"historique insuffisant ({len(m1)} bougies M1)"
            continue
        log(f"  {s}: {len(m1):,} bougies M1 ({m1.index[0]:%Y-%m-%d} → {m1.index[-1]:%Y-%m-%d})")
        data[s] = (FeatureView(features_from_m1(m1, entry_tf)), PricePath(m1))
        closes[s] = resample(m1, "H1")["close"]
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
              "symbols": list(data), "skipped": skipped, "metrics": {k: x for k, x in m.items() if k != "equity_curve"},
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
    L += ["", "Méthode : version figée (aucun réglage), exécution à l'ouverture suivant le signal, chemin du prix rejoué "
          "sur les bougies M1 réelles, SL prioritaire si SL et TP dans la même minute, spread typique + 7 $/lot de commission."]
    path.write_text("\n".join(L), encoding="utf-8")
