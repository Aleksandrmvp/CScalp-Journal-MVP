"""Monthly performance report built from the terminal's own data.

Headline return/profit come from the terminal's equity snapshots (money_snap.total_assets,
last per day) because futures P&L settles daily via variation margin: it includes the
mark-to-market of open arbitrage legs, which fill-based realized P&L cannot see.
Realized P&L and commissions are reconstructed from fills and shown as a breakdown;
the remainder (revaluation, transfers, anything not in fills) is reported as such.
"""
from __future__ import annotations

import bisect
import re
import sqlite3
from datetime import datetime

from . import db, forex, views


def _equity_by_day(con: sqlite3.Connection) -> list[tuple[str, float]]:
    """[(day, total equity)] — last snapshot per slot per day, summed with carry-forward."""
    rows = con.execute(
        "SELECT ts, slot, total_assets FROM money_snap WHERE total_assets IS NOT NULL ORDER BY ts"
    ).fetchall()
    per_day: dict[str, dict[str, float]] = {}
    for r in rows:
        per_day.setdefault(r["ts"][:10], {})[r["slot"]] = r["total_assets"]
    out: list[tuple[str, float]] = []
    carry: dict[str, float] = {}
    for day in sorted(per_day):
        carry.update(per_day[day])
        out.append((day, sum(carry.values())))
    return out


def _pos_setting(con: sqlite3.Connection, key: str) -> float | None:
    try:
        v = float(db.get_setting(con, key) or "")
    except ValueError:
        return None
    return v if v > 0 else None


def _scale(con: sqlite3.Connection) -> float:
    """Terminal 'total assets' -> user's real capital. The prop terminal reports the leveraged
    limit; the user anchors it once: capital_now at the terminal equity recorded in
    capital_anchor_eq. 1.0 when no anchor is set."""
    cap, anchor = _pos_setting(con, "capital_now"), _pos_setting(con, "capital_anchor_eq")
    if not (cap and anchor):
        return 1.0
    # the user's figure already includes ledger items booked after the last equity snapshot
    adj = _cash_between(con, db.get_setting(con, "capital_anchor_day"), db.get_setting(con, "capital_asof_day"))
    return (cap - adj) / anchor


def set_capital(con: sqlite3.Connection, start: float | None, current: float | None,
                peak: float | None = None, peak_month: str | None = None) -> None:
    """Persist start/peak capital and anchor the current capital to the latest terminal equity.

    An unchanged `current` keeps its original anchor — the form re-submits the stored value,
    and re-anchoring it to a newer equity would erase the gains made since.
    """
    db.set_setting(con, "start_capital", "" if start is None else repr(start))
    db.set_setting(con, "peak_capital", "" if peak is None else repr(peak))
    db.set_setting(con, "peak_month", (peak_month or "") if peak is not None else "")
    if current is None:
        db.set_setting(con, "capital_now", "")
        db.set_setting(con, "capital_anchor_eq", "")
        return
    if current == _pos_setting(con, "capital_now") and _pos_setting(con, "capital_anchor_eq"):
        return
    eq = _equity_by_day(con)
    db.set_setting(con, "capital_now", repr(current))
    db.set_setting(con, "capital_anchor_eq", repr(eq[-1][1]) if eq else "")
    db.set_setting(con, "capital_anchor_day", eq[-1][0] if eq else "")
    db.set_setting(con, "capital_asof_day", datetime.now().strftime("%Y-%m-%d"))


_CASH_LINE = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2})\s+([-+−–]?\s*\d[\d\s  ]*(?:[.,]\d+)?)"
    r"\s*(?:₽|руб\.?|RUB)?\s+(\D.*?)\s*$")
_REF_DAY = re.compile(r"за\s+(\d{2})\.(\d{2})\.(\d{4})")


def _cash_kind(desc: str) -> str:
    d = desc.lower()
    if "корректировк" in d:
        return "adjust"          # statistics-only correction entered by the user; not real cash
    if "payout" in d:
        return "payout"          # prop firm's share of profit
    if "перевод средств" in d:
        return "transfer"        # deposit / withdrawal between accounts, not performance
    if "прибыл" in d:
        return "profit"          # variation-margin profit credit
    if "убыт" in d:
        return "loss"            # variation-margin loss write-off
    if "фандинг" in d:
        return "funding"         # credit or debit
    if "перенос" in d:
        return "transfer_fee"
    return "other"


