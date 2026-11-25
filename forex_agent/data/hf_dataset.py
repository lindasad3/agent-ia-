"""Données historiques réelles : jeu public HuggingFace `mito0o852/OHLCV-1m-Forex`.

Bougies M1 (OHLC + volume) pour 49 instruments, de 2023-01 à 2026-02.
Les fichiers mensuels sont téléchargés puis éclatés par paire dans data/fx/{PAIR}_M1.parquet.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pandas as pd

DATASET = "mito0o852/OHLCV-1m-Forex"
API_TREE = f"https://huggingface.co/api/datasets/{DATASET}/tree/main?recursive=true"
FILE_URL = f"https://huggingface.co/datasets/{DATASET}/resolve/main/{{path}}"

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
FX_DIR = ROOT / "data" / "fx"

DEFAULT_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "EURJPY", "GBPJPY", "XAUUSD"]


def list_month_files() -> list[str]:
    r = httpx.get(API_TREE, timeout=30)
    r.raise_for_status()
    return sorted(i["path"] for i in r.json() if i["type"] == "file" and i["path"].endswith(".parquet"))


def download(path: str, retries: int = 4) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_DIR / Path(path).name
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return dest
    for attempt in range(retries):
        try:
            with httpx.stream("GET", FILE_URL.format(path=path), follow_redirects=True, timeout=300) as r:
                r.raise_for_status()
                tmp = dest.with_suffix(".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
                tmp.rename(dest)
            return dest
        except Exception:  # noqa: BLE001 - retry réseau
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Téléchargement impossible : {path}")


def build_pairs(pairs: list[str] = DEFAULT_PAIRS) -> dict:
    FX_DIR.mkdir(parents=True, exist_ok=True)
    files = list_month_files()
    chunks: dict[str, list[pd.DataFrame]] = {p: [] for p in pairs}
    for path in files:
        local = download(path)
        df = pd.read_parquet(local)
        df = df[df["ticker"].isin(pairs)]
        for p, g in df.groupby("ticker"):
            chunks[str(p)].append(g.drop(columns="ticker"))
        print(f"  {path}: {len(df):,} lignes retenues", flush=True)
    summary = {}
    for p, parts in chunks.items():
        if not parts:
            continue
        d = pd.concat(parts).sort_values("timestamp").drop_duplicates("timestamp")
        d = d.set_index("timestamp")
        d.index = d.index.tz_convert("UTC")
        d.to_parquet(FX_DIR / f"{p}_M1.parquet")
        summary[p] = {"rows": int(len(d)), "start": str(d.index[0]), "end": str(d.index[-1])}
    (FX_DIR / "manifest.json").write_text(json.dumps({"source": DATASET, "pairs": summary}, indent=2))
    return summary


def load_m1(pair: str) -> pd.DataFrame:
    path = FX_DIR / f"{pair}_M1.parquet"
    if not path.exists():
        raise FileNotFoundError(f"{path} absent : lancer `python scripts/download_data.py`")
    return pd.read_parquet(path)
