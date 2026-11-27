"""Forward test : pipeline complet sur données fournies par un chargeur (MT5 en production)."""
import json
from datetime import timedelta

import pandas as pd

from forex_agent.learning import registry
from forex_agent.learning.forward import run_forward
from tests.conftest import make_m1


def test_forward_pipeline_without_registering(monkeypatch, tmp_path):
    m1 = make_m1(days=200, seed=11)
    start = m1.index[0] + timedelta(days=120)
    calls = []

    def loader(sym, a, b):
        calls.append(sym)
        return m1[(m1.index >= pd.Timestamp(a)) & (m1.index < pd.Timestamp(b))]
    monkeypatch.setattr("forex_agent.learning.forward.ROOT", tmp_path)
    r = run_forward("v3", loader, start.to_pydatetime(), m1.index[-1].to_pydatetime(), symbols=["EURUSD"],
                    register=False, log=lambda *_: None)
    assert calls == ["EURUSD"] and r["symbols"] == ["EURUSD"]
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
