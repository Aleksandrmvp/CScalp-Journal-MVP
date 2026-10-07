"""SQLite storage. Idempotent upserts — safe to re-parse the same day repeatedly.

Dedup key for fills is trade_id (the terminal re-replicates trades on reconnect
with identical ids). See memory: cscalp-log-parsing-gotchas.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config
from .parser import OrderCmd, OrderLife, PositionSnap, Trade

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trade (
    trade_id TEXT PRIMARY KEY,
    order_id TEXT,
    ts       TEXT NOT NULL,
    ticker   TEXT NOT NULL,
    slot     TEXT,
    side     TEXT NOT NULL,
    price    REAL NOT NULL,
    qty      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_trade_ts ON trade(ts);
CREATE INDEX IF NOT EXISTS ix_trade_order ON trade(order_id);

CREATE TABLE IF NOT EXISTS order_life (
    order_id TEXT NOT NULL,
    state    TEXT NOT NULL,
    ts       TEXT NOT NULL,
    slot     TEXT,
    PRIMARY KEY (order_id, state)
);

-- key is a surrogate PK: SQLite treats NULLs as distinct in composite PKs,
-- so cancels (side/price/qty NULL) would never dedup. Build an explicit key.
CREATE TABLE IF NOT EXISTS order_cmd (
    key    TEXT PRIMARY KEY,
    ts     TEXT NOT NULL,
    ticker TEXT NOT NULL,
    kind   TEXT NOT NULL,
    side   TEXT,
    price  REAL,
    qty    REAL
);

CREATE TABLE IF NOT EXISTS position_snap (
    ts     TEXT NOT NULL,
    ticker TEXT NOT NULL,
    slot   TEXT,
    price  REAL,
    amount REAL,
    PRIMARY KEY (ts, ticker)
);

-- User-assembled arbitrage bundles (linkages of fills across legs).
CREATE TABLE IF NOT EXISTS bundle (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    note    TEXT,
    created TEXT NOT NULL
);

-- Internal legs: existing fills assigned to a bundle.
CREATE TABLE IF NOT EXISTS bundle_member (
    bundle_id INTEGER NOT NULL REFERENCES bundle(id) ON DELETE CASCADE,
    trade_id  TEXT NOT NULL REFERENCES trade(trade_id),
    PRIMARY KEY (bundle_id, trade_id)
);

-- External / manual legs (e.g. a usdcnh hedge on forex, not in CScalp logs).
CREATE TABLE IF NOT EXISTS bundle_manual_leg (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id INTEGER NOT NULL REFERENCES bundle(id) ON DELETE CASCADE,
    venue     TEXT,
    ticker    TEXT NOT NULL,
    side      TEXT NOT NULL,
    price     REAL NOT NULL,
    qty       REAL NOT NULL,
    ts        TEXT,
    note      TEXT
);

CREATE TABLE IF NOT EXISTS money_snap (
    ts           TEXT NOT NULL,
    slot         TEXT NOT NULL,
    commission   REAL,
    free_assets  REAL,
    total_assets REAL,
    PRIMARY KEY (ts, slot)
);

-- Broker cash movements (funding credits, transfer fees, loss write-offs), entered from the
-- broker's statement. `day` is the booking date; `ref_day` the trading day it refers to.
CREATE TABLE IF NOT EXISTS cash_flow (
    day         TEXT NOT NULL,
    amount      REAL NOT NULL,
    kind        TEXT NOT NULL,
    ref_day     TEXT,
    description TEXT NOT NULL,
    seq         INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (day, amount, description, seq)
);

-- Forex-leg results (USDT account at FxPro): signed USDT amount per day.
CREATE TABLE IF NOT EXISTS forex_result (
    id   TEXT PRIMARY KEY,
    day  TEXT NOT NULL,
    usd  REAL NOT NULL,
    note TEXT
);

-- Cached official USD/RUB rate by day.
CREATE TABLE IF NOT EXISTS fx_rate (day TEXT PRIMARY KEY, rate REAL NOT NULL);

CREATE TABLE IF NOT EXISTS settings (
    k TEXT PRIMARY KEY,
    v TEXT
);

-- Which bundle-cycle open/close events have already been posted (dedup).
CREATE TABLE IF NOT EXISTS notify_log (
    bundle_id INTEGER NOT NULL,
    cycle_key TEXT NOT NULL,
    event     TEXT NOT NULL,
    posted_at TEXT NOT NULL,
    PRIMARY KEY (bundle_id, cycle_key, event)
);
"""


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(_SCHEMA)
    if "seq" not in {r[1] for r in con.execute("PRAGMA table_info(cash_flow)")}:
        # identical rows on one day (e.g. two equal PayOut lines) need an occurrence index
        con.executescript("""
            ALTER TABLE cash_flow RENAME TO cash_flow_old;
            CREATE TABLE cash_flow (
                day TEXT NOT NULL, amount REAL NOT NULL, kind TEXT NOT NULL, ref_day TEXT,
                description TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (day, amount, description, seq));
            INSERT INTO cash_flow(day,amount,kind,ref_day,description,seq)
                SELECT day,amount,kind,ref_day,description,1 FROM cash_flow_old;
            DROP TABLE cash_flow_old;""")
    try:
        con.execute("ALTER TABLE forex_result ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")
    except sqlite3.OperationalError:
        pass
    try:
        con.execute("ALTER TABLE forex_result ADD COLUMN rub REAL")  # fixed RUB amount, overrides the rate
    except sqlite3.OperationalError:
        pass
    try:
        con.execute("ALTER TABLE bundle ADD COLUMN formula TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    return con


def _iso(dt) -> str:
    return dt.isoformat(sep=" ", timespec="milliseconds")


def upsert(con: sqlite3.Connection, trades: list[Trade], lives: list[OrderLife],
           positions: list[PositionSnap], cmds: list[OrderCmd], money=None) -> dict:
    cur = con.cursor()
    n_tr = n_life = n_pos = n_cmd = n_money = 0

    for t in trades:
        cur.execute(
            "INSERT OR IGNORE INTO trade(trade_id,order_id,ts,ticker,slot,side,price,qty)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (t.trade_id, t.order_id, _iso(t.ts), t.ticker, t.slot, t.side, t.price, t.qty),
        )
        n_tr += cur.rowcount

    for l in lives:
        # keep earliest active / latest inactive: REPLACE only moves inactive later
        cur.execute(
            "INSERT INTO order_life(order_id,state,ts,slot) VALUES(?,?,?,?) "
            "ON CONFLICT(order_id,state) DO UPDATE SET "
            "ts=CASE WHEN excluded.state='inactive' AND excluded.ts>order_life.ts THEN excluded.ts "
            "        WHEN excluded.state='active' AND excluded.ts<order_life.ts THEN excluded.ts "
            "        ELSE order_life.ts END",
            (l.order_id, l.state, _iso(l.ts), l.slot),
        )
        n_life += cur.rowcount

    for c in cmds:
        key = f"{_iso(c.ts)}|{c.ticker}|{c.kind}|{c.side}|{c.price}|{c.qty}"
        cur.execute(
            "INSERT OR IGNORE INTO order_cmd(key,ts,ticker,kind,side,price,qty) VALUES(?,?,?,?,?,?,?)",
            (key, _iso(c.ts), c.ticker, c.kind, c.side, c.price, c.qty),
        )
        n_cmd += cur.rowcount

    # two updates can share one millisecond; the later one in the log is the real state
    last_pos = {(_iso(p.ts), p.ticker): p for p in positions}
    for p in last_pos.values():
        cur.execute(
            "INSERT INTO position_snap(ts,ticker,slot,price,amount) VALUES(?,?,?,?,?) "
            "ON CONFLICT(ts,ticker) DO UPDATE SET slot=excluded.slot, price=excluded.price, "
            "amount=excluded.amount WHERE position_snap.amount IS NOT excluded.amount "
            "OR position_snap.price IS NOT excluded.price",
            (_iso(p.ts), p.ticker, p.slot, p.price, p.amount),
        )
        n_pos += cur.rowcount

    for mo in (money or []):
        cur.execute(
            "INSERT OR IGNORE INTO money_snap(ts,slot,commission,free_assets,total_assets) "
            "VALUES(?,?,?,?,?)",
            (_iso(mo.ts), mo.slot, mo.commission, mo.free_assets, mo.total_assets),
        )
        n_money += cur.rowcount

    con.commit()
    return {"trades": n_tr, "lives": n_life, "positions": n_pos, "cmds": n_cmd, "money": n_money}


# ---------------- bundle CRUD ----------------
from datetime import datetime as _dt


def create_bundle(con, name: str, note: str | None, trade_ids: list[str]) -> int:
    cur = con.cursor()
    cur.execute("INSERT INTO bundle(name,note,created) VALUES(?,?,?)",
                (name, note, _dt.now().isoformat(sep=" ", timespec="seconds")))
    bid = cur.lastrowid
    for tid in trade_ids:
        cur.execute("INSERT OR IGNORE INTO bundle_member(bundle_id,trade_id) VALUES(?,?)", (bid, tid))
    con.commit()
    return bid


def add_members(con, bundle_id: int, trade_ids: list[str]) -> None:
    cur = con.cursor()
    for tid in trade_ids:
        cur.execute("INSERT OR IGNORE INTO bundle_member(bundle_id,trade_id) VALUES(?,?)", (bundle_id, tid))
    con.commit()


def remove_member(con, bundle_id: int, trade_id: str) -> None:
    con.execute("DELETE FROM bundle_member WHERE bundle_id=? AND trade_id=?", (bundle_id, trade_id))
    con.commit()


def add_manual_leg(con, bundle_id: int, venue, ticker, side, price, qty, ts, note) -> int:
    cur = con.cursor()
    cur.execute(
        "INSERT INTO bundle_manual_leg(bundle_id,venue,ticker,side,price,qty,ts,note) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (bundle_id, venue, ticker, side, float(price), float(qty), ts, note),
    )
    con.commit()
    return cur.lastrowid


def remove_manual_leg(con, leg_id: int) -> None:
    con.execute("DELETE FROM bundle_manual_leg WHERE id=?", (leg_id,))
    con.commit()


def delete_bundle(con, bundle_id: int) -> None:
    con.execute("DELETE FROM bundle WHERE id=?", (bundle_id,))
    con.commit()


def rename_bundle(con, bundle_id: int, name: str, note: str | None) -> None:
    con.execute("UPDATE bundle SET name=?, note=? WHERE id=?", (name, note, bundle_id))
    con.commit()


def set_formula(con, bundle_id: int, formula: str) -> None:
    con.execute("UPDATE bundle SET formula=? WHERE id=?", (formula, bundle_id))
    con.commit()


def set_name(con, bundle_id: int, name: str) -> None:
    con.execute("UPDATE bundle SET name=? WHERE id=?", (name, bundle_id))
    con.commit()


# ---------------- settings + notify dedup ----------------
def get_setting(con, key: str) -> str | None:
    r = con.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
    return r["v"] if r else None


def set_setting(con, key: str, value: str) -> None:
    con.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (key, value))
    con.commit()


def notify_seen(con, bundle_id: int, cycle_key: str, event: str) -> bool:
    r = con.execute(
        "SELECT 1 FROM notify_log WHERE bundle_id=? AND cycle_key=? AND event=?",
        (bundle_id, cycle_key, event)).fetchone()
    return r is not None


def notify_mark(con, bundle_id: int, cycle_key: str, event: str, posted_at: str) -> None:
    con.execute(
        "INSERT OR IGNORE INTO notify_log(bundle_id,cycle_key,event,posted_at) VALUES(?,?,?,?)",
        (bundle_id, cycle_key, event, posted_at))
    con.commit()
