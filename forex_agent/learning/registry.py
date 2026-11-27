"""Registre des versions du modèle : aucune mise à jour n'est activée sans validation hors échantillon."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "models"
REGISTRY = MODELS / "registry.json"
# Résultats propres à la machine (tests sur données inédites, activation) : non versionnés, pour que
# « git pull » sur le VPS ne soit jamais bloqué par un conflit sur registry.json.
LOCAL = MODELS / "registry.local.json"


def _load_local() -> dict[str, Any]:
    return json.loads(LOCAL.read_text()) if LOCAL.exists() else {"active": None, "versions": {}}


def load() -> dict[str, Any]:
    reg = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {"active": None, "versions": []}
    loc = _load_local()
    for v in reg["versions"]:
        o = loc["versions"].get(v["version"])
        if o:
            v.update({k: o[k] for k in ("status", "gate") if k in o})
            v["forward_tests"] = v.get("forward_tests", []) + o.get("forward_tests", [])
    if loc.get("active"):
        reg["active"] = loc["active"]
        for v in reg["versions"]:
            if v["status"] == "active" and v["version"] != loc["active"]:
                v["status"] = "retired"
    return reg


def save(reg: dict[str, Any]) -> None:
    MODELS.mkdir(exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=2, ensure_ascii=False, default=str))


def gate(candidate: dict, active: dict | None, max_dd_pct: float = 15.0) -> tuple[bool, list[str]]:
    """Critères de promotion d'une nouvelle version."""
    m = candidate["oos_metrics"]
    reasons = []
    ok = True
    if (m.get("n_trades") or 0) < 30:
        ok = False; reasons.append(f"moins de 30 trades hors échantillon ({m.get('n_trades')})")
    if (m.get("expectancy_r") or -1) <= 0:
        ok = False; reasons.append(f"espérance OOS non positive ({m.get('expectancy_r')})")
    if (m.get("profit_factor") or 0) < 1.1:
        ok = False; reasons.append(f"profit factor OOS < 1.1 ({m.get('profit_factor')})")
    if (m.get("max_drawdown_pct") or 0) > max_dd_pct:
        ok = False; reasons.append(f"drawdown OOS > {max_dd_pct}% ({m.get('max_drawdown_pct')})")
    if active and ok:
        am = active["oos_metrics"]
        if (m.get("expectancy_r") or 0) < (am.get("expectancy_r") or 0) * 0.9:
            ok = False; reasons.append("moins performante que la version active")
    if ok:
        reasons.append("tous les critères OOS sont satisfaits")
    return ok, reasons


def register(candidate: dict) -> dict:
    reg = load()
    active = next((v for v in reg["versions"] if v["version"] == reg.get("active")), None)
    ok, reasons = gate(candidate, active)
    candidate["status"] = "active" if ok else "rejected"
    candidate["gate"] = reasons
    candidate["registered_at"] = datetime.now(timezone.utc).isoformat()
    if ok:
        for v in reg["versions"]:
            if v["status"] == "active":
                v["status"] = "retired"
        reg["active"] = candidate["version"]
    reg["versions"].append(candidate)
    save(reg)
    return candidate


def active_version() -> dict | None:
    reg = load()
    return next((v for v in reg["versions"] if v["version"] == reg.get("active")), None)


def latest_version() -> dict | None:
    reg = load()
    return reg["versions"][-1] if reg["versions"] else None


def record_forward(version: str, result: dict) -> dict:
    """Enregistre un test sur données inédites (fichier local) ; une version qui le réussit devient active."""
    if not any(x["version"] == version for x in load()["versions"]):
        raise KeyError(version)
    loc = _load_local()
    o = loc["versions"].setdefault(version, {})
    o.setdefault("forward_tests", []).append({k: result[k] for k in ("run_at", "period", "symbols", "metrics", "passed",
                                                                      "gate", "skipped", "coverage") if k in result}
                                             | {"report": result.get("report")})
    if result["passed"]:
        o["status"] = "active"
        o["gate"] = [f"validée par test sur données inédites ({result['period'][0][:10]} → {result['period'][1][:10]})"]
        loc["active"] = version
    MODELS.mkdir(exist_ok=True)
    LOCAL.write_text(json.dumps(loc, indent=2, ensure_ascii=False, default=str))
    return next(x for x in load()["versions"] if x["version"] == version)
