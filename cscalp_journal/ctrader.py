"""cTrader Open API (FxPro): OAuth, token storage and closed-position sync into forex_result.

Keys live in ctrader.json (in the data folder) ({"client_id", "client_secret"}); the access token is kept in
data/ctrader_token.json. The sync runs in a short-lived subprocess
(`python -m cscalp_journal.ctrader sync`) because the Twisted reactor cannot be restarted
inside the web process. Only LIVE accounts are read, with the read-only `accounts` scope.

Net result of a closed position (USDT) = gross profit + swap - total commission, which is how
cTrader's own statement computes it.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import config, db, forex

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = config.DATA_DIR / "ctrader.json"
TOKEN_PATH = config.DATA_DIR / "ctrader_token.json"
REDIRECT_URI = f"http://127.0.0.1:{config.WEB_PORT}/ctrader/callback"
SYNC_SECONDS = 15 * 60
LOOKBACK_DAYS = 120
_WEEK_MS = 7 * 24 * 3600 * 1000      # the API limits a deal-list window to one week
_running = threading.Lock()


# ---------------------------------------------------------------- config + token
def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def is_configured() -> bool:
    c = load_config()
    return bool(c.get("client_id") and c.get("client_secret"))


def _auth():
    from ctrader_open_api import Auth
    c = load_config()
    return Auth(c["client_id"], c["client_secret"], REDIRECT_URI)


def auth_url() -> str:
    return _auth().getAuthUri(scope="accounts")


def _save_token(tok: dict) -> None:
    tok["expires_at"] = time.time() + int(tok.get("expiresIn") or 0)
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(tok), encoding="utf-8")


def _load_token() -> dict | None:
    try:
        return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def exchange_code(code: str) -> None:
    tok = _auth().getToken(code)
    if not tok.get("accessToken"):
        raise RuntimeError(tok.get("description") or tok.get("errorCode") or "cTrader не выдал токен")
    _save_token(tok)


def access_token() -> str | None:
    """A valid access token, refreshed when it expires within a day."""
    tok = _load_token()
    if not tok:
        return None
    if tok.get("expires_at", 0) - time.time() < 86400 and tok.get("refreshToken"):
        new = _auth().refreshToken(tok["refreshToken"])
        if new.get("accessToken"):
            _save_token(new)
            tok = new
    return tok.get("accessToken")


def status(con) -> dict:
    last = db.get_setting(con, "ctrader_last_result")
    return {"configured": is_configured(), "connected": _load_token() is not None,
            "library": importlib.util.find_spec("ctrader_open_api") is not None,
            "stored": con.execute("SELECT COUNT(*) FROM forex_result WHERE source='api'").fetchone()[0],
            "last_sync": db.get_setting(con, "ctrader_last_sync"),
            "last_result": json.loads(last) if last else None, "redirect_uri": REDIRECT_URI}


# ---------------------------------------------------------------- net result
def closing_net(gross: float, swap: float, commission: float) -> float:
    """closePositionDetail.commission is the position's total (open + close); commissions are
    always costs, so their sign is ignored. Swap keeps its sign. Matches cTrader's statement."""
    return gross + swap - abs(commission)


def _deal_rows(deals, names: dict) -> list[dict]:
    """Closing deals -> rows for forex_trade (+ forex_result). A position's opening deal is in
    the same deal list (it just has no closePositionDetail): the earliest deal of a positionId
    gives the open time and side."""
    openings: dict[int, list] = {}
    for d in deals:
        if not d.HasField("closePositionDetail"):
            openings.setdefault(d.positionId, []).append(d)
    rows = []
    for d in deals:
        if not d.HasField("closePositionDetail"):
            continue
        cp = d.closePositionDetail
        scale = 10 ** (cp.moneyDigits or d.moneyDigits or 2)
        gross, swap, comm = cp.grossProfit / scale, cp.swap / scale, abs(cp.commission) / scale
        net = closing_net(gross, swap, comm)
        ts = datetime.fromtimestamp(d.executionTimestamp / 1000)
        first = sorted(openings.get(d.positionId, []), key=lambda x: x.executionTimestamp)
        # no opening deal in the window: unknown (NULL), not "now" - a zero-length trade is wrong
        ts_open = datetime.fromtimestamp(first[0].executionTimestamp / 1000) if first else None
        # tradeSide: 1 = BUY, 2 = SELL; without the opening deal the opposite of the closing side
        open_buy = (first[0].tradeSide == 1) if first else (d.tradeSide == 2)
        name = names.get(d.symbolId, str(d.symbolId))
        rows.append({"id": f"ct:{d.dealId}", "day": ts.strftime("%Y-%m-%d"), "usd": round(net, 2),
                     "note": f"{name}, закрыто {cp.closedVolume / 100:g} ед. по {d.executionPrice:g}",
                     "position_id": str(d.positionId), "symbol": name,
                     "ts_open": ts_open.strftime("%Y-%m-%d %H:%M:%S") if ts_open else None,
                     "ts_close": ts.strftime("%Y-%m-%d %H:%M:%S"),
                     "side": "Buy" if open_buy else "Sell", "volume": cp.closedVolume / 100,
                     "entry_price": cp.entryPrice, "exit_price": d.executionPrice,
                     "gross": round(gross, 2), "swap": round(swap, 2), "commission": round(comm, 2)})
    return rows


