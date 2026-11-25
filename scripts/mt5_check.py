"""Vérification de la connexion MT5 (à lancer sur le VPS Windows avec le terminal MT5 installé).

    python scripts/mt5_check.py                 # compte, cotations, historique, spécifications
    python scripts/mt5_check.py --test-order    # + ordre réel 0.01 lot BUY EURUSD puis clôture (compte DÉMO uniquement)
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forex_agent.config import Settings  # noqa: E402
from forex_agent.execution.mt5_broker import MT5Broker  # noqa: E402
from forex_agent.models import OrderRequest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-order", action="store_true")
    ap.add_argument("--symbol", default="EURUSD")
    a = ap.parse_args()
    st = Settings()
    b = MT5Broker(st.mt5_login, st.mt5_password.get_secret_value() if st.mt5_password else None, st.mt5_server,
                  st.mt5_path, allow_live=False, symbol_suffix=st.mt5_symbol_suffix, magic=st.magic)
    if not b.connect():
        print("ÉCHEC connexion :", b.last_error)
        return 1
    acc = b.account()
    print(f"OK compte {acc.login} @ {acc.server} — {'DÉMO' if acc.is_demo else 'RÉEL'} — solde {acc.balance} {acc.currency}, "
          f"levier 1:{acc.leverage} — décalage heure serveur {b.server_offset}")
    for s in st.symbols:
        try:
            t = b.tick(s)
            spec = b.symbol_spec(s)
            bars = b.bars(s, "H1", 50)
            print(f"  {s}: bid {t.bid} ask {t.ask} spread {spec.pips(t.spread):.1f} pips | lot min {spec.min_lot} pas {spec.lot_step} "
                  f"| valeur 1.0 prix/lot {b.value_per_price_unit(spec, t.bid):,.2f} {acc.currency} | {len(bars)} bougies H1, "
                  f"dernière {bars.index[-1]}")
        except Exception as e:  # noqa: BLE001
            print(f"  {s}: ERREUR {e}")
    print(f"Positions ouvertes : {len(b.positions())}")
    if a.test_order:
        if not acc.is_demo:
            print("Refus : ordre de test uniquement sur compte DÉMO")
            return 1
        t = b.tick(a.symbol)
        spec = b.symbol_spec(a.symbol)
        req = OrderRequest(a.symbol, "BUY", spec.min_lot, round(t.bid - 200 * spec.pip_size / 10, spec.digits),
                           round(t.ask + 400 * spec.pip_size / 10, spec.digits), comment="fxagent test")
        r = b.place_market(req)
        print(f"Ordre : ok={r.ok} vérifié={r.verified} ticket={r.ticket} prix={r.price} slippage={r.slippage_pips} — {r.message}")
        if r.ok:
            time.sleep(2)
            c = b.close(r.ticket)
            print(f"Clôture : ok={c.ok} vérifiée={c.verified} — {c.message}")
            time.sleep(1)
            d = b.closed_deal(r.ticket)
            print(f"Historique MT5 : {d}")
    b.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
