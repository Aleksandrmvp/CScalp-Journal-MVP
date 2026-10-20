"""Price step and its value from MOEX ISS (cached in the user's data folder).

Futures: value of one price point = STEPPRICE / MINSTEP. Shares and currency: LOTSIZE (CScalp
shows quantity in lots, the price per unit). The cache is refreshed in the background, so a
missing network never delays the start; the built-in table in instruments.py stays the fallback.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from datetime import date, datetime

from . import config

CACHE = config.DATA_DIR / "instrument_steps.json"
STALE_DAYS = 3
TIMEOUT = 20
BASE = "https://iss.moex.com"
FORTS = "/iss/engines/futures/markets/forts/securities.json?iss.meta=off"
SHARES = "/iss/engines/stock/markets/shares/boards/TQBR/securities.json?iss.meta=off"
CURR = "/iss/engines/currency/markets/selt/boards/CETS/securities.json?iss.meta=off"
_MONTH = re.compile(r"-\d{1,2}\.\d{2}$")      # Si-12.26 -> Si

_doc: dict | None = None
_lock = threading.Lock()


def _rows(url: str) -> list[dict]:
    req = urllib.request.Request(BASE + url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        doc = json.loads(r.read())
    cols = doc["securities"]["columns"]
    return [dict(zip(cols, row)) for row in doc["securities"]["data"]]


def build() -> dict:
    best: dict[str, dict] = {}
    for r in _rows(FORTS):
        short = str(r.get("SHORTNAME") or "")
        step, price = r.get("MINSTEP"), r.get("STEPPRICE")
        if not short or not step or price is None:
            continue
        expire = str(r.get("LASTTRADEDATE") or "2100-01-01")
        keys = {short, _MONTH.sub("", short), str(r.get("ASSETCODE") or "")}
        keys.discard("")
        for k in keys:                      # the nearest expiry wins for a base symbol
            if k not in best or expire < best[k]["expire"]:
                best[k] = {"expire": expire, "step": float(step), "value": float(price)}
    fut = {k: {"step": v["step"], "value": v["value"]} for k, v in best.items()}
    shr, cur = {}, {}
    for r in _rows(SHARES):
        sec, lot = r.get("SECID"), r.get("LOTSIZE")
        if sec and lot:
            step = float(r.get("MINSTEP") or 0.01)
            shr[str(sec)] = {"step": step, "value": step * float(lot)}
    for r in _rows(CURR):
        sec, lot = r.get("SECID"), r.get("LOTSIZE")
        if sec and lot:
            step = float(r.get("MINSTEP") or 0.0001)
            cur[str(sec)] = {"step": step, "value": step * float(lot)}
    return {"updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "futures": fut, "shares": shr, "currency": cur}


def load() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def refresh() -> dict:
    global _doc
    doc = build()
    if not (doc["futures"] and doc["shares"]):
        raise RuntimeError("пустой ответ MOEX ISS")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, CACHE)
    with _lock:
        _doc = doc
    return doc


def _stale(doc: dict) -> bool:
    try:
        return (date.today() - datetime.strptime(doc["updated"][:10], "%Y-%m-%d").date()).days >= STALE_DAYS
    except Exception:
        return True


def lookup(ticker: str, base: str) -> tuple[float, float] | None:
    """(step, step value) for a CScalp ticker; `base` is its contract-month-free symbol."""
    global _doc
    with _lock:
        if _doc is None:
            _doc = load()
        doc = _doc
    t = ticker.upper()
    sect = "futures" if t.startswith("FUT.") else "currency" if t.startswith("CETS.") else "shares"
    block = doc.get(sect) or {}
    for k in (base, base.rsplit(".", 1)[-1]):
        c = block.get(k)
        if c and c.get("step") and c.get("value") is not None:
            return float(c["step"]), float(c["value"])
    return None


def _background() -> None:
    while True:
        try:
            if _stale(load()):
                refresh()
        except Exception:
            pass                            # offline or ISS hiccup: keep the cache / built-in table
        time.sleep(6 * 3600)


def start_background() -> None:
    threading.Thread(target=_background, daemon=True, name="instruments-iss").start()
