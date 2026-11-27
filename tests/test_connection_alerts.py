"""Alertes de connexion : panne au démarrage signalée, une seule alerte par panne, retour annoncé."""
import pandas as pd

from forex_agent.agent.runtime import AgentRuntime
from forex_agent.execution.paper import PaperBroker, ReplayMarket
from forex_agent.storage.db import Store
from forex_agent.strategies.library import default_strategies
from tests.conftest import make_m1


class FlakyBroker(PaperBroker):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.up = False
        self.last_error = ""

    def connect(self):
        self._connected = self.up
        if not self.up:
            self.last_error = "login(123) a échoué : (-6, 'Terminal: Authorization failed')"
        return self.up

    def is_connected(self):
        return self.up and self._connected


class Engine:
    strategies, perf = default_strategies(), {}


class Spy:
    enabled, connected, sent, last_error = True, True, 0, ""

    def __init__(self):
        self.msgs = []
        self.handlers, self.callback_handler, self.on_security = {}, None, None

    def send(self, text, buttons=None):
        self.msgs.append(text)

    def start(self):
        pass


def test_connection_alerts(tmp_path):
    m1 = make_m1(days=20)
    b = FlakyBroker(ReplayMarket({"EURUSD": m1}, (m1.index[0] + pd.Timedelta(days=15)).to_pydatetime(), ("H1", "H4", "D1")))
    tg = Spy()
    rt = AgentRuntime(b, Store(f"sqlite:///{tmp_path}/a.db"), Engine(), {"version": "t"}, telegram=tg, symbols=["EURUSD"])
    rt.simulation  # paper : pas d'attente entre tentatives
    rt.start(background=False)
    assert any("Connexion impossible" in m and "Authorization failed" in m for m in tg.msgs)
    assert any("NON connecté" in m for m in tg.msgs)
    n = len(tg.msgs)
    rt.cycle(); rt.cycle()                       # toujours en panne : pas de nouvelle alerte (rappel horaire seulement)
    assert len(tg.msgs) == n
    b.up = True
    rt.cycle()
    assert any("rétablie" in m for m in tg.msgs[n:])
