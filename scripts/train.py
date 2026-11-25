"""Lance l'apprentissage offline complet (backtest, Optuna, walk-forward, méta-modèle, registre)."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forex_agent.learning.trainer import run  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--trials", type=int, default=12)
    ap.add_argument("--no-optimize", action="store_true")
    ap.add_argument("--version", default=None)
    a = ap.parse_args()
    c = run(a.symbols, a.tf, a.trials, version=a.version, optimize=not a.no_optimize)
    m = c["oos_metrics"]
    print(f"OOS: {m['n_trades']} trades, net {m['net_profit']}, PF {m['profit_factor']}, WR {m['win_rate']}, "
          f"DD {m['max_drawdown_pct']}% -> statut {c['status']}")