def _store(con, rows: list[dict]) -> int:
    added = 0
    for r in rows:
        added += con.execute(
            "INSERT INTO forex_result(id,day,usd,note,source) VALUES(?,?,?,?,'api') "
            "ON CONFLICT(id) DO UPDATE SET usd=excluded.usd, note=excluded.note, day=excluded.day",
            (r["id"], r["day"], r["usd"], r["note"])).rowcount
        con.execute(
            "INSERT INTO forex_trade(id,position_id,ts_open,ts_close,symbol,side,volume,entry_price,"
            "exit_price,gross,swap,commission,net_usd) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET ts_open=COALESCE(excluded.ts_open, forex_trade.ts_open), "
            "ts_close=excluded.ts_close, "
            "side=excluded.side, volume=excluded.volume, entry_price=excluded.entry_price, "
            "exit_price=excluded.exit_price, gross=excluded.gross, swap=excluded.swap, "
            "commission=excluded.commission, net_usd=excluded.net_usd",
            (r["id"], r["position_id"], r["ts_open"], r["ts_close"], r["symbol"], r["side"], r["volume"],
             r["entry_price"], r["exit_price"], r["gross"], r["swap"], r["commission"], r["usd"]))
        con.execute("DELETE FROM forex_open WHERE position_id=?", (r["position_id"],))
    con.commit()
    return added


def _open_rows(positions, names: dict) -> list[dict]:
    """ProtoOAReconcileRes.position -> rows for forex_open (positions that are still open)."""
    rows = []
    for p in positions:
        td = p.tradeData
        rows.append({"position_id": str(p.positionId), "symbol": names.get(td.symbolId, str(td.symbolId)),
                     "side": "Buy" if td.tradeSide == 1 else "Sell", "volume": td.volume / 100,
                     "entry_price": p.price,
                     "ts_open": datetime.fromtimestamp(td.openTimestamp / 1000).strftime("%Y-%m-%d %H:%M:%S")})
    return rows


def _store_open(con, rows: list[dict]) -> None:
    con.execute("DELETE FROM forex_open")
    for r in rows:
        con.execute("INSERT OR REPLACE INTO forex_open(position_id,symbol,side,volume,entry_price,ts_open) "
                    "VALUES(?,?,?,?,?,?)", (r["position_id"], r["symbol"], r["side"], r["volume"],
                                            r["entry_price"], r["ts_open"]))
    con.commit()


