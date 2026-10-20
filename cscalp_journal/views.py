"""Derived views over stored facts: maker/taker, positions, PnL (in points), journal.

PnL is expressed in PRICE POINTS x CONTRACTS, not RUB. Converting to money needs
per-instrument contract multipliers and the commission formula from the FAQ,
which are not in the logs yet. Kept honest until those are provided.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime

from . import config, commission, forex, instruments, formula as formula_mod


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


class _Lots:
    """Open lots of one instrument. A closing fill consumes the MOST RECENT lots first (LIFO),
    so a fresh scalp (open, then close) is judged against its own entry price instead of the
    average price of a large position carried from earlier days. Overall P&L (realized +
    unrealized) is the same as with average cost; only its split changes."""

    def __init__(self) -> None:
        self.lots: list[list[float]] = []      # [signed qty, price]; all lots share one direction

    @property
    def qty(self) -> float:
        return sum(l[0] for l in self.lots)

    @property
    def avg(self) -> float:
        q = sum(abs(l[0]) for l in self.lots)
        return sum(abs(l[0]) * l[1] for l in self.lots) / q if q else 0.0

    def __getitem__(self, key: str) -> float:
        return self.qty if key == "qty" else self.avg

    def apply(self, signed: float, price: float, point_value: float) -> float:
        """Add a fill (signed qty); return the P&L realized by the part that closes lots."""
        realized = 0.0
        while signed != 0 and self.lots and (self.lots[-1][0] > 0) != (signed > 0):
            lot = self.lots[-1]
            take = min(abs(signed), abs(lot[0]))
            direction = 1 if lot[0] > 0 else -1
            realized += take * (price - lot[1]) * direction * point_value
            lot[0] -= take * direction
            signed += take * direction
            if abs(lot[0]) < 1e-9:
                self.lots.pop()
        if signed != 0:
            self.lots.append([signed, price])
        return realized


def _intraday_recon(con: sqlite3.Connection, only_day: str | None = None) -> dict[str, dict]:
    """Lot-matching (LIFO) walk over fills -> per-ticker realized & fill count.

    Walks ALL fills chronologically so open lots carry across days; when `only_day`
    (YYYY-MM-DD) is set, realized/commission/fills are tallied for that day only.

    NOTE: net here does NOT include a day-open carried position that predates
    the parsed logs, so it must not be shown as the live position. Use terminal
    snapshots (position_snap) for the authoritative net. See positions().
    """
    trades = trades_with_liquidity(con)
    book: dict[str, dict] = {}
    for t in trades:
        on_day = only_day is None or t["ts"][:10] == only_day
        b = book.setdefault(t["ticker"], {"qty": 0.0, "avg": 0.0, "realized": 0.0,
                                          "fills": 0, "commission": 0.0, "lots": _Lots()})
        pv = instruments.point_value(t["ticker"])
        signed = t["qty"] if t["side"] == "Buy" else -t["qty"]
        if on_day:
            b["fills"] += 1
            b["commission"] += t["commission"]
        realized_leg = b["lots"].apply(signed, t["price"], pv)
        b["qty"], b["avg"] = b["lots"].qty, b["lots"].avg
        if on_day:
            b["realized"] += realized_leg
    for b in book.values():
        b.pop("lots")
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


_CUTOFF = os.environ.get("CSCALP_FX_CUTOFF", "1").strip().lower() not in ("0", "false", "no")
# quote currency of a forex symbol (the leg is converted to RUB at the CBR rate of that currency)
_QUOTE = {"EURUSD": "USD", "XAUUSD": "USD", "XAGUSD": "USD", "XPTUSD": "USD", "XPDUSD": "USD",
          "ETHUSD": "USD", "BTCUSD": "USD", "USDCNH": "CNY", "EURCNH": "CNY"}


def _forex_map() -> dict:
    """Forex symbol -> the MOEX INSTRUMENT it hedges (settings: forex_instruments). Keyed by
    instrument, not by bundle name: names are the user's to change, instruments are not."""
    m = {"EURUSD_xp": "ED", "XAUUSD_xp": "GOLD", "USDCNH_xp": "UCNY"}
    m.update(config.USER.get("forex_instruments") or {})
    return m


def _auto_map(con: sqlite3.Connection) -> dict[str, list[int]]:
    """MOEX instrument (lowercase base symbol) -> bundles that contain it."""
    out: dict[str, list[int]] = {}
    for r in con.execute("SELECT DISTINCT m.bundle_id, t.ticker FROM bundle_member m "
                         "JOIN trade t ON t.trade_id=m.trade_id"):
        ids = out.setdefault(instruments.base_symbol(r["ticker"]).lower(), [])
        if r["bundle_id"] not in ids:
            ids.append(r["bundle_id"])
    return out


