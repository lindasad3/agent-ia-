import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_m1(days: int = 40, seed: int = 7, start: str = "2024-01-01", base: float = 1.10, vol: float = 0.00012) -> pd.DataFrame:
    """Bougies M1 synthétiques (marche aléatoire) — uniquement pour les tests unitaires."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=days * 1440, freq="1min", tz="UTC")
    idx = idx[idx.day_of_week < 5]
    ret = rng.normal(0, vol, len(idx)) + 0.3 * vol * np.sin(np.arange(len(idx)) / 3000)
    close = base * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[base], close[:-1]])
    spread = np.abs(rng.normal(0, vol, len(idx)))
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.integers(1, 50, len(idx)).astype(float)}, index=idx)


@pytest.fixture(scope="session")
def m1():
    return make_m1()
