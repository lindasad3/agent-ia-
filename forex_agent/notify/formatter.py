"""Messages Telegram (HTML) : signaux, exécutions, suivi, alertes."""
from __future__ import annotations

from html import escape
from typing import Any

from ..analysis.regime import REGIME_LABELS
from ..symbols import get_spec


def _p(symbol: str, x: float | None) -> str:
    if x is None:
        return "-"
    return f"{x:.{get_spec(symbol).digits}f}"


def tag(simulation: bool) -> str:
    return "🧪 <b>SIMULATION</b> · " if simulation else ""


def signal_msg(sig: Any, currency: str, mode: str, simulation: bool = False) -> str:
    spec = get_spec(sig.symbol)
    sl_pips = spec.pips(abs(sig.entry - sig.sl)); tp_pips = spec.pips(abs(sig.tp - sig.entry))
    arrow = "🟢 BUY" if sig.direction == "BUY" else "🔴 SELL"
    lines = [
        f"{tag(simulation)}📡 <b>Signal {escape(sig.symbol)} — {arrow}</b>",
        f"Stratégie : <b>{escape(sig.strategy)}</b> · {sig.timeframe} · {REGIME_LABELS.get(sig.regime, sig.regime)}",
        f"Entrée : <code>{_p(sig.symbol, sig.entry)}</code>",
        f"Stop Loss : <code>{_p(sig.symbol, sig.sl)}</code> ({sl_pips:.1f} pips)",
        f"Take Profit : <code>{_p(sig.symbol, sig.tp)}</code> ({tp_pips:.1f} pips)"
        + (" · stop suiveur actif" if getattr(sig, "trail", 0) else ""),
        f"Volume recommandé : <b>{sig.volume:.2f} lot</b>",
        f"Risque : <b>{sig.risk_pct:.2f} %</b> ({sig.risk_amount:.2f} {currency})",
        f"Risk/Reward : <b>1:{sig.rr:.2f}</b> · Score : <b>{sig.score:.0f}/100</b> · Proba : {sig.probability:.0%}",
        f"Validité : jusqu'à {sig.valid_until:%H:%M} UTC",
        "",
        f"🧠 <i>{escape(sig.explanation[:900])}</i>",
        "",
        "Mode MANUEL : validez ou refusez ci-dessous." if mode == "manual" else "Mode AUTOMATIQUE : exécution en cours.",
    ]
    return "\n".join(lines)


def execution_msg(sym: str, direction: str, res: Any, simulation: bool = False) -> str:
    return "\n".join([
        f"{tag(simulation)}✅ <b>Ordre exécuté — {escape(sym)} {direction}</b>",
        f"Ticket MT5 : <code>{res.ticket}</code>",
        f"Prix réel : <code>{_p(sym, res.price)}</code> (demandé {_p(sym, res.requested_price)})",
        f"Volume exécuté : {res.volume:.2f} lot",
        f"SL confirmé : <code>{_p(sym, res.sl)}</code> · TP confirmé : <code>{_p(sym, res.tp)}</code>",
        f"Slippage : {res.slippage_pips:+.1f} pips · Vérifié dans le terminal : {'oui' if res.verified else 'NON'}",
    ])


def rejected_msg(sym: str, direction: str, reasons: list[str], simulation: bool = False) -> str:
    return f"{tag(simulation)}⛔ <b>Ordre refusé par le Risk Engine — {escape(sym)} {direction}</b>\n" + \
        "\n".join(f"• {escape(r)}" for r in reasons[:8])


def error_msg(sym: str, text: str) -> str:
    return f"❌ <b>Erreur d'exécution — {escape(sym)}</b>\n{escape(text)}"


def modify_msg(sym: str, ticket: int, what: str, sl: float, tp: float, simulation: bool = False) -> str:
    return (f"{tag(simulation)}🔧 <b>{escape(what)} — {escape(sym)}</b> (ticket {ticket})\n"
            f"SL : <code>{_p(sym, sl)}</code> · TP : <code>{_p(sym, tp)}</code>")


def close_msg(t: dict, currency: str, simulation: bool = False) -> str:
    pnl = t.get("pnl") or 0.0
    icon = "💰" if pnl > 0 else "🔻"
    return "\n".join([
        f"{tag(simulation)}{icon} <b>Position clôturée — {escape(t['symbol'])} {t['direction']}</b> (ticket {t['ticket']})",
        f"Entrée {_p(t['symbol'], t.get('entry_price'))} → Sortie {_p(t['symbol'], t.get('exit_price'))}",
        f"Motif : {escape(str(t.get('close_reason')))} · Durée : {t.get('duration', '-')}",
        f"Résultat net : <b>{pnl:+.2f} {currency}</b> ({(t.get('pnl_r') or 0):+.2f} R)",
    ])


def alert_msg(title: str, text: str) -> str:
    return f"🚨 <b>{escape(title)}</b>\n{escape(text)}"


def status_msg(s: dict) -> str:
    a = s.get("account") or {}
    return "\n".join([
        f"{tag(s.get('simulation', False))}🤖 <b>État de l'agent</b>",
        f"Fonctionnement : {'🟢 actif' if s['running'] else '🔴 arrêté'} · Entrées : {'⏸ en pause' if s['paused'] else '▶️ actives'}",
        f"Mode : <b>{s['mode'].upper()}</b> · Exécution auto : <b>{'ON' if s['auto_trading'] else 'OFF'}</b>",
        f"Broker : {s['broker']['name']} {'🟢' if s['broker']['connected'] else '🔴'} · Quantower : "
        f"{'🟢' if s['quantower']['connected'] else '⚪ non connecté'}",
        f"Modèle : {s.get('model', {}).get('version', '-')} ({s.get('model', {}).get('status', '-')})",
        f"Solde : {a.get('balance', 0):,.2f} · Equity : {a.get('equity', 0):,.2f} {a.get('currency', '')}",
        f"P&L jour : {s.get('pnl_today', 0):+.2f} · Positions : {s.get('open_positions', 0)}",
        f"Dernier cycle : {s.get('last_cycle') or '-'}",
    ])


def positions_msg(pos: list, currency: str) -> str:
    if not pos:
        return "Aucune position ouverte."
    lines = ["📊 <b>Positions ouvertes</b>"]
    for p in pos:
        lines.append(f"• {p.symbol} {p.direction} {p.volume:.2f} @ {_p(p.symbol, p.entry_price)} · SL {_p(p.symbol, p.sl)} "
                     f"· TP {_p(p.symbol, p.tp)} · {p.profit:+.2f} {currency} (#{p.ticket})")
    return "\n".join(lines)


def performance_msg(m: dict, currency: str) -> str:
    if not m.get("n_trades"):
        return "Aucune transaction clôturée enregistrée."
    pf = m.get("profit_factor")
    return "\n".join([
        "📈 <b>Performances (trades enregistrés)</b>",
        f"Transactions : {m['n_trades']} · Win rate : {(m.get('win_rate') or 0):.0%}",
        f"Profit net : {m['net_profit']:+.2f} {currency} · Profit factor : {pf if pf is not None else '-'}",
        f"Expectancy : {(m.get('expectancy') or 0):+.2f} · Drawdown max : {m.get('max_drawdown', 0):.2f}",
    ])


def signals_msg(rows: list[dict]) -> str:
    if not rows:
        return "Aucun signal récent."
    out = ["📡 <b>Derniers signaux</b>"]
    for r in rows[:8]:
        out.append(f"• {r['created_at'][11:16]} {r['symbol']} {r['direction']} {r['strategy']} score {r['score']:.0f} → {r['status']}")
    return "\n".join(out)
