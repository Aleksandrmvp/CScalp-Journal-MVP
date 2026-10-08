"""HTML for the monthly report page (/report). Data from /api/report."""

REPORT_PAGE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Итоги месяца — CScalp Journal</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script>try{document.documentElement.dataset.theme=localStorage.getItem('theme')||'dark'}catch(e){document.documentElement.dataset.theme='dark'}</script>
<style>
 :root{--bg:#0f1115;--fg:#e6e6e6;--mut:#8a90a0;--line:#242833;--buy:#3fb37a;--sell:#e0625f;--card:#161922;--accent:#5f9fe0;--btn:#1e2230;--input:#0f1218;--grid:#2a2f3c}
 :root[data-theme="light"]{--bg:#f5f6f8;--fg:#1a1d24;--mut:#697086;--line:#e1e4ea;--buy:#1a8a55;--sell:#c8433f;--card:#ffffff;--accent:#2563eb;--btn:#eef0f4;--input:#ffffff;--grid:#dde1e8}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 ui-monospace,Menlo,Consolas,monospace}
 header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;gap:14px;align-items:center;flex-wrap:wrap}
 h1{font-size:15px;margin:0;font-weight:600}
 a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
 .mut{color:var(--mut)} .pos{color:var(--buy)} .neg{color:var(--sell)}
 button,select,input{background:var(--btn);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px 10px;font:inherit}
 button{cursor:pointer} button:hover{border-color:var(--accent)}
 input{background:var(--input);width:150px}
 main{max-width:980px;width:100%;margin:0 auto;padding:18px 16px 40px;display:grid;gap:14px}
 .title{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap}
 .title h2{margin:0;font-size:30px;letter-spacing:-.01em}
 .badge{border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:12px;color:var(--mut)}
 .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
 .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--mut);margin-bottom:8px}
 .hero{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
 .big{font-size:52px;font-weight:700;line-height:1.05;letter-spacing:-.02em}
 .mid{font-size:34px;font-weight:700;line-height:1.1}
 .sm{font-size:12px;color:var(--mut);margin-top:8px}
 .trio{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
 .trio.cap4{grid-template-columns:repeat(4,minmax(0,1fr))}
 .trio .val{font-size:22px;font-weight:700;white-space:nowrap}
 .bar{height:5px;border-radius:3px;background:var(--line);margin-top:12px;overflow:hidden}
 .bar>i{display:block;height:100%}
 .tiles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
 .tiles .val{font-size:26px;font-weight:700}
 svg{width:100%;height:auto;display:block}
 .tbl{width:100%;border-collapse:collapse;font-size:12px;margin-top:10px}
 .tbl td,.tbl th{padding:4px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
 .tbl .r{text-align:right}
 textarea{width:100%;background:var(--input);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:8px;font:inherit}
 .formwrap{max-width:980px;margin:0 auto;padding:0 16px 40px}
 summary{cursor:pointer}
 .hero .card,.trio .card,.tiles .card{position:relative;overflow:hidden;transform-style:preserve-3d;
   transition:transform .25s ease,box-shadow .25s ease,border-color .25s ease;will-change:transform}
 .card.tilt{transform:perspective(900px) rotateX(var(--rx,0deg)) rotateY(var(--ry,0deg))}
 .card.tilt:hover{box-shadow:0 18px 38px rgba(0,0,0,.34),0 2px 0 rgba(255,255,255,.05) inset;border-color:var(--accent)}
 .card .glare{position:absolute;inset:0;border-radius:inherit;pointer-events:none;opacity:0;transition:opacity .25s;
   background:radial-gradient(circle at var(--gx,50%) var(--gy,0%),rgba(255,255,255,.16),transparent 55%)}
 .card.tilt:hover .glare{opacity:1}
 @keyframes cardIn{from{opacity:0;transform:perspective(900px) translateY(20px) rotateX(12deg)}to{opacity:1;transform:perspective(900px) translateY(0) rotateX(0)}}
 .card.enter{animation:cardIn .65s cubic-bezier(.2,.7,.2,1) backwards;animation-delay:calc(var(--i,0)*70ms)}
 @media (prefers-reduced-motion:reduce){.card.enter{animation:none}.card.tilt{transform:none!important;transition:none}}
 .mini{padding:2px 8px;font-size:11px;white-space:nowrap}
 .foot{font-size:11px;color:var(--mut);line-height:1.6}
 .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
 .warn{color:#e0a95f}
 @media(max-width:720px){.hero,.trio{grid-template-columns:minmax(0,1fr)}.trio.cap4{grid-template-columns:repeat(2,minmax(0,1fr))}.trio .val{white-space:normal}.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}.big{font-size:40px}}
</style></head>
<body>
<header>
 <a href="/">← Дневник</a>
 <h1>Итоги месяца</h1>
 <select id="month" onchange="load(this.value)"></select>
 <span class="row" style="margin-left:auto">
  <label class="mut">Старт, ₽ <input id="capStart" placeholder="не задан" inputmode="decimal"></label>
  <label class="mut">Сейчас, ₽ <input id="capNow" placeholder="= активы терминала" inputmode="decimal"></label>
  <label class="mut">Пик, ₽ <input id="capPeak" placeholder="не задан" inputmode="decimal"></label>
  <input id="capPeakMonth" type="month" title="Месяц пика" style="width:140px">
  <button onclick="saveCap()">Сохранить</button>
  <button onclick="toggleTheme()" title="Светлая/тёмная тема" style="padding:3px 9px">🌓</button>
 </span>
</header>
<main id="root"><div class="mut">загрузка…</div></main>
<div class="formwrap" style="padding-bottom:14px"><div class="card"><div class="lbl">cTrader (FxPro): форекс-результаты через Open API</div>
 <div id="ctStatus" class="sm">…</div>
 <div class="row" style="margin-top:8px"><a href="/ctrader/connect"><button>Подключить cTrader</button></a><button onclick="ctSync()">Синхронизировать сейчас</button></div>
</div></div>
<div class="formwrap" style="padding-bottom:14px"><details class="card"><summary class="lbl" style="margin:0">Добавить форекс-результат</summary>
 <p class="sm">Результат форекс-ноги за день в USDT (убыток со знаком минус). Рубли считаются по курсу ЦБ на эту дату. Чтобы зафиксировать рублёвую сумму (например, чтобы нога точно совпала с рублёвой), заполните «Сумма в ₽».</p>
 <div class="row"><input id="fxDay" type="date" title="Дата результата"><input id="fxUsd" placeholder="USDT, например -2100" inputmode="decimal"><input id="fxRub" placeholder="сумма в ₽ (по желанию)" inputmode="decimal"><input id="fxNote" placeholder="комментарий" style="width:200px"><button onclick="fxAdd()">Добавить</button><span id="fxMsg" class="mut"></span></div>
</details></div>
<div class="formwrap"><details class="card"><summary class="lbl" style="margin:0">Добавить движения по счёту</summary>
 <p class="sm">Вставьте строки из выписки брокера: дата проводки, сумма, описание (фандинг, комиссия за перенос, списание убытка). Повторы пропускаются. Строка со словом «Корректировка» учитывается только в статистике и в капитал не входит. Строки «Перевод средств» считаются переводами между счетами и в прибыль не входят. Любую запись можно исключить из статистики или удалить в таблице «Движения по счёту» выше.</p>
 <textarea id="cashText" rows="6" placeholder="2026-10-06  1 495 ₽  Зачисление фандинга за 05.10.2026"></textarea>
 <div class="row" style="margin-top:8px"><button onclick="saveCash()">Добавить</button><span id="cashMsg" class="mut"></span></div>
</details></div>
<script>
function toggleTheme(){const t=document.documentElement.dataset.theme==='light'?'dark':'light';document.documentElement.dataset.theme=t;try{localStorage.setItem('theme',t)}catch(e){}}
const MONTHS=['январь','февраль','март','апрель','май','июнь','июль','август','сентябрь','октябрь','ноябрь','декабрь'];
const num=(n,d=0)=>n==null?'—':n.toLocaleString('ru-RU',{minimumFractionDigits:d,maximumFractionDigits:d});
const sgn=(n,d=0)=>n==null?'—':(n>0?'+':(n<0?'−':''))+num(Math.abs(n),d);
const cls=n=>n>0?'pos':(n<0?'neg':'');
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dmy=d=>d.slice(8,10)+'.'+d.slice(5,7);
const mlabel=m=>MONTHS[+m.slice(5,7)-1]+' '+m.slice(0,4);
let cur=null, timer=null;

function niceTicks(lo,hi,n){
  const span=(hi-lo)||1, raw=span/n, p=Math.pow(10,Math.floor(Math.log10(raw))), m=raw/p;
  const step=(m<=1?1:m<=2?2:m<=5?5:10)*p, t=[];
  for(let v=Math.ceil(lo/step-1e-9)*step; v<=hi+1e-9; v+=step) t.push(+v.toFixed(10));
  return {t,step};
}

function chart(curve,spillDays){
  const W=860,H=300,L=52,R=60,T=22,B=30;
  const rets=curve.map(p=>p.ret_pct);
  let lo=Math.min(0,...rets), hi=Math.max(0,...rets);
  const pad=(hi-lo)*0.14||0.5; lo-=pad; hi+=pad;
  const {t:ticks,step}=niceTicks(lo,hi,4);
  const ts=curve.map(p=>Date.parse(p.day)), t0=ts[0], t1=ts[ts.length-1]||t0+1;
  const X=i=>L+(t1===t0?0:(ts[i]-t0)/(t1-t0))*(W-L-R);
  const Y=v=>T+(hi-v)/(hi-lo)*(H-T-B);
  const last=rets[rets.length-1], col=last>=0?'var(--buy)':'var(--sell)';
  const pts=curve.map((p,i)=>[X(i),Y(p.ret_pct)]);
  const line=pts.map((q,i)=>(i?'L':'M')+q[0].toFixed(1)+' '+q[1].toFixed(1)).join(' ');
  const y0=Y(0);
  const area=line+` L${pts[pts.length-1][0].toFixed(1)} ${y0.toFixed(1)} L${pts[0][0].toFixed(1)} ${y0.toFixed(1)} Z`;
  let s=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Накопленная доходность">
   <defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity=".35"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>`;
  for(const v of ticks){
    const y=Y(v);
    s+=`<line x1="${L}" x2="${W-R}" y1="${y}" y2="${y}" stroke="var(--grid)" ${v===0?'':'stroke-dasharray="3 4"'}/>
        <text x="${L-8}" y="${y+4}" text-anchor="end" font-size="11" fill="var(--mut)">${num(v,step<1?1:0)}%</text>`;
  }
  const lx=curve.length<=8?curve.map((_,i)=>i):[0,Math.floor(curve.length/3),Math.floor(curve.length*2/3),curve.length-1];
  for(const i of lx){
    s+=`<text x="${X(i)}" y="${H-8}" text-anchor="${i===0?'start':(i===curve.length-1?'end':'middle')}" font-size="11" fill="var(--mut)">${dmy(curve[i].day)}</text>`;
  }
  s+=`<path d="${area}" fill="url(#g)"/><path d="${line}" fill="none" stroke="${col}" stroke-width="2.5" stroke-linejoin="round"/>`;
  curve.forEach((p,i)=>{
    s+=`<circle cx="${pts[i][0]}" cy="${pts[i][1]}" r="${i===curve.length-1?5:3}" fill="${col}"><title>${dmy(p.day)}: ${sgn(p.ret_pct,2)}% · ${sgn(p.pnl)} ₽ · активы ${num(p.equity)} ₽</title></circle>`;
  });
  curve.forEach((p,i)=>{ if(spillDays&&spillDays.has(p.day))
    s+=`<text x="${pts[i][0]}" y="${Math.max(12,pts[i][1]-12)}" text-anchor="middle" font-size="11" font-weight="700" fill="#e0a95f">ПЕРЕЛИВ</text>`; });
  const ex=pts[pts.length-1];
  s+=`<text x="${Math.min(ex[0],W-4)}" y="${ex[1]-12}" text-anchor="end" font-size="13" font-weight="700" fill="${col}">${sgn(last,2)}%</text></svg>`;
  return s;
}

const ths=v=>Math.abs(v)>=1000?num(v/1000,v%1000?1:0)+' тыс':num(v);
function capChart(c){
  const S=c.series, W=860,H=260,L=64,R=24,T=18,B=30;
  const caps=S.map(p=>p.capital);
  const ref=c.reference;
  let lo=Math.min(...caps,ref), hi=Math.max(...caps,ref,c.peak_manual||0);
  const pad=(hi-lo)*0.12||hi*0.02||1; lo-=pad; hi+=pad;
  const {t:ticks}=niceTicks(lo,hi,4);
  const ts=S.map(p=>Date.parse(p.day)), t0=ts[0], t1=ts[ts.length-1];
  const X=i=>L+(t1===t0?(W-L-R)/2:(ts[i]-t0)/(t1-t0)*(W-L-R));
  const Y=v=>T+(hi-v)/(hi-lo)*(H-T-B);
  const P=(i,arr)=>X(i).toFixed(1)+' '+Y(arr[i]).toFixed(1);
  const line=caps.map((_,i)=>(i?'L':'M')+P(i,caps)).join(' ');
  const under=caps.map(v=>Math.min(v,ref)), refs=caps.map(()=>ref);
  const dd=refs.map((_,i)=>(i?'L':'M')+P(i,refs)).join(' ')+' '+under.map((_,i)=>'L'+P(under.length-1-i,under)).join(' ')+' Z';
  let s=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Капитал и просадка">`;
  for(const v of ticks){const y=Y(v);
    s+=`<line x1="${L}" x2="${W-R}" y1="${y}" y2="${y}" stroke="var(--grid)" stroke-dasharray="3 4"/><text x="${L-8}" y="${y+4}" text-anchor="end" font-size="11" fill="var(--mut)">${ths(v)}</text>`;}
  {const y=Y(ref);
    s+=`<line x1="${L}" x2="${W-R}" y1="${y}" y2="${y}" stroke="var(--accent)" stroke-width="1.5" stroke-dasharray="6 4"/><text x="${W-R}" y="${y+14}" text-anchor="end" font-size="11" fill="var(--accent)">${c.start?'старт':'пик'} ${num(ref)} ₽ — от него считается просадка</text>`;}
  if(c.peak_manual){const y=Y(c.peak_manual);
    s+=`<line x1="${L}" x2="${W-R}" y1="${y}" y2="${y}" stroke="#e0a95f" stroke-width="1.5" stroke-dasharray="6 4"/><text x="${W-R}" y="${y-6}" text-anchor="end" font-size="11" fill="#e0a95f">пик ${num(c.peak_manual)} ₽${c.peak_month?' · '+mlabel(c.peak_month):''}</text>`;}
  const lx=S.length<=8?S.map((_,i)=>i):[0,Math.floor(S.length/3),Math.floor(S.length*2/3),S.length-1];
  for(const i of lx)s+=`<text x="${X(i)}" y="${H-8}" text-anchor="${i===0?'start':(i===S.length-1?'end':'middle')}" font-size="11" fill="var(--mut)">${dmy(S[i].day)}</text>`;
  s+=`<path d="${dd}" fill="var(--sell)" fill-opacity=".18"/>
      <path d="${line}" fill="none" stroke="var(--fg)" stroke-width="2.5" stroke-linejoin="round"/>`;
  const sp=new Set((c.spills||[]).map(x=>x.day));
  S.forEach((p,i)=>{ if(sp.has(p.day)) s+=`<text x="${X(i)}" y="${Math.max(12,Y(p.capital)-9)}" text-anchor="middle" font-size="11" font-weight="700" fill="#e0a95f">ПЕРЕЛИВ</text>`; });
  S.forEach((p,i)=>{s+=`<circle cx="${X(i)}" cy="${Y(p.capital)}" r="${i===S.length-1?5:3}" fill="var(--fg)"><title>${dmy(p.day)}: ${num(p.capital)} ₽ · просадка ${num(p.dd_pct,2)}%</title></circle>`;});
  return s+'</svg>';
}

function capSection(c){
  if(!c)return '';
  const sc=c.scale!==1?`<br>активы терминала ${num(c.anchor.terminal_equity)} ₽ × ${num(c.scale,4)} (плечо проп-дилинга)`:'';
  const noStart=c.start==null;
  return `<div class="trio cap4">
   <div class="card"><div class="lbl">Стартовый капитал</div><div class="val">${noStart?'—':num(c.start)+' ₽'}</div><div class="sm">${noStart?'задайте «Старт» вверху':'от него считается просадка'}${c.peak_manual?`<br>пик ${num(c.peak_manual)} ₽${c.peak_month?' · '+mlabel(c.peak_month):''}`:''}</div></div>
   <div class="card"><div class="lbl">Текущий капитал</div><div class="val">${num(c.current)} ₽</div><div class="sm">${noStart?'':`<span class="${cls(c.pnl_since_start)}">${sgn(c.pnl_since_start)} ₽ · ${sgn(c.return_since_start_pct,2)}%</span> от старта<br>`}на ${dmy(c.last_day)}${sc}</div></div>
   <div class="card"><div class="lbl">Текущая просадка</div><div class="val ${c.drawdown_pct>0?'neg':'pos'}">${c.drawdown_pct>0?'−':''}${num(c.drawdown_pct,2)}%</div><div class="sm">${c.drawdown_pct>0?`−${num(c.drawdown_rub)} ₽ от ${c.start?'старта':'пика'} ${num(c.reference)} ₽`:'капитал не ниже '+(c.start?'старта':'пика')}</div></div>
   <div class="card"><div class="lbl">Макс. просадка</div><div class="val ${c.max_drawdown_pct>0?'neg':''}">${c.max_drawdown_pct>0?'−':''}${num(c.max_drawdown_pct,2)}%</div><div class="sm">${c.max_drawdown_day?'на '+dmy(c.max_drawdown_day):'просадок нет'}</div></div>
  </div>
  <div class="card"><div class="lbl">Капитал и просадка с начала учёта</div>${capChart(c)}<div class="sm">сплошная — капитал, синий пунктир — стартовый капитал (от него считается просадка), оранжевый — пик (справочно), красная заливка — просадка ниже старта. Метка «ПЕРЕЛИВ» — день, когда прибыль рублёвой ноги компенсирована убытком форекс-ноги. Отсчёт с ${dmy(c.first_day)}: до 24.09 по выписке брокера, дальше по активам терминала.${c.flows&&(c.flows.withdrawals||c.flows.deposits)?`<br>Переводы с ${dmy(c.flows.since)}: ${c.flows.deposits?'пополнения '+sgn(c.flows.deposits)+' ₽, ':''}выводы ${sgn(c.flows.withdrawals)} ₽ — они входят в просадку. Результат фандинга, комиссий и торговли за это время: ${sgn(c.flows.performance)} ₽.`:''}</div></div>`;
}

let EDITABLE=true;
const STAT_CARDS='#root .hero .card,#root .trio .card,#root .tiles .card';
const REDUCED=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
function countUp(el){
  const m=el.textContent.trim().match(/^([+−-]?)(\d(?:[\d\s\u00a0]*\d)?)(?:,(\d+))?(\s*(?:₽|%|USDT|п\.).*)?$/);
  if(!m)return;
  const dec=m[3]?m[3].length:0, tail=m[4]||'';
  const target=parseFloat(m[2].replace(/[\s\u00a0]/g,'')+(m[3]?'.'+m[3]:''));
  if(!isFinite(target)||target===0)return;
  const fin=el.textContent, t0=performance.now(), dur=900;
  setTimeout(()=>{el.textContent=fin},dur+400);   // safety net if animation frames are throttled
  const fmtv=v=>m[1]+v.toLocaleString('ru-RU',{minimumFractionDigits:dec,maximumFractionDigits:dec})+tail;
  (function step(now){
    const k=Math.min(1,(now-t0)/dur), e=1-Math.pow(1-k,3);
    el.textContent=k<1?fmtv(target*e):fin;
    if(k<1)requestAnimationFrame(step);
  })(t0);
}
function fx3d(first){
  document.querySelectorAll(STAT_CARDS).forEach((c,i)=>{
    c.classList.add('tilt');
    if(!c.querySelector('.glare')){const g=document.createElement('div');g.className='glare';c.appendChild(g);}
    if(first&&!REDUCED&&document.visibilityState==='visible'){
      c.style.setProperty('--i',i);c.classList.add('enter');
      c.querySelectorAll('.big,.mid,.val').forEach(countUp);
    }
    if(REDUCED)return;
    c.addEventListener('mousemove',ev=>{
      const r=c.getBoundingClientRect(), x=(ev.clientX-r.left)/r.width, y=(ev.clientY-r.top)/r.height;
      c.style.setProperty('--ry',((x-.5)*12).toFixed(2)+'deg');
      c.style.setProperty('--rx',((.5-y)*10).toFixed(2)+'deg');
      c.style.setProperty('--gx',(x*100).toFixed(0)+'%');c.style.setProperty('--gy',(y*100).toFixed(0)+'%');
    });
    c.addEventListener('mouseleave',()=>{c.style.setProperty('--rx','0deg');c.style.setProperty('--ry','0deg');});
  });
}
let _fxMonth=null;
function render(d){
  const root=document.getElementById('root');
  const sel=document.getElementById('month');
  sel.innerHTML=(d.months.length?d.months:[d.month]).map(m=>`<option value="${m}"${m===d.month?' selected':''}>${mlabel(m)}</option>`).join('');
  const cs=d.capital, a=document.getElementById('capStart'), n=document.getElementById('capNow');
  if(document.activeElement!==a)a.value=cs&&cs.start?cs.start:'';
  if(document.activeElement!==n)n.value=cs&&cs.anchor.capital_now?cs.anchor.capital_now:'';
  const pe=document.getElementById('capPeak'), pm=document.getElementById('capPeakMonth');
  if(document.activeElement!==pe)pe.value=cs&&cs.peak_manual?cs.peak_manual:'';
  if(document.activeElement!==pm)pm.value=cs&&cs.peak_month?cs.peak_month:'';
  const capHtml=capSection(cs);
  if(d.empty){root.innerHTML=capHtml+`<div class="title"><h2>Итоги: ${mlabel(d.month)}</h2></div><div class="card mut">Нет данных об активах терминала за этот месяц.</div>`;return}
  const t=d.trading, c=d.curve;
  const partial=d.source==='ledger'?' · по выписке брокера':(d.period.from_previous_month?'':` · данные с ${dmy(d.period.start)}`);
  const cs2=d.cash, KIND={funding:'фандинг',transfer_fee:'перенос',payout:'PayOut',profit:'прибыль',loss:'убыток',adjust:'корректировка',transfer:'перевод',excluded:'не учитывается',other:'прочее'};
  const realizedCard=`<div class="card"><div class="lbl">Реализовано по сделкам</div><div class="val ${cls(t.realized_net)}">${sgn(t.realized_net)} ₽</div><div class="sm">справочно, MOEX-нога<br>${t.fills} исполнений · ${num(t.lots)} лот · maker ${t.maker_share==null?'—':num(t.maker_share,1)+'%'}</div></div>`;
  const fx=d.fx;
  const forexCard=fx?`<div class="card"><div class="lbl">Форекс-ноги</div><div class="val ${cls(fx.net_rub)}">${sgn(fx.net_rub)} ₽</div><div class="sm">${sgn(fx.net_usd)} USDT ${fx.entries.every(e=>e.fixed)?'по курсу '+num(fx.entries[0].rate,2)+' ₽ (сумма зафиксирована)':'по курсу ЦБ на дату результата'}${fx.unpriced?`<br><span class="warn">курс не найден для ${fx.unpriced} записей</span>`:''}</div></div>`:(d.forex?`<div class="card"><div class="lbl">Форекс-ноги (вручную)</div><div class="val ${cls(d.forex.realized_points)}">${sgn(d.forex.realized_points,2)} п.</div><div class="sm">${d.forex.legs} исполнений внешних ног<br>в пунктах — в ₽ не пересчитывается</div></div>`:'');
  const fxLine=fx?`<div class="sm" style="margin-top:-4px">Итого MOEX и форекс-ноги за ${mlabel(d.month)}: <b class="${cls(d.net_profit+fx.net_rub)}">${sgn(d.net_profit+fx.net_rub)} ₽</b> — MOEX ${sgn(d.net_profit)} ₽, форекс ${sgn(fx.net_rub)} ₽${fx.entries[0]&&fx.entries[0].rate?` (${sgn(fx.net_usd)} USDT × курс ${num(fx.entries[0].rate,2)} на ${dmy(fx.entries[0].day)}${fx.entries.every(e=>e.fixed)?', зафиксирован':', курс ЦБ'})`:''}.</div>`:'';
  const cashHtml=(cs2?'':`<div class="card mut">Нет данных по фандингу и комиссиям — вставьте движения по счёту в форме внизу страницы.</div>`)+`<div class="trio cap4">`+(cs2?`
   <div class="card"><div class="lbl">Фандинг</div><div class="val ${cls(cs2.funding)}">${sgn(cs2.funding)} ₽</div><div class="sm">${cs2.counts.funding} проводок${cs2.funding_avg!=null?`<br>в среднем ${sgn(cs2.funding_avg)} ₽ в день (${num(cs2.funding_avg_pct,2)}% капитала)`:''}</div></div>
   <div class="card"><div class="lbl">Комиссии</div><div class="val ${cls(cs2.fees)}">${sgn(cs2.fees)} ₽</div><div class="sm">${cs2.funding>0?`съедают ${num(-cs2.fees/cs2.funding*100,1)}% фандинга`:'за период'}</div></div>
   <div class="card"><div class="lbl">Торговый результат</div><div class="val ${cls(cs2.trading)}">${sgn(cs2.trading)} ₽</div><div class="sm">зачисления прибыли минус списания убытка по счёту</div></div>`:'')+(d.source==='ledger'?'':realizedCard)+forexCard+`</div>`+(cs2?`
  <div class="sm" style="margin-top:-4px">Чистый результат по движениям за ${mlabel(d.month)}: <b class="${cls(cs2.net)}">${sgn(cs2.net)} ₽</b> — по дате проводки. За всё время с ${dmy(cs2.all_time.first_day)}: ${sgn(cs2.all_time.net)} ₽.${cs2.transfer?` Переводы между счетами за месяц: ${sgn(cs2.transfer)} ₽ — в доходность не входят.`:''}</div>`:'');
  const cashTable=cs2&&cs2.entries.length?`<details class="card"><summary class="lbl" style="margin:0">Движения по счёту за ${mlabel(d.month)}: ${cs2.entries.length} записей</summary><div style="overflow-x:auto"><table class="tbl"><tr><th>Проводка</th><th class="r">Сумма, ₽</th><th>Тип</th><th>За день</th><th>Описание</th>${EDITABLE?'<th></th>':''}</tr>${cs2.entries.map(e=>`<tr${e.kind==='excluded'?' style="opacity:.55"':''}><td>${dmy(e.day)}</td><td class="r ${cls(e.amount)}">${sgn(e.amount)}</td><td>${KIND[e.kind]||e.kind}</td><td>${e.ref_day?dmy(e.ref_day):''}</td><td>${esc(e.description)}</td>${EDITABLE?`<td class="r"><button class="mini" onclick="cashAct(${e.id},'toggle')">${e.kind==='excluded'?'вернуть':'не учитывать'}</button> <button class="mini" onclick="cashAct(${e.id},'delete')">удалить</button></td>`:''}</tr>`).join('')}</table></div></details>`:'';
  const fxTable=fx&&fx.entries.length?`<details class="card"><summary class="lbl" style="margin:0">Форекс-результаты за ${mlabel(d.month)}: ${fx.entries.length} записей</summary><div style="overflow-x:auto"><table class="tbl"><tr><th>Дата</th><th class="r">USDT</th><th class="r">Курс</th><th class="r">₽</th><th>Источник</th><th>Комментарий</th>${EDITABLE?'<th></th>':''}</tr>${fx.entries.map(e=>`<tr><td>${dmy(e.day)}</td><td class="r ${cls(e.usd)}">${sgn(e.usd,2)}</td><td class="r">${e.rate?num(e.rate,2)+(e.fixed?' (зафикс.)':''):'—'}</td><td class="r ${cls(e.rub)}">${e.rub==null?'—':sgn(e.rub)}</td><td>${e.source==='manual'?'вручную':'cTrader'}</td><td>${esc(e.note||'')}</td>${EDITABLE?`<td class="r">${e.source==='manual'?`<button class="mini" onclick="fxDel('${esc(e.id)}')">удалить</button>`:''}</td>`:''}</tr>`).join('')}</table></div></details>`:'';
  let h=capHtml+`<div class="title"><h2>Итоги: ${mlabel(d.month)}</h2><span class="badge">${dmy(d.period.start)} — ${dmy(d.period.end)}.${d.period.end.slice(0,4)}${partial}</span></div>
  <div class="hero">
   <div class="card"><div class="big ${cls(d.return_pct)}">${sgn(d.return_pct,2)}%</div><div class="sm">доходность на капитал за период<br>капитал на начало ${d.source==='ledger'?'месяца':'периода'}: ${num(d.month_base)} ₽</div></div>
   <div class="card"><div class="lbl">Прибыль за период</div><div class="mid ${cls(d.net_profit)}">${sgn(d.net_profit)} ₽</div><div class="sm">капитал: ${num(d.equity.start)} → ${num(d.equity.end)} ₽${cs2&&cs2.transfer?' (с учётом переводов)':''}<br>${d.source==='ledger'?'фандинг, комиссии, прибыль и убыток':'фандинг за вычетом комиссий и убытков'}</div></div>
  </div>
  ${cashHtml}
  ${fxLine}
  ${fxTable}
  ${cashTable}
  <div class="card"><div class="lbl">Накопленная доходность за период, %</div>${chart(c,new Set((d.spills||[]).map(x=>x.day)))}</div>
  <div class="tiles">
   <div class="card"><div class="val ${d.win_rate==null?'':(d.win_rate>=50?'pos':'neg')}">${d.win_rate==null?'—':num(d.win_rate,1)+'%'}</div><div class="sm">прибыльных дней<br>${d.win_days} из ${d.days}</div></div>
   <div class="card"><div class="val">${d.profit_factor==null?(d.gross_profit>0?'∞':'—'):num(d.profit_factor,2)}</div><div class="sm">profit factor<br>${d.profit_factor==null&&d.gross_profit>0?'убыточных дней нет':'прибыль / убыток по дням'}</div></div>
   <div class="card"><div class="val ${d.max_drawdown_pct>0?'neg':''}">${num(d.max_drawdown_pct,2)}%</div><div class="sm">максимальная просадка<br>${d.max_drawdown_day?'на '+dmy(d.max_drawdown_day):'просадок нет'}</div></div>
   <div class="card"><div class="val ${cls(d.best_day&&d.best_day.pnl)}">${d.best_day?sgn(d.best_day.pnl)+' ₽':'—'}</div><div class="sm">лучший день${d.best_day?' · '+dmy(d.best_day.day):''}${d.best_day&&(d.spills||[]).some(x=>x.day===d.best_day.day)?' · перелив':''}${d.best_cycle?`<br>лучшая арб-сделка: ${esc(d.best_cycle.bundle)} ${sgn(d.best_cycle.net)} ${d.best_cycle.points_only.length?'п.':'₽'}`:''}</div></div>
  </div>
  <div class="foot">${d.source==='ledger'?'За этот период терминал не писал логи, поэтому результат собран из выписки брокера: фандинг, комиссии (перенос и доля проп-фирмы PayOut), прибыль и убыток по счёту. Переводы между счетами в доходность не входят; процент считается от капитала на начало месяца. Капитал по дням восстановлен от текущего значения.':`Капитал, доходность, просадка, win rate и profit factor считаются по дневным снимкам «активов» терминала. Это лимит с плечом ×10: реальный капитал равен активам ÷ 10, коэффициент подбирается по вашему «Сейчас». Изменение капитала складывается из фандинга, комиссий за перенос и списаний убытка из выписки брокера; чтобы цифры были полными, загружайте новые строки выписки. «Реализовано по сделкам» дано справочно: оно восстановлено из исполнений MOEX-ноги, парная нога на форексе в CScalp не видна. Пополнения и выводы со счёта отчёт не отделяет.`}${t.points_mixed.length?`<br><span class="warn">Нет стоимости шага для: ${t.points_mixed.map(esc).join(', ')} — их PnL в пунктах, а не в рублях.</span>`:''} Прошлые результаты не гарантируют будущих.</div>`;
  root.innerHTML=h;
  const firstShow=_fxMonth!==d.month; _fxMonth=d.month;
  fx3d(firstShow);
}

async function load(m){
  cur=m||cur;
  try{
    const r=await fetch('/api/report'+(cur?'?month='+encodeURIComponent(cur):''));
    const d=await r.json();
    if(!r.ok){document.getElementById('root').innerHTML='<div class="card neg">Ошибка: '+esc(d.detail||r.status)+'</div>';return}
    cur=d.month; render(d);
  }catch(e){document.getElementById('root').innerHTML='<div class="card neg">Не удалось загрузить: '+esc(e)+'</div>'}
}
async function saveCash(){
  const ta=document.getElementById('cashText'), msg=document.getElementById('cashMsg');
  if(!ta.value.trim()){msg.textContent='Вставьте строки выписки';return}
  const r=await fetch('/api/report/cash',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:ta.value})});
  if(!r.ok){msg.textContent='Ошибка '+r.status;return}
  const d=await r.json();
  msg.textContent=`Добавлено: ${d.added}, повторов: ${d.duplicates}`+(d.unparsed.length?`, не распознано: ${d.unparsed.length}`:'');
  if(d.added)ta.value='';
  await load();
}
async function cashAct(id,act){
  if(act==='delete'&&!confirm('Удалить запись из дневника?'))return;
  const r=await fetch(act==='delete'?'/api/report/cash/'+id:'/api/report/cash/'+id+'/toggle',{method:act==='delete'?'DELETE':'POST'});
  if(!r.ok){alert('Не удалось выполнить действие: '+r.status);return}
  await load();
}
async function fxAdd(){
  const msg=document.getElementById('fxMsg'), day=document.getElementById('fxDay').value;
  const usd=parseMoney('fxUsd'), rub=parseMoney('fxRub'), note=document.getElementById('fxNote').value;
  if(!day){msg.textContent='Укажите дату';return}
  if(usd===null||!isFinite(usd)||usd===0){msg.textContent='Укажите сумму в USDT (убыток со знаком минус)';return}
  if(rub!==null&&!isFinite(rub)){msg.textContent='Сумма в ₽ указана неверно';return}
  const r=await fetch('/api/report/forex-result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({day,usd,rub,note})});
  if(!r.ok){msg.textContent='Ошибка '+r.status;return}
  msg.textContent='Добавлено';
  for(const id of ['fxUsd','fxRub','fxNote'])document.getElementById(id).value='';
  await load();
}
async function fxDel(rid){
  if(!confirm('Удалить форекс-результат?'))return;
  const r=await fetch('/api/report/forex-result/'+encodeURIComponent(rid),{method:'DELETE'});
  if(!r.ok){alert('Не удалось удалить: '+r.status);return}
  await load();
}
const parseMoney=id=>{const raw=document.getElementById(id).value.replace(/\s/g,'').replace(',','.');return raw===''?null:parseFloat(raw)};
async function saveCap(){
  const start=parseMoney('capStart'), current=parseMoney('capNow'), peak=parseMoney('capPeak');
  const peak_month=document.getElementById('capPeakMonth').value||null;
  for(const v of [start,current,peak])if(v!==null&&!(v>0)){alert('Введите положительное число или оставьте поле пустым');return}
  const r=await fetch('/api/report/capital',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({start,current,peak,peak_month})});
  if(!r.ok){alert('Не удалось сохранить: '+r.status);return}
  await load();
}
async function ctLoad(){
  const el=document.getElementById('ctStatus');
  try{
    const s=await (await fetch('/api/ctrader/status')).json();
    const r=s.last_result, when=s.last_sync?s.last_sync.slice(8,10)+'.'+s.last_sync.slice(5,7)+' '+s.last_sync.slice(11,16):'';
    if(!s.library){el.textContent='Модуль cTrader не установлен на этом Python. Дневник работает без автоподтяжки форекса.';return}
    if(!s.configured){el.textContent='В файле ctrader.json нет client_id и client_secret.';return}
    if(!s.connected){el.textContent='Не подключено. Нажмите «Подключить cTrader» и разрешите доступ в своём cTrader ID.';return}
    if(!r){el.textContent='Подключено, синхронизации ещё не было.';return}
    el.innerHTML=r.error?`Подключено. Последняя синхронизация ${when} не удалась: <span class="warn">${esc(r.error)}</span>`
      :`Подключено. Синхронизация ${when}: счетов ${r.accounts}, закрытых позиций ${r.positions}. Обновляется каждые 15 минут.`;
  }catch(e){}
}
async function ctSync(){
  document.getElementById('ctStatus').textContent='Синхронизация запущена…';
  await fetch('/api/ctrader/sync',{method:'POST'});
  setTimeout(async()=>{await ctLoad();await load()},12000);
}
ctLoad();
load();
timer=setInterval(()=>{if(!['capStart','capNow'].includes(document.activeElement.id))load()},30000);
</script></body></html>
"""