def parse_cash_text(text: str) -> tuple[list[dict], list[str]]:
    """Statement lines 'YYYY-MM-DD  -201 ₽  description' -> rows; unparsable lines are returned."""
    rows: list[dict] = []
    bad: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = _CASH_LINE.match(line)
        if not m:
            bad.append(line.strip())
            continue
        raw = re.sub(r"[\s  ]", "", m.group(2).replace("−", "-").replace("–", "-"))
        try:
            amount = float(raw.replace(",", "."))
        except ValueError:
            bad.append(line.strip())
            continue
        desc = m.group(3)
        ref = _REF_DAY.search(desc)
        rows.append({"day": m.group(1), "amount": amount, "kind": _cash_kind(desc),
                     "ref_day": f"{ref.group(3)}-{ref.group(2)}-{ref.group(1)}" if ref else None,
                     "description": desc})
    return rows, bad


def add_cash(con: sqlite3.Connection, text: str) -> dict:
    rows, bad = parse_cash_text(text)
    added = 0
    seen: dict[tuple, int] = {}
    for r in rows:
        key = (r["day"], r["amount"], r["description"])
        seen[key] = seen.get(key, 0) + 1   # identical rows on one day are distinct entries
        added += con.execute(
            "INSERT OR IGNORE INTO cash_flow(day,amount,kind,ref_day,description,seq) VALUES(?,?,?,?,?,?)",
            (r["day"], r["amount"], r["kind"], r["ref_day"], r["description"], seen[key])).rowcount
    con.commit()
    return {"parsed": len(rows), "added": added, "duplicates": len(rows) - added, "unparsed": bad}


def exclude_cash(con: sqlite3.Connection, day: str, amount: float) -> int:
    """Leave matching ledger rows out of every statistic (the rows themselves stay stored)."""
    n = con.execute("UPDATE cash_flow SET kind=? WHERE day=? AND amount=?", (EXCLUDED, day, amount)).rowcount
    con.commit()
    return n


def _cash_between(con: sqlite3.Connection, lo: str | None, hi: str | None) -> float:
    """Sum of ledger items booked after day `lo` up to and including day `hi`."""
    if not lo or not hi:
        return 0.0
    r = con.execute("SELECT COALESCE(SUM(amount),0) FROM cash_flow WHERE day>? AND day<=? AND kind NOT IN (?, 'adjust')",
        (lo, hi, EXCLUDED)).fetchone()
    return float(r[0])


_KINDS = ("funding", "transfer_fee", "payout", "profit", "loss", "adjust", "transfer", "other")
EXCLUDED = "excluded"   # rows the user asked to leave out of every statistic


def _cash_sums(rows) -> dict:
    """Totals by kind. `net` is performance only: funding, fees (transfer fee + prop payout),
    trading (profit credits + loss write-offs). Transfers between accounts are not performance."""
    out = {k: 0.0 for k in _KINDS}
    n = {k: 0 for k in _KINDS}
    for r in rows:
        if r["kind"] == EXCLUDED:
            continue
        k = r["kind"] if r["kind"] in out else "other"
        out[k] += r["amount"]
        n[k] += 1
    out = {k: round(v, 2) for k, v in out.items()}
    fees = round(out["transfer_fee"] + out["payout"], 2)
    trading = round(out["profit"] + out["loss"] + out["adjust"], 2)
    return {**out, "fees": fees, "trading": trading,
            "net": round(out["funding"] + fees + trading + out["other"], 2), "counts": n}


def _cash_block(con: sqlite3.Connection, month: str, base: float) -> dict | None:
    """Ledger movements: month totals by booking day and all-time totals."""
    allrows = con.execute("SELECT day,amount,kind,ref_day,description FROM cash_flow "
                          "ORDER BY day DESC, rowid DESC").fetchall()
    if not allrows:
        return None
    mrows = [r for r in allrows if r["day"][:7] == month]
    m = _cash_sums(mrows)
    f_n = m["counts"]["funding"]
    avg = m["funding"] / f_n if f_n else None
    return {
        **m,
        "funding_avg": round(avg, 2) if avg is not None else None,
        "funding_avg_pct": round(avg / base * 100, 3) if avg is not None and base else None,
        "entries": [dict(r) for r in mrows],
        "all_time": {**_cash_sums(allrows), "first_day": allrows[-1]["day"], "last_day": allrows[0]["day"]},
    }


