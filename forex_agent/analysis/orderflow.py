"""Order flow.

Deux sources :
  1. Quantower (temps réel) : delta réel, delta cumulé, DOM, volume profile, déséquilibres — reçus du
     plugin C# `quantower_bridge` via l'API (/api/quantower/ingest) et stockés dans `OrderFlowStore`.
  2. Proxy barres (historique / absence de Quantower) : delta estimé par la position de clôture dans la
     bougie, volume relatif, absorption, volume profile glissant. Toujours étiqueté « proxy ».

Note Forex : le spot FX n'a pas de carnet centralisé. Le vrai order flow vient des futures CME
(6E, 6B, 6J, 6A, 6C, 6S, GC) via le flux connecté à Quantower ; le mapping est dans la config.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


def bar_proxies(df: pd.DataFrame, atr: pd.Series) -> pd.DataFrame:
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    clv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng  # -1..1
    vol = df["volume"].astype(float)
    delta = (clv.fillna(0) * vol)
    out = pd.DataFrame(index=df.index)
    out["delta_proxy"] = delta
    out["cum_delta_50"] = delta.rolling(50, min_periods=10).sum()
    vm, vs = vol.rolling(50, min_periods=20).mean(), vol.rolling(50, min_periods=20).std(ddof=0)
    out["vol_z"] = ((vol - vm) / vs.replace(0, np.nan)).fillna(0)
    dm, ds = delta.rolling(50, min_periods=20).mean(), delta.rolling(50, min_periods=20).std(ddof=0)
    out["delta_z"] = ((delta - dm) / ds.replace(0, np.nan)).fillna(0)
    small = (df["high"] - df["low"]) < 0.6 * atr
    out["absorption"] = (out["vol_z"] > 2.0) & small
    # Divergence : nouveau plus bas 20 barres sans nouveau plus bas du delta cumulé (et inversement)
    ll = df["low"] <= df["low"].rolling(20).min()
    hh = df["high"] >= df["high"].rolling(20).max()
    cd = out["cum_delta_50"]
    out["delta_div_bull"] = ll & (cd > cd.rolling(20).min())
    out["delta_div_bear"] = hh & (cd < cd.rolling(20).max())
    return out


def volume_profile(df: pd.DataFrame, window: int = 96, bins: int = 30, step: int = 4,
                   value_area: float = 0.7) -> pd.DataFrame:
    """POC / VAH / VAL glissants calculés sur les `window` barres passées (rafraîchis toutes les `step`)."""
    n = len(df)
    tp = ((df["high"] + df["low"] + df["close"]) / 3).to_numpy()
    vol = df["volume"].to_numpy(dtype=float)
    poc = np.full(n, np.nan); vah = np.full(n, np.nan); val = np.full(n, np.nan)
    for t in range(window, n, step):
        p, v = tp[t - window + 1: t + 1], vol[t - window + 1: t + 1]
        if v.sum() <= 0:
            continue
        hist, edges = np.histogram(p, bins=bins, weights=v)
        centers = (edges[:-1] + edges[1:]) / 2
        k = int(hist.argmax())
        order = np.argsort(hist)[::-1]
        cum, chosen = 0.0, []
        for idx in order:
            chosen.append(idx); cum += hist[idx]
            if cum >= value_area * hist.sum():
                break
        poc[t], vah[t], val[t] = centers[k], centers[max(chosen)], centers[min(chosen)]
    out = pd.DataFrame({"vp_poc": poc, "vp_vah": vah, "vp_val": val}, index=df.index)
    return out.ffill()


@dataclass
class OrderFlowSnapshot:
    symbol: str
    ts: float
    source: str = "quantower"
    last: float = 0.0
    delta: float = 0.0
    cum_delta: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    poc: float | None = None
    vah: float | None = None
    val: float | None = None
    dom_bid: float = 0.0
    dom_ask: float = 0.0
    imbalances: list[dict] = field(default_factory=list)
    absorption: str | None = None  # "bull" | "bear" | None

    @property
    def dom_imbalance(self) -> float:
        tot = self.dom_bid + self.dom_ask
        return (self.dom_bid - self.dom_ask) / tot if tot > 0 else 0.0


class OrderFlowStore:
    """Dernier snapshot Quantower par symbole (thread-safe)."""

    def __init__(self, max_age_sec: float = 30.0):
        self._data: dict[str, OrderFlowSnapshot] = {}
        self._lock = threading.Lock()
        self.max_age_sec = max_age_sec
        self.last_update: float | None = None

    def update(self, snap: OrderFlowSnapshot) -> None:
        with self._lock:
            self._data[snap.symbol.upper()] = snap
            self.last_update = time.time()

    def get(self, symbol: str) -> OrderFlowSnapshot | None:
        with self._lock:
            s = self._data.get(symbol.upper())
        if s and time.time() - s.ts <= self.max_age_sec:
            return s
        return None

    @property
    def connected(self) -> bool:
        return self.last_update is not None and time.time() - self.last_update <= self.max_age_sec

    def all(self) -> dict[str, OrderFlowSnapshot]:
        with self._lock:
            return dict(self._data)
