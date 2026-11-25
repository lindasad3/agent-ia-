import numpy as np
import pandas as pd

from forex_agent.learning.backtest import ExecParams, PricePath


def path_from(rows):
    idx = pd.date_range("2025-01-06 08:00", periods=len(rows), freq="1min", tz="UTC")
    o, h, l, c = zip(*rows)
    return PricePath(pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=idx))


def test_sl_first_when_both_hit_same_minute():
    p = path_from([(1.0, 1.0, 1.0, 1.0), (1.0, 1.03, 0.97, 1.0)])
    r = p.simulate(0, "BUY", 0.98, 1.02, 0.0, ExecParams(be_trigger_r=0))
    assert r["reason"] == "SL" and np.isclose(r["r"], -1.0)


def test_take_profit_and_spread():
    p = path_from([(1.0, 1.0, 1.0, 1.0), (1.0, 1.021, 0.995, 1.02)])
    r = p.simulate(0, "BUY", 0.98, 1.02, 0.001, ExecParams(be_trigger_r=0))
    assert r["reason"] == "TP" and r["fill"] == 1.001 and r["r"] < 1.0


def test_break_even():
    rows = [(1.0, 1.0, 1.0, 1.0), (1.0, 1.021, 1.0, 1.02), (1.02, 1.02, 0.999, 1.0)]
    p = path_from(rows)
    r = p.simulate(0, "BUY", 0.98, 1.06, 0.0, ExecParams(be_trigger_r=1.0, be_lock_r=0.1))
    assert r["reason"] == "BE" and np.isclose(r["r"], 0.1)


def test_trailing_stop_sell():
    rows = [(1.0, 1.0, 1.0, 1.0), (1.0, 1.0, 0.95, 0.96), (0.96, 0.99, 0.95, 0.98)]
    p = path_from(rows)
    r = p.simulate(0, "SELL", 1.02, 0.80, 0.0, ExecParams(), trail=0.03)
    assert r["reason"] == "TRAIL" and np.isclose(r["exit"], 0.98) and r["r"] > 0
