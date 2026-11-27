"""Test d'une version figée sur des données postérieures à tout son entraînement (forward test).

Sur le VPS (historique réel du broker via MT5) :
    python scripts/forward_test.py                     # dernière version, depuis la fin de ses données
    python scripts/forward_test.py --version v3 --start 2026-03-01
Hors VPS (données HuggingFace, période courte) :  --source hf
Si la version remplit les critères (PF >= 1.1, espérance > 0, >= 30 trades, DD <= 15 %), elle devient active.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Console / journaux Windows : forcer l'UTF-8 (émojis, accents) même si la sortie est redirigée
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

import pandas as pd  # noqa: E402

from forex_agent.learning import registry  # noqa: E402
from forex_agent.learning.forward import run_forward  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version")
    ap.add_argument("--start", help="AAAA-MM-JJ (défaut : premier jour du mois suivant la fin des données d'entraînement)")
    ap.add_argument("--end", help="AAAA-MM-JJ (défaut : maintenant)")
    ap.add_argument("--source", choices=["mt5", "hf"], default="mt5")
    ap.add_argument("--no-register", action="store_true")
    a = ap.parse_args()
    v = next((x for x in registry.load()["versions"] if x["version"] == a.version), None) if a.version else registry.latest_version()
    if v is None:
        print("Aucune version dans models/registry.json")
        return 1
    if a.start:
        start = pd.Timestamp(a.start, tz="UTC").to_pydatetime()
    else:
        de = pd.Timestamp(v.get("data_end") or v["oos_period"][1])
        start = (de.tz_convert("UTC").normalize() + pd.offsets.MonthBegin(1)).to_pydatetime()
    if v.get("data_end") and pd.Timestamp(start) <= pd.Timestamp(v["data_end"]):
        print(f"Refus : le début ({start:%Y-%m-%d}) doit être après la fin des données d'entraînement ({v['data_end'][:10]})")
        return 1
    end = pd.Timestamp(a.end, tz="UTC").to_pydatetime() if a.end else datetime.now(timezone.utc)
    print(f"Version {v['version']} (statut {v['status']}) — test sur {start:%Y-%m-%d} → {end:%Y-%m-%d}, source {a.source}")

    if a.source == "mt5":
        from forex_agent.config import Settings
        from forex_agent.execution.mt5_broker import MT5Broker
        st = Settings()
        b = MT5Broker(st.mt5_login, st.mt5_password.get_secret_value() if st.mt5_password else None, st.mt5_server,
                      st.mt5_path, symbol_suffix=st.mt5_symbol_suffix, symbol_map=st.mt5_symbol_map,
                      server_utc_offset=st.mt5_server_utc_offset, symbols=v["symbols"])
        if not b.connect():
            print("Connexion MT5 impossible :", b.last_error)
            return 1
        print("Téléchargement de l'historique M1 depuis MT5 (quelques minutes)...")
        loader = lambda s, a_, b_: b.history_bars(s, "M1", a_, b_)  # noqa: E731
    else:
        from forex_agent.data.hf_dataset import load_m1

        def loader(s, a_, b_):
            m = load_m1(s)
            return m[(m.index >= pd.Timestamp(a_)) & (m.index < pd.Timestamp(b_))]
    r = run_forward(v["version"], loader, start, end, register=not a.no_register)
    m = r["metrics"]
    print(f"\n{'✅ VALIDÉE' if r['passed'] else '❌ NON VALIDÉE'} — {'; '.join(r['gate'])}")
    print(f"Trades {m['n_trades']} · net {m['net_profit']} · rendement {m.get('return_pct')} % · PF {m['profit_factor']} · "
          f"win rate {m['win_rate']} · DD max {m['max_drawdown_pct']} %")
    print(f"Rapport : {r['report']}\\REPORT.md")
    if r["passed"]:
        print("La version est maintenant ACTIVE : mettez FXA_ALLOW_UNVALIDATED_MODEL=false dans .env puis redémarrez l'agent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
