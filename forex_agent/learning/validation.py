"""Pré-validation obligatoire avant toute optimisation (skill strategy-workflow)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..analysis.features import HTF_FOR, build_features, resample


def check_data(symbol: str, m1: pd.DataFrame) -> dict:
    span_days = (m1.index[-1] - m1.index[0]).days
    nan = int(m1[["open", "high", "low", "close"]].isna().sum().sum())
    bad = int(((m1["high"] < m1["low"]) | (m1["close"] > m1["high"]) | (m1["close"] < m1["low"])).sum())
    gaps = m1.index.to_series().diff().dt.total_seconds().div(3600)
    weekday_gaps = gaps[(gaps > 6) & (m1.index.day_of_week < 5) & (m1.index.day_of_week > 0)]
    return {"symbol": symbol, "rows": int(len(m1)), "days": int(span_days), "nan": nan, "ohlc_invalid": bad,
            "largest_gap_h": round(float(gaps.max()), 1), "weekday_gaps_gt6h": int(len(weekday_gaps)),
            "ok": span_days >= 90 and nan == 0 and bad == 0}


def check_no_lookahead(m1: pd.DataFrame, entry_tf: str = "H1", probes: int = 3) -> dict:
    """Les features d'une barre calculées sur l'historique tronqué doivent égaler celles du calcul complet."""
    htf = HTF_FOR[entry_tf]
    full = build_features(resample(m1, entry_tf), {tf: resample(m1, tf) for tf in htf}, entry_tf)
    cols = ["regime", "structure", "last_sh", "last_sl", "fvg_bull_top", "ob_bull_top", "sweep_bull", "htf1_trend",
            "htf2_trend", "htf1_structure", "atr", "rsi", "vp_poc"]
    mism = []
    n = len(full)
    for frac in np.linspace(0.6, 0.95, probes):
        cut_time = full.index[int(n * frac)]
        trunc_m1 = m1[m1.index < cut_time + pd.Timedelta(hours=1 if entry_tf == "H1" else 0.25)]
        part = build_features(resample(trunc_m1, entry_tf), {tf: resample(trunc_m1, tf) for tf in htf}, entry_tf)
        last = part.index[-1]
        for c in cols:
            a, b = part.loc[last, c], full.loc[last, c]
            if isinstance(a, float) and isinstance(b, float):
                if not (np.isnan(a) and np.isnan(b)) and not np.isclose(a, b, rtol=1e-6, atol=1e-9):
                    mism.append({"time": str(last), "col": c, "truncated": a, "full": b})
            elif a != b and not (pd.isna(a) and pd.isna(b)):
                mism.append({"time": str(last), "col": c, "truncated": str(a), "full": str(b)})
    return {"probes": probes, "mismatches": mism[:10], "ok": not mism}


def check_mtf(m1: pd.DataFrame) -> dict:
    """Une barre H1 doit agréger exactement les 60 minutes qui la composent."""
    h1 = resample(m1, "H1")
    t = h1.index[len(h1) // 2]
    seg = m1[(m1.index >= t) & (m1.index < t + pd.Timedelta(hours=1))]
    ok = (np.isclose(h1.loc[t, "high"], seg["high"].max()) and np.isclose(h1.loc[t, "low"], seg["low"].min())
          and np.isclose(h1.loc[t, "open"], seg["open"].iloc[0]) and np.isclose(h1.loc[t, "close"], seg["close"].iloc[-1]))
    return {"bar": str(t), "ok": bool(ok)}


def check_trades(raw: pd.DataFrame, min_trades: int = 30) -> dict:
    counts = raw.groupby("strategy").size().to_dict() if len(raw) else {}
    return {"per_strategy": {k: int(v) for k, v in counts.items()},
            "insufficient": [k for k, v in counts.items() if v < min_trades], "min_trades": min_trades}
