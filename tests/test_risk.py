from datetime import datetime, timezone

import pytest

from forex_agent.models import AccountInfo, Position, Signal, Tick
from forex_agent.risk.engine import RiskConfig, RiskEngine, RiskState
from forex_agent.symbols import get_spec, value_per_price_unit

NOW = datetime(2025, 3, 3, 10, tzinfo=timezone.utc)


def vfn(spec, px):
    return value_per_price_unit(spec, px, "USD", {"USDJPY": 150.0})


def sig(symbol="EURUSD", d="BUY", entry=1.1000, sl=1.0980, tp=1.1040, score=70):
    return Signal(symbol, "H1", d, "test", entry, sl, tp, score, 0.5, abs(tp - entry) / abs(entry - sl), 0.2, "RANGE",
                  [], "", created_at=NOW)


def acct(eq=10_000):
    return AccountInfo(eq, eq, eq)


def state(eq=10_000, **kw):
    return RiskState(kw.get("day", eq), kw.get("week", eq), kw.get("peak", eq), kw.get("halted", False))


def tick(sym="EURUSD", bid=1.1000, spread=0.00008):
    return Tick(sym, bid, bid + spread, NOW)


def checks(rd):
    return {c.name: c.ok for c in rd.checks}


def test_sizing_eurusd():
    rd = RiskEngine().evaluate(sig(), acct(), [], tick(), state(), vfn, now=NOW)
    assert rd.approved, rd.rejections
    # 1 % de 10 000 = 100 $ ; SL ~20.8 pips depuis l'ask -> ~0.48 lot
    assert 0.45 <= rd.volume <= 0.50
    assert rd.risk_amount <= 100.0 + 1e-6


def test_sizing_usdjpy_and_gold():
    s = sig("USDJPY", entry=150.00, sl=149.70, tp=150.60)
    rd = RiskEngine().evaluate(s, acct(), [], Tick("USDJPY", 150.00, 150.01, NOW), state(), vfn, now=NOW)
    assert rd.approved and rd.risk_amount <= 100 + 1e-6 and rd.volume > 0.3
    g = sig("XAUUSD", entry=2900.0, sl=2885.0, tp=2930.0)
    rd = RiskEngine().evaluate(g, acct(), [], Tick("XAUUSD", 2900.0, 2900.25, NOW), state(), vfn, now=NOW)
    assert rd.approved and rd.risk_amount <= 100 + 1e-6 and rd.volume == pytest.approx(0.06, abs=0.01)


def test_reject_low_rr():
    rd = RiskEngine().evaluate(sig(tp=1.1015), acct(), [], tick(), state(), vfn, now=NOW)
    assert not rd.approved and not checks(rd)["Ratio Risk/Reward"]


def test_reject_spread():
    rd = RiskEngine().evaluate(sig(), acct(), [], tick(spread=0.0005), state(), vfn, now=NOW)
    assert not rd.approved and not checks(rd)["Spread"]


def test_reject_daily_loss_and_drawdown():
    rd = RiskEngine().evaluate(sig(), acct(9_650), [], tick(), state(9_650, day=10_000, week=10_000, peak=10_000), vfn, now=NOW)
    assert not checks(rd)["Perte journalière"]
    rd = RiskEngine().evaluate(sig(), acct(8_900), [], tick(), state(8_900, day=8_900, week=8_900, peak=10_000), vfn, now=NOW)
    assert not checks(rd)["Drawdown maximal"]


def test_reject_max_positions_duplicates_exposure():
    pos = [Position(1, "EURUSD", "BUY", 0.5, 1.09, 1.088, 1.095, NOW), Position(2, "GBPUSD", "BUY", 0.3, 1.27, 1.268, 1.275, NOW),
           Position(3, "AUDUSD", "BUY", 0.3, 0.65, 0.648, 0.655, NOW)]
    rd = RiskEngine().evaluate(sig(), acct(), pos, tick(), state(), vfn, now=NOW)
    c = checks(rd)
    assert not c["Positions simultanées"] and not c["Protection doublons"] and not c["Exposition par devise"]


def test_halted_and_news():
    rd = RiskEngine().evaluate(sig(), acct(), [], tick(), state(halted=True), vfn, now=NOW, news_block="NFP")
    c = checks(rd)
    assert not c["Arrêt d'urgence"] and not c["Calendrier économique"]


def test_volume_too_small_rejected():
    s = sig(sl=1.0850, tp=1.1300)  # SL 150 pips, compte minuscule
    rd = RiskEngine(RiskConfig(max_sl_pips={"DEFAULT": 300})).evaluate(s, acct(50), [], tick(), state(50), vfn, now=NOW)
    assert not checks(rd)["Volume calculable"]


def test_pip_specs():
    assert get_spec("EURUSD").pip_size == 0.0001 and get_spec("USDJPY").pip_size == 0.01
    assert get_spec("XAUUSD").contract_size == 100
