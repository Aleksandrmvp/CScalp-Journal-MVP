"""Forex-leg results (USDT account at FxPro), converted to RUB at the Bank of Russia rate.

A result is a signed USDT amount on a day (USDT is treated as 1 USD). It is converted at the
official USD rate for that day, or the nearest earlier cached one. Rates live in `fx_rate`.
"""
from __future__ import annotations

import sqlite3
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime


def _fetch_rate(day: str) -> float | None:
    d = datetime.strptime(day, "%Y-%m-%d")
    url = "https://www.cbr.ru/scripts/XML_daily.asp?date_req=" + d.strftime("%d/%m/%Y")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                                    timeout=20) as r:
            root = ET.fromstring(r.read())
        for v in root.findall("Valute"):
            if v.findtext("CharCode") == "USD":
                return float(v.findtext("Value").replace(",", ".")) / float(v.findtext("Nominal") or 1)
    except Exception:
        return None
    return None


def cached_rate(con: sqlite3.Connection, day: str) -> float | None:
    r = con.execute("SELECT rate FROM fx_rate WHERE day<=? ORDER BY day DESC LIMIT 1", (day,)).fetchone()
    return r["rate"] if r else None


def ensure_rates(con: sqlite3.Connection) -> int:
    """Cache the official USD/RUB rate for every day that has a result but no rate yet."""
    fetched = 0
    for r in con.execute("SELECT DISTINCT day FROM forex_result ORDER BY day").fetchall():
        if con.execute("SELECT 1 FROM fx_rate WHERE day=?", (r["day"],)).fetchone():
            continue
        rate = _fetch_rate(r["day"])
        if rate is not None:
            con.execute("INSERT OR REPLACE INTO fx_rate(day,rate) VALUES(?,?)", (r["day"], rate))
            fetched += 1
    con.commit()
    return fetched


def add_result(con: sqlite3.Connection, day: str, usd: float, note: str = "",
               rub: float | None = None) -> dict:
    """Record a forex-leg result (negative = loss). `rub` fixes the ruble amount (to match the
    ruble leg exactly); otherwise it is converted at the day's official USD/RUB rate."""
    datetime.strptime(day, "%Y-%m-%d")
    rid = f"manual:{day}:{usd}"
    added = con.execute(
        "INSERT INTO forex_result(id,day,usd,note,rub) VALUES(?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET note=excluded.note, rub=excluded.rub",
        (rid, day, usd, note, rub)).rowcount
    rate_known = bool(con.execute("SELECT 1 FROM fx_rate WHERE day=?", (day,)).fetchone())
    if not rate_known:
        rate = _fetch_rate(day)
        if rate is not None:
            con.execute("INSERT OR REPLACE INTO fx_rate(day,rate) VALUES(?,?)", (day, rate))
            rate_known = True
    con.commit()
    return {"added": added, "rate_cached": rate_known}


def delete_result(con: sqlite3.Connection, rid: str) -> int:
    """Only hand-entered results can be deleted; the broker API's rows are re-created by sync."""
    n = con.execute("DELETE FROM forex_result WHERE id=? AND source='manual'", (rid,)).rowcount
    con.commit()
    return n


def _entries(con: sqlite3.Connection, rows) -> list[dict]:
    """Priced entries. A hand-entered result for a day overrides the broker API's rows for it."""
    manual_days = {r["day"] for r in rows if r["source"] == "manual"}
    out = []
    for r in rows:
        if r["source"] != "manual" and r["day"] in manual_days:
            continue
        fixed = r["rub"] is not None
        rate = r["rub"] / r["usd"] if fixed and r["usd"] else cached_rate(con, r["day"])
        rub = r["rub"] if fixed else (r["usd"] * rate if rate else None)
        out.append({"id": r["id"], "source": r["source"], "day": r["day"], "usd": r["usd"], "rate": round(rate, 4) if rate else None,
                    "rub": round(rub, 2) if rub is not None else None, "fixed": fixed, "note": r["note"]})
    return out


def rub_by_day(con: sqlite3.Connection) -> dict[str, float]:
    rows = con.execute("SELECT id, day, usd, note, rub, source FROM forex_result ORDER BY day").fetchall()
    out: dict[str, float] = {}
    for e in _entries(con, rows):
        if e["rub"] is not None:
            out[e["day"]] = out.get(e["day"], 0.0) + e["rub"]
    return out


def month_block(con: sqlite3.Connection, month: str) -> dict | None:
    rows = con.execute("SELECT id, day, usd, note, rub, source FROM forex_result WHERE substr(day,1,7)=? "
                       "ORDER BY day", (month,)).fetchall()
    entries = _entries(con, rows)
    if not entries:
        return None
    priced = [e["rub"] for e in entries if e["rub"] is not None]
    return {"results": len(entries), "net_usd": round(sum(e["usd"] for e in entries), 2),
            "net_rub": round(sum(priced), 2), "unpriced": len(entries) - len(priced), "entries": entries}
