"""Fee model for netting cross-venue edges. Pure: no qa import. Sources and dates: docs/venues.md."""
from math import ceil

FEE_VERSION = "2026-10-19"  # date of the fee entries in docs/venues.md; stamped on every edge row
KALSHI_RATE = 0.07  # secondhand, unverified: no Kalshi-owned page fetched states the coefficient (docs/venues.md)
_KALSHI_QUADRATIC = {"quadratic", "quadratic_with_maker_fees"}

# Assumption (2026-10-19), not a venue number: round-trip cost of moving capital between venues.
TRANSFER = {"fixed_usd": 10.0, "conversion_bps": 10.0}
US_EXECUTABLE = {"kalshi": True, "polymarket": False}  # docs/venues.md geoblock entry


def kalshi_taker_fee(price: float, contracts: float, fee_type: str, multiplier: float) -> float:
    if fee_type not in _KALSHI_QUADRATIC:
        raise ValueError(f"unsupported kalshi fee_type {fee_type!r}")
    # round(..., 6) first so float noise (e.g. 175.00000000000003 cents) cannot bump the ceil by a cent
    return ceil(round(100 * KALSHI_RATE * multiplier * contracts * price * (1 - price), 6)) / 100


def polymarket_taker_fee(price: float, contracts: float, schedule: dict | None, fees_enabled: bool) -> float:
    if not fees_enabled or not schedule:
        return 0.0
    # ponytail: no rounding, the docs do not state Polymarket's rule; add it once verified
    return contracts * schedule["rate"] * (price * (1 - price)) ** schedule["exponent"]


def transfer_cost(notional_usd: float) -> float:
    return TRANSFER["fixed_usd"] + notional_usd * TRANSFER["conversion_bps"] / 1e4
