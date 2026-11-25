"""Indicateurs de performance calculés à partir des trades réellement enregistrés."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _safe(x: float) -> float | None:
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return None
    return round(float(x), 4)


def compute(trades: pd.DataFrame, initial_capital: float | None = None) -> dict[str, Any]:
    """`trades` : colonnes pnl, [pnl_r, close_time, strategy, symbol, timeframe, regime]."""
    if trades is None or len(trades) == 0:
        return {"n_trades": 0, "net_profit": 0.0, "win_rate": None, "profit_factor": None, "expectancy": None,
                "expectancy_r": None, "max_drawdown": 0.0, "max_drawdown_pct": 0.0, "avg_win": None,
                "avg_loss": None, "equity_curve": [], "by_strategy": {}, "by_symbol": {}, "by_timeframe": {},
                "by_regime": {}}
    t = trades.copy()
    if "close_time" in t:
        t = t.sort_values("close_time")
    pnl = t["pnl"].astype(float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gross_w, gross_l = wins.sum(), -losses.sum()
    eq0 = initial_capital if initial_capital is not None else 0.0
    equity = eq0 + pnl.cumsum()
    peak = np.maximum.accumulate(np.concatenate([[eq0], equity.to_numpy()]))[1:]
    dd = peak - equity.to_numpy()
    max_dd = float(dd.max()) if len(dd) else 0.0
    max_dd_pct = float((dd / np.where(peak > 0, peak, np.nan)).max() * 100) if initial_capital else None
    out: dict[str, Any] = {
        "n_trades": int(len(t)),
        "net_profit": round(float(pnl.sum()), 2),
        "return_pct": _safe(100 * pnl.sum() / initial_capital) if initial_capital else None,
        "win_rate": _safe(len(wins) / len(t)),
        "profit_factor": _safe(gross_w / gross_l) if gross_l > 0 else None,
        "expectancy": _safe(pnl.mean()),
        "expectancy_r": _safe(t["pnl_r"].mean()) if "pnl_r" in t else None,
        "avg_win": _safe(wins.mean()) if len(wins) else None,
        "avg_loss": _safe(losses.mean()) if len(losses) else None,
        "best": _safe(pnl.max()), "worst": _safe(pnl.min()),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": _safe(max_dd_pct) if max_dd_pct is not None else None,
        "max_consecutive_losses": int(_max_streak(pnl.to_numpy() <= 0)),
    }
    if "close_time" in t:
        out["equity_curve"] = [{"t": pd.Timestamp(ct).isoformat(), "equity": round(float(e), 2)}
                               for ct, e in zip(t["close_time"], equity)]
        if "pnl_r" in t and len(t) > 5:
            daily = t.set_index(pd.to_datetime(t["close_time"]))["pnl_r"].resample("D").sum()
            daily = daily[daily != 0]
            out["sharpe_r_daily"] = _safe(daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else None
    for key, col in (("by_strategy", "strategy"), ("by_symbol", "symbol"), ("by_timeframe", "timeframe"),
                     ("by_regime", "regime")):
        out[key] = {}
        if col in t:
            for k, g in t.groupby(col):
                gp = g["pnl"].astype(float)
                gl = -gp[gp <= 0].sum()
                out[key][str(k)] = {"n": int(len(g)), "net": round(float(gp.sum()), 2),
                                    "win_rate": _safe((gp > 0).mean()),
                                    "profit_factor": _safe(gp[gp > 0].sum() / gl) if gl > 0 else None,
                                    "expectancy_r": _safe(g["pnl_r"].mean()) if "pnl_r" in g else None}
    return out


def _max_streak(mask: np.ndarray) -> int:
    best = cur = 0
    for m in mask:
        cur = cur + 1 if m else 0
        best = max(best, cur)
    return best
