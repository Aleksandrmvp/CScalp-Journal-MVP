"""Parse CScalp / FSR Launcher HTML debug logs into structured events.

The terminal writes human-readable debug logs that decode the prop firm's
binary protocol. We read them passively. Regex ports 1:1 from the PowerShell
prototype. See memory: cscalp-log-parsing-gotchas.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

# --- one HTML log entry: <div class="flexy"><div>[ts]</div>...<div..>msg</div></div>
_ENTRY = re.compile(
    r'<div class="flexy">\s*<div>\[(?P<ts>\d{2}:\d{2}:\d{2}\.\d{3})\]</div>.*?'
    r'<div[^>]*>(?P<msg>.*?)</div>\s*</div>',
    re.DOTALL,
)
_TAG = re.compile(r"<[^>]+>")

# --- dispatcher patterns
_ON_TRADE = re.compile(
    r"Slot \((?P<slot>\w+)\): Layer \((?P<ticker>[^)]+)\): PM: OnTrade: "
    r"ID=(?P<tid>-?[\d,.]+); OrderID=(?P<oid>-?\d+); Price=(?P<price>[\d ,.]+); "
    r"Amount=(?P<amt>[\d ,.]+); Direction=(?P<dir>\w+); Time=(?P<time>[\d. :]+)"
)
_ORDER_ACTIVE = re.compile(
    r"Slot \((?P<slot>\w+)\): OrdersStorage: Add: OrderID=(?P<oid>\d+) "
    r"(?P<state>add to active|remove from active)"
)
_POS = re.compile(
    r"Slot \((?P<slot>\w+)\): Layer \((?P<ticker>[^)]+)\): PM: OnPositionUpdate: "
    r"Price=(?P<price>[-\d ,.]+); Amount=(?P<amt>[-\d ,.]+)"
)

# --- trader patterns
_SEND = re.compile(
    r"TradeLogic: SendLimitOrder: Price=(?P<price>[\d ,.]+); Direction=(?P<dir>\w+)"
)
_AMT = re.compile(r"TradeLogic: GetSendOrderAmount: .*Amount=(?P<amt>[\d ,.]+)")
_CANCEL = re.compile(r"TradeLogic: CancelLimitOrders: Count=(?P<c>\d+)")
_TRADER_TICKER = re.compile(r"TrySetLayer: Ticker=(?P<t>\S+)")


def _num(s: str) -> float:
    """ru-RU decimal comma -> float."""
    return float(s.replace(" ", "").replace(",", "."))


def _ts(day: date, hhmmss: str) -> datetime:
    t = time.fromisoformat(hhmmss)
    return datetime.combine(day, t)


@dataclass
class Trade:
    trade_id: str
    order_id: str
    ts: datetime
    ticker: str
    slot: str
    side: str
    price: float
    qty: float


@dataclass
class OrderLife:
    order_id: str
    slot: str
    ts: datetime
    state: str  # 'active' | 'inactive'


@dataclass
class OrderCmd:
    ts: datetime
    ticker: str
    kind: str  # 'submit' | 'cancel'
    side: str | None
    price: float | None
    qty: float | None


@dataclass
class PositionSnap:
    ts: datetime
    ticker: str
    slot: str
    price: float
    amount: float


@dataclass
class MoneySnap:
    ts: datetime
    slot: str
    commission: float
    free_assets: float
    total_assets: float


_MONEY = re.compile(
    r"Slot \((?P<slot>\w+)\): MoneyStorage: DataState: "
    r"TotalAssets=(?P<total>[-\d ,.]+); FreeAssets=(?P<free>[-\d ,.]+); "
    r"PoolAssets=[-\d ,.]+; Commission=(?P<comm>[-\d ,.]+)"
)


def _entries(path: Path):
    if not path.exists():
        return
    raw = path.read_text(encoding="utf-8", errors="replace")
    for m in _ENTRY.finditer(raw):
        msg = _TAG.sub("", m.group("msg"))
        msg = (
            msg.replace("&lt;", "<").replace("&gt;", ">")
            .replace("&amp;", "&").replace("&quot;", '"').strip()
        )
        yield m.group("ts"), msg


def parse_dispatcher(path: Path, day: date):
    trades: list[Trade] = []
    lives: list[OrderLife] = []
    positions: list[PositionSnap] = []
    money: list[MoneySnap] = []
    for ts, msg in _entries(path):
        m = _MONEY.search(msg)
        if m:
            money.append(MoneySnap(
                _ts(day, ts), m.group("slot"), _num(m.group("comm")),
                _num(m.group("free")), _num(m.group("total")),
            ))
            continue
        m = _ON_TRADE.search(msg)
        if m:
            tid = m.group("tid")
            tm = m.group("time")
            # skip day-open snapshot: negative synthetic id or midnight time
            if tid.startswith("-") or tm.rstrip().endswith("0:00:00"):
                continue
            trades.append(Trade(
                trade_id=tid, order_id=m.group("oid"), ts=_ts(day, ts),
                ticker=m.group("ticker"), slot=m.group("slot"),
                side=m.group("dir"), price=_num(m.group("price")),
                qty=_num(m.group("amt")),
            ))
            continue
        m = _ORDER_ACTIVE.search(msg)
        if m:
            state = "active" if m.group("state") == "add to active" else "inactive"
            lives.append(OrderLife(m.group("oid"), m.group("slot"), _ts(day, ts), state))
            continue
        m = _POS.search(msg)
        if m:
            positions.append(PositionSnap(
                _ts(day, ts), m.group("ticker"), m.group("slot"),
                _num(m.group("price")), _num(m.group("amt")),
            ))
    return trades, lives, positions, money


def parse_trader(path: Path, day: date) -> list[OrderCmd]:
    entries = list(_entries(path))
    ticker = path.stem
    for _, msg in entries:
        mt = _TRADER_TICKER.search(msg)
        if mt:
            ticker = mt.group("t")
            break
    cmds: list[OrderCmd] = []
    pending = None
    for ts, msg in entries:
        m = _SEND.search(msg)
        if m:
            pending = (ts, _num(m.group("price")), m.group("dir"))
            continue
        m = _AMT.search(msg)
        if m and pending:
            p_ts, p_price, p_dir = pending
            cmds.append(OrderCmd(_ts(day, p_ts), ticker, "submit", p_dir, p_price, _num(m.group("amt"))))
            pending = None
            continue
        m = _CANCEL.search(msg)
        if m:
            cmds.append(OrderCmd(_ts(day, ts), ticker, "cancel", None, None, None))
    return cmds


def day_from_dirname(name: str) -> date:
    return datetime.strptime(name, "%d.%m.%Y").date()


def parse_day(day_dir: Path):
    """Parse one day folder. Returns (trades, lives, positions, cmds, money).

    A running session keeps its dispatcher log in the SESSION-START folder and
    rotates it per day with a numeric suffix, so day D's dispatcher data can live
    in <D-k>/Dispatcher_XDSD_00k.html. Discover it by folder date + suffix.
    """
    day = day_from_dirname(day_dir.name)
    _disp = re.compile(r"Dispatcher_XDSD(?:_(\d+))?\.html$")
    trades: list[Trade] = []
    lives: list[OrderLife] = []
    positions: list[PositionSnap] = []
    money: list[MoneySnap] = []
    for folder in sorted(day_dir.parent.glob("*")):
        if not folder.is_dir():
            continue
        try:
            base = day_from_dirname(folder.name)
        except ValueError:
            continue
        for pth in sorted(folder.glob("Dispatcher_XDSD*.html")):
            m = _disp.search(pth.name)
            if not m:
                continue
            k = int(m.group(1)) if m.group(1) else 0
            if base + timedelta(days=k) != day:
                continue
            tr, lv, ps, mo = parse_dispatcher(pth, day)
            trades += tr; lives += lv; positions += ps; money += mo
    cmds: list[OrderCmd] = []
    for tp in sorted(day_dir.glob("Trader_*.html")):
        cmds.extend(parse_trader(tp, day))
    return trades, lives, positions, cmds, money

