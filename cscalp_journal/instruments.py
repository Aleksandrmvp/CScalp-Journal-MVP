"""Per-instrument price step + step value -> point value for RUB PnL.

MOEX PnL formula:  PnL_rub = (Δprice / step_size) * step_value * qty
so the point value (RUB per 1.0 price move per contract) = step_value / step_size.

Keyed by BASE symbol (contract month stripped), so FUT.GOLD-12.26 and a later
FUT.GOLD-03.27 both resolve. step_size (tick) inferred from observed fills and
cross-checked against the user-provided step values (2026-09-28).
"""
from __future__ import annotations

import re

from . import config, instruments_iss

# base symbol -> (step_size, step_value_rub)
STEP: dict[str, tuple[float, float]] = {
    "USDRUBF": (0.01, 10.0),
    "CNYRUBF": (0.001, 1.0),
    "EURRUBF": (0.01, 10.0),      # tick assumed like USDRUBF (not traded in sample)
    "GLDRUBF": (0.1, 0.1),
    "GOLD":    (0.1, 8.43414),    # step value tracks USD/RUB; snapshot value
    "Si":      (1.0, 1.0),
    "ED":      (0.0001, 8.4),     # EUR/USD futures: 0.1 USD per step, ~USD/RUB (approximate)
    "TATN":    (0.1, 0.1),        # shares (TQBR), lot = 1 share
    "TATNP":   (0.1, 0.1),
    "CNY":     (0.001, 1.0),
}

# user overrides: {"instruments": {"Si": {"step": 1.0, "step_value": 1.0}, ...}}
_USER: dict[str, tuple[float, float]] = {}
for _name, _v in config.USER.get("instruments", {}).items():
    _USER[_name] = STEP[_name] = (float(_v["step"]), float(_v["step_value"]))

_SUFFIX = re.compile(r"-\d{1,2}\.\d{2}$")   # FUT.Si-12.26 and FUT.Si-3.27 (one-digit month)


def base_symbol(ticker: str) -> str:
    """FUT.GOLD-12.26 -> GOLD ; FUT.USDRUBF -> USDRUBF."""
    t = ticker
    if t.startswith("FUT."):
        t = t[4:]
    return _SUFFIX.sub("", t)


def _step(ticker: str):
    """(step, step value): the user's own settings, then the MOEX ISS cache (shares may be keyed
    'TQBR.TATN' or 'TATN'), then the built-in table."""
    b = base_symbol(ticker)
    tail = b.rsplit(".", 1)[-1]
    for k in (b, tail):
        if k in _USER:
            return _USER[k]
    return instruments_iss.lookup(ticker, b) or STEP.get(b) or STEP.get(tail)


def point_value(ticker: str) -> float:
    """RUB per 1.0 price move per contract. 1.0 (points) if unconfigured."""
    s = _step(ticker)
    if not s:
        return 1.0
    size, value = s
    return value / size


def is_configured(ticker: str) -> bool:
    return _step(ticker) is not None
