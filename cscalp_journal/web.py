"""FastAPI dashboard: positions, journal, and interactive arbitrage bundles.

Run:
    python -m cscalp_journal.web
Background ingest loop tails today's logs into SQLite; dashboard at :8777.
"""
from __future__ import annotations

import html
import math
import threading

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

import re


from . import config, ctrader, db, forex, ingest, notify, report, telegram, views
from .report_page import REPORT_PAGE
from .theme import BG_PATH, with_photo

app = FastAPI(title="CScalp Journal")


def _ingest_thread() -> None:
    con = db.connect()
    ingest.loop(con)


@app.on_event("startup")
def _start_ingest() -> None:
    threading.Thread(target=_ingest_thread, daemon=True).start()
    threading.Thread(target=ctrader.loop, daemon=True).start()


@app.get("/api/positions")
def api_positions():
    return JSONResponse(views.positions(db.connect()))


@app.get("/api/journal")
def api_journal():
    return JSONResponse(views.journal(db.connect()))


@app.get("/api/summary")
def api_summary():
    return JSONResponse(views.summary(db.connect()))


@app.get("/api/unassigned")
def api_unassigned():
    return JSONResponse(views.unassigned_fills(db.connect()))


@app.get("/api/telegram")
def api_telegram_status():
    c = telegram.load_config()
    return {"has_token": bool(c.get("token")), "chat_id": c.get("chat_id") or "",
            "enabled": bool(c.get("enabled"))}


class TgConfig(BaseModel):
    chat_id: str | None = None
    enabled: bool | None = None


@app.put("/api/telegram")
def api_telegram_set(cfg: TgConfig):
    c = telegram.load_config()
    if cfg.chat_id is not None:
        c["chat_id"] = cfg.chat_id.strip()
    if cfg.enabled is not None:
        c["enabled"] = cfg.enabled
    telegram.save_config(c)
    return {"ok": True}


@app.post("/api/telegram/test")
def api_telegram_test():
    return JSONResponse(telegram.send_message("✅ Тест: бот подключён к каналу."))


class PublishReq(BaseModel):
    trade_idx: int
    event: str


@app.post("/api/bundles/{bundle_id}/publish")
def api_publish(bundle_id: int, p: PublishReq):
    return JSONResponse(notify.publish_trade(db.connect(), bundle_id, p.trade_idx, p.event))


@app.get("/api/bundles")
def api_bundles():
    return JSONResponse(views.bundles(db.connect()))


@app.get("/api/bundles/{bundle_id}/trades")
def api_bundle_trades(bundle_id: int):
    t = views.bundle_trades(db.connect(), bundle_id)
    if not t:
        raise HTTPException(404, "bundle not found")
    return JSONResponse(t)


class Formula(BaseModel):
    formula: str


@app.put("/api/bundles/{bundle_id}/formula")
def api_set_formula(bundle_id: int, f: Formula):
    db.set_formula(db.connect(), bundle_id, f.formula)
    return {"ok": True}


class Rename(BaseModel):
    name: str


@app.put("/api/bundles/{bundle_id}/name")
def api_rename_bundle(bundle_id: int, r: Rename):
    if not r.name.strip():
        raise HTTPException(400, "name required")
    db.set_name(db.connect(), bundle_id, r.name.strip())
    return {"ok": True}


class NewBundle(BaseModel):
    name: str
    note: str | None = None
    trade_ids: list[str] = []


class Members(BaseModel):
    trade_ids: list[str]


class ManualLeg(BaseModel):
    ticker: str
    side: str
    price: float
    qty: float
    venue: str | None = "forex"
    ts: str | None = None
    note: str | None = None


@app.post("/api/bundles")
def api_create_bundle(b: NewBundle):
    if not b.name.strip():
        raise HTTPException(400, "name required")
    bid = db.create_bundle(db.connect(), b.name.strip(), b.note, b.trade_ids)
    return {"id": bid}


@app.post("/api/bundles/{bundle_id}/members")
def api_add_members(bundle_id: int, m: Members):
    db.add_members(db.connect(), bundle_id, m.trade_ids)
    return {"ok": True}


@app.delete("/api/bundles/{bundle_id}/members/{trade_id}")
def api_remove_member(bundle_id: int, trade_id: str):
    db.remove_member(db.connect(), bundle_id, trade_id)
    return {"ok": True}


@app.post("/api/bundles/{bundle_id}/legs")
def api_add_leg(bundle_id: int, leg: ManualLeg):
    side = leg.side.capitalize()
    if side not in ("Buy", "Sell"):
        raise HTTPException(400, "side must be Buy or Sell")
    lid = db.add_manual_leg(db.connect(), bundle_id, leg.venue, leg.ticker.strip(),
                            side, leg.price, leg.qty, leg.ts, leg.note)
    return {"id": lid}


@app.delete("/api/legs/{leg_id}")
def api_remove_leg(leg_id: int):
    db.remove_manual_leg(db.connect(), leg_id)
    return {"ok": True}


@app.delete("/api/bundles/{bundle_id}")
def api_delete_bundle(bundle_id: int):
    db.delete_bundle(db.connect(), bundle_id)
    return {"ok": True}


@app.get("/api/report")
def api_report(month: str | None = None):
    if month is not None and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(400, "month must be YYYY-MM")
    return JSONResponse(report.build(db.connect(), month))


class Capital(BaseModel):
    start: float | None = None
    current: float | None = None
    peak: float | None = None
    peak_month: str | None = None


@app.put("/api/report/capital")
def api_report_capital(c: Capital):
    if any(v is not None and v <= 0 for v in (c.start, c.current, c.peak)):
        raise HTTPException(400, "capital must be positive")
    if c.peak_month and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", c.peak_month):
        raise HTTPException(400, "peak_month must be YYYY-MM")
    report.set_capital(db.connect(), c.start, c.current, c.peak, c.peak_month)
    return {"ok": True}


class ForexResultIn(BaseModel):
    day: str
    usd: float
    rub: float | None = None
    note: str | None = None


