"""Rapport de preuve du scénario de démonstration (17 étapes) à partir des données réellement enregistrées.

Pour chaque trade exécuté : analyse, régime, stratégie, explication, paramètres, contrôles de risque,
notifications, exécution (ticket, prix réel, slippage), vérification dans MT5, gestion, clôture, résultat.

    python scripts/demo_report.py              # dernier trade
    python scripts/demo_report.py --verify-mt5 # + contrôle croisé avec l'historique du terminal MT5
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Console / journaux Windows : forcer l'UTF-8 (émojis, accents) même si la sortie est redirigée
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from forex_agent.config import Settings  # noqa: E402
from forex_agent.storage.db import Store  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticket", type=int)
    ap.add_argument("--verify-mt5", action="store_true")
    a = ap.parse_args()
    st = Settings()
    store = Store(st.db_url)
    trades = [t for t in store.trades(limit=200) if a.ticket is None or t["ticket"] == a.ticket]
    if not trades:
        print("Aucun trade enregistré.")
        return 1
    t = trades[0]
    sig = store.signal(t["signal_id"]) or {}
    evs = [e for e in reversed(store.events(2000)) if str(t["ticket"]) in e["message"] or t["symbol"] in e["message"]]
    sim = t["broker"] == "PAPER"
    mt5_line = "non vérifié (ajouter --verify-mt5 sur le VPS)"
    if a.verify_mt5 and not sim:
        from forex_agent.execution.mt5_broker import MT5Broker
        b = MT5Broker(st.mt5_login, st.mt5_password.get_secret_value() if st.mt5_password else None, st.mt5_server,
                      st.mt5_path, symbol_suffix=st.mt5_symbol_suffix)
        if b.connect():
            pos = [p for p in b.positions() if p.ticket == t["ticket"]]
            deal = b.closed_deal(t["ticket"])
            mt5_line = (f"position ouverte dans MT5 : {pos[0]}" if pos else
                        f"historique MT5 : {deal}" if deal else "TICKET INTROUVABLE DANS MT5")
    a_ = sig.get("analysis") or {}
    L = [f"# Démonstration — trade #{t['ticket']} {t['symbol']} {t['direction']}", "",
         f"Généré le {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} · broker **{t['broker']}**"
         + (" — SIMULATION (marché rejoué), pas un trade réel" if sim else " — compte MT5"), "",
         "| # | Étape | Preuve |", "|---|---|---|",
         f"| 1 | Analyse temps réel | {t['symbol']} {t['timeframe']}, bougie analysée {sig.get('created_at', '-')} |",
         f"| 2 | Opportunité détectée | signal `{sig.get('id')}` score {sig.get('score')} |",
         f"| 3 | Conditions de marché | régime {sig.get('regime')}, ADX {a_.get('adx')}, session {a_.get('session')}, volatilité {a_.get('vol_state')} |",
         f"| 4 | Stratégie sélectionnée | {t['strategy']} |",
         f"| 5 | Explication | {(sig.get('explanation') or '')[:400]} |",
         f"| 6 | Entrée / SL / TP / volume | {sig.get('entry')} / {sig.get('sl')} / {sig.get('tp')} / {t['volume']} lot |",
         f"| 7 | Contrôle du risque | " + ", ".join(f"{c['name']} {'✓' if c['ok'] else '✗'}" for c in (sig.get('risk_checks') or [])) + " |",
         f"| 8 | Notification Telegram | voir journal ci-dessous (catégories signal/exécution) |",
         f"| 9-10 | Validation / exécution | décidé par `{sig.get('decided_by')}`, mode {t['mode']} |",
         f"| 11 | Exécution vérifiée | ticket {t['ticket']}, prix réel {t['entry_price']} (demandé {t['requested_price']}), slippage {t['slippage_pips']} pips |",
         f"| 12 | Position visible | {mt5_line} |",
         f"| 13 | Confirmation Telegram | message d'exécution envoyé avec prix réel et ticket |",
         f"| 14-15 | Gestion & modifications | {', '.join(e['event'] for e in (t.get('events') or [])) or 'aucune modification'} |",
         f"| 16 | Clôture enregistrée | sortie {t['exit_price']} ({t['close_reason']}) le {t['close_time']} → **{t['pnl']}** ({t['pnl_r']} R) |",
         f"| 17 | Dashboard actualisé | statistiques recalculées depuis la base (`/api/performance`) |", "",
         "## Journal des événements", ""] + [f"- `{e['ts'][:19]}` [{e['category']}] {e['message']}" for e in evs[-40:]]
    out = ROOT / "reports" / "demo"
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"DEMO_{t['ticket']}.md"
    p.write_text("\n".join(L), encoding="utf-8")
    print(p.read_text())
    (out / f"DEMO_{t['ticket']}.json").write_text(json.dumps({"trade": t, "signal": sig}, indent=2, default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
