"""Smart Money Concepts : Fair Value Gaps, Order Blocks, Liquidity Sweeps, BSL/SSL, Premium/Discount.

Tout est calculé barre par barre avec les seules informations disponibles à la clôture de la barre.
"""
from __future__ import annotations

import numpy as np


def fvg(high: np.ndarray, low: np.ndarray, close: np.ndarray, atr: np.ndarray, min_gap_atr: float = 0.1,
        memory: int = 12) -> dict[str, np.ndarray]:
    n = len(high)
    out = {k: np.full(n, np.nan) for k in ("fvg_bull_top", "fvg_bull_bot", "fvg_bear_top", "fvg_bear_bot")}
    out["fvg_bull_new"] = np.zeros(n, dtype=bool); out["fvg_bear_new"] = np.zeros(n, dtype=bool)
    out["fvg_bull_retest"] = np.zeros(n, dtype=bool); out["fvg_bear_retest"] = np.zeros(n, dtype=bool)
    bulls: list[list[float]] = []; bears: list[list[float]] = []  # [top, bot]
    for t in range(n):
        a = atr[t] if not np.isnan(atr[t]) else 0.0
        # mitigation / retest des gaps existants
        for g in bulls:
            if low[t] <= g[0] and close[t] > g[1]:
                out["fvg_bull_retest"][t] = True
        for g in bears:
            if high[t] >= g[1] and close[t] < g[0]:
                out["fvg_bear_retest"][t] = True
        bulls = [g for g in bulls if low[t] > g[1]]
        bears = [g for g in bears if high[t] < g[0]]
        if t >= 2 and a > 0:
            if low[t] - high[t - 2] >= min_gap_atr * a:
                bulls.append([low[t], high[t - 2]]); out["fvg_bull_new"][t] = True
            if low[t - 2] - high[t] >= min_gap_atr * a:
                bears.append([low[t - 2], high[t]]); out["fvg_bear_new"][t] = True
        bulls, bears = bulls[-memory:], bears[-memory:]
        c = close[t]
        below = [g for g in bulls if g[0] <= c]
        above = [g for g in bears if g[1] >= c]
        if below:
            g = max(below, key=lambda x: x[0]); out["fvg_bull_top"][t], out["fvg_bull_bot"][t] = g
        if above:
            g = min(above, key=lambda x: x[1]); out["fvg_bear_top"][t], out["fvg_bear_bot"][t] = g
    return out


def order_blocks(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, bos: np.ndarray,
                 choch: np.ndarray, last_sh_idx: np.ndarray, last_sl_idx: np.ndarray) -> dict[str, np.ndarray]:
    """OB haussier = dernière bougie baissière avant l'impulsion qui casse la structure (et inversement)."""
    n = len(close)
    out = {k: np.full(n, np.nan) for k in ("ob_bull_top", "ob_bull_bot", "ob_bear_top", "ob_bear_bot")}
    out["ob_bull_retest"] = np.zeros(n, dtype=bool); out["ob_bear_retest"] = np.zeros(n, dtype=bool)
    bull = bear = None
    for t in range(n):
        brk = bos[t] + choch[t]
        if brk > 0:
            start = max(0, int(last_sl_idx[t]) if last_sl_idx[t] >= 0 else t - 20)
            for k in range(t - 1, start - 1, -1):
                if close[k] < open_[k]:
                    bull = [high[k], low[k]]; break
        elif brk < 0:
            start = max(0, int(last_sh_idx[t]) if last_sh_idx[t] >= 0 else t - 20)
            for k in range(t - 1, start - 1, -1):
                if close[k] > open_[k]:
                    bear = [high[k], low[k]]; break
        if bull is not None:
            if close[t] < bull[1]:
                bull = None
            else:
                if low[t] <= bull[0] and brk <= 0:
                    out["ob_bull_retest"][t] = True
                out["ob_bull_top"][t], out["ob_bull_bot"][t] = bull
        if bear is not None:
            if close[t] > bear[0]:
                bear = None
            else:
                if high[t] >= bear[1] and brk >= 0:
                    out["ob_bear_retest"][t] = True
                out["ob_bear_top"][t], out["ob_bear_bot"][t] = bear
    return out


def liquidity(high: np.ndarray, low: np.ndarray, close: np.ndarray, atr: np.ndarray, last_sh: np.ndarray,
              last_sl: np.ndarray, prev_sh: np.ndarray, prev_sl: np.ndarray) -> dict[str, np.ndarray]:
    n = len(close)
    sweep_bull = (low < last_sl) & (close > last_sl)   # prise de liquidité côté vendeur (SSL)
    sweep_bear = (high > last_sh) & (close < last_sh)  # prise de liquidité côté acheteur (BSL)
    since_sb = np.full(n, 9999); since_sbe = np.full(n, 9999)
    sweep_low = np.full(n, np.nan); sweep_high = np.full(n, np.nan)
    a = b = 9999; lo = hi = np.nan
    for t in range(n):
        a += 1; b += 1
        if sweep_bull[t]:
            a, lo = 0, low[t]
        if sweep_bear[t]:
            b, hi = 0, high[t]
        since_sb[t], since_sbe[t], sweep_low[t], sweep_high[t] = a, b, lo, hi
    tol = 0.15 * np.nan_to_num(atr)
    eq_highs = np.abs(last_sh - prev_sh) <= tol   # BSL : sommets égaux
    eq_lows = np.abs(last_sl - prev_sl) <= tol    # SSL : creux égaux
    bsl = np.where(eq_highs, np.maximum(last_sh, prev_sh), last_sh)
    ssl = np.where(eq_lows, np.minimum(last_sl, prev_sl), last_sl)
    rng = last_sh - last_sl
    pd_pos = np.where(rng > 0, (close - last_sl) / np.where(rng > 0, rng, 1), np.nan)
    return {"sweep_bull": sweep_bull, "sweep_bear": sweep_bear, "since_sweep_bull": since_sb,
            "since_sweep_bear": since_sbe, "sweep_low": sweep_low, "sweep_high": sweep_high,
            "bsl": bsl, "ssl": ssl, "eq_highs": eq_highs, "eq_lows": eq_lows, "pd_pos": pd_pos}
