"""Assemblage de l'application : broker, stockage, cerveau, risque, Telegram, Quantower, API."""
from __future__ import annotations

import logging
import secrets
import sys
from pathlib import Path

import pandas as pd

from .agent.runtime import AgentRuntime
from .analysis.orderflow import OrderFlowStore
from .api.app import create_app
from .api.auth import Auth, hash_password
from .brain.factory import load_engine
from .config import Settings
from .data.calendar import EconomicCalendar
from .notify.telegram import TelegramBot
from .risk.engine import RiskConfig, RiskEngine
from .storage.db import Store

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("main")


def _secret(s) -> str | None:
    return s.get_secret_value() if s is not None else None


def build_broker(st: Settings):
    if st.broker == "mt5":
        from .execution.mt5_broker import MT5Broker
        return MT5Broker(st.mt5_login, _secret(st.mt5_password), st.mt5_server, st.mt5_path,
                         allow_live=st.allow_live_account, symbol_suffix=st.mt5_symbol_suffix, magic=st.magic,
                         symbol_map=st.mt5_symbol_map, server_utc_offset=st.mt5_server_utc_offset,
                         symbols=st.symbols)
    from .data.hf_dataset import load_m1
    from .execution.paper import PaperBroker, ReplayMarket
    start = pd.Timestamp(st.paper_start)
    m1 = {s: load_m1(s) for s in st.symbols}
    warm = start - pd.Timedelta(days=400)
    m1 = {s: d[d.index >= warm] for s, d in m1.items()}
    return PaperBroker(ReplayMarket(m1, start.to_pydatetime(), tfs=("H1", "H4", "D1", "M15")), st.paper_balance)


def build(st: Settings | None = None):
    st = st or Settings()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    store = Store(st.db_url)
    # Mot de passe de l'application : .env, sinon généré une fois et affiché au premier démarrage
    pw_hash = store.get("api_password_hash")
    if st.api_password is not None:
        pw_hash = hash_password(st.api_password.get_secret_value())
        store.set("api_password_hash", pw_hash)
    elif pw_hash is None:
        pw = secrets.token_urlsafe(10)
        pw_hash = hash_password(pw)
        store.set("api_password_hash", pw_hash)
        print(f"\n*** Mot de passe initial de l'application (utilisateur '{st.api_user}') : {pw}\n"
              f"*** Définissez FXA_API_PASSWORD dans .env pour le remplacer.\n", flush=True)
    jwt_secret = _secret(st.jwt_secret) or store.get("jwt_secret")
    if not jwt_secret:
        jwt_secret = secrets.token_hex(32)
        store.set("jwt_secret", jwt_secret)
    auth = Auth(st.api_user, pw_hash, jwt_secret)

    broker = build_broker(st)
    allow_unval = st.allow_unvalidated_model
    if allow_unval and st.broker == "mt5" and st.allow_live_account:
        raise SystemExit("Refus : un modèle non validé ne peut pas trader un compte réel "
                         "(FXA_ALLOW_UNVALIDATED_MODEL et FXA_ALLOW_LIVE_ACCOUNT sont incompatibles).")
    engine, info = load_engine(st.model_version, allow_unval, store.get("strategy_overrides"))
    if info.get("entry_tf") and info["entry_tf"] != st.entry_tf:
        log.warning("Timeframe du modèle (%s) différent de la config (%s) : utilisation du modèle", info["entry_tf"], st.entry_tf)
        st.entry_tf = info["entry_tf"]
    risk_cfg = RiskConfig(**(store.get("risk_config") or {}))
    tg = TelegramBot(_secret(st.telegram_token), st.telegram_chat_ids, _secret(st.telegram_pin))
    cal = EconomicCalendar(st.calendar_url if st.calendar_enabled else None, st.news_block_minutes)
    rt = AgentRuntime(broker, store, engine, info, RiskEngine(risk_cfg), tg, OrderFlowStore(), cal, st.symbols,
                      st.entry_tf, st.loop_seconds, st.mode, st.auto_trading, st.magic, st.be_trigger_r, st.be_lock_r,
                      st.paper_sim_minutes_per_cycle if st.broker == "paper" else 0, st.backup_dir, st.backup_every_hours)
    rt.quantower_map = st.quantower_symbol_map
    app = create_app(rt, auth, _secret(st.quantower_token))

    @app.on_event("startup")
    def _start():
        rt.start(background=True)

    @app.on_event("shutdown")
    def _stop():
        rt.stop()

    return app, rt, st