def _ledger_index(con: sqlite3.Connection) -> dict | None:
    """Capital by day reconstructed from the ledger, anchored on the user's stated current
    capital: cap(d) = capital_now + flows booked in (asof, d]  (minus those in (d, asof])."""
    rows = con.execute("SELECT day, kind, amount FROM cash_flow WHERE kind!=?", (EXCLUDED,)).fetchall()
    cap_now = _pos_setting(con, "capital_now")
    asof = db.get_setting(con, "capital_asof_day")
    if not rows or not cap_now or not asof:
        return None
    per: dict[str, dict] = {}
    for r in rows:
        d = per.setdefault(r["day"], {"pnl": 0.0, "transfer": 0.0, "adj": 0.0})
        d["transfer" if r["kind"] == "transfer" else "adj" if r["kind"] == "adjust" else "pnl"] += r["amount"]
    ds = sorted(per)
    cum, t = [], 0.0
    for d in ds:
        t += per[d]["pnl"] + per[d]["transfer"]
        cum.append(t)

    def flows_upto(day: str) -> float:
        i = bisect.bisect_right(ds, day)
        return cum[i - 1] if i else 0.0

    f_asof = flows_upto(asof)
    return {"per": per, "ds": ds, "cap_now": cap_now, "f_asof": f_asof, "F": flows_upto,
            "cap": lambda day: cap_now + flows_upto(day) - f_asof}


def _spill_days(con: sqlite3.Connection) -> list[dict]:
    """'Перелив': a day when the ruble leg's profit is offset by the forex leg's loss
    (the loss is 50-150% of the profit), as with the 187 000 RUB trade on 2026-09-14."""
    ix = _ledger_index(con)
    if not ix:
        return []
    out = []
    for day, rub in sorted(forex.rub_by_day(con).items()):
        d = ix["per"].get(day)
        profit = d["pnl"] + d["adj"] if d else 0.0
        if profit > 0 and rub < 0 and 0.5 <= -rub / profit <= 1.5:
            out.append({"day": day, "profit": round(profit, 2), "forex": round(rub, 2)})
    return out


def _trading_stats(con: sqlite3.Connection, month: str) -> dict:
    """Fill-based stats for the month: counts, maker share, realized P&L (reference only)."""
    fills = 0
    lots = maker_lots = realized = commission = 0.0
    for t in views.trades_with_liquidity(con):
        if t["ts"][:7] != month:
            continue
        fills += 1
        lots += t["qty"]
        if t["liquidity"] == "maker":
            maker_lots += t["qty"]
    unconfigured: set[str] = set()
    month_days = sorted(r[0] for r in con.execute(
        "SELECT DISTINCT substr(ts,1,10) FROM trade WHERE substr(ts,1,7)=?", (month,)).fetchall())
    for d in month_days:
        for tkr, v in views._intraday_recon(con, only_day=d).items():
            realized += v["realized"]
            commission += v["commission"]
            if v["fills"] and not views.instruments.is_configured(tkr):
                unconfigured.add(tkr)
    return {"fills": fills, "lots": lots,
            "maker_share": round(maker_lots / lots * 100, 1) if lots else None,
            "realized": round(realized, 2), "commission": round(commission, 2),
            "realized_net": round(realized - commission, 2), "points_mixed": sorted(unconfigured)}


def _day_stats(daily: list[dict]) -> dict:
    gp = sum(x["pnl"] for x in daily if x["pnl"] > 0)
    gl = -sum(x["pnl"] for x in daily if x["pnl"] < 0)
    wins = sum(1 for x in daily if x["pnl"] > 0)
    return {"days": len(daily), "win_days": wins,
            "win_rate": round(wins / len(daily) * 100, 1) if daily else None,
            "profit_factor": round(gp / gl, 2) if gl > 0 else None,
            "gross_profit": round(gp, 2), "gross_loss": round(gl, 2),
            "best_day": max(daily, key=lambda x: x["pnl"]) if daily else None,
            "worst_day": min(daily, key=lambda x: x["pnl"]) if daily else None}


