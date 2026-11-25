"""Registre des versions du modèle : aucune mise à jour n'est activée sans validation hors échantillon."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "models"
REGISTRY = MODELS / "registry.json"


def load() -> dict[str, Any]:
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text())
    return {"active": None, "versions": []}


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
