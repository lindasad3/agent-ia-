"""Analyse offline du journal réel : trades gagnants/perdants, erreurs, performance par contexte."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from ..performance.metrics import compute


def review(store) -> dict:
    trades = pd.DataFrame([t for t in store.trades(status="closed", limit=10000) if t.get("pnl") is not None])
    sig = pd.DataFrame(store.signals(5000))
    out: dict = {"generated_at": datetime.now(timezone.utc).isoformat(), "n_closed": int(len(trades))}
    if len(sig):
        out["signals"] = sig["status"].value_counts().to_dict()
        blocked = sig[sig["status"] == "blocked"]
        reasons: dict[str, int] = {}
        for rc in blocked["risk_checks"].dropna():
            for c in rc or []:
                if not c.get("ok"):
                    reasons[c["name"]] = reasons.get(c["name"], 0) + 1
        out["rejection_reasons"] = dict(sorted(reasons.items(), key=lambda kv: -kv[1]))
    if not len(trades):
        out["findings"] = ["Pas encore de trade clôturé : l'analyse se remplira avec l'activité réelle."]
        return out
    trades["close_time"] = pd.to_datetime(trades["close_time"])
    m = compute(trades)
    out["metrics"] = {k: v for k, v in m.items() if k != "equity_curve"}
    findings = []
    for key in ("by_strategy", "by_symbol", "by_regime"):
        for k, v in (m.get(key) or {}).items():
            if v["n"] >= 10 and (v.get("expectancy_r") or 0) < 0:
                findings.append(f"{key.split('_')[1]} « {k} » : espérance négative ({v['expectancy_r']:+.2f} R sur {v['n']} trades)")
    slip = trades["slippage_pips"].dropna()
    if len(slip) and slip.abs().mean() > 0.5:
        findings.append(f"Slippage moyen élevé : {slip.abs().mean():.2f} pips")
    reasons = trades["close_reason"].value_counts().to_dict()
    out["close_reasons"] = reasons
    losers = trades[trades["pnl"] <= 0]
    if len(losers) >= 5:
        by_sess = losers.groupby(losers["open_time"].str[11:13]).size().sort_values(ascending=False).head(3).to_dict()
        out["loss_hours_utc"] = by_sess
    out["findings"] = findings or ["Aucune anomalie significative détectée sur l'échantillon actuel."]
    return out
