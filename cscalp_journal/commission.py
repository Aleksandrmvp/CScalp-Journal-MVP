"""Commission model for MOEX FORTS futures (prop fee schedule).

Per-lot, per fill, charged on both open and close. The prop's formulas:

    maker (limit-order entry): 0.18 RUB/lot   (clearing fee only)
    taker (market-order entry): lots * (reg_fee * 1.8 + 0.18)

where `reg_fee` is the exchange "Сбор за регистрацию сделки" — instrument
specific (REG_FEE below). The clearing fee (0.18) is flat for every instrument,
so MAKER commission is always exact; TAKER needs the instrument's reg_fee.

Keyed by base symbol so it survives contract-month rollover.
"""
from __future__ import annotations

CLEARING_PER_LOT = 0.18   # RUB per lot, both maker and taker
TAKER_MULT = 1.8          # multiplier on the exchange registration fee for takers

# base symbol -> "Сбор за регистрацию сделки" (RUB per lot). Only priced on
# TAKER fills; maker fills ignore it. Values calibrated against the real
# KasCapital fee export for 2026-09-28 (see scratchpad/reg2.py) using the
# terminal's maker/taker split. Approximate — refine from the official fee
# schedule when available. GOLD had only maker fills, so its reg is unknown
# (left out -> GOLD priced at the flat 0.18 maker rate, matching the export).
REG_FEE: dict[str, float] = {
    "CNY": 0.78,
    "CNYRUBF": 0.52,
    "GLDRUBF": 1.53,
    "Si": 3.08,
    "USDRUBF": 2.93,
    "UCNY": 2.07,
    # "GOLD", "EURRUBF": no taker samples yet.
}


# user overrides: {"commission": {"clearing_per_lot": 0.18, "taker_multiplier": 1.8, "reg_fee": {...}}}
from . import config as _config

_user = _config.USER.get("commission", {})
CLEARING_PER_LOT = float(_user.get("clearing_per_lot", CLEARING_PER_LOT))
TAKER_MULT = float(_user.get("taker_multiplier", TAKER_MULT))
REG_FEE.update({k: float(v) for k, v in _user.get("reg_fee", {}).items()})


def _base(ticker: str) -> str:
    from . import instruments
    return instruments.base_symbol(ticker)


def per_fill(ticker: str, side: str = "", price: float = 0.0, qty: float = 0.0,
             liquidity: str | None = None) -> float:
    """Commission (RUB) for a single fill of `qty` lots.

    maker/unknown -> clearing only. taker -> clearing + reg_fee * 1.8. If a
    taker fill's reg_fee is unknown, only the (exact) clearing part is returned;
    use `needs_reg_fee` to surface that the exchange part is still missing.
    """
    lots = abs(qty)
    fee = CLEARING_PER_LOT * lots
    if liquidity == "taker":
        reg = REG_FEE.get(_base(ticker))
        if reg is not None:
            fee += reg * TAKER_MULT * lots
    return fee


def needs_reg_fee(ticker: str, liquidity: str | None) -> bool:
    """True if this fill is a taker whose instrument reg_fee is not configured."""
    return liquidity == "taker" and _base(ticker) not in REG_FEE


def is_configured(ticker: str) -> bool:
    """Maker side is always priced; taker also needs the reg_fee."""
    return _base(ticker) in REG_FEE