@app.post("/api/report/forex-result")
def api_forex_add(r: ForexResultIn):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r.day):
        raise HTTPException(400, "day must be YYYY-MM-DD")
    if not math.isfinite(r.usd) or r.usd == 0 or (r.rub is not None and not math.isfinite(r.rub)):
        raise HTTPException(400, "usd must be a non-zero number")
    try:
        return {"ok": True, **forex.add_result(db.connect(), r.day, r.usd, (r.note or "").strip()[:200], r.rub)}
    except ValueError:
        raise HTTPException(400, "bad date")


@app.delete("/api/report/forex-result/{rid:path}")
def api_forex_delete(rid: str):
    if not forex.delete_result(db.connect(), rid):
        raise HTTPException(404, "not found or not a manual entry")
    return {"ok": True}


@app.delete("/api/report/cash/{rid}")
def api_cash_delete(rid: int):
    if not report.delete_cash(db.connect(), rid):
        raise HTTPException(404, "not found")
    return {"ok": True}


@app.post("/api/report/cash/{rid}/toggle")
def api_cash_toggle(rid: int):
    kind = report.toggle_exclude_cash(db.connect(), rid)
    if kind is None:
        raise HTTPException(404, "not found")
    return {"ok": True, "kind": kind}


class CashText(BaseModel):
    text: str


@app.post("/api/report/cash")
def api_report_cash(c: CashText):
    if len(c.text) > 200_000:
        raise HTTPException(413, "text too long")
    return report.add_cash(db.connect(), c.text)


@app.get("/ctrader/connect")
def ctrader_connect():
    if not ctrader.is_configured():
        return HTMLResponse("<p>В файле ctrader.json нет client_id и client_secret.</p>", status_code=400)
    return RedirectResponse(ctrader.auth_url())


@app.get("/ctrader/callback")
def ctrader_callback(code: str | None = None, error: str | None = None):
    if not code:
        return HTMLResponse(f"<p>cTrader не вернул код авторизации: {html.escape(error or 'доступ не разрешён')}"
                            "</p><p><a href='/report'>Вернуться к отчёту</a></p>", status_code=400)
    try:
        ctrader.exchange_code(code)
    except Exception as e:
        return HTMLResponse(f"<p>Не удалось получить токен: {html.escape(str(e))}</p>"
                            "<p><a href='/report'>Вернуться к отчёту</a></p>", status_code=400)
    threading.Thread(target=ctrader.sync_now, daemon=True).start()
    return RedirectResponse("/report")


@app.get("/api/ctrader/status")
def api_ctrader_status():
    return ctrader.status(db.connect())


@app.post("/api/ctrader/sync")
def api_ctrader_sync():
    threading.Thread(target=ctrader.sync_now, daemon=True).start()
    return {"started": True}


@app.get("/report", response_class=HTMLResponse)
def report_page():
    return REPORT_PAGE


@app.get("/", response_class=HTMLResponse)
def index():
    return _PAGE


@app.get("/bundle/{bundle_id}", response_class=HTMLResponse)
def bundle_page(bundle_id: int):
    return _BUNDLE_PAGE


