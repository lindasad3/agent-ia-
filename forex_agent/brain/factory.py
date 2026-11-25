"""Charge la version de modèle à utiliser en production à partir du registre."""
from __future__ import annotations

import json
from pathlib import Path

from ..learning import registry
from ..strategies.library import by_name, default_strategies
from .decision import BrainConfig, DecisionEngine
from .meta_model import MetaModel

ROOT = Path(__file__).resolve().parents[2]


def load_engine(version: str | None = None, allow_unvalidated: bool = False,
                overrides: dict | None = None) -> tuple[DecisionEngine, dict]:
    """Retourne (moteur, info). Sans version validée, le moteur n'a aucune statistique et ne trade pas."""
    reg = registry.load()
    v = None
    if version:
        v = next((x for x in reg["versions"] if x["version"] == version), None)
    if v is None:
        v = registry.active_version()
    validated = v is not None and v.get("status") == "active"
    if v is None and allow_unvalidated:
        v = registry.latest_version()
    if v is not None and v.get("status") != "active" and not allow_unvalidated:
        v = None  # une version rejetée n'est jamais chargée sans autorisation explicite (démo/paper)
    info = {"version": None, "status": "aucun modèle validé", "validated": False, "warning": ""}
    if v is None:
        eng = DecisionEngine(default_strategies(), {}, None, BrainConfig())
        info["warning"] = "Aucune version validée : l'agent analyse mais ne propose aucun trade."
        return eng, info
    mdir = ROOT / v["artifacts"]
    params = json.loads((mdir / "strategies.json").read_text())
    strategies = []
    for name, cfg in params.items():
        st = by_name(name)()
        st.params = {**st.params, **cfg["params"]}
        st.enabled = cfg.get("enabled", True)
        if overrides and name in overrides:
            st.enabled = bool(overrides[name])
        strategies.append(st)
    matrix = json.loads((mdir / "perf_matrix.json").read_text())
    meta = None
    mp = mdir / "meta_model.joblib"
    if v.get("meta_model_active") and mp.exists():
        meta = MetaModel.load(mp)
    cfg = BrainConfig(**{k: val for k, val in v.get("brain_config", {}).items() if k in BrainConfig.__dataclass_fields__})
    eng = DecisionEngine(strategies, matrix, meta, cfg)
    info = {"version": v["version"], "status": v.get("status"), "validated": validated, "entry_tf": v.get("entry_tf"),
            "oos_metrics": {k: (v.get("oos_metrics") or {}).get(k) for k in
                            ("n_trades", "return_pct", "profit_factor", "win_rate", "max_drawdown_pct", "expectancy_r")},
            "warning": "" if validated else "Version NON validée (critères OOS non atteints) — usage démo/paper uniquement."}
    return eng, info