def _ledger_month(con: sqlite3.Connection, month: str, first_eq_day: str | None) -> dict | None:
    """Month report for periods the terminal did not log: built from the broker ledger.
    Transfers between accounts are kept out of performance; the return base is the capital
    at the start of the month plus deposits made during it."""
    ix = _ledger_index(con)
    month_start = f"{month}-01"
    if not ix:
        return None
    ds_m = [d for d in ix["ds"] if d[:7] == month]
    if not ds_m or (first_eq_day and month_start >= first_eq_day):
        return None
    per, cap = ix["per"], ix["cap"]
    prev = [d for d in ix["ds"] if d < month_start]
    cap_start = ix["cap_now"] + (ix["F"](prev[-1]) if prev else 0.0) - ix["f_asof"]
    tr = [r[0] for r in con.execute(
        "SELECT amount FROM cash_flow WHERE kind='transfer' AND substr(day,1,7)=?", (month,)).fetchall()]
    deposits = sum(x for x in tr if x > 0)
    base = cap_start + deposits

    cum = 0.0
    curve = [{"day": month_start, "equity": round(cap_start, 2), "pnl": 0.0, "ret_pct": 0.0}]
    daily = []
    peak = base
    max_dd, max_dd_day = 0.0, None
    for d in ds_m:
        day_pnl = per[d]["pnl"] + per[d]["adj"]
        cum += day_pnl
        curve.append({"day": d, "equity": round(cap(d), 2), "pnl": round(cum, 2),
                      "ret_pct": round(cum / base * 100, 4) if base > 0 else 0.0})
        if abs(day_pnl) > 1e-9:
            daily.append({"day": d, "pnl": round(day_pnl, 2)})
        if base > 0:
            peak = max(peak, base + cum)
            dd = (peak - (base + cum)) / peak * 100
            if dd > max_dd:
                max_dd, max_dd_day = dd, d
    return {
        "empty": False, "source": "ledger",
        "period": {"start": ds_m[0], "end": ds_m[-1], "from_previous_month": True},
        "month_base": round(base, 2),
        "equity": {"start": round(cap_start, 2), "end": round(cap(ds_m[-1]), 2)},
        "net_profit": round(cum, 2),
        "return_pct": round(cum / base * 100, 4) if base > 0 else None,
        "curve": curve, "daily": daily, **_day_stats(daily),
        "max_drawdown_pct": round(max_dd, 3), "max_drawdown_day": max_dd_day,
        "best_cycle": _best_cycle(con, month),
        "trading": _trading_stats(con, month),
        "cash": _cash_block(con, month, base),
        "forex": _forex_legs(con, month),
        "fx": forex.month_block(con, month),
    }


def _capital_block(con: sqlite3.Connection, eq: list[tuple[str, float]], k: float) -> dict | None:
    """All-time capital curve (eq already scaled by k). Drawdown is ALWAYS measured from the
    starting capital (never from a running peak); it is 0 while capital is at/above the start.
    The user-entered historical peak is informational only. Without a starting capital the
    running peak is used as the reference."""
    if not eq:
        return None
    ix = _ledger_index(con)
    if ix:
        # before the terminal's logs begin, the curve comes from the ledger, from the first deposit
        deposits = [d for d in ix["ds"] if ix["per"][d]["transfer"] > 0]
        first = deposits[0] if deposits else ix["ds"][0]
        eq = [(d, ix["cap"](d)) for d in ix["ds"] if first <= d < eq[0][0]] + eq
    start = _pos_setting(con, "start_capital")
    peak_manual = _pos_setting(con, "peak_capital")
    run_peak = None
    series = []
    for d, c in eq:
        run_peak = c if run_peak is None else max(run_peak, c)
        ref = start or run_peak
        series.append({"day": d, "capital": round(c, 2),
                       "dd_pct": round(max(0.0, (ref - c) / ref * 100), 3)})
    # ledger items booked after the last equity snapshot are already real money: extend the curve
    c = eq[-1][1]
    for day, amt in con.execute("SELECT day, SUM(amount) FROM cash_flow WHERE day>? AND kind NOT IN (?, 'adjust') "
                                "GROUP BY day ORDER BY day", (eq[-1][0], EXCLUDED)).fetchall():
        c += amt
        run_peak = max(run_peak, c)
        ref = start or run_peak
        series.append({"day": day, "capital": round(c, 2), "dd_pct": round(max(0.0, (ref - c) / ref * 100), 3)})
    cur = series[-1]
    ref = start or run_peak
    worst = max(series, key=lambda s: s["dd_pct"])
    d0 = series[0]["day"]
    trs = [r[0] for r in con.execute("SELECT amount FROM cash_flow WHERE kind='transfer' AND day>=?", (d0,))]
    perf = con.execute("SELECT COALESCE(SUM(amount),0) FROM cash_flow WHERE kind NOT IN ('transfer', 'adjust', ?) AND day>=?",
                       (EXCLUDED, d0)).fetchone()[0]
    flows = {"since": d0, "deposits": round(sum(x for x in trs if x > 0), 2),
             "withdrawals": round(sum(x for x in trs if x < 0), 2), "performance": round(perf, 2)}
    return {
        "flows": flows,
        "start": start, "reference": round(ref, 2), "current": cur["capital"],
        "peak": round(max(peak_manual or 0.0, run_peak), 2),
        "peak_manual": peak_manual, "peak_month": db.get_setting(con, "peak_month") or None,
        "pnl_since_start": round(cur["capital"] - start, 2) if start else None,
        "return_since_start_pct": round((cur["capital"] - start) / start * 100, 3) if start else None,
        "drawdown_pct": cur["dd_pct"], "drawdown_rub": round(max(0.0, ref - cur["capital"]), 2),
        "max_drawdown_pct": worst["dd_pct"], "max_drawdown_day": worst["day"] if worst["dd_pct"] else None,
        "first_day": series[0]["day"], "last_day": cur["day"],
        "series": series, "scale": round(k, 6),
        "anchor": {"capital_now": _pos_setting(con, "capital_now"),
                   "terminal_equity": _pos_setting(con, "capital_anchor_eq")},
    }