_PAGE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>CScalp Journal</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script>try{document.documentElement.dataset.theme=localStorage.getItem('theme')||'dark'}catch(e){document.documentElement.dataset.theme='dark'}</script>
<style>
 :root{--bg:#0f1115;--fg:#e6e6e6;--mut:#8a90a0;--line:#242833;--buy:#3fb37a;--sell:#e0625f;--card:#161922;--accent:#5f9fe0;--btn:#1e2230;--btnp:#22406a;--input:#0f1218;--panel2:#12151d}
 :root[data-theme="light"]{--bg:#f5f6f8;--fg:#1a1d24;--mut:#697086;--line:#e1e4ea;--buy:#1a8a55;--sell:#c8433f;--card:#ffffff;--accent:#2563eb;--btn:#eef0f4;--btnp:#dbe6fb;--input:#ffffff;--panel2:#f0f2f6}
 body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 ui-monospace,Menlo,Consolas,monospace}
 header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
 h1{font-size:15px;margin:0;font-weight:600}
 .mut{color:var(--mut)}
 main{padding:16px;display:grid;gap:20px}
 h2{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:0 0 8px}
 table{border-collapse:collapse;width:100%;font-size:13px}
 th,td{text-align:right;padding:4px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
 th:first-child,td:first-child,th.l,td.l{text-align:left}
 .buy{color:var(--buy)} .sell{color:var(--sell)}
 a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
 .pill{font-size:11px;padding:1px 7px;border-radius:10px;color:#0f1115;font-weight:600}
 .tag{font-size:11px;padding:1px 6px;border-radius:4px;border:1px solid var(--line)}
 .taker{color:#e0a95f;border-color:#5a4a2a}.maker{color:#5f9fe0;border-color:#2a3f5a}.unknown{color:var(--mut)}
 .card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
 .pos-flat{color:var(--mut)} .neg{color:var(--sell)}.pos{color:var(--buy)}
 button{background:var(--btn);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px 10px;cursor:pointer;font:inherit}
 button:hover{border-color:var(--accent)}
 button.primary{background:var(--btnp);border-color:var(--accent)}
 button.danger:hover{border-color:var(--sell);color:var(--sell)}
 input,select{background:var(--input);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px 8px;font:inherit}
 .bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
 .bundle{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-bottom:12px;background:var(--panel2)}
 .bundle h3{font-size:14px;margin:0 0 6px;display:flex;gap:10px;align-items:center}
 .legform{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
 .legform input{width:92px}.legform input.tk{width:120px}
 .selcount{color:var(--accent)}
 .venue{font-size:11px;color:var(--mut)}
 .chk{width:16px}
 td.chkcell{cursor:pointer;user-select:none;width:30px}
 table.compact{width:auto;min-width:0}
 table.compact th,table.compact td{padding:3px 9px}
 tr.picked td{background:rgba(95,159,224,.22)}
 tr.inb td{background:#2a2d35;color:#9aa1b2;border-bottom-color:#22252d}
 tr.inb .mut{color:#767d8f} tr.inb .buy{color:#4a9d78} tr.inb .sell{color:#b0605d}
 tr.inb.picked td{background:#2a2d35}
 input.chk:disabled{opacity:.6;cursor:default}
 :root[data-theme="light"] tr.inb td{background:#c8ccd6;color:#2a2e3a;border-bottom-color:#b4b9c6}
 :root[data-theme="light"] tr.inb .mut{color:#566075}
</style></head>
<body>
<header>
 <h1>CScalp Journal</h1>
 <a href="/report">Итоги месяца</a>
 <span class="mut" id="summary">…</span>
 <span class="mut" id="clock" style="margin-left:auto"></span>
 <button onclick="toggleTheme()" title="Светлая/тёмная тема" style="padding:3px 9px">🌓</button>
</header>
<main>
 <section class="card"><h2>Открытые позиции</h2><div id="positions"></div></section>

 <section class="card"><h2>Арбитражные связки</h2>
   <div class="bar">
     <input id="bname" placeholder="Название связки (напр. CNYRUBF/CNY-12.26)" style="width:320px">
     <button class="primary" onclick="createBundle()">Создать из выбранных (<span class="selcount" id="selc">0</span>)</button>
     <span class="mut">— отметьте сделки чекбоксами в журнале ниже</span>
   </div>
   <div id="bundles"></div>
 </section>

 <section class="card"><h2>Журнал действий</h2>
   <div class="bar">
     <label class=mut>Инструмент <select id="fTicker" onchange="setJF('ticker',this.value)"><option value="">все</option></select></label>
     <label class=mut>Событие <select id="fEvent" onchange="setJF('event',this.value)"><option value="">все</option><option>SUBMIT</option><option>CANCEL</option><option selected>FILL</option></select></label>
     <label class=mut>Сторона <select id="fSide" onchange="setJF('side',this.value)"><option value="">все</option><option>Buy</option><option>Sell</option></select></label>
     <label class=mut>Ликвидность <select id="fLiq" onchange="setJF('liq',this.value)"><option value="">все</option><option>maker</option><option>taker</option><option>unknown</option></select></label>
     <button onclick="resetJF()">сбросить</button>
     <button onclick="selAllNew()" title="Отметить все новые сделки по текущему фильтру на всех страницах">выбрать все новые</button>
     <button onclick="selClear()">снять выделение</button>
     <span class=mut title="Светлые строки уже добавлены в связку">▪ светлые строки — уже в связке</span>
     <span class=mut id="jcount"></span>
     <span style="margin-left:auto;display:flex;gap:6px;align-items:center">
       <label class=mut>На странице <select id="fPage" onchange="setPageSize(this.value)"><option>50</option><option>100</option></select></label>
       <button onclick="jprev()">←</button><span class=mut id="jpageinfo"></span><button onclick="jnext()">→</button>
     </span>
   </div>
   <div id="journal"></div>
 </section>

 <section class="card"><h2>Telegram-канал</h2>
   <div class="bar">
     <span id="tgstatus" class="mut">…</span>
   </div>
   <div class="bar">
     <input id="tgchat" placeholder="@канал или -100... (id канала)" style="width:280px">
     <button onclick="tgSaveChat()">Сохранить канал</button>
     <button onclick="tgTest()">Тест</button>
   </div>
   <div class="bar"><span class="mut">Автопостинг отключён — публикация только вручную кнопками 📢 на странице связки.</span></div>
 </section>
</main>
<script>
function toggleTheme(){const t=document.documentElement.dataset.theme==='light'?'dark':'light';document.documentElement.dataset.theme=t;try{localStorage.setItem('theme',t)}catch(e){}}
const sel = new Set();
let BUNDLES = [];
const fmt = n => n===null||n===undefined ? '' : (Math.round(n*1e6)/1e6).toLocaleString('ru-RU');
const sideCls = s => s==='Buy'?'buy':(s==='Sell'?'sell':'');
async function j(u){return (await fetch(u)).json()}
async function post(u,body){return fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}
async function del(u){return fetch(u,{method:'DELETE'})}

function renderPositions(rows){
  if(!rows.length){positions.innerHTML='<div class="mut">нет сделок</div>';return}
  let h='<table><tr><th class=l>Инструмент</th><th>Позиция</th><th>Ср. цена</th><th>Реализ., ₽</th><th>Сделок</th></tr>';
  for(const r of rows){
    const flat=r.net_qty===0, qcls=flat?'pos-flat':(r.net_qty>0?'buy':'sell');
    const rcls=r.realized_points>0?'pos':(r.realized_points<0?'neg':'mut');
    h+=`<tr><td class=l>${r.ticker}</td><td class="${qcls}">${fmt(r.net_qty)}</td><td>${flat?'':fmt(r.avg_price)}</td><td class="${rcls}">${fmt(r.realized_points)}</td><td class=mut>${r.fills}</td></tr>`;
  }
  positions.innerHTML=h+'</table>';
}

let drag=null,lastPick=null;
function pickRow(tr,state){
  const id=tr.dataset.id; if(!id)return;
  if(state)sel.add(id); else sel.delete(id);
  tr.classList.toggle('picked',state);
  const cb=tr.querySelector('input.chk'); if(cb)cb.checked=state;
  selc.textContent=sel.size;
}
function syncSelAll(){
  const rows=[...journal.querySelectorAll('tr[data-id]:not(.inb)')], box=document.getElementById('selAll');
  if(box)box.checked=rows.length>0&&rows.every(r=>sel.has(r.dataset.id));
}
function selPage(on){ journal.querySelectorAll('tr[data-id]:not(.inb)').forEach(tr=>pickRow(tr,on)); }
function selAllNew(){
  JOURNAL.filter(matchJF).filter(r=>r.event==='FILL'&&r.id&&!(r.bundles||[]).length).forEach(r=>sel.add(r.id));
  selc.textContent=sel.size; _lastJournalJson=''; renderJournal();
}
function selClear(){ sel.clear(); selc.textContent=0; _lastJournalJson=''; renderJournal(); }
document.getElementById('journal').addEventListener('mousedown',ev=>{
  const td=ev.target.closest('td.chkcell'); if(!td||ev.button!==0)return;
  const tr=td.parentElement, state=!sel.has(tr.dataset.id);
  ev.preventDefault();
  if(ev.shiftKey&&lastPick&&lastPick!==tr&&lastPick.isConnected){
    const rows=[...journal.querySelectorAll('tr[data-id]')], a=rows.indexOf(lastPick), b=rows.indexOf(tr);
    rows.slice(Math.min(a,b),Math.max(a,b)+1).forEach(r=>pickRow(r,state));
  } else pickRow(tr,state);
  drag={state}; lastPick=tr; syncSelAll();
});
document.getElementById('journal').addEventListener('mouseover',ev=>{
  if(!drag||ev.buttons!==1)return;
  const tr=ev.target.closest('tr[data-id]'); if(tr){pickRow(tr,drag.state); lastPick=tr; syncSelAll();}
});
document.addEventListener('mouseup',()=>{drag=null});

let JOURNAL=[];
const jf={ticker:'',event:'FILL',side:'',liq:''};
let jpage=1, jpsize=50;
function setJF(k,v){jf[k]=v;jpage=1;renderJournal();}
function resetJF(){for(const k in jf)jf[k]='';for(const el of ['fTicker','fEvent','fSide','fLiq'])document.getElementById(el).value='';jpage=1;renderJournal();}
function setPageSize(v){jpsize=+v;jpage=1;renderJournal();}
function jprev(){if(jpage>1){jpage--;renderJournal();}}
function jnext(){jpage++;renderJournal();}
function matchJF(r){
  if(jf.ticker&&r.ticker!==jf.ticker)return false;
  if(jf.event&&r.event!==jf.event)return false;
  if(jf.side&&r.side!==jf.side)return false;
  if(jf.liq&&(r.liquidity||'')!==jf.liq)return false;
  return true;
}
let _lastTickers='';
function syncTickerOptions(){
  const ts=[...new Set(JOURNAL.map(r=>r.ticker))].sort();
  const sig=ts.join(',');
  if(sig===_lastTickers)return; _lastTickers=sig;
  const cur=jf.ticker;
  fTicker.innerHTML='<option value="">все</option>'+ts.map(t=>`<option${t===cur?' selected':''}>${t}</option>`).join('');
}
let _lastJournalJson='';
function renderJournal(){
  syncTickerOptions();
  const filtered=JOURNAL.filter(matchJF);
  const total=filtered.length, pages=Math.max(1,Math.ceil(total/jpsize));
  if(jpage>pages)jpage=pages;
  const start=(jpage-1)*jpsize;
  const rows=filtered.slice().reverse().slice(start,start+jpsize);
  const sig=JSON.stringify(rows)+'|'+jpage+'|'+jpsize+'|'+total+'|'+[...sel].join(',');
  if(sig===_lastJournalJson)return;
  _lastJournalJson=sig;
  document.getElementById('jcount').textContent=`показано: ${total} из ${JOURNAL.length}`;
  document.getElementById('jpageinfo').textContent=`стр ${jpage}/${pages}`;
  if(!rows.length){journal.innerHTML='<div class="mut">нет событий по фильтру</div>';return}
  let h='<table class=compact><tr><th class=chk><input type=checkbox id=selAll title="Выбрать все новые сделки на странице" onchange="selPage(this.checked)"></th><th class=l>Инструмент</th><th class=l>Связка</th><th class=l>Время</th><th class=l>Событие</th><th class=l>Сторона</th><th>Цена</th><th>Кол-во</th><th class=l>Ликвидность</th></tr>';
  for(const r of rows){
    const [dpart,tpart]=r.ts.split(' '); const t=`<span class=mut>${dpart.slice(8,10)}.${dpart.slice(5,7)}.${dpart.slice(2,4)}</span> ${tpart||''}`;
    const bundled=(r.bundles||[]).length>0, picked=r.id&&sel.has(r.id), isFill=r.event==='FILL'&&r.id, isNew=isFill&&!bundled;
    const liq=r.liquidity?`<span class="tag ${r.liquidity}">${r.liquidity}</span>`:'';
    const chk=isFill?(bundled?`<input type=checkbox class=chk checked disabled title="Уже в связке">`:`<input type=checkbox class=chk tabindex=-1 ${sel.has(r.id)?'checked':''}>`):'';
    const bnd=(r.bundles||[]).map(b=>`<a href="/bundle/${b.id}" class="tag" style="border-color:var(--accent)">${b.name}</a>`).join(' ');
    h+=`<tr${isNew?` data-id="${r.id}"`:''} class="${bundled?'inb':''} ${picked?'picked':''}"><td class="${isNew?'chkcell':''}">${chk}</td><td class=l>${r.ticker}</td><td class=l>${bnd}</td><td class=l>${t}</td><td class=l>${r.event}</td><td class="l ${sideCls(r.side)}">${r.side||''}</td><td>${fmt(r.price)}</td><td>${fmt(r.qty)}</td><td class=l>${liq}</td></tr>`;
  }
  journal.innerHTML=h+'</table>';
  syncSelAll();
}

const STAT={open:['Открыта','--reduce'],closed:['Закрыта','--mut']};
let _lastBundlesJson='';
function renderBundles(bs){
  BUNDLES=bs;
  const sig=JSON.stringify(bs)+'|'+sel.size;
  if(sig===_lastBundlesJson)return;
  _lastBundlesJson=sig;
  if(!bs.length){bundles.innerHTML='<div class="mut">пока нет связок — отметьте сделки в журнале и создайте</div>';return}
  let h='<table><tr><th class=l>Связка</th><th class=l>Статус</th><th>Сделок</th><th>Общий PnL</th><th class=l>Открытые ноги</th><th class=l>Раздвижка</th><th></th></tr>';
  for(const b of bs){
    const st=b.is_open?'<span class="pill" style="background:var(--buy);color:#0f1115">открыта</span>':'<span class=mut>нет позиции</span>';
    const unit=b.points_only.length?'п.':'₽';
    const bnet=b.total_realized_net!==undefined?b.total_realized_net:b.total_realized;
    const pcls=bnet>0?'pos':(bnet<0?'neg':'mut');
    const legs=b.open_legs.map(l=>`${l.ticker}:<b class="${l.net_qty>0?'buy':'sell'}">${fmt(l.net_qty)}</b>`).join('  ')||'<span class=mut>—</span>';
    let spread='<span class=mut>—</span>';
    if(b.spread){
      if(b.spread.error){spread='<span class=warn>ошибка</span>';}
      else{
        const syn=Object.values(b.spread.bindings||{}).filter(x=>x.synthetic);
        if(syn.length){spread=syn.map(x=>`<b title="синтетическая цена — нет форекс-ноги">${x.formula||'синт.'} = ${fmt(x.price)}</b>`).join('<br>');}
        else{spread=`<b>${fmt(b.spread.value)}%</b>`;}
      }
    }
    h+=`<tr>
      <td class=l><a href="/bundle/${b.id}">#${b.id} ${b.name} ↗</a></td>
      <td class=l>${st}</td><td>${b.num_trades}</td>
      <td class="${pcls}">${fmt(bnet)} ${unit}</td>
      <td class=l>${legs}</td><td class=l>${spread}</td>
      <td class=l><button onclick="addSelTo(${b.id})">+ выбр. (${sel.size})</button>
      <button class=danger onclick="delBundle(${b.id})">✕</button></td></tr>`;
  }
  bundles.innerHTML=h+'</table>';
}

async function createBundle(){
  const name=bname.value.trim();
  if(!name){alert('Укажите название связки');return}
  if(!sel.size){alert('Отметьте хотя бы одну сделку');return}
  await post('/api/bundles',{name,trade_ids:[...sel]});
  bname.value='';sel.clear();selc.textContent=0;tick();
}
async function addSelTo(bid){ if(!sel.size){alert('Ничего не выбрано');return} await post(`/api/bundles/${bid}/members`,{trade_ids:[...sel]}); sel.clear();selc.textContent=0;tick(); }
async function delBundle(bid){ if(confirm('Удалить связку #'+bid+'?')){ await del('/api/bundles/'+bid); tick(); } }

async function tgLoad(){
  try{
    const s=await j('/api/telegram');
    const ch=document.getElementById('tgchat'); if(document.activeElement!==ch)ch.value=s.chat_id||'';
    const st=document.getElementById('tgstatus');
    st.textContent = !s.has_token?'токен не задан':(!s.chat_id?'канал не задан — добавьте бота админом и укажите @канал/-100... id':'готов к ручной публикации (📢 на странице связки)');
    st.className = s.has_token&&s.chat_id?'pos':'mut';
  }catch(e){}
}
async function tgSaveChat(){ await put('/api/telegram',{chat_id:document.getElementById('tgchat').value}); tgLoad(); }
async function tgTest(){ const r=await (await fetch('/api/telegram/test',{method:'POST'})).json(); alert(r.ok?'Отправлено ✓':('Ошибка: '+(r.description||r.error||JSON.stringify(r)))); }
async function put(u,b){return fetch(u,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})}

async function tick(){
  try{
    const [pos,jr,sm,bs]=await Promise.all([j('/api/positions'),j('/api/journal'),j('/api/summary'),j('/api/bundles')]);
    JOURNAL=jr;
    renderPositions(pos);renderBundles(bs);renderJournal();
    const liq=Object.entries(sm.by_liquidity||{}).map(([k,v])=>`${k}:${v}`).join('  ');
    const rt=(sm.realized_today_net!==undefined?sm.realized_today_net:sm.realized_today), rcls=rt>0?'pos':(rt<0?'neg':'mut');
    const comm=sm.commission_today||0;
    const pend=sm.commission_pending||[];
    const pendStr=pend.length?`, занижена — нет биржевого сбора для ${pend.join(', ')}`:'';
    const commStr=comm?` <span class=mut>(−${fmt(comm)} ₽ комиссия${pendStr})</span>`:'';
    const mix=sm.points_mixed&&sm.points_mixed.length?' (часть в пунктах)':'';
    summary.innerHTML=`сделок: ${sm.fills}   ${liq}   ·   за день (net): <b class="${rcls}">${fmt(rt)} ₽</b>${commStr}${mix}`;
    tgLoad();
  }catch(e){summary.textContent='ошибка загрузки';}
  clock.textContent=new Date().toLocaleTimeString('ru-RU');
}
const summary=document.getElementById('summary'),clock=document.getElementById('clock');
const positions=document.getElementById('positions'),journal=document.getElementById('journal'),bundles=document.getElementById('bundles');
const bname=document.getElementById('bname'),selc=document.getElementById('selc');
const fTicker=document.getElementById('fTicker'),fEvent=document.getElementById('fEvent'),fSide=document.getElementById('fSide'),fLiq=document.getElementById('fLiq');
tick();setInterval(tick,3000);
</script>
</body></html>"""


_BUNDLE_PAGE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Связка — CScalp Journal</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script>try{document.documentElement.dataset.theme=localStorage.getItem('theme')||'dark'}catch(e){document.documentElement.dataset.theme='dark'}</script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
 :root{--bg:#0f1115;--fg:#e6e6e6;--mut:#8a90a0;--line:#242833;--buy:#3fb37a;--sell:#e0625f;--card:#161922;--accent:#5f9fe0;
       --open:#3fb37a;--add:#5f9fe0;--reduce:#e0a95f;--close:#e0625f;--btn:#1e2230;--btnp:#22406a;--input:#0f1218;--panel2:#12151d}
 :root[data-theme="light"]{--bg:#f5f6f8;--fg:#1a1d24;--mut:#697086;--line:#e1e4ea;--buy:#1a8a55;--sell:#c8433f;--card:#ffffff;--accent:#2563eb;--btn:#eef0f4;--btnp:#dbe6fb;--input:#ffffff;--panel2:#f0f2f6}
 body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 ui-monospace,Menlo,Consolas,monospace}
 header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
 a{color:var(--accent);text-decoration:none} h1{font-size:15px;margin:0}
 main{padding:16px;display:grid;gap:20px;max-width:1100px}
 .card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px}
 h2{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:0 0 10px}
 .mut{color:var(--mut)} .neg{color:var(--sell)} .pos{color:var(--buy)}
 .big{font-size:22px;font-weight:600}
 .kpis{display:flex;gap:28px;flex-wrap:wrap;align-items:baseline}
 table{border-collapse:collapse;width:100%;font-size:13px} th,td{text-align:right;padding:4px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
 th:first-child,td:first-child,th.l,td.l{text-align:left}
 .buy{color:var(--buy)}.sell{color:var(--sell)}
 .pill{font-size:11px;padding:1px 7px;border-radius:10px;color:#0f1115;font-weight:600}
 .OPEN{background:var(--open)}.ADD{background:var(--add)}.REDUCE{background:var(--reduce)}.CLOSE{background:var(--close)}
 .warn{color:var(--reduce);font-size:12px}
 .tag{font-size:11px;padding:1px 6px;border-radius:4px;border:1px solid var(--line)}
 .taker{color:#e0a95f}.maker{color:#5f9fe0}
 button{background:var(--btn);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px 10px;cursor:pointer;font:inherit}
 button:hover{border-color:var(--accent)} button.primary{background:var(--btnp);border-color:var(--accent)}
 button.danger:hover{border-color:var(--sell);color:var(--sell)}
 input,select{background:var(--input);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px 8px;font:inherit}
 .trade{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-bottom:12px}
 .trade h3{font-size:14px;margin:0 0 8px;display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
 .legform{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
 .legform input{width:92px}.legform input.tk{width:130px}
 .fx{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
 #formula{width:520px;max-width:100%}
 .bind{font-size:12px;color:var(--mut)}
</style></head>
<body>
<header><a href="/">← Журнал</a><h1 id="title">Связка…</h1><button onclick="startRename()" title="переименовать">✎</button><span class="mut" id="sub"></span><button onclick="toggleTheme()" title="Светлая/тёмная тема" style="margin-left:auto;padding:3px 9px">🌓</button></header>
<main>
 <section class="card"><h2>Формула раздвижки (%)</h2>
   <div class="fx">
     <input id="formula" placeholder="RUS:GLDRUB.P/RUS:SI1!*31.1/RUS:GD1!*100000">
     <button class="primary" onclick="saveFormula()">Сохранить</button>
     <span class="mut">символы сопоставляются с ногами по тикеру; значение считается по цене входа сделки</span>
   </div>
   <div class="bind" id="fbind"></div>
 </section>

 <section class="card"><h2>Итог по связке</h2><div class="kpis" id="kpis"></div><div id="warn" class="warn"></div></section>

 <section class="card"><h2>Траектория PnL (все сделки)</h2><canvas id="chart" height="110"></canvas>
   <div class="mut" style="margin-top:8px">
     <span class="pill OPEN">OPEN</span> открытие <span class="pill ADD">ADD</span> добавление
     <span class="pill REDUCE">REDUCE</span> убавление <span class="pill CLOSE">CLOSE</span> закрытие
   </div>
 </section>

 <section class="card"><h2>Арбитражные сделки</h2><div id="trades"></div></section>

 <section class="card"><h2>Ноги связки</h2>
   <div class="legform" style="margin-bottom:10px">
     <span class=mut>внешняя нога:</span>
     <input class=tk id="lt" placeholder="usdcnh"><select id="ls"><option>Buy</option><option>Sell</option></select>
     <input id="lp" placeholder="цена"><input id="lq" placeholder="кол-во">
     <input id="lv" placeholder="площадка" value="forex" style="width:80px">
     <input id="ltm" placeholder="время ЧЧ:ММ:СС" style="width:120px">
     <button onclick="addLeg()">+ добавить</button>
   </div>
   <div id="legs"></div>
 </section>

 <section class="card"><h2>Нераспределённые сделки (fill)</h2>
   <div class="mut" style="margin-bottom:8px">сделки, ещё не закреплённые ни за одной связкой — нажмите + чтобы добавить в эту</div>
   <div id="unassigned"></div>
 </section>
</main>
<script>
function toggleTheme(){const t=document.documentElement.dataset.theme==='light'?'dark':'light';document.documentElement.dataset.theme=t;try{localStorage.setItem('theme',t)}catch(e){}load();}
const fmt=n=>n===null||n===undefined?'':(Math.round(n*1e6)/1e6).toLocaleString('ru-RU');
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const id=parseInt(location.pathname.split('/').pop());
const hhmmss=t=>t?(t.split(' ')[1]||t):'—';
const sideCls=s=>s==='Buy'?'buy':'sell';
async function post(u,b){return fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})}
async function put(u,b){return fetch(u,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})}
async function del(u){return fetch(u,{method:'DELETE'})}
let chart=null;
let LEGS=[], UN=[], COV={uncovered:[]}, legPage=1, unPage=1; const PSZ=20;

function eventsTable(events){
  let h='<table><tr><th class=l>Время</th><th class=l>Действие</th><th class=l>Площадка</th><th class=l>Инструмент</th><th class=l>Сторона</th><th>Цена</th><th>Кол-во</th><th class=l>Ликв.</th><th>Нетто</th><th>PnL ногой</th><th>PnL Σ</th><th></th></tr>';
  for(const e of events){
    const liq=e.liquidity?`<span class="tag ${e.liquidity}">${e.liquidity}</span>`:'';
    const rc=e.realized_leg>0?'pos':(e.realized_leg<0?'neg':'mut');
    const rm=e.kind==='manual'
      ? `<button class=danger title="удалить эту ногу" onclick="delEvent('manual',${e.ref})">✕</button>`
      : `<button class=danger title="убрать эту сделку из связки" onclick="delEvent('internal','${e.ref}')">✕</button>`;
    h+=`<tr><td class=l>${hhmmss(e.ts)}</td><td class=l><span class="pill ${e.action}">${e.action}</span></td><td class=l>${e.venue}</td><td class=l>${e.ticker}</td><td class="l ${sideCls(e.side)}">${e.side}</td><td>${fmt(e.price)}</td><td>${fmt(e.qty)}</td><td class=l>${liq}</td><td>${fmt(e.net_after)}</td><td class="${rc}">${e.realized_leg?fmt(e.realized_leg):''}</td><td>${fmt(e.realized_cum)}</td><td>${rm}</td></tr>`;
  }
  return h+'</table>';
}
async function delEvent(kind,ref){
  if(!confirm(kind==='manual'?'Удалить эту внешнюю ногу из связки?':'Убрать эту сделку из связки?'))return;
  if(kind==='manual')await del('/api/legs/'+ref);
  else await del('/api/bundles/'+id+'/members/'+ref);
  load();
}

let curName='';
async function startRename(){
  const nn=window.prompt('Новое название связки:', curName);
  if(nn&&nn.trim()&&nn.trim()!==curName){ await put('/api/bundles/'+id+'/name',{name:nn.trim()}); load(); }
}
async function load(){
  const t=await (await fetch('/api/bundles/'+id+'/trades')).json();
  curName=t.name;
  document.getElementById('title').textContent=`#${t.id} ${t.name}`;
  document.getElementById('sub').textContent=t.note||'';
  const fEl=document.getElementById('formula');
  if(document.activeElement!==fEl)fEl.value=t.formula||'';
  fEl.style.borderColor=(t.coverage&&t.coverage.has_formula)?'':'var(--sell)';
  const cov=t.coverage||{uncovered:[],has_formula:false};
  const unit=t.points_only.length?'п.':'₽';

  // KPIs
  const net=t.total_realized_net!==undefined?t.total_realized_net:t.total_realized;
  const pcls=net>0?'pos':(net<0?'neg':'');
  let kpis=`<div><div class=mut>Статус</div><div class=big>${t.is_open?'Открыта':'Нет позиции'}</div></div>`+
    `<div><div class=mut>PnL (net)</div><div class="big ${pcls}">${fmt(net)} ${unit}</div></div>`+
    (t.total_commission?`<div><div class=mut>Комиссия</div><div class="big mut">−${fmt(t.total_commission)} ${unit}</div></div>`:'')+
    `<div><div class=mut>Арб. сделок</div><div class=big>${t.num_trades}</div></div>`;
  document.getElementById('kpis').innerHTML=kpis;
  document.getElementById('warn').textContent=t.points_only.length
    ? `PnL в пунктах (не в рублях): не задана стоимость пункта для ${t.points_only.join(', ')} — заполните cscalp_journal/instruments.py`:'';

  // formula bindings from the latest trade's spread
  const lastSp=t.trades.length?t.trades[t.trades.length-1].spread:null;
  const fb=document.getElementById('fbind');
  if(lastSp&&lastSp.error)fb.innerHTML=`<span class=warn>ошибка формулы: ${lastSp.error}</span>`;
  else if(lastSp)fb.innerHTML='сопоставление: '+Object.entries(lastSp.bindings).map(([s,b])=>{
      const tag=b.synthetic?' <b style="color:var(--accent)">синт.</b>':(b.defaulted?' <span class=warn>=1</span>':'');
      return `${s} → ${b.ticker} (${fmt(b.price)})${tag}`;
    }).join(' · ');
  else fb.textContent='';

  // overall chart: cumulative PnL across all trades' events
  const all=[]; let cum=0;
  for(const tr of t.trades)for(const e of tr.events){cum+=e.realized_leg;all.push({...e,cum,tno:tr.idx});}
  if(chart)chart.destroy();
  chart=new Chart(document.getElementById('chart'),{type:'line',
    data:{labels:all.map(e=>hhmmss(e.ts)),datasets:[{data:all.map(e=>e.cum),
      borderColor:css('--accent'),borderWidth:2,tension:0,
      pointBackgroundColor:all.map(e=>css('--'+e.action.toLowerCase())),
      pointBorderColor:all.map(e=>css('--'+e.action.toLowerCase())),pointRadius:5,pointHoverRadius:8,fill:false}]},
    options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>{const e=all[c.dataIndex];
      return `сделка #${e.tno} · ${e.action} ${e.ticker} ${e.side} ${fmt(e.price)}×${fmt(e.qty)} · PnL Σ ${fmt(e.cum)}`;}}}},
      scales:{x:{grid:{color:css('--line')},ticks:{color:css('--mut')}},y:{grid:{color:css('--line')},ticks:{color:css('--mut')}}}}});

  // per-trade breakdown
  let th='';
  for(const tr of [...t.trades].reverse()){
    const st=tr.status==='open'?'<span class="pill" style="background:var(--reduce)">открыта</span>':'<span class="pill" style="background:var(--mut)">закрыта</span>';
    const trnet=tr.realized_net!==undefined?tr.realized_net:tr.realized_pnl;
    const pc=trnet>0?'pos':(trnet<0?'neg':'mut');
    const commNote=tr.commission?` <span class=mut>(−${fmt(tr.commission)} комис.)</span>`:'';
    const sp=tr.spread&&!tr.spread.error?`раздвижка входа: <b>${fmt(tr.spread.value)}%</b>`:'';
    const per=`${hhmmss(tr.entry_ts)} → ${tr.exit_ts&&tr.status==='closed'?hhmmss(tr.exit_ts):'…'}`;
    const pubBtns=`<button onclick="publish(${tr.idx},'open')" title="опубликовать открытие в канал">📢 открытие</button>`+
        (tr.status==='closed'?`<button onclick="publish(${tr.idx},'close')" title="опубликовать закрытие в канал">📢 закрытие</button>`:'');
    th+=`<div class=trade><h3>Сделка #${tr.idx} ${st}<span class=mut>${per}</span>`+
        `<span class="${pc}">PnL ${fmt(trnet)} ${unit}${commNote}</span><span class=mut>${sp}</span>${pubBtns}</h3>`;
    if(tr.open_legs.length)th+=`<div class=mut style="margin-bottom:6px">открыто: `+
        tr.open_legs.map(l=>`${l.ticker} <b class="${l.net_qty>0?'buy':'sell'}">${fmt(l.net_qty)}</b> @ ${fmt(l.avg_price)}`).join(', ')+`</div>`;
    th+=eventsTable(tr.events)+`</div>`;
  }
  document.getElementById('trades').innerHTML=th||'<div class=mut>нет сделок</div>';

  // legs + unassigned fills — both paginated, 20 per page
  LEGS=t.legs; COV=cov; renderLegs();
  const un=await (await fetch('/api/unassigned')).json();
  UN=un; renderUnassigned();
}

function pagerHtml(page,pages,total,navFn){
  return total>PSZ?`<div class="bar" style="margin-top:8px"><button onclick="${navFn}(-1)">←</button><span class=mut>стр ${page}/${pages} · всего ${total}</span><button onclick="${navFn}(1)">→</button></div>`:'';
}
function renderLegs(){
  const rows=LEGS, total=rows.length, pages=Math.max(1,Math.ceil(total/PSZ));
  if(legPage>pages)legPage=pages; if(legPage<1)legPage=1;
  const page=rows.slice((legPage-1)*PSZ,legPage*PSZ);
  let lh='<table><tr><th class=l>Площадка</th><th class=l>Инструмент</th><th class=l>Сторона</th><th>Цена</th><th>Кол-во</th><th class=l>Время</th><th></th></tr>';
  for(const l of page){
    const rm=l.kind==='manual'?`<button class=danger onclick="delLeg(${l.ref})">✕</button>`:`<button class=danger onclick="delMember('${l.ref}')">✕</button>`;
    const bad=(COV.uncovered||[]).includes(l.ticker);
    const rst=bad?' style="background:rgba(224,98,95,0.12)"':'';
    const warn=bad?' <span class=warn title="инструмент не сопоставлен с формулой">⚠ не в формуле</span>':'';
    lh+=`<tr${rst}><td class=l><span class=mut>${l.venue}</span></td><td class=l>${l.ticker}${warn}</td><td class="l ${sideCls(l.side)}">${l.side}</td><td>${fmt(l.price)}</td><td>${fmt(l.qty)}</td><td class=l>${hhmmss(l.ts)}</td><td>${rm}</td></tr>`;
  }
  document.getElementById('legs').innerHTML=lh+'</table>'+pagerHtml(legPage,pages,total,'legNav');
}
function legNav(d){legPage+=d;renderLegs();}
function renderUnassigned(){
  const rows=UN.slice().reverse(), total=rows.length;
  if(!total){document.getElementById('unassigned').innerHTML='<div class=mut>нет нераспределённых сделок</div>';return}
  const pages=Math.max(1,Math.ceil(total/PSZ));
  if(unPage>pages)unPage=pages; if(unPage<1)unPage=1;
  const page=rows.slice((unPage-1)*PSZ,unPage*PSZ);
  let uh='<table><tr><th class=l>Время</th><th class=l>Инструмент</th><th class=l>Сторона</th><th>Цена</th><th>Кол-во</th><th class=l>Ликв.</th><th></th></tr>';
  for(const f of page){
    const liq=f.liquidity?`<span class="tag ${f.liquidity}">${f.liquidity}</span>`:'';
    uh+=`<tr><td class=l>${hhmmss(f.ts)}</td><td class=l>${f.ticker}</td><td class="l ${sideCls(f.side)}">${f.side}</td><td>${fmt(f.price)}</td><td>${fmt(f.qty)}</td><td class=l>${liq}</td><td><button class=primary onclick="addFill('${f.trade_id}')">+</button></td></tr>`;
  }
  document.getElementById('unassigned').innerHTML=uh+'</table>'+pagerHtml(unPage,pages,total,'unNav');
}
function unNav(d){unPage+=d;renderUnassigned();}
async function addFill(tid){ await post('/api/bundles/'+id+'/members',{trade_ids:[tid]}); load(); }

async function saveFormula(){ await put('/api/bundles/'+id+'/formula',{formula:document.getElementById('formula').value}); load(); }
async function addLeg(){
  const ticker=document.getElementById('lt').value.trim();
  const price=parseFloat(document.getElementById('lp').value.replace(',','.'));
  const qty=parseFloat(document.getElementById('lq').value.replace(',','.'));
  const side=document.getElementById('ls').value, venue=document.getElementById('lv').value.trim()||'forex';
  const tm=document.getElementById('ltm').value.trim();
  if(!ticker||isNaN(price)||isNaN(qty)){alert('Заполните инструмент, цену и количество');return}
  const ts=tm?`${new Date().toISOString().slice(0,10)} ${tm}`:null;
  await post('/api/bundles/'+id+'/legs',{ticker,side,price,qty,venue,ts});
  document.getElementById('lt').value='';document.getElementById('lp').value='';document.getElementById('lq').value='';document.getElementById('ltm').value='';
  load();
}
async function delLeg(lid){ await del('/api/legs/'+lid); load(); }
async function delMember(tid){ await del('/api/bundles/'+id+'/members/'+tid); load(); }
async function publish(idx,ev){
  const r=await (await post('/api/bundles/'+id+'/publish',{trade_idx:idx,event:ev})).json();
  alert(r.ok?'Опубликовано в канал ✓':('Ошибка: '+(r.description||r.error||JSON.stringify(r))));
}
load();setInterval(load,5000);
</script>
</body></html>"""


_PAGE = with_photo(_PAGE)
_BUNDLE_PAGE = with_photo(_BUNDLE_PAGE)
REPORT_PAGE = with_photo(REPORT_PAGE)

_BG_BYTES = BG_PATH.read_bytes() if BG_PATH.exists() else None


@app.get("/static/bg.jpg")
def static_bg():
    if _BG_BYTES is None:
        raise HTTPException(404, "no background image")
    return Response(_BG_BYTES, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


def main() -> None:
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT, log_level="warning")


if __name__ == "__main__":
    main()
