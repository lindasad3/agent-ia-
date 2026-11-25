"""Spécifications des instruments et calcul de la valeur monétaire d'un mouvement de prix."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolSpec:
    name: str
    base: str
    quote: str
    pip_size: float
    digits: int
    contract_size: float
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 50.0
    typical_spread_pips: float = 1.0
    commission_per_lot: float = 7.0  # aller-retour, devise du compte

    def pips(self, price_distance: float) -> float:
        return price_distance / self.pip_size

    def round_price(self, price: float) -> float:
        return round(price, self.digits)


_SPREADS = {"EURUSD": 0.8, "GBPUSD": 1.2, "USDJPY": 1.0, "AUDUSD": 1.0, "USDCAD": 1.4, "USDCHF": 1.4,
            "EURJPY": 1.6, "GBPJPY": 2.2, "NZDUSD": 1.4, "EURGBP": 1.2, "XAUUSD": 2.5}


def get_spec(symbol: str) -> SymbolSpec:
    s = symbol.upper().replace("/", "")[:6]
    base, quote = s[:3], s[3:]
    if base in ("XAU", "XAG"):
        return SymbolSpec(s, base, quote, pip_size=0.1 if base == "XAU" else 0.01, digits=2 if base == "XAU" else 3,
                          contract_size=100 if base == "XAU" else 5000, typical_spread_pips=_SPREADS.get(s, 3.0),
                          max_lot=20.0)
    jpy = quote == "JPY"
    return SymbolSpec(s, base, quote, pip_size=0.01 if jpy else 0.0001, digits=3 if jpy else 5,
                      contract_size=100_000, typical_spread_pips=_SPREADS.get(s, 1.5))


def quote_to_account_rate(quote: str, account_ccy: str, rates: dict[str, float]) -> float:
    """Taux pour convertir 1 unité de la devise de cotation en devise du compte.

    `rates` contient des prix récents, ex. {"USDJPY": 150.2, "EURUSD": 1.08}.
    """
    if quote == account_ccy:
        return 1.0
    direct, inverse = f"{quote}{account_ccy}", f"{account_ccy}{quote}"
    if rates.get(direct):
        return rates[direct]
    if rates.get(inverse):
        return 1.0 / rates[inverse]
    raise KeyError(f"Taux de conversion {quote}->{account_ccy} indisponible")


def value_per_price_unit(spec: SymbolSpec, price: float, account_ccy: str = "USD",
                         rates: dict[str, float] | None = None) -> float:
    """Gain/perte (devise du compte) pour 1 lot et un mouvement de 1.0 de prix."""
    rates = dict(rates or {})
    rates.setdefault(spec.name, price)
    return spec.contract_size * quote_to_account_rate(spec.quote, account_ccy, rates)


def floor_to_step(volume: float, step: float) -> float:
    n = int(volume / step + 1e-9)
    return round(n * step, 8)
