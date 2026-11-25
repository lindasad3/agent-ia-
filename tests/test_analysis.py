import numpy as np
import pandas as pd

from forex_agent.analysis import indicators as ind
from forex_agent.analysis import smc
from forex_agent.analysis.features import build_features, features_from_m1, resample
from forex_agent.analysis.price_action import structure, swings


def test_indicators_bounds(m1):
    h1 = resample(m1, "H1")
    r = ind.rsi(h1["close"])
    assert r.between(0, 100).all()
    a = ind.atr(h1).dropna()
    assert (a > 0).all()
    adx, _, _ = ind.adx(h1)
    assert adx.dropna().between(0, 100).all()
    er = ind.efficiency_ratio(h1["close"])
    assert er.between(0, 1).all()


def test_resample_exact(m1):
    h1 = resample(m1, "H1")
    t = h1.index[10]
    seg = m1[(m1.index >= t) & (m1.index < t + pd.Timedelta(hours=1))]
    assert h1.loc[t, "high"] == seg["high"].max() and h1.loc[t, "low"] == seg["low"].min()
    assert h1.loc[t, "open"] == seg["open"].iloc[0] and h1.loc[t, "close"] == seg["close"].iloc[-1]


def test_no_lookahead(m1):
    """Les features d'une barre ne changent pas quand on ajoute des données futures."""
    full = features_from_m1(m1, "H1")
    cut = full.index[len(full) - 80]
    part = features_from_m1(m1[m1.index < cut + pd.Timedelta(hours=1)], "H1")
    t = part.index[-1]
    assert t == cut
    for c in ["regime", "structure", "last_sh", "last_sl", "fvg_bull_top", "ob_bull_top", "htf1_trend", "htf2_trend",
              "atr", "rsi", "vp_poc", "since_sweep_bull", "pd_pos", "asia_high"]:
        a, b = part.loc[t, c], full.loc[t, c]
        if isinstance(a, float) and np.isnan(a):
            assert np.isnan(b), c
        else:
            assert a == b or np.isclose(a, b), c


def test_swing_confirmation_lag():
    high = np.array([1, 2, 3, 6, 3, 2, 1, 2, 3, 2, 1], dtype=float)
    low = high - 0.5
    sw = swings(high, low, 2, 2)
    # Le sommet à l'indice 3 n'est connu qu'à l'indice 5
    assert np.isnan(sw["last_sh"][4]) and sw["last_sh"][5] == 6


def test_bos_choch():
    close = np.array([1, 2, 3, 2, 1.5, 2.5, 3.5, 2, 1, 0.5], dtype=float)
    sw = {"last_sh": np.array([np.nan, np.nan, np.nan, np.nan, 3, 3, 3, 3, 3, 3]),
          "last_sl": np.array([np.nan] * 6 + [1.5, 1.5, 1.5, 1.5]),
          "last_sh_idx": np.array([-1, -1, -1, -1, 2, 2, 2, 2, 2, 2]),
          "last_sl_idx": np.array([-1] * 6 + [4, 4, 4, 4])}
    st = structure(close, sw)
    assert st["bos"][6] == 1            # cassure du sommet 3 -> BOS haussier
    assert st["choch"][8] == -1         # cassure du creux 1.5 contre la tendance -> CHOCH baissier


def test_fvg_detection():
    high = np.array([1.0, 1.1, 1.5, 1.6, 1.7])
    low = np.array([0.9, 1.0, 1.2, 1.5, 1.6])
    close = np.array([1.0, 1.1, 1.45, 1.55, 1.65])
    atr = np.full(5, 0.1)
    out = smc.fvg(high, low, close, atr)
    assert out["fvg_bull_new"][2]  # low[2]=1.2 > high[0]=1.0
    assert out["fvg_bull_bot"][2] == 1.0 and out["fvg_bull_top"][2] == 1.2


def test_liquidity_sweep():
    high = np.array([1.0, 1.0, 1.0]); low = np.array([0.9, 0.85, 0.9]); close = np.array([0.95, 0.95, 0.95])
    last_sl = np.array([0.88, 0.88, 0.88]); last_sh = np.array([1.2, 1.2, 1.2])
    out = smc.liquidity(high, low, close, np.full(3, 0.05), last_sh, last_sl, last_sh, last_sl)
    assert out["sweep_bull"][1] and not out["sweep_bull"][0]


def test_build_features_columns(m1):
    f = features_from_m1(m1, "H1")
    for c in ["regime", "vol_state", "session", "htf1_trend", "htf2_trend", "fvg_bull_top", "ob_bear_top", "bsl", "ssl",
              "vp_poc", "delta_proxy", "absorption", "dc55_high", "asia_high"]:
        assert c in f.columns
    assert set(f["regime"].unique()) <= {"TREND_UP", "TREND_DOWN", "RANGE", "TRANSITION"}
