"""Safe arithmetic evaluator for a user-editable spread formula.

Supports + - * / and parentheses over numbers and instrument symbols. No Python
eval, no names/functions — symbols are resolved to leg entry prices via a
callback. Example (TradingView notation), evaluated left-to-right like TV:
    RUS:GLDRUB.P/RUS:SI1!*31.1/RUS:GD1!*100000
"""
from __future__ import annotations

import re
from typing import Callable

_TOKEN = re.compile(r"\s*(?:(?P<num>\d+(?:[.,]\d+)?)|(?P<op>[()+\-*/])|(?P<sym>[A-Za-z][A-Za-z0-9:._!\-]*))")


def _tokenize(expr: str):
    pos = 0
    out = []
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m or m.end() == pos:
            if expr[pos:].strip() == "":
                break
            raise ValueError(f"неразборчивый символ в позиции {pos}: {expr[pos:pos+10]!r}")
        pos = m.end()
        if m.group("num"):
            out.append(("num", float(m.group("num").replace(",", "."))))
        elif m.group("op"):
            out.append(("op", m.group("op")))
        elif m.group("sym"):
            out.append(("sym", m.group("sym")))
    return out


def evaluate(expr: str, resolve: Callable[[str], float]) -> float:
    """Evaluate expr, resolving symbols via resolve(symbol)->price."""
    toks = _tokenize(expr)
    i = 0

    def peek():
        return toks[i] if i < len(toks) else (None, None)

    def eat(op=None):
        nonlocal i
        t = toks[i]
        i += 1
        return t

    def factor() -> float:
        nonlocal i
        kind, val = peek()
        if kind == "op" and val == "-":
            eat(); return -factor()
        if kind == "op" and val == "+":
            eat(); return factor()
        if kind == "op" and val == "(":
            eat()
            v = expr_()
            k2, v2 = peek()
            if not (k2 == "op" and v2 == ")"):
                raise ValueError("нет закрывающей скобки")
            eat()
            return v
        if kind == "num":
            eat(); return val
        if kind == "sym":
            eat(); return float(resolve(val))
        raise ValueError("ожидалось число или инструмент")

    def term() -> float:
        v = factor()
        while True:
            kind, val = peek()
            if kind == "op" and val in "*/":
                eat()
                r = factor()
                v = v * r if val == "*" else v / r
            else:
                return v

    def expr_() -> float:
        v = term()
        while True:
            kind, val = peek()
            if kind == "op" and val in "+-":
                eat()
                r = term()
                v = v + r if val == "+" else v - r
            else:
                return v

    result = expr_()
    if i != len(toks):
        raise ValueError("лишние символы в конце формулы")
    return result


def symbols(expr: str) -> list[str]:
    return [v for k, v in _tokenize(expr) if k == "sym"]


def _core(s: str) -> str:
    """Alphabetic core of a symbol/ticker for fuzzy matching."""
    s = s.upper()
    if ":" in s:
        s = s.split(":", 1)[1]
    if s.startswith("FUT."):
        s = s[4:]
    s = s.replace(".P", "").replace("!", "")
    return re.sub(r"[^A-Z]", "", s)


# TradingView symbol core -> CScalp base symbol (uppercased). MOEX FORTS short
# codes differ from CScalp names: CR=CNY futures, GD=GOLD, Si=Si, etc.
_TVALIAS = {
    "CR": "CNY",           # RUS:CR1! -> FUT.CNY-12.26
    "GD": "GOLD",          # RUS:GD1! -> FUT.GOLD-12.26
    "SI": "SI",            # RUS:SI1! -> FUT.Si-12.26
    "CNYRUB": "CNYRUBF",   # RUS:CNYRUB.P -> FUT.CNYRUBF (perpetual)
    "USDRUB": "USDRUBF",
    "EURRUB": "EURRUBF",
    "GLDRUB": "GLDRUBF",
}


def _leg_base(ticker: str) -> str:
    """FUT.CNY-12.26 -> CNY ; FUT.CNYRUBF -> CNYRUBF (uppercased)."""
    from . import instruments
    return instruments.base_symbol(ticker).upper()


def _usdrub(lbp: dict[str, float]):
    if "SI" in lbp:
        return lbp["SI"] / 1000.0, "SI/1000"   # Si price = USD/RUB * 1000
    if "USDRUBF" in lbp:
        return lbp["USDRUBF"], "USDRUBF"
    return None, None


def _cnyrub(lbp: dict[str, float]):
    if "CNY" in lbp:
        return lbp["CNY"], "CNY"
    if "CNYRUBF" in lbp:
        return lbp["CNYRUBF"], "CNYRUBF"
    return None, None


def _synthetic(core: str, lbp: dict[str, float]):
    """Synthetic forex rate implied by the MOEX legs (when no forex leg exists).

    Returns (price, label) where label names the leg ratio, e.g. USDRUBF/CNYRUBF.
    """
    if core == "USDCNH":
        u, un = _usdrub(lbp)
        c, cn = _cnyrub(lbp)
        if u and c:
            return u / c, f"{un}/{cn}"      # USD/CNH = USD/RUB ÷ CNY/RUB
    return None, None


def _is_forex(sym: str) -> bool:
    """A forex/external symbol (TradingView-style FX: or OANDA: prefix)."""
    p = sym.split(":", 1)[0].upper() if ":" in sym else ""
    return p in ("FX", "FX_IDC", "OANDA", "FOREXCOM")


def build_resolver(entry_prices: dict[str, float]):
    """Return (resolve, bindings_recorder). resolve(sym)->price; bindings dict filled.

    An unmatched forex-prefixed symbol (e.g. FX:USDCNH) resolves to 1.0 — the
    forex hedge leg is optional, so when it isn't in the bundle it drops out of
    the formula (identity for * and /). Unmatched non-forex symbols still raise,
    so a typo in a MOEX leg isn't silently swallowed.
    """
    leg_base = {_leg_base(t): t for t in entry_prices}
    leg_base_price = {b: entry_prices[t] for b, t in leg_base.items()}
    bindings: dict[str, dict] = {}

    def _match(canon: str):
        if canon in leg_base:
            return leg_base[canon]
        for base, t in leg_base.items():            # prefix fallback
            if base.startswith(canon) or canon.startswith(base):
                return t
        return None

    def resolve(sym: str) -> float:
        c = _core(sym)                              # e.g. CR, GD, CNYRUB
        t = _match(_TVALIAS.get(c, c))
        if t is None and len(c) >= 3:               # dated code e.g. GDZ2026 -> GDZ -> GD
            c2 = c[:-1]
            t = _match(_TVALIAS.get(c2, c2))
        if t is not None:
            bindings[sym] = {"ticker": t, "price": entry_prices[t]}
            return entry_prices[t]
        if _is_forex(sym):
            # no forex leg -> use the synthetic rate implied by the MOEX legs
            syn, label = _synthetic(c, leg_base_price)
            if syn is not None:
                bindings[sym] = {"ticker": "синтетика", "price": syn,
                                 "synthetic": True, "formula": label}
                return syn
            bindings[sym] = {"ticker": "(нет форекс-ноги)", "price": 1.0, "defaulted": True}
            return 1.0
        raise KeyError(f"символ {sym!r} не сопоставлен ни одной ноге связки")

    return resolve, bindings
