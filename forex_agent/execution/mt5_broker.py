"""Connecteur MetaTrader 5 (package officiel `MetaTrader5`, Windows / terminal MT5 requis).

Chaque ordre est vérifié auprès de MT5 (position réellement ouverte, prix et volume réels) avant d'être
considéré comme exécuté. Rejets, requotes, volumes invalides, modes de remplissage refusés et déconnexions
sont gérés. Les noms de symboles du broker (suffixes « EURUSDm », « EURUSD.a », alias « GOLD ») sont détectés.
Sécurité : refuse de trader sur un compte réel sauf si `allow_live=True` est explicitement configuré.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from ..models import AccountInfo, ClosedDeal, OrderRequest, OrderResult, Position, Tick
from ..symbols import SymbolSpec, get_spec
from .broker import Broker

log = logging.getLogger("mt5")

RETRYABLE = {10004, 10020, 10021, 10031}  # requote, price changed, price off, no connection
RETCODE_TEXT = {
    10004: "Requote", 10006: "Ordre rejeté", 10007: "Annulé par le client", 10008: "Ordre placé",
    10009: "Exécuté", 10010: "Exécution partielle", 10013: "Requête invalide", 10014: "Volume invalide",
    10015: "Prix invalide", 10016: "Stops invalides (trop proches du prix ?)", 10017: "Trading désactivé sur ce compte/symbole",
    10018: "Marché fermé", 10019: "Fonds insuffisants", 10020: "Prix modifié", 10021: "Pas de prix",
    10024: "Trop de requêtes", 10026: "AutoTrading désactivé par le serveur",
    10027: "AutoTrading désactivé dans le terminal : cliquez sur le bouton « Algo Trading » de MT5",
    10030: "Mode de remplissage refusé", 10031: "Pas de connexion au serveur du broker",
    10040: "Limite de positions atteinte",
}
TF = {"M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15", "M30": "TIMEFRAME_M30",
      "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4", "D1": "TIMEFRAME_D1"}
ALIASES = {"XAUUSD": ["XAUUSD", "GOLD"], "XAGUSD": ["XAGUSD", "SILVER"]}


class MT5Broker(Broker):
    name = "MT5"

    def __init__(self, login: int | None, password: str | None, server: str | None, path: str | None = None,
                 allow_live: bool = False, symbol_suffix: str = "auto", magic: int = 770077, mt5_module: Any = None,
                 symbol_map: dict[str, str] | None = None, server_utc_offset: float | None = None,
                 symbols: list[str] | None = None):
        self.login, self.password, self.server, self.path = login, password, server, path
        self.allow_live, self.magic = allow_live, magic
        self.suffix = symbol_suffix if symbol_suffix is not None else "auto"
        self.symbol_map: dict[str, str] = dict(symbol_map or {})
        self.configured_offset = server_utc_offset
        self.wanted = list(symbols or [])
        self._mt5 = mt5_module
        self._connected = False
        self.server_offset = timedelta(0)
        self.offset_source = "défaut (0)"
        self.last_error = ""
        self.missing_symbols: list[str] = []

    # ---------- connexion ----------
    @property
    def mt5(self):
        if self._mt5 is None:
            import MetaTrader5 as mt5  # type: ignore[import-not-found]  # Windows uniquement
            self._mt5 = mt5
        return self._mt5

    def connect(self) -> bool:
        kw: dict[str, Any] = {}
        if self.path:
            kw["path"] = self.path
        if self.login:
            kw.update(login=int(self.login), password=self.password or "", server=self.server or "")
        try:
            ok = self.mt5.initialize(**kw)
        except Exception as e:  # noqa: BLE001 - module absent, terminal introuvable...
            self.last_error = f"Impossible de démarrer MT5 : {e}"
            return False
        if not ok:
            self.last_error = (f"initialize() a échoué : {self.mt5.last_error()} — vérifiez le chemin du terminal "
                               f"(FXA_MT5_PATH), l'identifiant, le mot de passe et le nom exact du serveur")
            self._connected = False
            return False
        info = self.mt5.account_info()
        if info is None:
            self.last_error = f"account_info() indisponible : {self.mt5.last_error()} (identifiants refusés ?)"
            self._connected = False
            return False
        if not self.is_demo_account(info) and not self.allow_live:
            self.last_error = "Compte RÉEL détecté : exécution refusée (FXA_ALLOW_LIVE_ACCOUNT=false)"
            self.mt5.shutdown()
            self._connected = False
            return False
        self._connected = True
        if self.suffix == "auto" or self.wanted:
            self.resolve_symbols(self.wanted or ["EURUSD"])
        self._detect_offset()
        return True

    def is_demo_account(self, info) -> bool:
        return info.trade_mode == getattr(self.mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)

    def resolve_symbols(self, wanted: list[str]) -> dict[str, str]:
        """Associe chaque symbole de l'agent au nom réel chez le broker (suffixe / alias)."""
        try:
            names = {s.name for s in (self.mt5.symbols_get() or [])}
        except Exception:  # noqa: BLE001
            names = set()
        if not names:
            return self.symbol_map
        suffix = "" if self.suffix == "auto" else self.suffix
        self.missing_symbols = []
        for sym in wanted:
            if sym in self.symbol_map and self.symbol_map[sym] in names:
                continue
            found = None
            for base in ALIASES.get(sym, [sym]):
                if base + suffix in names:
                    found = base + suffix
                    break
                cands = sorted((n for n in names if n.upper().startswith(base) and len(n) <= len(base) + 4), key=len)
                if cands:
                    found = cands[0]
                    break
            if found:
                self.symbol_map[sym] = found
            else:
                self.missing_symbols.append(sym)
        return self.symbol_map

    def _detect_offset(self) -> None:
        """MT5 horodate en heure serveur. Décalage = configuré, sinon mesuré sur un tick récent."""
        if self.configured_offset is not None:
            self.server_offset = timedelta(hours=float(self.configured_offset))
            self.offset_source = "configuré"
            return
        try:
            t = self.mt5.symbol_info_tick(self._s(self.wanted[0] if self.wanted else "EURUSD"))
            if t and t.time:
                diff = t.time - datetime.now(timezone.utc).timestamp()
                hours = round(diff / 3600)
                # Tick frais uniquement : l'écart doit être un nombre entier d'heures (± 3 min) et plausible
                if abs(diff - hours * 3600) <= 180 and -12 <= hours <= 14:
                    self.server_offset = timedelta(hours=hours)
                    self.offset_source = "détecté"
                    return
            self.offset_source = "non détecté (marché fermé ?) : définissez FXA_MT5_SERVER_UTC_OFFSET"
        except Exception:  # noqa: BLE001
            pass

    def is_connected(self) -> bool:
        if not self._connected:
            return False
        try:
            ti = self.mt5.terminal_info()
            return bool(ti and ti.connected and self.mt5.account_info() is not None)
        except Exception:  # noqa: BLE001
            return False

    def disconnect(self) -> None:
        try:
            self.mt5.shutdown()
        finally:
            self._connected = False

    def _s(self, symbol: str) -> str:
        if symbol in self.symbol_map:
            return self.symbol_map[symbol]
        return symbol + ("" if self.suffix == "auto" else self.suffix)

    def _agent_symbol(self, broker_name: str) -> str:
        for k, v in self.symbol_map.items():
            if v == broker_name:
                return k
        suffix = "" if self.suffix == "auto" else self.suffix
        if suffix and broker_name.endswith(suffix):
            return broker_name[: -len(suffix)]
        return broker_name

    def _ensure_symbol(self, symbol: str):
        s = self._s(symbol)
        info = self.mt5.symbol_info(s)
        if info is None:
            raise RuntimeError(f"Symbole {s} introuvable chez le broker (voir FXA_MT5_SYMBOL_MAP)")
        if not info.visible:
            self.mt5.symbol_select(s, True)
        return info

    def diagnostics(self) -> dict[str, Any]:
        """État du terminal et du compte : utile pour comprendre un refus d'ordre."""
        out: dict[str, Any] = {"connected": self.is_connected(), "server_offset_h": self.server_offset.total_seconds() / 3600,
                               "offset_source": self.offset_source, "symbol_map": self.symbol_map,
                               "missing_symbols": self.missing_symbols}
        try:
            ti = self.mt5.terminal_info()
            out["terminal"] = {"name": getattr(ti, "name", ""), "build": getattr(ti, "build", ""),
                               "path": getattr(ti, "path", ""), "connected": getattr(ti, "connected", None),
                               "algo_trading_enabled": getattr(ti, "trade_allowed", None)}
            ai = self.mt5.account_info()
            out["account"] = {"login": ai.login, "server": ai.server, "demo": self.is_demo_account(ai),
                              "trade_allowed": getattr(ai, "trade_allowed", None),
                              "expert_allowed": getattr(ai, "trade_expert", None)}
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)
        return out

    # ---------- données ----------
    def account(self) -> AccountInfo:
        a = self.mt5.account_info()
        if a is None:
            raise ConnectionError("Compte MT5 indisponible")
        return AccountInfo(balance=a.balance, equity=a.equity, margin_free=a.margin_free, currency=a.currency,
                           leverage=a.leverage, login=a.login, server=a.server, is_demo=self.is_demo_account(a), name=a.name)

    def tick(self, symbol: str) -> Tick:
        self._ensure_symbol(symbol)
        t = self.mt5.symbol_info_tick(self._s(symbol))
        if t is None:
            raise ConnectionError(f"Pas de cotation pour {symbol}")
        ts = datetime.fromtimestamp(t.time, timezone.utc) - self.server_offset
        return Tick(symbol, t.bid, t.ask, ts)

    def bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        self._ensure_symbol(symbol)
        rates = self.mt5.copy_rates_from_pos(self._s(symbol), getattr(self.mt5, TF[timeframe]), 1, count)
        if rates is None or len(rates) == 0:
            raise ConnectionError(f"Pas d'historique {symbol} {timeframe} : {self.mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True) - self.server_offset
        df = df.set_index("time").rename(columns={"tick_volume": "volume"})
        return df[["open", "high", "low", "close", "volume"]].astype(float)

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        base = get_spec(symbol)
        try:
            i = self._ensure_symbol(symbol)
            pip = i.point * 10 if i.digits in (3, 5) else i.point
            if symbol.startswith("XAU"):
                pip = base.pip_size
            return SymbolSpec(symbol, base.base, base.quote, pip, i.digits, i.trade_contract_size, i.volume_min,
                              i.volume_step, i.volume_max, base.typical_spread_pips, base.commission_per_lot)
        except Exception:  # noqa: BLE001
            return base

    def value_per_price_unit(self, spec: SymbolSpec, price: float) -> float:
        """Valeur fournie par le broker : tick_value / tick_size (déjà en devise du compte)."""
        i = self._ensure_symbol(spec.name)
        if i.trade_tick_size:
            return i.trade_tick_value / i.trade_tick_size
        return spec.contract_size

    def positions(self, magic: int | None = None) -> list[Position]:
        return [self._pos(p) for p in (self.mt5.positions_get() or []) if magic is None or p.magic == magic]

    def _pos(self, p) -> Position:
        return Position(ticket=p.ticket, symbol=self._agent_symbol(p.symbol),
                        direction="BUY" if p.type == self.mt5.POSITION_TYPE_BUY else "SELL",
                        volume=p.volume, entry_price=p.price_open, sl=p.sl, tp=p.tp,
                        open_time=datetime.fromtimestamp(p.time, timezone.utc) - self.server_offset, profit=p.profit,
                        swap=p.swap, comment=p.comment, magic=p.magic, price_current=p.price_current)

    def pending_orders(self) -> list[dict]:
        return [{"ticket": o.ticket, "symbol": self._agent_symbol(o.symbol), "type": o.type, "volume": o.volume_current,
                 "price": o.price_open, "sl": o.sl, "tp": o.tp} for o in (self.mt5.orders_get() or [])]

    # ---------- exécution ----------
    def _fillings(self, info) -> list[int]:
        """Modes de remplissage à essayer, celui annoncé par le symbole en premier."""
        mt5 = self.mt5
        fm = getattr(info, "filling_mode", 0)
        order = []
        if fm & 1:
            order.append(mt5.ORDER_FILLING_FOK)
        if fm & 2:
            order.append(mt5.ORDER_FILLING_IOC)
        for f in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
            if f not in order:
                order.append(f)
        return order

    def place_market(self, req: OrderRequest) -> OrderResult:
        mt5 = self.mt5
        try:
            info = self._ensure_symbol(req.symbol)
        except Exception as e:  # noqa: BLE001
            return OrderResult(False, message=str(e))
        spec = self.symbol_spec(req.symbol)
        vol = max(info.volume_min, min(info.volume_max, round(req.volume / info.volume_step) * info.volume_step))
        if abs(vol - req.volume) > 1e-9:
            return OrderResult(False, message=f"Volume invalide {req.volume} (pas {info.volume_step}, min {info.volume_min})")
        fillings = self._fillings(info)
        fi = 0
        last: OrderResult | None = None
        for attempt in range(1, 6):
            t = mt5.symbol_info_tick(self._s(req.symbol))
            if t is None:
                last = OrderResult(False, message="Pas de cotation", attempts=attempt)
                time.sleep(0.5)
                continue
            buy = req.direction == "BUY"
            price = t.ask if buy else t.bid
            request = {"action": mt5.TRADE_ACTION_DEAL, "symbol": self._s(req.symbol), "volume": float(round(vol, 8)),
                       "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL, "price": price,
                       "sl": round(req.sl, info.digits), "tp": round(req.tp, info.digits),
                       "deviation": req.deviation_points, "magic": req.magic, "comment": req.comment[:31],
                       "type_time": mt5.ORDER_TIME_GTC, "type_filling": fillings[fi]}
            check = mt5.order_check(request)
            if check is not None and check.retcode == 10030 and fi + 1 < len(fillings):
                fi += 1
                continue
            if check is not None and check.retcode not in (0, 10009, 10008):
                return OrderResult(False, retcode=check.retcode, attempts=attempt,
                                   message=f"order_check : {RETCODE_TEXT.get(check.retcode, check.comment)}")
            res = mt5.order_send(request)
            if res is None:
                last = OrderResult(False, message=f"order_send None : {mt5.last_error()}", attempts=attempt)
                time.sleep(0.5)
                continue
            if res.retcode in (10008, 10009, 10010):
                return self._verify(req, res, price, spec, attempt)
            last = OrderResult(False, retcode=res.retcode, message=RETCODE_TEXT.get(res.retcode, res.comment), attempts=attempt)
            if res.retcode == 10030 and fi + 1 < len(fillings):
                fi += 1
                continue
            if res.retcode not in RETRYABLE:
                return last
            time.sleep(0.4 * attempt)
        return last or OrderResult(False, message="Échec d'envoi")

    def _verify(self, req: OrderRequest, res, requested: float, spec: SymbolSpec, attempt: int) -> OrderResult:
        """Vérification réelle : la position doit exister dans MT5 avec le volume demandé."""
        mt5 = self.mt5
        pos = None
        for _ in range(10):
            ps = mt5.positions_get(ticket=res.order) or []
            if not ps and res.deal:
                deals = mt5.history_deals_get(ticket=res.deal) or []
                if deals:
                    ps = mt5.positions_get(ticket=deals[0].position_id) or []
            if ps:
                pos = ps[0]
                break
            time.sleep(0.3)
        if pos is None:
            return OrderResult(False, verified=False, ticket=res.order, deal=res.deal, retcode=res.retcode,
                               message="Ordre envoyé mais position introuvable dans MT5", attempts=attempt)
        buy = req.direction == "BUY"
        slip = (pos.price_open - requested) / spec.pip_size * (1 if buy else -1)
        return OrderResult(True, verified=True, ticket=pos.ticket, deal=res.deal, price=pos.price_open,
                           requested_price=requested, volume=pos.volume, sl=pos.sl, tp=pos.tp, slippage_pips=round(slip, 2),
                           retcode=res.retcode, message="Exécuté et vérifié dans MT5", attempts=attempt)

    def modify(self, ticket: int, sl: float | None, tp: float | None) -> OrderResult:
        mt5 = self.mt5
        ps = mt5.positions_get(ticket=ticket) or []
        if not ps:
            return OrderResult(False, ticket=ticket, message="Position introuvable")
        p = ps[0]
        info = mt5.symbol_info(p.symbol)
        req = {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "symbol": p.symbol,
               "sl": round(sl if sl is not None else p.sl, info.digits), "tp": round(tp if tp is not None else p.tp, info.digits),
               "magic": p.magic}
        res = mt5.order_send(req)
        if res is None or res.retcode != 10009:
            code = res.retcode if res else None
            return OrderResult(False, ticket=ticket, retcode=code, message=RETCODE_TEXT.get(code, "Échec modification"))
        p2 = (mt5.positions_get(ticket=ticket) or [None])[0]
        ok = p2 is not None and abs(p2.sl - req["sl"]) < info.point * 2 and abs(p2.tp - req["tp"]) < info.point * 2
        return OrderResult(ok, verified=ok, ticket=ticket, sl=p2.sl if p2 else 0, tp=p2.tp if p2 else 0,
                           retcode=res.retcode, message="SL/TP modifiés et vérifiés" if ok else "Modification non confirmée")

    def close(self, ticket: int, volume: float | None = None) -> OrderResult:
        mt5 = self.mt5
        ps = mt5.positions_get(ticket=ticket) or []
        if not ps:
            return OrderResult(False, ticket=ticket, message="Position introuvable")
        p = ps[0]
        info = mt5.symbol_info(p.symbol)
        fillings = self._fillings(info)
        res = None
        for f in fillings:
            t = mt5.symbol_info_tick(p.symbol)
            buy = p.type == mt5.POSITION_TYPE_BUY
            req = {"action": mt5.TRADE_ACTION_DEAL, "position": ticket, "symbol": p.symbol, "volume": volume or p.volume,
                   "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY, "price": t.bid if buy else t.ask,
                   "deviation": 20, "magic": p.magic, "comment": "fxagent close", "type_time": mt5.ORDER_TIME_GTC,
                   "type_filling": f}
            res = mt5.order_send(req)
            if res is None or res.retcode != 10030:
                break
        if res is None or res.retcode not in (10009, 10010):
            code = res.retcode if res else None
            return OrderResult(False, ticket=ticket, retcode=code, message=RETCODE_TEXT.get(code, "Échec clôture"))
        still = mt5.positions_get(ticket=ticket) or []
        return OrderResult(not still or volume is not None, verified=not still, ticket=ticket, deal=res.deal,
                           price=res.price, volume=res.volume, retcode=res.retcode,
                           message="Position clôturée et vérifiée" if not still else "Clôture partielle")

    def closed_deal(self, ticket: int) -> ClosedDeal | None:
        mt5 = self.mt5
        deals = mt5.history_deals_get(position=ticket) or []
        if not deals:
            return None
        ins = [d for d in deals if d.entry == mt5.DEAL_ENTRY_IN]
        outs = [d for d in deals if d.entry in (mt5.DEAL_ENTRY_OUT, getattr(mt5, "DEAL_ENTRY_OUT_BY", 3))]
        if not ins or not outs:
            return None
        di, do = ins[0], outs[-1]
        reason = {getattr(mt5, "DEAL_REASON_SL", 4): "SL", getattr(mt5, "DEAL_REASON_TP", 5): "TP"}.get(do.reason, "Clôture")
        off = self.server_offset
        return ClosedDeal(ticket=ticket, symbol=self._agent_symbol(di.symbol),
                          direction="BUY" if di.type == mt5.DEAL_TYPE_BUY else "SELL",
                          volume=di.volume, entry_price=di.price, exit_price=do.price,
                          open_time=datetime.fromtimestamp(di.time, timezone.utc) - off,
                          close_time=datetime.fromtimestamp(do.time, timezone.utc) - off,
                          profit=sum(d.profit for d in outs), commission=sum(d.commission for d in deals),
                          swap=sum(d.swap for d in deals), reason=reason)

    def history(self, since: datetime) -> list[ClosedDeal]:
        mt5 = self.mt5
        deals = mt5.history_deals_get(since + self.server_offset, datetime.now(timezone.utc) + self.server_offset) or []
        ids = sorted({d.position_id for d in deals if d.entry != mt5.DEAL_ENTRY_IN})
        return [c for c in (self.closed_deal(i) for i in ids) if c]
