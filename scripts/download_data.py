"""Télécharge les données historiques réelles (M1) et construit data/fx/{PAIR}_M1.parquet."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forex_agent.data.hf_dataset import DEFAULT_PAIRS, build_pairs  # noqa: E402

if __name__ == "__main__":
    pairs = sys.argv[1:] or DEFAULT_PAIRS
    print(json.dumps(build_pairs(pairs), indent=2))
