"""Bibliothèque de stratégies. Chacune formule une hypothèse mesurable et des niveaux SL/TP explicites.

Le signal est évalué à la clôture de la barre i ; l'entrée se fait au prix de marché suivant.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..analysis.regime import RANGE, TRANSITION, TREND_DOWN, TREND_UP
from ..models import Candidate
from .base import FeatureView, Strategy, ok


@dataclass
class TrendPullback(Strategy):
    name: str = "trend_pullback"
    label: str = "Trend Pullback"
    description: str = ("Suit la tendance H4/H1 : achat sur repli vers l'EMA20/50 en zone de discount avec "
                        "bougie de reprise (inverse en vente).")
    hypothesis: str = ("En tendance établie (ADX>=23, ER>=0.25, HTF alignés), un repli sur la moyenne mobile "
                       "suivi d'une reprise a une espérance positive à 2R sur 1 à 2 jours.")
    regimes: tuple[str, ...] = (TREND_UP, TREND_DOWN)
    params: dict = field(default_factory=lambda: {"rr": 2.0, "sl_atr": 0.4, "min_sl_atr": 1.0, "rsi_lo": 38, "rsi_hi": 68})
    search_space: dict = field(default_factory=lambda: {"rr": (1.5, 3.0), "sl_atr": (0.2, 1.0), "min_sl_atr": (0.8, 2.0)})

    def evaluate(self, F: FeatureView, i: int) -> Candidate | None:
        p = self.params
        c, o, h, l, a = F["close"][i], F["open"][i], F["high"][i], F["low"][i], F["atr"][i]
        if not ok(a) or a <= 0 or i < 2:
            return None
        e20, e50, rsi = F["ema20"][i], F["ema50"][i], F["rsi"][i]
        h4, h1 = F["htf2_trend"][i], F["htf1_structure"][i]
        reasons: list[str] = []
        for d, sgn in (("BUY", 1), ("SELL", -1)):
            regime_ok = F["regime"][i] == (TREND_UP if sgn > 0 else TREND_DOWN)
            if not regime_ok or h4 != sgn:
                continue
            touched = (l <= e20 + 0.2 * a) if sgn > 0 else (h >= e20 - 0.2 * a)
            held = (c > e50) if sgn > 0 else (c < e50)
            trigger = ((c > o) and (c > F["high"][i - 1] or F["engulf"][i] == 1 or F["pin"][i] == 1)) if sgn > 0 else \
                      ((c < o) and (c < F["low"][i - 1] or F["engulf"][i] == -1 or F["pin"][i] == -1))
            rsi_ok = (p["rsi_lo"] <= rsi <= p["rsi_hi"]) if sgn > 0 else (100 - p["rsi_hi"] <= rsi <= 100 - p["rsi_lo"])
            if not (touched and held and trigger and rsi_ok):
                continue
            swing = F["last_sl"][i] if sgn > 0 else F["last_sh"][i]
            extreme = min(l, swing) if (sgn > 0 and ok(swing)) else max(h, swing) if ok(swing) else (l if sgn > 0 else h)
            sl = extreme - p["sl_atr"] * a if sgn > 0 else extreme + p["sl_atr"] * a
            if abs(c - sl) < p["min_sl_atr"] * a:
                sl = c - p["min_sl_atr"] * a if sgn > 0 else c + p["min_sl_atr"] * a
            target = F["near_res"][i] if sgn > 0 else F["near_sup"][i]
            lv = self._levels(d, c, sl, p["rr"], None)
            if not lv:
                continue
            score = 55.0
            reasons = [f"Tendance {'haussière' if sgn > 0 else 'baissière'} confirmée (ADX {F['adx'][i]:.0f}, ER {F['er'][i]:.2f})",
                       "H4 aligné (EMA50/EMA200)", "Repli sur EMA20 tenu au-dessus de l'EMA50" if sgn > 0 else
                       "Repli sur EMA20 tenu sous l'EMA50", "Bougie de reprise"]
            if h1 == sgn:
                score += 8; reasons.append("Structure H1 alignée")
            pdp = F["pd_pos"][i]
            if ok(pdp) and ((sgn > 0 and pdp < 0.5) or (sgn < 0 and pdp > 0.5)):
                score += 6; reasons.append("Entrée en zone de " + ("discount" if sgn > 0 else "premium"))
            if (F["since_bull_break"][i] if sgn > 0 else F["since_bear_break"][i]) <= 12:
                score += 6; reasons.append("BOS récent dans le sens du trade")
            if F["liquidity"][i] >= 2:
                score += 5; reasons.append(f"Liquidité élevée ({F['session'][i]})")
            if ok(target) and abs(target - c) > abs(lv[1] - c):
                score += 4; reasons.append("Espace libre jusqu'au prochain niveau")
            return Candidate(self.name, d, c, lv[0], lv[1], min(score, 95), reasons)
        return None


@dataclass
class VolatilityBreakout(Strategy):
    name: str = "breakout"
    label: str = "Volatility Breakout"
    description: str = "Cassure d'un canal de Donchian 20 après compression de volatilité (squeeze Bollinger)."
    hypothesis: str = ("Après une compression (largeur BB < 30e percentile), une clôture hors du canal 20 barres "
                       "avec volume relatif élevé initie une expansion exploitable à 2R.")
    regimes: tuple[str, ...] = (RANGE, TRANSITION)
    params: dict = field(default_factory=lambda: {"rr": 2.0, "squeeze_rank": 0.3, "sl_atr": 1.2, "vol_z": 0.5})
    search_space: dict = field(default_factory=lambda: {"rr": (1.5, 3.0), "squeeze_rank": (0.15, 0.45), "sl_atr": (0.8, 2.0)})

    def evaluate(self, F: FeatureView, i: int) -> Candidate | None:
        p = self.params
        if i < 12:
            return None
        c, a = F["close"][i], F["atr"][i]
        dch, dcl = F["dc_high"][i - 1], F["dc_low"][i - 1]
        if not (ok(a) and ok(dch) and ok(dcl)) or a <= 0:
            return None
        squeeze = np.nanmin(F["bb_width_rank"][i - 10:i]) < p["squeeze_rank"]
        if not squeeze or F["vol_z"][i] < p["vol_z"] or F["liquidity"][i] < 1:
            return None
        for d, sgn in (("BUY", 1), ("SELL", -1)):
            broke = c > dch if sgn > 0 else c < dcl
            if not broke or F["htf1_trend"][i] == -sgn:
                continue
            mid = (dch + dcl) / 2
            sl = min(c - p["sl_atr"] * a, mid) if sgn > 0 else max(c + p["sl_atr"] * a, mid)
            lv = self._levels(d, c, sl, p["rr"])
            if not lv:
                continue
            score = 58.0
            reasons = [f"Compression de volatilité puis cassure du canal 20 barres ({'haut' if sgn > 0 else 'bas'})",
                       f"Volume relatif z={F['vol_z'][i]:.1f}"]
            if F["htf2_trend"][i] == sgn:
                score += 8; reasons.append("H4 dans le sens de la cassure")
            if F["delta_z"][i] * sgn > 1:
                score += 6; reasons.append("Delta (proxy) agressif dans le sens de la cassure")
            if F["liquidity"][i] >= 2:
                score += 5; reasons.append(f"Session liquide ({F['session'][i]})")
            return Candidate(self.name, d, c, lv[0], lv[1], min(score, 95), reasons)
        return None


@dataclass
class RangeMeanReversion(Strategy):
    name: str = "mean_reversion"
    label: str = "Range Mean Reversion"
    description: str = "En range, achat sous la bande de Bollinger basse avec RSI extrême et rejet ; objectif la moyenne."
    hypothesis: str = ("En range (ADX<20, volatilité non élevée), les excès hors bandes de Bollinger avec bougie de "
                       "rejet reviennent vers la moyenne 20 dans la majorité des cas.")
    regimes: tuple[str, ...] = (RANGE,)
    params: dict = field(default_factory=lambda: {"rsi_ext": 30, "sl_atr": 0.6, "min_rr": 1.2, "max_rr": 2.5})
    search_space: dict = field(default_factory=lambda: {"rsi_ext": (22, 35), "sl_atr": (0.3, 1.2), "min_rr": (1.0, 1.6)})

    def evaluate(self, F: FeatureView, i: int) -> Candidate | None:
        p = self.params
        if i < 2 or F["regime"][i] != RANGE or F["vol_state"][i] == "HIGH":
            return None
        c, o, h, l, a = F["close"][i], F["open"][i], F["high"][i], F["low"][i], F["atr"][i]
        mid, up, lo, rsi = F["bb_mid"][i], F["bb_up"][i], F["bb_lo"][i], F["rsi"][i]
        if not (ok(a) and ok(mid)) or a <= 0:
            return None
        prev_low, prev_high = F["low"][i - 1], F["high"][i - 1]
        for d, sgn in (("BUY", 1), ("SELL", -1)):
            if sgn > 0:
                excess = min(l, prev_low) < lo and F["rsi"][i - 1] <= p["rsi_ext"] + 5 and rsi <= 45
                reject = c > o and (F["pin"][i] == 1 or F["engulf"][i] == 1 or c > F["close"][i - 1])
                sl = min(l, prev_low) - p["sl_atr"] * a
            else:
                excess = max(h, prev_high) > up and F["rsi"][i - 1] >= 100 - p["rsi_ext"] - 5 and rsi >= 55
                reject = c < o and (F["pin"][i] == -1 or F["engulf"][i] == -1 or c < F["close"][i - 1])
                sl = max(h, prev_high) + p["sl_atr"] * a
            if not (excess and reject):
                continue
            risk = abs(c - sl)
            rr = abs(mid - c) / risk if risk > 0 else 0
            if rr < p["min_rr"]:
                continue
            rr = min(rr, p["max_rr"])
            tp = c + rr * risk if sgn > 0 else c - rr * risk
            score = 56.0
            reasons = ["Marché en range (ADX < 20)", f"Excès hors bande de Bollinger, RSI {F['rsi'][i - 1]:.0f}",
                       "Bougie de rejet", "Objectif : retour à la moyenne 20"]
            if F["absorption"][i] or F["absorption"][i - 1]:
                score += 7; reasons.append("Absorption détectée sur l'excès (proxy volume)")
            vv = F["vp_val"][i] if sgn > 0 else F["vp_vah"][i]
            if ok(vv) and ((sgn > 0 and l <= vv) or (sgn < 0 and h >= vv)):
                score += 6; reasons.append("Rejet à la bordure de la value area")
            if (sgn > 0 and F["sweep_bull"][i]) or (sgn < 0 and F["sweep_bear"][i]):
                score += 6; reasons.append("Balayage de liquidité sur l'excès")
            return Candidate(self.name, d, c, sl, tp, min(score, 95), reasons)
        return None


@dataclass
class SMCSweepReversal(Strategy):
    name: str = "smc_sweep"
    label: str = "SMC Liquidity Sweep"
    description: str = ("Smart Money : balayage de liquidité (SSL/BSL) puis changement de caractère (CHOCH) ; entrée "
                        "sur le retest du FVG/Order Block, objectif la liquidité opposée.")
    hypothesis: str = ("Un balayage de liquidité suivi d'un CHOCH dans les 6 barres signale une prise de stops "
                       "institutionnelle ; le retour vers la liquidité opposée offre >= 1.8R.")
    regimes: tuple[str, ...] = (RANGE, TRANSITION, TREND_UP, TREND_DOWN)
    params: dict = field(default_factory=lambda: {"window": 6, "sl_atr": 0.25, "rr": 2.5, "min_rr": 1.8})
    search_space: dict = field(default_factory=lambda: {"window": (3, 10), "sl_atr": (0.1, 0.8), "rr": (1.8, 4.0)})

    def evaluate(self, F: FeatureView, i: int) -> Candidate | None:
        p = self.params
        c, a = F["close"][i], F["atr"][i]
        if not ok(a) or a <= 0:
            return None
        w = int(p["window"])
        for d, sgn in (("BUY", 1), ("SELL", -1)):
            if sgn > 0:
                swept = F["since_sweep_bull"][i] <= w
                shift = F["since_choch_bull"][i] <= 2 or F["since_bull_break"][i] == 0
                zone = F["fvg_bull_retest"][i] or F["ob_bull_retest"][i] or F["fvg_bull_new"][i]
                ext = F["sweep_low"][i]; sl = ext - p["sl_atr"] * a if ok(ext) else np.nan
                pdp_ok = ok(F["pd_pos"][i]) and F["pd_pos"][i] < 0.6
                target = F["bsl"][i]
            else:
                swept = F["since_sweep_bear"][i] <= w
                shift = F["since_choch_bear"][i] <= 2 or F["since_bear_break"][i] == 0
                zone = F["fvg_bear_retest"][i] or F["ob_bear_retest"][i] or F["fvg_bear_new"][i]
                ext = F["sweep_high"][i]; sl = ext + p["sl_atr"] * a if ok(ext) else np.nan
                pdp_ok = ok(F["pd_pos"][i]) and F["pd_pos"][i] > 0.4
                target = F["ssl"][i]
            if not (swept and shift and zone and ok(sl)):
                continue
            if abs(c - sl) < 0.5 * a or abs(c - sl) > 4 * a:
                continue
            lv = self._levels(d, c, sl, p["rr"], target, p["min_rr"])
            if not lv:
                continue
            score = 60.0
            reasons = [f"Balayage de liquidité {'sell-side (SSL)' if sgn > 0 else 'buy-side (BSL)'}",
                       f"CHOCH/BOS {'haussier' if sgn > 0 else 'baissier'} après le balayage",
                       "Retest d'une zone FVG/Order Block"]
            if pdp_ok:
                score += 6; reasons.append("Position " + ("discount" if sgn > 0 else "premium") + " du range")
            if F["htf2_trend"][i] == sgn:
                score += 7; reasons.append("H4 aligné")
            if (F["eq_lows"][i] if sgn > 0 else F["eq_highs"][i]):
                score += 5; reasons.append("Liquidité de creux/sommets égaux prise")
            if F["liquidity"][i] >= 2:
                score += 4; reasons.append(f"Session liquide ({F['session'][i]})")
            return Candidate(self.name, d, c, lv[0], lv[1], min(score, 95), reasons)
        return None


@dataclass
class OrderFlowAbsorption(Strategy):
    name: str = "orderflow_absorption"
    label: str = "Order Flow Absorption"
    description: str = ("Absorption de volume à un niveau clé (value area / S-R) avec divergence de delta ; "
                        "utilise le delta réel et le DOM Quantower quand disponibles, sinon un proxy.")
    hypothesis: str = ("Un fort volume absorbé sans progression du prix à un extrême, avec divergence du delta "
                       "cumulé, précède un retournement vers le POC.")
    regimes: tuple[str, ...] = (RANGE, TRANSITION)
    params: dict = field(default_factory=lambda: {"sl_atr": 0.4, "rr": 2.0, "min_rr": 1.3})
    search_space: dict = field(default_factory=lambda: {"sl_atr": (0.2, 1.0), "rr": (1.5, 3.0)})
    needs_orderflow: bool = True

    def evaluate(self, F: FeatureView, i: int) -> Candidate | None:
        p = self.params
        if i < 3:
            return None
        c, a = F["close"][i], F["atr"][i]
        if not ok(a) or a <= 0:
            return None
        absorbed = F["absorption"][i] or F["absorption"][i - 1]
        if not absorbed:
            return None
        lo2, hi2 = min(F["low"][i], F["low"][i - 1]), max(F["high"][i], F["high"][i - 1])
        for d, sgn in (("BUY", 1), ("SELL", -1)):
            div = F["delta_div_bull"][i - 1] or F["delta_div_bull"][i] if sgn > 0 else \
                  F["delta_div_bear"][i - 1] or F["delta_div_bear"][i]
            edge = F["vp_val"][i] if sgn > 0 else F["vp_vah"][i]
            level = F["near_sup"][i] if sgn > 0 else F["near_res"][i]
            at_level = (ok(edge) and ((sgn > 0 and lo2 <= edge) or (sgn < 0 and hi2 >= edge))) or \
                       (ok(level) and abs((lo2 if sgn > 0 else hi2) - level) <= 0.3 * a)
            confirm = (c > F["open"][i]) if sgn > 0 else (c < F["open"][i])
            if not (div and at_level and confirm):
                continue
            sl = lo2 - p["sl_atr"] * a if sgn > 0 else hi2 + p["sl_atr"] * a
            lv = self._levels(d, c, sl, p["rr"], F["vp_poc"][i], p["min_rr"])
            if not lv:
                continue
            score = 60.0
            reasons = ["Absorption de volume (volume élevé, faible progression)",
                       "Divergence du delta cumulé", "Niveau clé : bordure de value area / S-R"]
            if F["liquidity"][i] >= 2:
                score += 5; reasons.append(f"Session liquide ({F['session'][i]})")
            if (sgn > 0 and F["sweep_bull"][i]) or (sgn < 0 and F["sweep_bear"][i]):
                score += 6; reasons.append("Balayage de liquidité simultané")
            return Candidate(self.name, d, c, lv[0], lv[1], min(score, 95), reasons)
        return None


@dataclass
class MomentumContinuation(Strategy):
    name: str = "momentum"
    label: str = "Momentum Continuation"
    description: str = "Impulsion MACD dans une tendance forte (ADX>30) : entrée sur reprise du momentum après pause."
    hypothesis: str = ("Quand l'ADX dépasse 30 et que l'histogramme MACD repasse dans le sens de la tendance, "
                       "la continuation atteint 1.8R plus souvent que le stop.")
    regimes: tuple[str, ...] = (TREND_UP, TREND_DOWN)
    params: dict = field(default_factory=lambda: {"adx_min": 30, "sl_atr": 1.5, "rr": 1.8})
    search_space: dict = field(default_factory=lambda: {"adx_min": (25, 40), "sl_atr": (1.0, 2.5), "rr": (1.3, 3.0)})

    def evaluate(self, F: FeatureView, i: int) -> Candidate | None:
        p = self.params
        if i < 2:
            return None
        c, a = F["close"][i], F["atr"][i]
        if not ok(a) or a <= 0 or F["adx"][i] < p["adx_min"]:
            return None
        mh, mh1 = F["macd_hist"][i], F["macd_hist"][i - 1]
        for d, sgn in (("BUY", 1), ("SELL", -1)):
            if F["regime"][i] != (TREND_UP if sgn > 0 else TREND_DOWN):
                continue
            cross = (mh > 0 >= mh1) if sgn > 0 else (mh < 0 <= mh1)
            if not cross or (c - F["ema20"][i]) * sgn <= 0 or F["htf1_trend"][i] != sgn:
                continue
            sl = c - p["sl_atr"] * a if sgn > 0 else c + p["sl_atr"] * a
            lv = self._levels(d, c, sl, p["rr"])
            score = 55.0
            reasons = [f"Tendance forte (ADX {F['adx'][i]:.0f})", "Reprise du momentum MACD", "H1 aligné"]
            if F["htf2_trend"][i] == sgn:
                score += 7; reasons.append("H4 aligné")
            if F["liquidity"][i] >= 2:
                score += 5; reasons.append(f"Session liquide ({F['session'][i]})")
            return Candidate(self.name, d, c, lv[0], lv[1], min(score, 95), reasons) if lv else None
        return None


@dataclass
class TrendBreakoutTrail(Strategy):
    name: str = "trend_trail"
    label: str = "Trend Breakout + Trailing"
    description: str = ("Momentum de série temporelle : cassure du plus haut/bas 55 barres dans le sens du contexte "
                        "supérieur, stop initial 2 ATR puis stop suiveur — les gains courent, les pertes sont coupées.")
    hypothesis: str = ("Les rendements de change présentent du momentum (Moskowitz, Ooi & Pedersen 2012) : une cassure "
                       "de canal 55 barres suivie au stop suiveur capte des tendances à queue épaisse (gain moyen >> perte moyenne).")
    regimes: tuple[str, ...] = (TREND_UP, TREND_DOWN, TRANSITION)
    params: dict = field(default_factory=lambda: {"sl_atr": 2.0, "trail_atr": 3.0, "cap_rr": 8.0})
    search_space: dict = field(default_factory=lambda: {"sl_atr": (1.5, 3.0), "trail_atr": (2.0, 4.5)})

    def evaluate(self, F: FeatureView, i: int) -> Candidate | None:
        p = self.params
        if i < 60:
            return None
        c, a = F["close"][i], F["atr"][i]
        hi, lo = F["dc55_high"][i - 1], F["dc55_low"][i - 1]
        if not (ok(a) and ok(hi) and ok(lo)) or a <= 0:
            return None
        for d, sgn in (("BUY", 1), ("SELL", -1)):
            broke = c > hi if sgn > 0 else c < lo
            fresh = (F["close"][i - 1] <= hi) if sgn > 0 else (F["close"][i - 1] >= lo)
            if not (broke and fresh) or F["htf2_trend"][i] != sgn:
                continue
            sl = c - p["sl_atr"] * a if sgn > 0 else c + p["sl_atr"] * a
            lv = self._levels(d, c, sl, p["cap_rr"])
            if not lv:
                continue
            score = 60.0
            reasons = [f"Cassure du canal 55 barres ({'plus haut' if sgn > 0 else 'plus bas'})",
                       "Contexte supérieur aligné (EMA50/EMA200)", f"Stop suiveur {p['trail_atr']:.1f} ATR"]
            if F["htf1_trend"][i] == sgn:
                score += 6; reasons.append("Contexte intermédiaire aligné")
            if F["adx"][i] >= 20:
                score += 4; reasons.append(f"ADX {F['adx'][i]:.0f}")
            return Candidate(self.name, d, c, lv[0], lv[1], min(score, 95), reasons, trail=p["trail_atr"] * a)
        return None


@dataclass
class LondonBreakout(Strategy):
    name: str = "london_breakout"
    label: str = "London Session Breakout"
    description: str = ("Cassure du range asiatique (00h-07h UTC) pendant la session de Londres (07h-11h UTC), "
                        "avec range ni trop étroit ni trop large ; stop au milieu du range.")
    hypothesis: str = ("L'arrivée de la liquidité européenne casse souvent le range accumulé pendant la session "
                       "asiatique et prolonge le mouvement dans la journée.")
    regimes: tuple[str, ...] = (RANGE, TRANSITION, TREND_UP, TREND_DOWN)
    params: dict = field(default_factory=lambda: {"rr": 2.0, "min_rng_atr": 1.0, "max_rng_atr": 4.0, "trail_atr": 0.0})
    search_space: dict = field(default_factory=lambda: {"rr": (1.5, 3.0), "min_rng_atr": (0.5, 1.5), "max_rng_atr": (2.5, 5.0)})

    def evaluate(self, F: FeatureView, i: int) -> Candidate | None:
        p = self.params
        hr = F["hour"][i]
        if not (7 <= hr <= 10) or i < 2:
            return None
        ah, al, c, a = F["asia_high"][i], F["asia_low"][i], F["close"][i], F["atr"][i]
        if not (ok(ah) and ok(al) and ok(a)) or a <= 0:
            return None
        rng = ah - al
        if not (p["min_rng_atr"] * a <= rng <= p["max_rng_atr"] * a):
            return None
        for d, sgn in (("BUY", 1), ("SELL", -1)):
            broke = (c > ah and F["close"][i - 1] <= ah) if sgn > 0 else (c < al and F["close"][i - 1] >= al)
            if not broke:
                continue
            sl = (ah + al) / 2
            lv = self._levels(d, c, sl, p["rr"])
            if not lv:
                continue
            score = 58.0
            reasons = [f"Cassure du range asiatique ({rng / a:.1f} ATR) à l'ouverture de Londres",
                       "Stop au milieu du range"]
            if F["htf2_trend"][i] == sgn:
                score += 7; reasons.append("Contexte supérieur aligné")
            if F["vol_z"][i] > 0.5:
                score += 5; reasons.append(f"Volume relatif z={F['vol_z'][i]:.1f}")
            return Candidate(self.name, d, c, lv[0], lv[1], min(score, 95), reasons, trail=p["trail_atr"] * a)
        return None


ALL_STRATEGIES = [TrendPullback, VolatilityBreakout, RangeMeanReversion, SMCSweepReversal, OrderFlowAbsorption,
                  MomentumContinuation, TrendBreakoutTrail, LondonBreakout]


def default_strategies() -> list[Strategy]:
    return [cls() for cls in ALL_STRATEGIES]


def by_name(name: str) -> type[Strategy]:
    for cls in ALL_STRATEGIES:
        if cls().name == name:
            return cls
    raise KeyError(name)