def _forex_legs(con: sqlite3.Connection, month: str) -> dict | None:
    """Manual (external) legs closed/realized in the month. Price points, not RUB."""
    legs = 0
    realized = 0.0
    for b in con.execute("SELECT id FROM bundle").fetchall():
        t = views.bundle_trades(con, b["id"])
        for c in t.get("trades", []):
            for e in c["events"]:
                if e["kind"] == "manual" and (e["ts"] or "")[:7] == month:
                    legs += 1
                    realized += e["realized_leg"]
    return {"legs": legs, "realized_points": round(realized, 4)} if legs else None


def _best_cycle(con: sqlite3.Connection, month: str) -> dict | None:
    best = None
    for b in con.execute("SELECT id FROM bundle").fetchall():
        t = views.bundle_trades(con, b["id"])
        for c in t.get("trades", []):
            if c["status"] != "closed" or (c["exit_ts"] or "")[:7] != month:
                continue
            if best is None or c["realized_net"] > best["net"]:
                best = {"bundle": t["name"], "net": c["realized_net"], "exit": c["exit_ts"],
                        "points_only": t["points_only"]}
    return best


def build(con: sqlite3.Connection, month: str | None = None) -> dict:
    k = _scale(con)
    eq = [(d, v * k) for d, v in _equity_by_day(con)]
    trade_days = [r[0] for r in con.execute("SELECT DISTINCT substr(ts,1,10) FROM trade").fetchall()]
    led_months = {r[0][:7] for r in con.execute("SELECT DISTINCT day FROM cash_flow").fetchall()}
    fx_months = {r[0] for r in con.execute("SELECT DISTINCT substr(day,1,7) FROM forex_result").fetchall()}
    months = sorted({d[:7] for d, _ in eq} | {d[:7] for d in trade_days} | led_months | fx_months)
    if not month:
        month = months[-1] if months else datetime.now().strftime("%Y-%m")

    in_month = [(d, v) for d, v in eq if d[:7] == month]
    spills = _spill_days(con)
    capital = _capital_block(con, eq, k)
    if capital:
        capital["spills"] = spills
    base_out = {"month": month, "months": months, "empty": not in_month, "capital": capital,
                "spills": [s for s in spills if s["day"][:7] == month]}
    led = _ledger_month(con, month, eq[0][0] if eq else None)
    if led:
        return {**base_out, **led}
    if not in_month:
        return base_out

    prev = [(d, v) for d, v in eq if d[:7] < month]
    if prev:
        points = [prev[-1]] + in_month
        carried_from_prev = True
    else:
        points = in_month
        carried_from_prev = False
    start_day, start_eq = points[0]
    end_day, end_eq = points[-1]

    base = start_eq
    curve = [{"day": d, "equity": round(v, 2), "pnl": round(v - start_eq, 2),
              "ret_pct": round((v - start_eq) / base * 100, 4)} for d, v in points]

    daily = [{"day": points[i][0], "pnl": round(points[i][1] - points[i - 1][1], 2)}
             for i in range(1, len(points))]

    peak = points[0][1]
    max_dd = 0.0
    max_dd_day = None
    for d, v in points:
        peak = max(peak, v)
        dd = (peak - v) / peak * 100 if peak else 0.0
        if dd > max_dd:
            max_dd, max_dd_day = dd, d

    net = end_eq - start_eq

    cash = _cash_block(con, month, base)
    return {
        **base_out, "source": "equity",
        "period": {"start": start_day, "end": end_day, "from_previous_month": carried_from_prev},
        "month_base": round(base, 2),
        "equity": {"start": round(start_eq, 2), "end": round(end_eq, 2)},
        "net_profit": round(net, 2),
        "return_pct": round(net / base * 100, 4),
        "curve": curve,
        "daily": daily,
        **_day_stats(daily),
        "max_drawdown_pct": round(max_dd, 3),
        "max_drawdown_day": max_dd_day,
        "best_cycle": _best_cycle(con, month),
        "trading": _trading_stats(con, month),
        "cash": cash,
        "forex": _forex_legs(con, month),
        "fx": forex.month_block(con, month),
    }
