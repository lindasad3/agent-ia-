"""Connecteur MT5 testé contre un faux module MetaTrader5 (le vrai n'existe que sous Windows)."""
from types import SimpleNamespace as NS

from forex_agent.execution.mt5_broker import MT5Broker
from forex_agent.models import OrderRequest


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_REAL = 0, 2
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = 1, 6
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    ORDER_TIME_GTC = 0
    DEAL_ENTRY_IN, DEAL_ENTRY_OUT = 0, 1
    DEAL_TYPE_BUY = 0
    DEAL_REASON_SL, DEAL_REASON_TP = 4, 5
    TIMEFRAME_H1 = 16385

    def __init__(self, trade_mode=0, retcodes=(10009,), names=None, filling_mode=2, tick_time=1_700_000_000):
        self.trade_mode = trade_mode
        self.retcodes = list(retcodes)
        self.positions = {}
        self.sent = []
        self.names = names
        self.filling_mode = filling_mode
        self.tick_time = tick_time

    def initialize(self, **kw): return True
    def shutdown(self): pass
    def last_error(self): return (0, "ok")
    def account_info(self): return NS(trade_mode=self.trade_mode, balance=1000.0, equity=1000.0, margin_free=900.0,
                                      currency="USD", leverage=100, login=123, server="Demo", name="test")
    def terminal_info(self): return NS(connected=True)
    def symbols_get(self): return [NS(name=n) for n in (self.names or ["EURUSD", "GBPUSD", "XAUUSD"])]

    def symbol_info(self, s):
        if self.names is not None and s not in self.names:
            return None
        return NS(visible=True, digits=5, point=0.00001, volume_min=0.01, volume_max=50, volume_step=0.01,
                  trade_contract_size=100000, trade_tick_value=1.0, trade_tick_size=0.00001, filling_mode=self.filling_mode)
    def symbol_select(self, s, v): return True
    def symbol_info_tick(self, s): return NS(bid=1.1000, ask=1.1001, time=self.tick_time)

    def order_check(self, r):
        # Simule un broker qui n'accepte que FOK
        if self.filling_mode == 0 and r.get("type_filling") != self.ORDER_FILLING_FOK and "position" not in r:
            return NS(retcode=10030, comment="Unsupported filling mode")
        return NS(retcode=0, comment="")

    def order_send(self, r):
        self.sent.append(r)
        if r["action"] == self.TRADE_ACTION_SLTP:
            p = self.positions[r["position"]]
            p.sl, p.tp = r["sl"], r["tp"]
            return NS(retcode=10009, comment="")
        if "position" in r:
            self.positions.pop(r["position"], None)
            return NS(retcode=10009, deal=9, price=r["price"], volume=r["volume"], comment="")
        code = self.retcodes.pop(0) if self.retcodes else 10009
        if code != 10009:
            return NS(retcode=code, comment="err", order=0, deal=0)
        t = 5000 + len(self.sent)
        self.positions[t] = NS(ticket=t, symbol=r["symbol"], type=r["type"], volume=r["volume"], price_open=r["price"] + 0.00002,
                               sl=r["sl"], tp=r["tp"], time=1_700_000_000, profit=0.0, swap=0.0, comment=r["comment"],
                               magic=r["magic"], price_current=r["price"])
        return NS(retcode=10009, order=t, deal=t + 1, price=r["price"], volume=r["volume"], comment="done")

    def positions_get(self, ticket=None):
        if ticket is not None:
            return [self.positions[ticket]] if ticket in self.positions else []
        return list(self.positions.values())

    def history_deals_get(self, *a, **kw): return []
    def orders_get(self): return []


def broker(symbols=None, offset=None, **kw):
    b = MT5Broker(123, "x", "Demo", mt5_module=FakeMT5(**kw), symbols=symbols, server_utc_offset=offset)
    assert b.connect()
    return b


def req(vol=0.10):
    return OrderRequest("EURUSD", "BUY", vol, 1.0980, 1.1040)


def test_execution_verified_with_slippage():
    b = broker()
    r = b.place_market(req())
    assert r.ok and r.verified and r.ticket in b.mt5.positions
    assert abs(r.slippage_pips - 0.2) < 1e-6  # fill 2 points au-dessus de l'ask
    assert b.mt5.sent[0]["type_filling"] == FakeMT5.ORDER_FILLING_IOC


def test_requote_retried_then_success():
    b = broker(retcodes=(10004, 10009))
    r = b.place_market(req())
    assert r.ok and r.attempts == 2


def test_rejection_not_retried():
    b = broker(retcodes=(10019,))
    r = b.place_market(req())
    assert not r.ok and r.retcode == 10019 and "insuffisants" in r.message


def test_invalid_volume():
    r = broker().place_market(req(vol=0.015))
    assert not r.ok and "Volume invalide" in r.message


def test_live_account_refused():
    b = MT5Broker(1, "x", "Live", mt5_module=FakeMT5(trade_mode=2))
    assert not b.connect() and "RÉEL" in b.last_error


def test_modify_and_close():
    b = broker()
    r = b.place_market(req())
    m = b.modify(r.ticket, 1.1001, None)
    assert m.ok and m.verified and m.sl == 1.1001
    c = b.close(r.ticket)
    assert c.ok and c.verified and r.ticket not in b.mt5.positions


def test_symbol_suffix_and_alias_detection():
    b = broker(symbols=["EURUSD", "XAUUSD", "USDJPY"], names=["EURUSDm", "GBPUSDm", "GOLDm", "EURUSD.pro.long"])
    assert b.symbol_map["EURUSD"] == "EURUSDm" and b.symbol_map["XAUUSD"] == "GOLDm"
    assert b.missing_symbols == ["USDJPY"]
    r = b.place_market(req())
    assert r.ok and b.mt5.sent[-1]["symbol"] == "EURUSDm"
    assert b.positions()[0].symbol == "EURUSD"  # renvoyé sous le nom de l'agent


def test_filling_mode_fallback():
    b = broker(filling_mode=0)  # le symbole n'annonce rien, le broker n'accepte que FOK
    r = b.place_market(req())
    assert r.ok and b.mt5.sent[-1]["type_filling"] == FakeMT5.ORDER_FILLING_FOK


def test_server_offset_configured_and_detected():
    import time as _t
    assert broker(offset=3).server_offset.total_seconds() == 3 * 3600
    fresh = broker(tick_time=int(_t.time()) + 2 * 3600 + 5)
    assert fresh.server_offset.total_seconds() == 2 * 3600 and fresh.offset_source == "détecté"
    stale = broker(tick_time=int(_t.time()) - 40 * 3600 - 1234)  # week-end : dernier tick vendredi
    assert stale.server_offset.total_seconds() == 0 and "non détecté" in stale.offset_source


def test_diagnostics():
    d = broker().diagnostics()
    assert d["account"]["demo"] is True and d["connected"] is True
