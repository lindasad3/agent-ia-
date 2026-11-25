"""Price action : swings confirmés, structure (BOS / CHOCH), supports/résistances, chandeliers.

Anti-fuite : un pivot à l'indice j n'est connu qu'à j + right (barres de confirmation).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def swings(high: np.ndarray, low: np.ndarray, left: int = 3, right: int = 3) -> dict[str, np.ndarray]:
    n = len(high)
    last_sh = np.full(n, np.nan); last_sl = np.full(n, np.nan)
    last_sh_idx = np.full(n, -1); last_sl_idx = np.full(n, -1)
    prev_sh = np.full(n, np.nan); prev_sl = np.full(n, np.nan)
    sh = sl = np.nan; shi = sli = -1; psh = psl = np.nan
    for t in range(n):
        j = t - right
        if j - left >= 0:
            wh = high[j - left: t + 1]
            if high[j] == wh.max() and (wh == high[j]).sum() == 1:
                psh, sh, shi = sh, high[j], j
            wl = low[j - left: t + 1]
            if low[j] == wl.min() and (wl == low[j]).sum() == 1:
                psl, sl, sli = sl, low[j], j
        last_sh[t], last_sl[t], last_sh_idx[t], last_sl_idx[t] = sh, sl, shi, sli
        prev_sh[t], prev_sl[t] = psh, psl
    return {"last_sh": last_sh, "last_sl": last_sl, "last_sh_idx": last_sh_idx, "last_sl_idx": last_sl_idx,
            "prev_sh": prev_sh, "prev_sl": prev_sl}


def structure(close: np.ndarray, sw: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """BOS = cassure dans le sens de la structure ; CHOCH = cassure contre la structure."""
    n = len(close)
    bos = np.zeros(n, dtype=np.int8); choch = np.zeros(n, dtype=np.int8); state = np.zeros(n, dtype=np.int8)
    since_bull = np.full(n, 9999); since_bear = np.full(n, 9999)
    since_choch_bull = np.full(n, 9999); since_choch_bear = np.full(n, 9999)
    trend = 0; broken_h = broken_l = -1
    sb = sbe = scb = scbe = 9999
    for t in range(n):
        sb += 1; sbe += 1; scb += 1; scbe += 1
        shi, sli = sw["last_sh_idx"][t], sw["last_sl_idx"][t]
        if shi >= 0 and shi != broken_h and close[t] > sw["last_sh"][t]:
            if trend == -1:
                choch[t] = 1; scb = 0
            else:
                bos[t] = 1
            trend, broken_h, sb = 1, shi, 0
        elif sli >= 0 and sli != broken_l and close[t] < sw["last_sl"][t]:
            if trend == 1:
                choch[t] = -1; scbe = 0
            else:
                bos[t] = -1
            trend, broken_l, sbe = -1, sli, 0
        state[t] = trend
        since_bull[t], since_bear[t], since_choch_bull[t], since_choch_bear[t] = sb, sbe, scb, scbe
    return {"bos": bos, "choch": choch, "structure": state, "since_bull_break": since_bull,
            "since_bear_break": since_bear, "since_choch_bull": since_choch_bull, "since_choch_bear": since_choch_bear}


def nearest_levels(close: np.ndarray, high: np.ndarray, low: np.ndarray, left: int = 3, right: int = 3,
                   memory: int = 30) -> tuple[np.ndarray, np.ndarray]:
    """Résistance confirmée la plus proche au-dessus et support le plus proche en dessous."""
    n = len(close)
    res = np.full(n, np.nan); sup = np.full(n, np.nan)
    highs: list[float] = []; lows: list[float] = []
    for t in range(n):
        j = t - right
        if j - left >= 0:
            if high[j] == high[j - left: t + 1].max():
                highs.append(high[j]); highs[:] = highs[-memory:]
            if low[j] == low[j - left: t + 1].min():
                lows.append(low[j]); lows[:] = lows[-memory:]
        c = close[t]
        above = [h for h in highs if h > c]
        below = [x for x in lows if x < c]
        if above:
            res[t] = min(above)
        if below:
            sup[t] = max(below)
    return res, sup


def candles(df: pd.DataFrame) -> dict[str, np.ndarray]:
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    po, pc = np.roll(o, 1), np.roll(c, 1)
    po[0], pc[0] = o[0], c[0]
    body = np.abs(c - o); rng = np.maximum(h - l, 1e-12)
    upper = h - np.maximum(o, c); lower = np.minimum(o, c) - l
    engulf = np.where((c > o) & (pc < po) & (c >= po) & (o <= pc) & (body > np.abs(pc - po)), 1,
             np.where((c < o) & (pc > po) & (c <= po) & (o >= pc) & (body > np.abs(pc - po)), -1, 0))
    pin = np.where((lower >= 2 * body) & (lower >= 0.55 * rng) & (upper <= 0.25 * rng), 1,
          np.where((upper >= 2 * body) & (upper >= 0.55 * rng) & (lower <= 0.25 * rng), -1, 0))
    ph, pl = np.roll(h, 1), np.roll(l, 1)
    inside = (h <= ph) & (l >= pl)
    inside[0] = False
    doji = body <= 0.1 * rng
    return {"engulf": engulf.astype(np.int8), "pin": pin.astype(np.int8), "inside": inside, "doji": doji}


def sr_levels(df: pd.DataFrame, atr_value: float, lookback: int = 300, left: int = 3, right: int = 3) -> list[dict]:
    """Niveaux S/R regroupés (pour l'affichage) : prix moyen, nombre de touches, type."""
    d = df.tail(lookback)
    h, l = d["high"].to_numpy(), d["low"].to_numpy()
    pts: list[tuple[float, str]] = []
    for j in range(left, len(d) - right):
        if h[j] == h[j - left: j + right + 1].max():
            pts.append((h[j], "R"))
        if l[j] == l[j - left: j + right + 1].min():
            pts.append((l[j], "S"))
    pts.sort()
    tol = 0.35 * atr_value
    clusters: list[list[tuple[float, str]]] = []
    for p in pts:
        if clusters and p[0] - clusters[-1][-1][0] <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    last = float(d["close"].iloc[-1])
    out = []
    for c in clusters:
        price = float(np.mean([x[0] for x in c]))
        out.append({"price": price, "touches": len(c), "type": "Résistance" if price > last else "Support"})
    out.sort(key=lambda x: (-x["touches"], abs(x["price"] - last)))
    return out[:8]
