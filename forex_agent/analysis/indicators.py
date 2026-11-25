"""Indicateurs techniques vectorisés. Chaque valeur à l'instant t n'utilise que les données <= t."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up, dn = wilder(d.clip(lower=0), n), wilder(-d.clip(upper=0), n)
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def adx(df: pd.DataFrame, n: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = wilder(true_range(df), n)
    pdi = 100 * wilder(plus_dm, n) / tr
    mdi = 100 * wilder(minus_dm, n) / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return wilder(dx.fillna(0), n), pdi, mdi


def bollinger(s: pd.Series, n: int = 20, k: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    mid = sma(s, n)
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    up, lo = mid + k * sd, mid - k * sd
    width = (up - lo) / mid
    return mid, up, lo, width


def macd(s: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    m = ema(s, fast) - ema(s, slow)
    sg = ema(m, sig)
    return m, sg, m - sg


def donchian(df: pd.DataFrame, n: int = 20) -> tuple[pd.Series, pd.Series]:
    return df["high"].rolling(n, min_periods=n).max(), df["low"].rolling(n, min_periods=n).min()


def stochastic(df: pd.DataFrame, n: int = 14, d: int = 3) -> tuple[pd.Series, pd.Series]:
    hh, ll = df["high"].rolling(n).max(), df["low"].rolling(n).min()
    k = 100 * (df["close"] - ll) / (hh - ll).replace(0, np.nan)
    return k.fillna(50), k.rolling(d).mean().fillna(50)


def efficiency_ratio(s: pd.Series, n: int = 20) -> pd.Series:
    """Kaufman : 1 = tendance parfaite, 0 = bruit pur."""
    change = (s - s.shift(n)).abs()
    vol = s.diff().abs().rolling(n).sum()
    return (change / vol.replace(0, np.nan)).fillna(0)


def rolling_pct_rank(s: pd.Series, n: int = 500) -> pd.Series:
    """Rang percentile de la valeur courante dans la fenêtre passée (0..1)."""
    return s.rolling(n, min_periods=max(20, n // 5)).rank(pct=True)


def zscore(s: pd.Series, n: int = 50) -> pd.Series:
    m, sd = s.rolling(n).mean(), s.rolling(n).std(ddof=0)
    return ((s - m) / sd.replace(0, np.nan)).fillna(0)
