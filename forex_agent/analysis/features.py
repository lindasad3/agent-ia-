"""Construction de la table de features multi-timeframe utilisée par les stratégies, le cerveau et le ML.

Même code en backtest et en temps réel : la ligne t ne contient que de l'information disponible à la
clôture de la barre t (HTF alignés sur leur heure de clôture, pivots confirmés, fenêtres glissantes).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind
from . import price_action as pa
from . import smc
from .orderflow import bar_proxies, volume_profile
from .regime import classify

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def resample(m1: pd.DataFrame, tf: str) -> pd.DataFrame:
    if tf == "M1":
        return m1
    rule = f"{TF_MINUTES[tf]}min"
    agg = m1.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return agg.dropna(subset=["open"])


def _base(df: pd.DataFrame) -> pd.DataFrame:
    f = df[["open", "high", "low", "close", "volume"]].astype(float).copy()
    c = f["close"]
    f["ema20"], f["ema50"], f["ema200"] = ind.ema(c, 20), ind.ema(c, 50), ind.ema(c, 200)
    f["ema50_slope"] = (f["ema50"] - f["ema50"].shift(5))
    f["atr"] = ind.atr(f, 14)
    f["atr_rank"] = ind.rolling_pct_rank(f["atr"], 500)
    f["rsi"] = ind.rsi(c, 14)
    f["adx"], f["pdi"], f["mdi"] = ind.adx(f, 14)
    f["bb_mid"], f["bb_up"], f["bb_lo"], f["bb_width"] = ind.bollinger(c, 20, 2.0)
    f["bb_width_rank"] = ind.rolling_pct_rank(f["bb_width"], 500)
    f["macd"], f["macd_sig"], f["macd_hist"] = ind.macd(c)
    f["dc_high"], f["dc_low"] = ind.donchian(f, 20)
    f["stoch_k"], f["stoch_d"] = ind.stochastic(f)
    f["er"] = ind.efficiency_ratio(c, 20)
    return f


def _htf(df: pd.DataFrame, tf: str, prefix: str) -> pd.DataFrame:
    f = _base(df)
    h, l, c = f["high"].to_numpy(), f["low"].to_numpy(), f["close"].to_numpy()
    sw = pa.swings(h, l, 3, 3)
    st = pa.structure(c, sw)
    f = classify(f)
    out = pd.DataFrame(index=f.index)
    out[f"{prefix}_trend"] = np.sign(f["ema50"] - f["ema200"]).fillna(0)
    out[f"{prefix}_structure"] = st["structure"]
    out[f"{prefix}_regime"] = f["regime"]
    out[f"{prefix}_adx"] = f["adx"]
    out[f"{prefix}_rsi"] = f["rsi"]
    out[f"{prefix}_close_vs_ema50"] = np.sign(f["close"] - f["ema50"]).fillna(0)
    out["close_time"] = out.index + pd.Timedelta(minutes=TF_MINUTES[tf])
    return out


def build_features(entry: pd.DataFrame, htfs: dict[str, pd.DataFrame], entry_tf: str = "M15") -> pd.DataFrame:
    f = _base(entry)
    o, h, l, c = (f[k].to_numpy() for k in ("open", "high", "low", "close"))
    atr = f["atr"].to_numpy()
    sw = pa.swings(h, l, 3, 3)
    st = pa.structure(c, sw)
    for k, v in {**sw, **st}.items():
        f[k] = v
    f["near_res"], f["near_sup"] = pa.nearest_levels(c, h, l)
    for k, v in pa.candles(f).items():
        f[k] = v
    for k, v in smc.fvg(h, l, c, atr).items():
        f[k] = v
    for k, v in smc.order_blocks(o, h, l, c, st["bos"], st["choch"], sw["last_sh_idx"], sw["last_sl_idx"]).items():
        f[k] = v
    for k, v in smc.liquidity(h, l, c, atr, sw["last_sh"], sw["last_sl"], sw["prev_sh"], sw["prev_sl"]).items():
        f[k] = v
    f = f.join(bar_proxies(f, f["atr"])).join(volume_profile(f))
    f = classify(f)
    # Alignement HTF sur l'heure de clôture : pas de fuite d'information
    f["close_time"] = f.index + pd.Timedelta(minutes=TF_MINUTES[entry_tf])
    f = f.reset_index().rename(columns={f.index.name or "index": "time"})
    if "time" not in f.columns:
        f = f.rename(columns={f.columns[0]: "time"})
    # htf1 = contexte intermédiaire, htf2 = contexte supérieur (ex. M15 -> H1/H4, H1 -> H4/D1)
    for k, (tf, df) in enumerate(htfs.items(), start=1):
        hf = _htf(df, tf, f"htf{k}").reset_index(drop=True).sort_values("close_time")
        f = pd.merge_asof(f.sort_values("close_time"), hf, on="close_time", direction="backward")
    f = f.set_index("time")
    f["hour"] = f.index.hour
    f["dow"] = f.index.day_of_week
    f["dc55_high"], f["dc55_low"] = ind.donchian(f, 55)
    day = f.index.normalize()
    asia = f[f["hour"] < 7].groupby(day[f["hour"] < 7]).agg(asia_high=("high", "max"), asia_low=("low", "min"))
    f = f.join(asia, on=day)
    late = f["hour"] < 7
    f.loc[late, ["asia_high", "asia_low"]] = np.nan  # range non terminé avant 07:00 UTC
    return f


HTF_FOR = {"M5": ("M30", "H4"), "M15": ("H1", "H4"), "M30": ("H4", "D1"), "H1": ("H4", "D1"), "H4": ("D1", "D1")}


def features_from_m1(m1: pd.DataFrame, entry_tf: str = "M15", htf: tuple[str, ...] | None = None) -> pd.DataFrame:
    htf = htf or HTF_FOR[entry_tf]
    entry = resample(m1, entry_tf)
    return build_features(entry, {tf: resample(m1, tf) for tf in htf}, entry_tf)
