"""Post arb-trade open/close events to a Telegram channel.

Fires once per bundle cycle open/close, deduped via notify_log. A cutoff time is
stored when notifications are first enabled, so existing history is never posted —
only opens/closes that happen after enabling. Post format follows the user's
channel style (formula, avg = entry spread %, size, PnL on close).
"""
from __future__ import annotations

from datetime import datetime

from . import db, telegram, views


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="milliseconds")


def _fmt(x: float | None) -> str:
    if x is None:
        return ""
    s = f"{x:,.4f}".rstrip("0").rstrip(".")
    return s.replace(",", " ").replace(".", ",")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _peak_sizes(trade: dict) -> dict[str, float]:
    """Max absolute net position reached per ticker during the cycle."""
    book: dict[str, float] = {}
    peak: dict[str, float] = {}
    for e in trade["events"]:
        signed = e["qty"] if e["side"] == "Buy" else -e["qty"]
        book[e["ticker"]] = book.get(e["ticker"], 0.0) + signed
        peak[e["ticker"]] = max(peak.get(e["ticker"], 0.0), abs(book[e["ticker"]]))
    return peak


def _spread_line(trade: dict) -> str | None:
    sp = trade.get("spread")
    if sp and not sp.get("error") and sp.get("value") is not None:
        return f"avg: {_fmt(sp['value'])}%"
    return None


def _synthetic_lines(trade: dict) -> list[str]:
    sp = trade.get("spread") or {}
    out = []
    for sym, val in (sp.get("synthetic") or {}).items():
        name = sym.split(":", 1)[-1]
        out.append(f"{name} (синт.): {_fmt(val)}")
    return out


def _open_text(name: str, formula: str, trade: dict) -> str:
    lines = [f"🟢 Открыта: {_esc(name)}"]
    if formula and formula.strip():
        lines.append(f"<code>{_esc(formula)}</code>")
    sp = _spread_line(trade)
    if sp:
        lines.append(sp)
    lines += _synthetic_lines(trade)
    sizes = _peak_sizes(trade)
    if sizes:
        lines.append("size: " + "/".join(_fmt(v) for v in sizes.values()))
    return "\n".join(lines)


def _close_text(name: str, trade: dict, unit: str) -> str:
    lines = [f"🔴 Закрыта: {_esc(name)}"]
    sp = _spread_line(trade)
    if sp:
        lines.append(sp.replace("avg:", "avg входа:"))
    lines += _synthetic_lines(trade)
    net = trade.get("realized_net", trade["realized_pnl"])
    lines.append(f"PnL: {_fmt(net)} {unit}")
    if trade.get("commission"):
        lines.append(f"(комиссия −{_fmt(trade['commission'])} {unit})")
    return "\n".join(lines)


def publish_trade(con, bundle_id: int, trade_idx: int, event: str) -> dict:
    """Manually post a specific trade's open/close now (bypasses the cutoff)."""
    t = views.bundle_trades(con, bundle_id)
    if not t:
        return {"ok": False, "error": "bundle not found"}
    tr = next((x for x in t["trades"] if x["idx"] == trade_idx), None)
    if not tr:
        return {"ok": False, "error": "trade not found"}
    unit = "п." if t["points_only"] else "₽"
    msg = _open_text(t["name"], t["formula"], tr) if event == "open" else _close_text(t["name"], tr, unit)
    r = telegram.send_message(msg)
    if r.get("ok"):
        db.notify_mark(con, bundle_id, tr["entry_ts"] or "", event, _now())
    return r


def check_and_post(con) -> None:
    if not telegram.is_enabled():
        return
    cutoff = db.get_setting(con, "tg_cutoff")
    if not cutoff:
        cutoff = _now()
        db.set_setting(con, "tg_cutoff", cutoff)

    for row in con.execute("SELECT id FROM bundle").fetchall():
        try:
            t = views.bundle_trades(con, row["id"])
        except Exception:
            continue
        unit = "п." if t["points_only"] else "₽"
        for tr in t["trades"]:
            key = tr["entry_ts"] or ""
            # OPEN
            if key and key > cutoff and not db.notify_seen(con, t["id"], key, "open"):
                r = telegram.send_message(_open_text(t["name"], t["formula"], tr))
                if r.get("ok"):
                    db.notify_mark(con, t["id"], key, "open", _now())
            # CLOSE
            if (tr["status"] == "closed" and tr.get("exit_ts") and tr["exit_ts"] > cutoff
                    and not db.notify_seen(con, t["id"], key, "close")):
                r = telegram.send_message(_close_text(t["name"], tr, unit))
                if r.get("ok"):
                    db.notify_mark(con, t["id"], key, "close", _now())