def _auto_bundles(automap: dict[str, list[int]], symbol: str) -> list[int]:
    """Bundle for a forex symbol by the symbol rule. None or several matches: no auto-binding
    (a trade must not count twice) - the user ticks it in the journal."""
    target = str(_forex_map().get(symbol) or "").strip().lower()
    ids = automap.get(target) or []
    return list(ids) if len(ids) == 1 else []


def _bundle_since(con: sqlite3.Connection, bundle_id: int, cache: dict | None = None) -> str:
    """Time of the bundle's first MOEX fill: older automatic forex trades have no MOEX half."""
    if cache is not None and bundle_id in cache:
        return cache[bundle_id]
    row = con.execute("SELECT MIN(t.ts) FROM bundle_member m JOIN trade t ON t.trade_id=m.trade_id "
                      "WHERE m.bundle_id=?", (bundle_id,)).fetchone()
    val = (row[0] if row else None) or ""
    if cache is not None:
        cache[bundle_id] = val
    return val


def _forex_bundles(con, key: str, symbol: str, ts: str | None, automap=None, since=None) -> list[dict]:
    """Bundle mark of a cTrader journal row: manual binding first, else the symbol rule (cut off
    by date). A manual binding is the user's decision and is never cut off."""
    row = con.execute("SELECT bundle_id FROM forex_bundle_member WHERE deal_id=?", (key,)).fetchone()
    if row:
        if row["bundle_id"] is None:        # taken out by the user
            return []
        bids = [row["bundle_id"]]
    else:
        bids = [b for b in _auto_bundles(automap if automap is not None else _auto_map(con), symbol)
                if not (_CUTOFF and ts and ts < _bundle_since(con, b, since))]
    out = []
    for bid in bids:
        b = con.execute("SELECT name FROM bundle WHERE id=?", (bid,)).fetchone()
        out.append({"id": bid, "name": b["name"] if b else str(bid)})
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
    automap, since = _auto_map(con), {}
    for f in con.execute("SELECT id,ts_close,symbol,side,volume,exit_price FROM forex_trade").fetchall():
        key = "fx:" + f["id"]
        items.append({      # cTrader closing deal: the side is the opposite of the opening one
            "ts": f["ts_close"], "event": "FILL", "ticker": f["symbol"],
            "side": "Sell" if f["side"] == "Buy" else "Buy", "price": f["exit_price"],
            "qty": f["volume"], "liquidity": "forex", "id": key, "venue": "forex",
            "bundles": _forex_bundles(con, key, f["symbol"], f["ts_close"], automap, since),
        })
    for f in con.execute("SELECT position_id,ts_open,symbol,side,volume,entry_price FROM forex_open").fetchall():
        key = "pos:" + str(f["position_id"])
        items.append({      # still open cTrader position: shown as its opening fill
            "ts": f["ts_open"] or "", "event": "FILL", "ticker": f["symbol"], "side": f["side"],
            "price": f["entry_price"], "qty": f["volume"], "liquidity": "forex", "id": key,
            "venue": "forex", "bundles": _forex_bundles(con, key, f["symbol"], f["ts_open"], automap, since),
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
            "auto": bool(con.execute("SELECT auto FROM bundle WHERE id=?", (b["id"],)).fetchone()[0]),
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
    since = min((l["ts"] for l in legs if l.get("ts")), default=None)
    legs.extend(_forex_legs(con, bundle_id, since))
    legs.sort(key=lambda l: (l["ts"] or "9999"))
    return legs


def _forex_legs(con: sqlite3.Connection, bundle_id: int, since: str | None) -> list[dict]:
    """cTrader legs of a bundle: an opening and a closing leg per closed trade, one opening leg per
    open position. Prices are in RUB (CBR rate of the quote currency) so MOEX and forex add up;
    `native` keeps the pair's own price for the spread formula. The closing price is shifted by
    net result / volume, so the leg gives exactly the net result (swap and commission included).
    qty is the volume in base currency (EURUSD 10 000 = 0.1 lot). No rate -> the leg is skipped."""
    automap = _auto_map(con)
    legs: list[dict] = []

    def mine(key: str, symbol: str, ts: str | None) -> bool:
        fixed = con.execute("SELECT bundle_id FROM forex_bundle_member WHERE deal_id=?", (key,)).fetchone()
        if fixed:
            return fixed["bundle_id"] == bundle_id          # NULL never equals
        if bundle_id not in _auto_bundles(automap, symbol):
            return False
        return not (_CUTOFF and since and ts and ts < since)

    def rate_for(symbol: str, ts: str):
        rates = forex.rub_rates(con, ts[:10])
        quote = _QUOTE.get(str(symbol).split("_")[0].upper(), "USD")
        return quote, rates, rates.get(quote)

    for t in con.execute("SELECT id,ts_open,ts_close,symbol,side,volume,entry_price,net_usd FROM forex_trade "
                         "ORDER BY ts_close").fetchall():
        key = "fx:" + t["id"]
        if not (t["ts_open"] and t["ts_close"]) or not mine(key, t["symbol"], t["ts_close"]):
            continue
        quote, rates, rate = rate_for(t["symbol"], t["ts_close"])
        entry, vol, net = float(t["entry_price"] or 0), float(t["volume"] or 0), float(t["net_usd"] or 0)
        if not (rate and entry and vol):
            continue
        side = "Buy" if str(t["side"]).lower().startswith("b") else "Sell"
        delta = net / vol
        if quote != "USD":
            delta *= rates["USD"] / rates["CNY"]            # net is in USD, the pair quotes in CNY
        close_eff = entry + (1 if side == "Buy" else -1) * delta
        legs.append({"kind": "forex", "ref": key, "venue": "forex", "ts": t["ts_open"], "ticker": t["symbol"],
                     "side": side, "price": round(entry * rate, 8), "native": round(entry, 8),
                     "qty": vol, "liquidity": None})
        legs.append({"kind": "forex", "ref": key, "venue": "forex", "ts": t["ts_close"], "ticker": t["symbol"],
                     "side": "Sell" if side == "Buy" else "Buy", "price": round(close_eff * rate, 8),
                     "native": round(close_eff, 8), "qty": vol, "liquidity": None})
    for p in con.execute("SELECT position_id,ts_open,symbol,side,volume,entry_price FROM forex_open").fetchall():
        key = "pos:" + str(p["position_id"])
        if not p["ts_open"] or not mine(key, p["symbol"], p["ts_open"]):
            continue
        quote, rates, rate = rate_for(p["symbol"], p["ts_open"])
        if not (rate and p["entry_price"] and p["volume"]):
            continue
        legs.append({"kind": "forex", "ref": key, "venue": "forex", "ts": p["ts_open"], "ticker": p["symbol"],
                     "side": p["side"], "price": round(p["entry_price"] * rate, 8),
                     "native": round(p["entry_price"], 8), "qty": p["volume"], "liquidity": None})
    return legs


def _eval_spread(formula: str | None, entry_prices: dict[str, float],
                 all_tickers: set[str] | None = None) -> dict | None:
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
    except KeyError as e:
        # the formula fits the bundle's legs, but this cycle lacks one of them: not a formula error
        if all_tickers and _symbols_resolvable(formula, set(all_tickers)):
            return {"value": None, "error": None, "missing": True, "bindings": {}, "synthetic": {}}
        return {"value": None, "error": str(e).strip("\"'"), "bindings": {}, "synthetic": {}}
    except Exception as e:
        return {"value": None, "error": str(e), "bindings": {}, "synthetic": {}}


def _symbols_resolvable(formula: str, tickers: set[str]) -> bool:
    """True when every symbol of the formula matches some leg of the bundle (ignoring cycles)."""
    resolve, _ = formula_mod.build_resolver({t: 1.0 for t in tickers})
    try:
        for sym in formula_mod.symbols(formula):
            resolve(sym)
    except Exception:
        return False
    return True


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


def _forex_tickers(con: sqlite3.Connection) -> set[str]:
    """cTrader symbols: their legs are already in RUB, so they are never 'points only'."""
    return ({r[0] for r in con.execute("SELECT DISTINCT symbol FROM forex_trade")}
            | {r[0] for r in con.execute("SELECT DISTINCT symbol FROM forex_open")})


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
    book: dict[str, _Lots] = {}
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
        bk = book.setdefault(tkr, _Lots())
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
        realized_leg = bk.apply(signed, leg["price"], pv)
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
    all_legs = {l["ticker"] for l in legs}
    for idx, c in enumerate(cycles, 1):
        by_t: dict[str, list] = {}
        for f in c["fills"]:
            by_t.setdefault(f["ticker"], []).append(f)
        entry_prices: dict[str, float] = {}
        native_prices: dict[str, float] = {}      # the formula is written in the pairs' own prices
        for t, fs in by_t.items():
            oside = fs[0]["side"]
            num = sum(x["qty"] * x["price"] for x in fs if x["side"] == oside)
            numn = sum(x["qty"] * (x.get("native") or x["price"]) for x in fs if x["side"] == oside)
            den = sum(x["qty"] for x in fs if x["side"] == oside)
            entry_prices[t] = num / den if den else fs[0]["price"]
            native_prices[t] = numn / den if den else (fs[0].get("native") or fs[0]["price"])
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
            "spread": _eval_spread(b["formula"], native_prices, all_legs),
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
        "points_only": sorted(unconfigured - _forex_tickers(con)),
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
