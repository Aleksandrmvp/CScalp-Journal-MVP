"""Derived views over stored facts: maker/taker, positions, PnL (in points), journal.

PnL is expressed in PRICE POINTS x CONTRACTS, not RUB. Converting to money needs
per-instrument contract multipliers and the commission formula from the FAQ,
which are not in the logs yet. Kept honest until those are provided.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from . import config, commission, instruments, formula as formula_mod


def _order_liquidity(con: sqlite3.Connection) -> dict[str, str]:
    """order_id -> 'maker'|'taker'|'unknown' from resting time in the book."""
    rows = con.execute(
        "SELECT order_id, "
        " MIN(CASE WHEN state='active' THEN ts END) AS act, "
        " MAX(CASE WHEN state='inactive' THEN ts END) AS inact "
        "FROM order_life GROUP BY order_id"
    ).fetchall()
    out: dict[str, str] = {}
    for r in rows:
        if not r["act"]:
            out[r["order_id"]] = "unknown"
            continue
        if not r["inact"]:
            out[r["order_id"]] = "maker"  # still resting / open
            continue
        act = datetime.fromisoformat(r["act"])
        inact = datetime.fromisoformat(r["inact"])
        resting = (inact - act).total_seconds()
        out[r["order_id"]] = "taker" if resting < config.TAKER_MAX_RESTING_SECONDS else "maker"
    return out


def trades_with_liquidity(con: sqlite3.Connection) -> list[dict]:
    liq = _order_liquidity(con)
    rows = con.execute(
        "SELECT trade_id,order_id,ts,ticker,slot,side,price,qty FROM trade ORDER BY ts"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["liquidity"] = liq.get(r["order_id"], "unknown")
        d["commission"] = commission.per_fill(r["ticker"], r["side"], r["price"], r["qty"], d["liquidity"])
        out.append(d)
    return out


def _intraday_recon(con: sqlite3.Connection, only_day: str | None = None) -> dict[str, dict]:
    """Average-cost walk over fills -> per-ticker realized & fill count.

    Walks ALL fills chronologically so the average cost basis carries across
    days; when `only_day` (YYYY-MM-DD) is set, realized/commission/fills are
    tallied for that day only (cost basis still reflects earlier fills).

    NOTE: net here does NOT include a day-open carried position that predates
    the parsed logs, so it must not be shown as the live position. Use terminal
    snapshots (position_snap) for the authoritative net. See positions().
    """
    trades = trades_with_liquidity(con)
    book: dict[str, dict] = {}
    for t in trades:
        on_day = only_day is None or t["ts"][:10] == only_day
        b = book.setdefault(t["ticker"], {"qty": 0.0, "avg": 0.0, "realized": 0.0,
                                          "fills": 0, "commission": 0.0})
        pv = instruments.point_value(t["ticker"])
        signed = t["qty"] if t["side"] == "Buy" else -t["qty"]
        if on_day:
            b["fills"] += 1
            b["commission"] += t["commission"]
        realized_leg = 0.0
        if b["qty"] == 0 or (b["qty"] > 0) == (signed > 0):
            new_qty = b["qty"] + signed
            if new_qty != 0:
                b["avg"] = (b["avg"] * b["qty"] + t["price"] * signed) / new_qty
            b["qty"] = new_qty
        else:
            closing = min(abs(signed), abs(b["qty"]))
            direction = 1 if b["qty"] > 0 else -1
            realized_leg = closing * (t["price"] - b["avg"]) * direction * pv
            b["qty"] += signed
            if (b["qty"] > 0) != (direction > 0) and b["qty"] != 0:
                b["avg"] = t["price"]
        if on_day:
            b["realized"] += realized_leg
    return book


def _latest_snapshots(con: sqlite3.Connection) -> dict[str, dict]:
    """Latest terminal-reported position per ticker (authoritative net + avg)."""
    rows = con.execute(
        "SELECT ticker, amount, price, ts FROM position_snap p "
        "WHERE ts = (SELECT MAX(ts) FROM position_snap p2 WHERE p2.ticker=p.ticker)"
    ).fetchall()
    return {r["ticker"]: {"amount": r["amount"], "price": r["price"], "ts": r["ts"]} for r in rows}


def positions(con: sqlite3.Connection) -> list[dict]:
    """Open positions from the terminal's own reported state (source of truth).

    Net qty and average price come from the latest OnPositionUpdate snapshot the
    terminal logged; this self-heals from carried overnight positions and any
    unparsed fills. Realized PnL and fill count are intraday, from parsed trades.
    """
    snaps = _latest_snapshots(con)
    recon = _intraday_recon(con)
    out = []
    for ticker in sorted(set(snaps) | set(recon)):
        snap = snaps.get(ticker)
        r = recon.get(ticker, {"realized": 0.0, "fills": 0, "qty": 0.0})
        net = snap["amount"] if snap else None
        # "Open positions" table: show only instruments that are actually open
        if not net:
            continue
        comm = r.get("commission", 0.0)
        out.append({
            "ticker": ticker,
            "net_qty": round(net, 8) if net is not None else None,
            "avg_price": round(snap["price"], 6) if (snap and net) else None,
            "realized_points": round(r["realized"], 6),
            "commission": round(comm, 6),
            "realized_net": round(r["realized"] - comm, 6),
            "fills": r["fills"],
            "source": "terminal" if snap else "reconstructed",
            "synced": bool(snap),
        })
    return out


def _trade_bundles(con: sqlite3.Connection) -> dict[str, list[dict]]:
    """trade_id -> [{id, name}] of bundles it belongs to."""
    out: dict[str, list[dict]] = {}
    for r in con.execute(
        "SELECT m.trade_id, b.id, b.name FROM bundle_member m JOIN bundle b ON b.id=m.bundle_id"
    ).fetchall():
        out.setdefault(r["trade_id"], []).append({"id": r["id"], "name": r["name"]})
    return out


def journal(con: sqlite3.Connection) -> list[dict]:
    """Merged chronological action log: submits, cancels, fills."""
    liq = _order_liquidity(con)
    tb = _trade_bundles(con)
    items: list[dict] = []
    for r in con.execute("SELECT ts,ticker,kind,side,price,qty FROM order_cmd").fetchall():
        items.append({
            "ts": r["ts"], "event": r["kind"].upper(), "ticker": r["ticker"],
            "side": r["side"], "price": r["price"], "qty": r["qty"],
            "liquidity": None, "id": None, "bundles": [],
        })
    for r in con.execute("SELECT trade_id,order_id,ts,ticker,side,price,qty FROM trade").fetchall():
        items.append({
            "ts": r["ts"], "event": "FILL", "ticker": r["ticker"], "side": r["side"],
            "price": r["price"], "qty": r["qty"],
            "liquidity": liq.get(r["order_id"], "unknown"), "id": r["trade_id"],
            "bundles": tb.get(r["trade_id"], []),
        })
    items.sort(key=lambda x: x["ts"])
    return items


def unassigned_fills(con: sqlite3.Connection) -> list[dict]:
    """Fills not yet assigned to any bundle."""
    liq = _order_liquidity(con)
    rows = con.execute(
        "SELECT trade_id,order_id,ts,ticker,side,price,qty FROM trade "
        "WHERE trade_id NOT IN (SELECT trade_id FROM bundle_member) ORDER BY ts"
    ).fetchall()
    return [{
        "trade_id": r["trade_id"], "ts": r["ts"], "ticker": r["ticker"],
        "side": r["side"], "price": r["price"], "qty": r["qty"],
        "liquidity": liq.get(r["order_id"], "unknown"),
    } for r in rows]


def _agg_legs(legs: list[dict]) -> list[dict]:
    """Aggregate bundle legs per ticker: net qty, VWAP per side, realized points."""
    book: dict[str, dict] = {}
    for l in legs:
        b = book.setdefault(l["ticker"], {
            "buy_qty": 0.0, "buy_notional": 0.0, "sell_qty": 0.0, "sell_notional": 0.0,
        })
        if l["side"] == "Buy":
            b["buy_qty"] += l["qty"]; b["buy_notional"] += l["qty"] * l["price"]
        else:
            b["sell_qty"] += l["qty"]; b["sell_notional"] += l["qty"] * l["price"]
    out = []
    for ticker, b in sorted(book.items()):
        buy_vwap = b["buy_notional"] / b["buy_qty"] if b["buy_qty"] else None
        sell_vwap = b["sell_notional"] / b["sell_qty"] if b["sell_qty"] else None
        matched = min(b["buy_qty"], b["sell_qty"])
        realized = (sell_vwap - buy_vwap) * matched if (buy_vwap and sell_vwap) else 0.0
        out.append({
            "ticker": ticker,
            "net_qty": round(b["buy_qty"] - b["sell_qty"], 8),
            "buy_qty": b["buy_qty"], "sell_qty": b["sell_qty"],
            "buy_vwap": round(buy_vwap, 6) if buy_vwap else None,
            "sell_vwap": round(sell_vwap, 6) if sell_vwap else None,
            "realized_points": round(realized, 6),
        })
    return out


def bundles(con: sqlite3.Connection) -> list[dict]:
    """Compact per-bundle summary for the main page (no per-leg detail)."""
    result = []
    for b in con.execute("SELECT id FROM bundle ORDER BY id DESC").fetchall():
        t = bundle_trades(con, b["id"])
        last = t["trades"][-1] if t["trades"] else None
        result.append({
            "id": t["id"], "name": t["name"], "note": t["note"],
            "is_open": t["is_open"], "num_trades": t["num_trades"],
            "total_realized": t["total_realized"],
            "total_commission": t["total_commission"],
            "total_realized_net": t["total_realized_net"],
            "points_only": t["points_only"],
            "open_legs": last["open_legs"] if (last and t["is_open"]) else [],
            "spread": last["spread"] if last else None,
            "leg_count": t["leg_count"],
        })
    return result


def _bundle_legs_sorted(con: sqlite3.Connection, bundle_id: int) -> list[dict]:
    """All legs of a bundle (internal fills + manual), sorted by time."""
    liq = _order_liquidity(con)
    legs: list[dict] = []
    for r in con.execute(
        "SELECT t.trade_id,t.order_id,t.ts,t.ticker,t.side,t.price,t.qty "
        "FROM bundle_member m JOIN trade t ON t.trade_id=m.trade_id WHERE m.bundle_id=?",
        (bundle_id,),
    ).fetchall():
        legs.append({"kind": "internal", "ref": r["trade_id"], "venue": "CScalp",
                     "ts": r["ts"], "ticker": r["ticker"], "side": r["side"],
                     "price": r["price"], "qty": r["qty"],
                     "liquidity": liq.get(r["order_id"], "unknown")})
    for r in con.execute(
        "SELECT id,venue,ticker,side,price,qty,ts,note FROM bundle_manual_leg WHERE bundle_id=?",
        (bundle_id,),
    ).fetchall():
        legs.append({"kind": "manual", "ref": r["id"], "venue": r["venue"] or "manual",
                     "ts": r["ts"] or "", "ticker": r["ticker"], "side": r["side"],
                     "price": r["price"], "qty": r["qty"], "liquidity": None})
    legs.sort(key=lambda l: (l["ts"] or "9999"))
    return legs


def _eval_spread(formula: str | None, entry_prices: dict[str, float]) -> dict | None:
    """Evaluate the user's spread formula over a trade's entry prices."""
    if not formula or not formula.strip():
        return None
    resolve, bindings = formula_mod.build_resolver(entry_prices)
    try:
        val = formula_mod.evaluate(formula, resolve)
        return {
            "value": round(val, 6), "error": None,
            "bindings": {k: {"ticker": v["ticker"], "price": round(v["price"], 6),
                             "synthetic": v.get("synthetic", False),
                             "defaulted": v.get("defaulted", False),
                             "formula": v.get("formula")}
                         for k, v in bindings.items()},
            "synthetic": {k: round(v["price"], 6) for k, v in bindings.items()
                          if v.get("synthetic")},
        }
    except Exception as e:
        return {"value": None, "error": str(e), "bindings": {}, "synthetic": {}}


def _formula_coverage(formula: str | None, tickers: set[str]) -> dict:
    """Which leg tickers the formula references. Uncovered legs are flagged red."""
    if not formula or not formula.strip():
        return {"has_formula": False, "covered": [], "uncovered": sorted(tickers)}
    resolve, bindings = formula_mod.build_resolver({t: 1.0 for t in tickers})
    try:
        for sym in formula_mod.symbols(formula):
            try:
                resolve(sym)
            except Exception:
                pass
    except Exception:
        pass
    covered = {b["ticker"] for b in bindings.values() if not b.get("defaulted")}
    return {"has_formula": True, "covered": sorted(covered),
            "uncovered": sorted(t for t in tickers if t not in covered)}


def bundle_trades(con: sqlite3.Connection, bundle_id: int) -> dict:
    """Segment an arbitrage bundle into individual trades (open->close cycles).

    A bundle is a recurring arb structure that can be re-opened. Each cycle from
    all-legs-flat to all-legs-flat is one arbitrage trade with its own entry/exit,
    events (OPEN/ADD/REDUCE/CLOSE), realized PnL, and entry spread (via the
    bundle's editable formula). The last cycle may still be open.
    """
    b = con.execute("SELECT id,name,note,created,formula FROM bundle WHERE id=?",
                    (bundle_id,)).fetchone()
    if not b:
        return {}
    legs = _bundle_legs_sorted(con, bundle_id)
    book: dict[str, dict] = {}
    unconfigured: set[str] = set()
    cycles: list[dict] = []
    cur: dict | None = None

    def total_abs() -> float:
        return sum(abs(v["qty"]) for v in book.values())

    for leg in legs:
        tkr = leg["ticker"]
        if not instruments.is_configured(tkr):
            unconfigured.add(tkr)
        pv = instruments.point_value(tkr)
        bk = book.setdefault(tkr, {"qty": 0.0, "avg": 0.0})
        signed = leg["qty"] if leg["side"] == "Buy" else -leg["qty"]
        exposure_before = total_abs()
        if exposure_before == 0:
            cur = {"events": [], "realized": 0.0, "commission": 0.0, "entry_ts": leg["ts"],
                   "exit_ts": None, "fills": [], "status": "open"}
            cycles.append(cur)
        if leg["kind"] == "internal":  # FORTS fees apply to CScalp legs, not external forex hedges
            cur["commission"] += commission.per_fill(
                tkr, side=leg["side"], qty=leg["qty"], liquidity=leg["liquidity"])
        prev_abs = abs(bk["qty"])
        realized_leg = 0.0
        if bk["qty"] == 0 or (bk["qty"] > 0) == (signed > 0):
            new_qty = bk["qty"] + signed
            if new_qty != 0:
                bk["avg"] = (bk["avg"] * bk["qty"] + leg["price"] * signed) / new_qty
            bk["qty"] = new_qty
        else:
            closing = min(abs(signed), abs(bk["qty"]))
            direction = 1 if bk["qty"] > 0 else -1
            realized_leg = closing * (leg["price"] - bk["avg"]) * direction * pv
            bk["qty"] += signed
            if bk["qty"] != 0 and (bk["qty"] > 0) != (direction > 0):
                bk["avg"] = leg["price"]
        cur["realized"] += realized_leg
        new_abs = abs(bk["qty"])
        exposure_after = total_abs()
        if exposure_before == 0:
            action = "OPEN"
        elif new_abs >= prev_abs:
            action = "ADD"
        elif exposure_after == 0:
            action = "CLOSE"
        else:
            action = "REDUCE"
        cur["events"].append({
            "ts": leg["ts"], "venue": leg["venue"], "ticker": tkr, "side": leg["side"],
            "price": leg["price"], "qty": leg["qty"], "liquidity": leg["liquidity"],
            "kind": leg["kind"], "ref": leg["ref"],
            "action": action, "realized_leg": round(realized_leg, 6),
            "realized_cum": round(cur["realized"], 6),
            "net_after": round(bk["qty"], 8), "exposure_after": round(exposure_after, 8),
        })
        cur["fills"].append(leg)
        cur["exit_ts"] = leg["ts"]
        if exposure_after == 0:
            cur["status"] = "closed"
            cur = None

    trades_out: list[dict] = []
    for idx, c in enumerate(cycles, 1):
        by_t: dict[str, list] = {}
        for f in c["fills"]:
            by_t.setdefault(f["ticker"], []).append(f)
        entry_prices: dict[str, float] = {}
        for t, fs in by_t.items():
            oside = fs[0]["side"]
            num = sum(x["qty"] * x["price"] for x in fs if x["side"] == oside)
            den = sum(x["qty"] for x in fs if x["side"] == oside)
            entry_prices[t] = num / den if den else fs[0]["price"]
        open_legs = []
        if c["status"] == "open":
            open_legs = [{"ticker": t, "net_qty": round(v["qty"], 8),
                          "avg_price": round(v["avg"], 6)}
                         for t, v in sorted(book.items()) if abs(v["qty"]) > 1e-9]
        trades_out.append({
            "idx": idx, "status": c["status"], "entry_ts": c["entry_ts"],
            "exit_ts": c["exit_ts"], "realized_pnl": round(c["realized"], 6),
            "commission": round(c["commission"], 6),
            "realized_net": round(c["realized"] - c["commission"], 6),
            "entry_prices": {t: round(p, 6) for t, p in entry_prices.items()},
            "spread": _eval_spread(b["formula"], entry_prices),
            "events": c["events"], "open_legs": open_legs,
        })

    return {
        "id": b["id"], "name": b["name"], "note": b["note"], "created": b["created"],
        "formula": b["formula"] or "",
        "trades": trades_out,
        "num_trades": len(trades_out),
        "total_realized": round(sum(c["realized"] for c in cycles), 6),
        "total_commission": round(sum(c["commission"] for c in cycles), 6),
        "total_realized_net": round(sum(c["realized"] - c["commission"] for c in cycles), 6),
        "is_open": any(c["status"] == "open" for c in cycles),
        "points_only": sorted(unconfigured),
        "legs": legs,
        "leg_count": len(legs),
        "coverage": _formula_coverage(b["formula"], {l["ticker"] for l in legs}),
    }


def summary(con: sqlite3.Connection) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    tr = [t for t in trades_with_liquidity(con) if t["ts"][:10] == today]
    by_liq: dict[str, int] = {}
    for t in tr:
        by_liq[t["liquidity"]] = by_liq.get(t["liquidity"], 0) + 1
    recon = _intraday_recon(con, only_day=today)
    realized_today = round(sum(v["realized"] for v in recon.values()), 2)
    # estimated commission from configured per-contract rates (covers all fills)
    commission_today = round(sum(v.get("commission", 0.0) for v in recon.values()), 2)
    # terminal MoneyStorage checkpoint (only logged at start/reconnect -> may be stale)
    rows = con.execute(
        "SELECT slot, commission, ts FROM money_snap m "
        "WHERE ts = (SELECT MAX(ts) FROM money_snap m2 WHERE m2.slot=m.slot)"
    ).fetchall()
    checkpoint_val = round(sum((r["commission"] or 0.0) for r in rows), 2)
    checkpoint_ts = max((r["ts"] for r in rows), default=None)
    unconfigured = sorted({t for t in recon if not instruments.is_configured(t)})
    # taker fills whose exchange registration fee isn't set yet -> commission is
    # a lower bound (only the flat clearing part is counted for them).
    comm_pending = sorted({instruments.base_symbol(t["ticker"]) for t in tr
                           if commission.needs_reg_fee(t["ticker"], t["liquidity"])})
    return {
        "fills": len(tr),
        "by_liquidity": by_liq,
        "tickers": sorted({t["ticker"] for t in tr}),
        "realized_today": realized_today,
        "commission_today": commission_today,
        "commission_pending": comm_pending,
        "commission_checkpoint": checkpoint_val,
        "commission_checkpoint_ts": checkpoint_ts,
        "realized_today_net": round(realized_today - commission_today, 2),
        "points_mixed": unconfigured,
    }
