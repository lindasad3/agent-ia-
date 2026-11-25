"""Détection des conditions de marché : tendance, range, volatilité, liquidité (session)."""
from __future__ import annotations

import numpy as np
import pandas as pd

TREND_UP, TREND_DOWN, RANGE, TRANSITION = "TREND_UP", "TREND_DOWN", "RANGE", "TRANSITION"
REGIMES = [TREND_UP, TREND_DOWN, RANGE, TRANSITION]
REGIME_LABELS = {TREND_UP: "Tendance haussière", TREND_DOWN: "Tendance baissière", RANGE: "Range",
                 TRANSITION: "Transition"}
VOL_LABELS = {"LOW": "faible", "NORMAL": "normale", "HIGH": "élevée"}


def session_of(hour_utc: int) -> tuple[str, int]:
    """Session et niveau de liquidité (0 faible .. 3 maximale)."""
    if 12 <= hour_utc < 16:
        return "Overlap Londres/New York", 3
    if 7 <= hour_utc < 12:
        return "Londres", 2
    if 16 <= hour_utc < 20:
        return "New York", 2
    if 0 <= hour_utc < 7:
        return "Asie", 1
    return "Fin de séance", 0


def classify(f: pd.DataFrame) -> pd.DataFrame:
    """Ajoute regime / vol_state / session / liquidity à un DataFrame de features."""
    adx_v, er, slope = f["adx"], f["er"], f["ema50_slope"]
    bbw_rank = f["bb_width_rank"]
    trend = (adx_v >= 23) & (er >= 0.25)
    regime = np.where(trend & (slope > 0) & (f["close"] > f["ema50"]), TREND_UP,
             np.where(trend & (slope < 0) & (f["close"] < f["ema50"]), TREND_DOWN,
             np.where((adx_v < 20) & (bbw_rank < 0.6), RANGE, TRANSITION)))
    f["regime"] = regime
    atr_rank = f["atr_rank"]
    f["vol_state"] = np.where(atr_rank > 0.85, "HIGH", np.where(atr_rank < 0.2, "LOW", "NORMAL"))
    hours = f.index.hour
    sess = [session_of(int(h)) for h in hours]
    f["session"] = [s for s, _ in sess]
    f["liquidity"] = [lvl for _, lvl in sess]
    return f
