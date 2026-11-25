"""Risk Management Engine — module indépendant du cerveau IA.

Il ne fait confiance à aucune valeur calculée par le moteur de décision : il recalcule le volume,
le risque, le R/R et vérifie chaque limite. Il peut refuser tout ordre.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from ..models import AccountInfo, Position, RiskCheck, RiskDecision, Signal, Tick
from ..symbols import SymbolSpec, floor_to_step, get_spec


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 3.0
    max_weekly_loss_pct: float = 6.0
    max_drawdown_pct: float = 10.0
    max_open_positions: int = 3
    max_total_risk_pct: float = 3.0
    max_currency_exposure_pct: float = 2.0
    min_rr: float = 1.5
    max_spread_pips: dict[str, float] = field(default_factory=lambda: {"DEFAULT": 2.5, "XAUUSD": 5.0, "GBPJPY": 4.0,
                                                                       "EURJPY": 3.0})
    max_spread_sl_ratio: float = 0.15
    max_slippage_pips: float = 1.5
    duplicate_window_min: int = 60
    min_sl_pips: float = 5.0
    max_sl_pips: dict[str, float] = field(default_factory=lambda: {"DEFAULT": 150.0, "XAUUSD": 600.0, "GBPJPY": 250.0,
                                                                    "EURJPY": 200.0})
    min_score: float = 60.0

    def sl_limit(self, symbol: str) -> float:
        if isinstance(self.max_sl_pips, (int, float)):
            return float(self.max_sl_pips)
        return self.max_sl_pips.get(symbol, self.max_sl_pips.get("DEFAULT", 150.0))

    def spread_limit(self, symbol: str) -> float:
        return self.max_spread_pips.get(symbol, self.max_spread_pips.get("DEFAULT", 2.5))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskState:
    day_start_equity: float
    week_start_equity: float
    peak_equity: float
    halted: bool = False
    halt_reason: str = ""


# fonction (spec, price) -> valeur monétaire (devise du compte) pour 1 lot et 1.0 de prix
ValueFn = Callable[[SymbolSpec, float], float]


class RiskEngine:
    def __init__(self, config: RiskConfig | None = None):
        self.cfg = config or RiskConfig()

    def position_risk(self, pos: Position, value_fn: ValueFn) -> float:
        spec = get_spec(pos.symbol)
        if pos.sl <= 0:
            return float("inf")
        dist = (pos.entry_price - pos.sl) if pos.direction == "BUY" else (pos.sl - pos.entry_price)
        return max(0.0, dist) * pos.volume * value_fn(spec, pos.entry_price)

    def evaluate(self, sig: Signal, account: AccountInfo, positions: list[Position], tick: Tick, state: RiskState,
                 value_fn: ValueFn, recent: list[Signal] | None = None, spec: SymbolSpec | None = None,
                 news_block: str | None = None, now: datetime | None = None) -> RiskDecision:
        cfg, now = self.cfg, now or datetime.now(timezone.utc)
        spec = spec or get_spec(sig.symbol)
        checks: list[RiskCheck] = []

        def add(name: str, ok: bool, detail: str) -> None:
            checks.append(RiskCheck(name, bool(ok), detail))

        eq = account.equity
        add("Arrêt d'urgence", not state.halted, state.halt_reason or "inactif")
        add("Validité du signal", not sig.expired(now), f"valide jusqu'à {sig.valid_until:%H:%M} UTC")
        dd_day = 100 * (state.day_start_equity - eq) / state.day_start_equity if state.day_start_equity else 0
        add("Perte journalière", dd_day < cfg.max_daily_loss_pct, f"{dd_day:.2f}% / max {cfg.max_daily_loss_pct}%")
        dd_week = 100 * (state.week_start_equity - eq) / state.week_start_equity if state.week_start_equity else 0
        add("Perte hebdomadaire", dd_week < cfg.max_weekly_loss_pct, f"{dd_week:.2f}% / max {cfg.max_weekly_loss_pct}%")
        dd = 100 * (state.peak_equity - eq) / state.peak_equity if state.peak_equity else 0
        add("Drawdown maximal", dd < cfg.max_drawdown_pct, f"{dd:.2f}% / max {cfg.max_drawdown_pct}%")
        add("Positions simultanées", len(positions) < cfg.max_open_positions,
            f"{len(positions)} ouvertes / max {cfg.max_open_positions}")
        add("Score minimal", sig.score >= cfg.min_score, f"{sig.score:.0f} / min {cfg.min_score:.0f}")
        add("Calendrier économique", news_block is None, news_block or "aucune annonce majeure proche")

        # Doublons : même symbole + même sens déjà ouvert, ou signal identique récent
        dup_pos = [p for p in positions if p.symbol == sig.symbol and p.direction == sig.direction]
        cutoff = now - timedelta(minutes=cfg.duplicate_window_min)
        dup_sig = [s for s in (recent or []) if s.id != sig.id and s.symbol == sig.symbol and s.direction == sig.direction
                   and s.created_at >= cutoff and s.status in ("executed", "approved", "pending")]
        add("Protection doublons", not dup_pos and not dup_sig,
            "position identique déjà ouverte" if dup_pos else "signal identique récent" if dup_sig else "aucun doublon")

        # Géométrie des niveaux (recalculée à partir du prix réel)
        entry = tick.ask if sig.direction == "BUY" else tick.bid
        risk_dist = (entry - sig.sl) if sig.direction == "BUY" else (sig.sl - entry)
        reward_dist = (sig.tp - entry) if sig.direction == "BUY" else (entry - sig.tp)
        sl_pips = spec.pips(risk_dist)
        max_sl = cfg.sl_limit(sig.symbol)
        add("Stop Loss cohérent", risk_dist > 0 and cfg.min_sl_pips <= sl_pips <= max_sl,
            f"{sl_pips:.1f} pips (bornes {cfg.min_sl_pips}-{max_sl:.0f})")
        rr = reward_dist / risk_dist if risk_dist > 0 else 0.0
        add("Ratio Risk/Reward", rr >= cfg.min_rr, f"{rr:.2f} / min {cfg.min_rr}")
        spread_pips = spec.pips(tick.spread)
        lim = cfg.spread_limit(sig.symbol)
        sp_ratio = tick.spread / risk_dist if risk_dist > 0 else 1
        add("Spread", spread_pips <= lim and sp_ratio <= cfg.max_spread_sl_ratio,
            f"{spread_pips:.1f} pips (max {lim}), {100 * sp_ratio:.0f}% du SL (max {100 * cfg.max_spread_sl_ratio:.0f}%)")

        # Volume
        vpu = value_fn(spec, entry)
        risk_amount_target = eq * cfg.risk_per_trade_pct / 100
        volume = 0.0
        if risk_dist > 0 and vpu > 0:
            volume = floor_to_step(risk_amount_target / (risk_dist * vpu), spec.lot_step)
            volume = min(volume, spec.max_lot)
        risk_amount = volume * risk_dist * vpu
        risk_pct = 100 * risk_amount / eq if eq else 0
        add("Volume calculable", volume >= spec.min_lot,
            f"{volume:.2f} lot pour {risk_amount:.2f} {account.currency} ({risk_pct:.2f}%)" if volume >= spec.min_lot
            else f"SL trop large pour {cfg.risk_per_trade_pct}% du capital (min {spec.min_lot} lot)")
        add("Marge disponible", account.margin_free > 0, f"marge libre {account.margin_free:.2f}")

        # Exposition globale et par devise
        open_risks = [(p, self.position_risk(p, value_fn)) for p in positions]
        total = sum(r for _, r in open_risks) + risk_amount
        total_pct = 100 * total / eq if eq else 0
        add("Exposition globale", total_pct <= cfg.max_total_risk_pct + 1e-9,
            f"{total_pct:.2f}% du capital en risque / max {cfg.max_total_risk_pct}%")
        exposure: dict[str, float] = {}
        for p, r in open_risks + [(Position(0, sig.symbol, sig.direction, volume, entry, sig.sl, sig.tp, now), risk_amount)]:
            sp = get_spec(p.symbol)
            sgn = 1 if p.direction == "BUY" else -1
            pct = 100 * r / eq if eq else 0
            exposure[sp.base] = exposure.get(sp.base, 0) + sgn * pct
            exposure[sp.quote] = exposure.get(sp.quote, 0) - sgn * pct
        worst = max(exposure.items(), key=lambda kv: abs(kv[1])) if exposure else ("-", 0)
        add("Exposition par devise", abs(worst[1]) <= cfg.max_currency_exposure_pct + 1e-9,
            f"max {worst[0]} {worst[1]:+.2f}% / limite {cfg.max_currency_exposure_pct}%")

        approved = all(c.ok for c in checks)
        return RiskDecision(approved, volume if approved else 0.0, risk_amount, risk_pct, checks)

    def check_slippage(self, spec: SymbolSpec, slippage_pips: float) -> RiskCheck:
        ok = abs(slippage_pips) <= self.cfg.max_slippage_pips
        return RiskCheck("Slippage", ok, f"{slippage_pips:+.1f} pips / max {self.cfg.max_slippage_pips}")