# ---------------------------------------------------------------- sync (runs in a subprocess)
def run_sync() -> dict:
    result: dict = {"accounts": 0, "deals": 0, "closed": 0, "open": 0, "error": None}
    open_rows: list[dict] = []
    cfg, token = load_config(), None
    if not is_configured():
        return {**result, "error": "в ctrader.json нет ключей"}
    try:
        token = access_token()
    except Exception as e:
        return {**result, "error": f"не удалось обновить токен: {e}"}
    if not token:
        return {**result, "error": "cTrader не подключён"}

    from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAAccountAuthReq, ProtoOAApplicationAuthReq, ProtoOADealListReq,
        ProtoOAGetAccountListByAccessTokenReq, ProtoOAReconcileReq, ProtoOASymbolsListReq)
    from twisted.internet import defer, reactor

    con = db.connect()
    now_ms = int(time.time() * 1000)
    first_ms = (int(datetime.strptime(cfg["history_from"], "%Y-%m-%d").timestamp() * 1000)
                if cfg.get("history_from") else now_ms - LOOKBACK_DAYS * 86400 * 1000)
    since_ms = int(db.get_setting(con, "ctrader_since_ms") or 0) or first_ms
    if not con.execute("SELECT COUNT(*) FROM forex_trade").fetchone()[0]:
        since_ms = first_ms      # forex_trade is new: read the whole history once to fill it
    client = Client(EndPoints.PROTOBUF_LIVE_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)

    def reply(msg):
        res = Protobuf.extract(msg)
        if res.DESCRIPTOR.name == "ProtoOAErrorRes":
            raise RuntimeError(f"{res.errorCode}: {res.description}")
        return res

    @defer.inlineCallbacks
    def work():
        req = ProtoOAApplicationAuthReq()
        req.clientId, req.clientSecret = cfg["client_id"], cfg["client_secret"]
        reply((yield client.send(req, responseTimeoutInSeconds=30)))
        req = ProtoOAGetAccountListByAccessTokenReq()
        req.accessToken = token
        wanted = {int(x) for x in cfg.get("account_logins", [])}   # empty = every live account
        accounts = [a for a in reply((yield client.send(req, responseTimeoutInSeconds=30))).ctidTraderAccount
                    if a.isLive and (not wanted or a.traderLogin in wanted)]
        result["accounts"] = len(accounts)
        for acc in accounts:
            for attempt in (1, 2):      # a slow account often answers on the second try
                try:
                    yield sync_account(acc)
                    break
                except Exception as e:  # one unreachable account must not sink the others
                    if attempt == 2:
                        hint = (" — нет ответа от счёта; если он вам не нужен, впишите нужный номер "
                                "в account_logins в ctrader.json") if "Deferred" in str(e) else ""
                        result.setdefault("account_errors", []).append(f"{acc.traderLogin}: {e}{hint}")
        forex.ensure_rates(con)
        if not result.get("account_errors"):
            _store_open(con, open_rows)
            # re-read a few days back next time so late-posted closings are not missed
            db.set_setting(con, "ctrader_since_ms", str(now_ms - 3 * 86400 * 1000))
        forex.ensure_cbr_rates(con)

    @defer.inlineCallbacks
    def sync_account(acc):
        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId, req.accessToken = acc.ctidTraderAccountId, token
        reply((yield client.send(req, responseTimeoutInSeconds=30)))
        req = ProtoOASymbolsListReq()
        req.ctidTraderAccountId = acc.ctidTraderAccountId
        names = {s.symbolId: s.symbolName
                 for s in reply((yield client.send(req, responseTimeoutInSeconds=30))).symbol}
        deals, t = [], since_ms
        while t < now_ms:
            req = ProtoOADealListReq()
            req.ctidTraderAccountId = acc.ctidTraderAccountId
            req.fromTimestamp, req.toTimestamp, req.maxRows = t, min(t + _WEEK_MS, now_ms), 1000
            deals.extend(reply((yield client.send(req, responseTimeoutInSeconds=60))).deal)
            t += _WEEK_MS
        # closing deals whose opening deal is older than the window (and whose open time is still
        # unknown) -> read the older history once so ts_open gets filled
        opened = {d.positionId for d in deals if not d.HasField("closePositionDetail")}
        orphans = [f"ct:{d.dealId}" for d in deals
                   if d.HasField("closePositionDetail") and d.positionId not in opened]
        if orphans and since_ms > first_ms:
            known = {r[0] for r in con.execute(
                "SELECT id FROM forex_trade WHERE ts_open IS NOT NULL AND id IN (%s)" % ",".join("?" * len(orphans)),
                orphans)}
            if len(known) < len(orphans):
                t = first_ms
                while t < since_ms:
                    req = ProtoOADealListReq()
                    req.ctidTraderAccountId = acc.ctidTraderAccountId
                    req.fromTimestamp, req.toTimestamp, req.maxRows = t, min(t + _WEEK_MS, since_ms), 1000
                    deals.extend(reply((yield client.send(req, responseTimeoutInSeconds=60))).deal)
                    t += _WEEK_MS
        rows = _deal_rows(deals, names)
        rows = [r for r in rows if r["ts_close"] >= datetime.fromtimestamp(since_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")]
        result["deals"] += len(deals)
        result["closed"] += len(rows)
        _store(con, rows)
        req = ProtoOAReconcileReq()
        req.ctidTraderAccountId = acc.ctidTraderAccountId
        open_rows.extend(_open_rows(reply((yield client.send(req, responseTimeoutInSeconds=30))).position, names))
        result["open"] = len(open_rows)

    def done(_):
        if reactor.running:
            reactor.stop()

    def failed(f):
        result["error"] = str(f.value)

    client.setConnectedCallback(lambda c: work().addErrback(failed).addBoth(done))
    client.setDisconnectedCallback(lambda c, reason: None)
    client.startService()
    reactor.callLater(230, done, None)
    reactor.run(installSignalHandlers=False)
    return result


def sync_now() -> dict:
    """Run one sync in a subprocess and record the outcome. Safe to call from threads."""
    if not _running.acquire(blocking=False):
        return {"error": "синхронизация уже идёт"}
    try:
        con = db.connect()
        try:
            out = subprocess.run([sys.executable, "-m", "cscalp_journal.ctrader", "sync"], cwd=ROOT,
                                 capture_output=True, text=True, timeout=280)
            res = json.loads(out.stdout.strip().splitlines()[-1]) if out.stdout.strip() else \
                {"error": (out.stderr or "пустой ответ").strip()[-300:]}
        except Exception as e:
            res = {"error": str(e)}
        db.set_setting(con, "ctrader_last_sync", datetime.now().isoformat(sep=" ", timespec="seconds"))
        db.set_setting(con, "ctrader_last_result", json.dumps(res, ensure_ascii=False))
        return res
    finally:
        _running.release()


def loop() -> None:
    """Background thread: sync every SYNC_SECONDS while connected."""
    time.sleep(30)
    while True:
        if is_configured() and _load_token() is not None:
            sync_now()
        time.sleep(SYNC_SECONDS)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        print(json.dumps(run_sync(), ensure_ascii=False))
    else:
        print(__doc__)
