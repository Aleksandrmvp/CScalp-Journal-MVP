"""cTrader Open API (FxPro): OAuth, token storage and closed-position sync into forex_result.

Keys live in ./ctrader.json ({"client_id", "client_secret"}); the access token is kept in
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
CONFIG_PATH = ROOT / "ctrader.json"
TOKEN_PATH = config.DB_PATH.parent / "ctrader_token.json"
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
    """Closing deals -> forex_result rows."""
    rows = []
    for d in deals:
        if not d.HasField("closePositionDetail"):
            continue
        cp = d.closePositionDetail
        scale = 10 ** (cp.moneyDigits or d.moneyDigits or 2)
        net = closing_net(cp.grossProfit / scale, cp.swap / scale, cp.commission / scale)
        day = datetime.fromtimestamp(d.executionTimestamp / 1000).strftime("%Y-%m-%d")
        name = names.get(d.symbolId, str(d.symbolId))
        rows.append({"id": f"ct:{d.dealId}", "day": day, "usd": round(net, 2),
                     "note": f"{name}, закрыто {cp.closedVolume / 100:g} ед. по {d.executionPrice:g}"})
    return rows


def _store(con, rows: list[dict]) -> int:
    added = 0
    for r in rows:
        added += con.execute(
            "INSERT INTO forex_result(id,day,usd,note,source) VALUES(?,?,?,?,'api') "
            "ON CONFLICT(id) DO UPDATE SET usd=excluded.usd, note=excluded.note, day=excluded.day",
            (r["id"], r["day"], r["usd"], r["note"])).rowcount
    con.commit()
    return added


# ---------------------------------------------------------------- sync (runs in a subprocess)
def run_sync() -> dict:
    result: dict = {"accounts": 0, "deals": 0, "positions": 0, "error": None}
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
        ProtoOAGetAccountListByAccessTokenReq, ProtoOASymbolsListReq)
    from twisted.internet import defer, reactor

    con = db.connect()
    now_ms = int(time.time() * 1000)
    since_ms = int(db.get_setting(con, "ctrader_since_ms") or 0) or now_ms - LOOKBACK_DAYS * 86400 * 1000
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
            try:
                yield sync_account(acc)
            except Exception as e:      # one unreachable account must not sink the others
                result.setdefault("account_errors", []).append(f"{acc.traderLogin}: {e}")
        forex.ensure_rates(con)
        if not result.get("account_errors"):
            # re-read a few days back next time so late-posted closings are not missed
            db.set_setting(con, "ctrader_since_ms", str(now_ms - 3 * 86400 * 1000))

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
        rows = _deal_rows(deals, names)
        result["deals"] += len(deals)
        result["positions"] += len(rows)
        _store(con, rows)

    def done(_):
        if reactor.running:
            reactor.stop()

    def failed(f):
        result["error"] = str(f.value)

    client.setConnectedCallback(lambda c: work().addErrback(failed).addBoth(done))
    client.setDisconnectedCallback(lambda c, reason: None)
    client.startService()
    reactor.callLater(180, done, None)
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
                                 capture_output=True, text=True, timeout=240)
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
