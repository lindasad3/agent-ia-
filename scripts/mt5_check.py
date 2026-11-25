"""Diagnostic de la connexion MT5 (sur le VPS Windows, terminal MT5 installé).

    python scripts/mt5_check.py                 # terminal, compte, symboles, cotations, historique
    python scripts/mt5_check.py --test-order    # + ordre réel 0.01 lot ouvert puis fermé (compte DÉMO uniquement)
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Console / journaux Windows : forcer l'UTF-8 (émojis, accents) même si la sortie est redirigée
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from forex_agent.config import Settings  # noqa: E402
from forex_agent.execution.mt5_broker import MT5Broker  # noqa: E402
from forex_agent.models import OrderRequest  # noqa: E402

OK, KO, WARN = "✔", "✖", "⚠"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-order", action="store_true")
    ap.add_argument("--symbol", default="EURUSD")
    a = ap.parse_args()
    st = Settings()
    if st.broker != "mt5":
        print(f"{WARN} FXA_BROKER={st.broker} dans .env : lancez d'abord  python scripts/setup.py mt5")
    b = MT5Broker(st.mt5_login, st.mt5_password.get_secret_value() if st.mt5_password else None, st.mt5_server,
                  st.mt5_path, allow_live=False, symbol_suffix=st.mt5_symbol_suffix, magic=st.magic,
                  symbol_map=st.mt5_symbol_map, server_utc_offset=st.mt5_server_utc_offset, symbols=st.symbols)
    print("1. Connexion au terminal MT5...")
    if not b.connect():
        print(f"   {KO} {b.last_error}")
        print("   Pistes : terminal installé et ouvert une fois ? login/serveur exacts ? compte DÉMO ?")
        return 1
    d = b.diagnostics()
    acc = b.account()
    term = d.get("terminal", {})
    print(f"   {OK} terminal {term.get('name')} build {term.get('build')} — {term.get('path')}")
    print(f"   {OK} compte {acc.login} @ {acc.server} — {'DÉMO' if acc.is_demo else 'RÉEL'} — {acc.balance:,.2f} {acc.currency} "
          f"— levier 1:{acc.leverage}")
    algo = term.get("algo_trading_enabled")
    print(f"   {OK if algo else KO} Algo Trading du terminal : {'activé' if algo else 'DÉSACTIVÉ → cliquez sur « Algo Trading » (barre d’outils MT5)'}")
    acct_ok = d.get("account", {})
    if acct_ok.get("trade_allowed") is False or acct_ok.get("expert_allowed") is False:
        print(f"   {KO} Le compte n'autorise pas le trading automatique (trade_allowed={acct_ok.get('trade_allowed')}, "
              f"expert={acct_ok.get('expert_allowed')}) : contactez le broker ou utilisez un autre compte démo")
    print(f"   {OK if b.offset_source in ('détecté', 'configuré') else WARN} décalage heure serveur : {b.server_offset} ({b.offset_source})")

    print("2. Symboles et cotations")
    if b.missing_symbols:
        print(f"   {WARN} introuvables chez ce broker : {', '.join(b.missing_symbols)} → retirez-les de FXA_SYMBOLS "
              f"ou indiquez leur nom dans FXA_MT5_SYMBOL_MAP")
    errors = 0
    for s in st.symbols:
        if s in b.missing_symbols:
            continue
        try:
            t = b.tick(s)
            spec = b.symbol_spec(s)
            bars = b.bars(s, "H1", 700)
            print(f"   {OK} {s:7} → {b._s(s):10} bid {t.bid:<10} spread {spec.pips(t.spread):4.1f} pips | lot min {spec.min_lot} "
                  f"pas {spec.lot_step} | {b.value_per_price_unit(spec, t.bid):>12,.2f} {acc.currency}/lot/1.0 | "
                  f"{len(bars)} bougies H1 (dernière {bars.index[-1]:%Y-%m-%d %H:%M} UTC)")
            if len(bars) < 300:
                print(f"     {WARN} historique court : ouvrez un graphique H1 de {s} dans MT5 pour le télécharger")
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"   {KO} {s}: {e}")
    print(f"   Correspondance à garder dans .env : FXA_MT5_SYMBOL_MAP={json.dumps(b.symbol_map)}")

    if a.test_order:
        print("3. Ordre de test (0.01 lot, ouvert puis fermé)")
        if not acc.is_demo:
            print(f"   {KO} refusé : ordre de test uniquement sur compte DÉMO")
            return 1
        t = b.tick(a.symbol)
        spec = b.symbol_spec(a.symbol)
        req = OrderRequest(a.symbol, "BUY", spec.min_lot, round(t.bid - 20 * spec.pip_size, spec.digits),
                           round(t.ask + 40 * spec.pip_size, spec.digits), comment="fxagent test")
        r = b.place_market(req)
        print(f"   {OK if r.ok else KO} ouverture : vérifié dans MT5={r.verified} ticket={r.ticket} prix={r.price} "
              f"slippage={r.slippage_pips} pips — {r.message} (code {r.retcode})")
        if r.ok:
            time.sleep(2)
            c = b.close(r.ticket)
            print(f"   {OK if c.ok else KO} clôture : vérifiée={c.verified} — {c.message}")
            time.sleep(1.5)
            print(f"   {OK} historique MT5 : {b.closed_deal(r.ticket)}")
        else:
            errors += 1
    b.disconnect()
    print("\nRésultat :", "tout est opérationnel" if errors == 0 else f"{errors} problème(s) à corriger (voir ci-dessus)")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
