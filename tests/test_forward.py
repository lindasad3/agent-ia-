"""Forward test : pipeline complet sur données fournies par un chargeur (MT5 en production)."""
import json
from datetime import timedelta

import pandas as pd

from forex_agent.analysis.features import resample
from forex_agent.learning import registry
from forex_agent.learning.forward import run_forward
from tests.conftest import make_m1


def test_forward_pipeline_without_registering(monkeypatch, tmp_path):
    m1 = make_m1(days=560, seed=11)
    start = m1.index[0] + timedelta(days=500)
    calls = []

    def loader(sym, tf, a, b):
        calls.append((sym, tf))
        return resample(m1[(m1.index >= pd.Timestamp(a)) & (m1.index < pd.Timestamp(b))], tf)
    monkeypatch.setattr("forex_agent.learning.forward.ROOT", tmp_path)
    r = run_forward("v3", loader, start.to_pydatetime(), m1.index[-1].to_pydatetime(), symbols=["EURUSD"],
                    register=False, log=lambda *_: None)
    assert ("EURUSD", "M5") in calls and ("EURUSD", "D1") in calls and r["symbols"] == ["EURUSD"]
    assert r["coverage"]["EURUSD"] > 0.95
    assert "n_trades" in r["metrics"] and isinstance(r["passed"], bool) and r["gate"]
    assert (tmp_path / r["report"] / "REPORT.md").exists()


def test_record_forward_activates_on_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "REGISTRY", tmp_path / "registry.json")
    monkeypatch.setattr(registry, "LOCAL", tmp_path / "registry.local.json")
    monkeypatch.setattr(registry, "MODELS", tmp_path)
    registry.save({"active": None, "versions": [{"version": "vX", "status": "rejected"}]})
    res = {"run_at": "now", "period": ["2026-03-01", "2026-11-27"], "symbols": ["EURUSD"], "metrics": {"n_trades": 40},
           "passed": True, "gate": ["ok"], "skipped": {}}
    v = registry.record_forward("vX", res)
    assert v["status"] == "active" and registry.load()["active"] == "vX"
    registry.record_forward("vX", {**res, "passed": False})
    assert len(registry.load()["versions"][0]["forward_tests"]) == 2
    # le fichier versionné n'est jamais modifié par un test sur le VPS
    assert json.loads((tmp_path / "registry.json").read_text())["versions"][0]["status"] == "rejected"


def test_coverage_detects_gaps():
    from forex_agent.learning.forward import coverage
    m1 = make_m1(days=60, seed=2)
    start, end = m1.index[0] + timedelta(days=10), m1.index[-1]
    assert coverage(m1, start, end) > 0.95
    holes = m1[(m1.index < start + timedelta(days=15)) | (m1.index > start + timedelta(days=35))]
    assert coverage(holes, start, end) < 0.7


def test_short_history_is_excluded_not_judged(monkeypatch, tmp_path):
    """Cas réel MT5 : seules les 100 000 dernières bougies existent -> paire exclue, pas de verdict trompeur."""
    m1 = make_m1(days=200, seed=4)
    start = m1.index[0] + timedelta(days=60)
    late = m1[m1.index > m1.index[0] + timedelta(days=150)]  # l'historique ne commence qu'après le début du test

    def loader(sym, tf, a, b):
        return resample(late[(late.index >= pd.Timestamp(a)) & (late.index < pd.Timestamp(b))], tf)
    monkeypatch.setattr("forex_agent.learning.forward.ROOT", tmp_path)
    import pytest
    with pytest.raises(RuntimeError, match="incomplet|insuffisant"):
        run_forward("v3", loader, start.to_pydatetime(), m1.index[-1].to_pydatetime(), symbols=["EURUSD"],
                    register=False, log=lambda *_: None)
