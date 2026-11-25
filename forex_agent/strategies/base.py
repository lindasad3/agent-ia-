"""Socle commun des stratégies."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..models import Candidate


class FeatureView:
    """Accès rapide (numpy) aux colonnes de la table de features."""

    def __init__(self, f: pd.DataFrame):
        self.df = f
        self.index = f.index
        self._cols = {c: f[c].to_numpy() for c in f.columns}
        self.n = len(f)

    def __getitem__(self, key: str) -> np.ndarray:
        return self._cols[key]

    def __contains__(self, key: str) -> bool:
        return key in self._cols

    def row(self, i: int) -> dict[str, Any]:
        return {k: v[i] for k, v in self._cols.items()}


def ok(x: float) -> bool:
    return x is not None and not (isinstance(x, float) and np.isnan(x))


@dataclass
class Strategy:
    name: str = "base"
    label: str = "Base"
    description: str = ""
    hypothesis: str = ""
    regimes: tuple[str, ...] = ()
    params: dict[str, float] = field(default_factory=dict)
    search_space: dict[str, tuple[float, float]] = field(default_factory=dict)
    enabled: bool = True
    needs_orderflow: bool = False

    def with_params(self, **kw: float) -> "Strategy":
        new = type(self)()
        new.params = {**self.params, **kw}
        new.enabled = self.enabled
        return new

    def evaluate(self, F: FeatureView, i: int) -> Candidate | None:  # pragma: no cover - interface
        raise NotImplementedError

    # Utilitaires de construction des niveaux
    def _levels(self, direction: str, entry: float, sl: float, rr: float, target: float | None = None,
                min_rr: float = 1.2) -> tuple[float, float] | None:
        risk = (entry - sl) if direction == "BUY" else (sl - entry)
        if risk <= 0:
            return None
        tp = entry + rr * risk if direction == "BUY" else entry - rr * risk
        if target is not None and ok(target):
            t_rr = (target - entry) / risk if direction == "BUY" else (entry - target) / risk
            if t_rr >= min_rr:
                tp = target if t_rr < rr else tp
        return sl, tp

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label, "description": self.description,
                "hypothesis": self.hypothesis, "regimes": list(self.regimes), "params": self.params,
                "enabled": self.enabled, "needs_orderflow": self.needs_orderflow}
