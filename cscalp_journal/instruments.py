"""Per-instrument price step + step value -> point value for RUB PnL.

MOEX PnL formula:  PnL_rub = (Δprice / step_size) * step_value * qty
so the point value (RUB per 1.0 price move per contract) = step_value / step_size.

Keyed by BASE symbol (contract month stripped), so FUT.GOLD-12.26 and a later
FUT.GOLD-03.27 both resolve. step_size (tick) inferred from observed fills and
cross-checked against the user-provided step values (2026-09-28).
"""
from __future__ import annotations

import re

from . import config

# base symbol -> (step_size, step_value_rub)
STEP: dict[str, tuple[float, float]] = {
    "USDRUBF": (0.01, 10.0),
    "CNYRUBF": (0.001, 1.0),
    "EURRUBF": (0.01, 10.0),      # tick assumed like USDRUBF (not traded in sample)
    "GLDRUBF": (0.1, 0.1),
    "GOLD":    (0.1, 8.43414),    # step value tracks USD/RUB; snapshot value
    "Si":      (1.0, 1.0),
    "CNY":     (0.001, 1.0),
}

# user overrides: {"instruments": {"Si": {"step": 1.0, "step_value": 1.0}, ...}}
for _name, _v in config.USER.get("instruments", {}).items():
    STEP[_name] = (float(_v["step"]), float(_v["step_value"]))

_SUFFIX = re.compile(r"-\d{2}\.\d{2}$")


def base_symbol(ticker: str) -> str:
    """FUT.GOLD-12.26 -> GOLD ; FUT.USDRUBF -> USDRUBF."""
    t = ticker
    if t.startswith("FUT."):
        t = t[4:]
    return _SUFFIX.sub("", t)


def point_value(ticker: str) -> float:
    """RUB per 1.0 price move per contract. 1.0 (points) if unconfigured."""
    s = STEP.get(base_symbol(ticker))
    if not s:
        return 1.0
    size, value = s
    return value / size


def is_configured(ticker: str) -> bool:
    return base_symbol(ticker) in STEP
