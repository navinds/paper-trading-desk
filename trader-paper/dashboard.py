#!/usr/bin/env python3
"""The paper trading dashboard for trader-paper. Standard library only.

    python3 dashboard.py                          # http://127.0.0.1:8787, reads trader/paper/state.json
    python3 dashboard.py --state /tmp/replay.json --port 8788

It serves one page and /api/state (the engine's state file, as it is on disk).
The page polls every 5 seconds, so it is live while paper.py run is running.
It listens on 127.0.0.1 only: nothing leaves your computer.

    python3 dashboard.py --export docs --state-url https://raw.githubusercontent.com/USER/REPO/data/state.json
    python3 dashboard.py --bundle state.json      # the file that static page reads

The static copy is for GitHub Pages (the GitHub Actions setup): the page reads
state.json from the repo's data branch once a minute.
"""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Paper Trading Desk</title>
<style>
:root{color-scheme:light;--bg:#f6f5f2;--surface:#fcfcfb;--border:#e4e2dc;--grid:#ecebe6;--text:#0b0b0b;--text2:#52514e;
--muted:#7a7974;--gain:#2a78d6;--loss:#e34948;--accent:#2a78d6;--warn:#fab219;--good:#0ca30c;--crit:#d03b3b;--wash:#f0efec}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--bg:#111110;--surface:#1a1a19;
--border:#2e2e2b;--grid:#262624;--text:#f0efec;--text2:#c3c2b7;--muted:#8f8e86;--gain:#3987e5;--loss:#e66767;--accent:#3987e5;--wash:#383835}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#111110;--surface:#1a1a19;--border:#2e2e2b;--grid:#262624;--text:#f0efec;
--text2:#c3c2b7;--muted:#8f8e86;--gain:#3987e5;--loss:#e66767;--accent:#3987e5;--wash:#383835}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,system-ui,"Segoe UI",sans-serif}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;padding:14px 20px;border-bottom:1px solid var(--border);background:var(--surface);position:sticky;top:0;z-index:5}
h1{font-size:17px;margin:0}h2{font-size:13px;margin:0 0 10px;color:var(--text2);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.pill{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:99px;background:var(--wash);color:var(--text2);font-size:12px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}.dot.on{background:var(--good)}
main{padding:16px 20px;display:grid;gap:16px;max-width:1400px;margin:0 auto}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px;min-width:0}
.tile .k{color:var(--text2);font-size:12px}.tile .v{font-size:22px;font-weight:650;margin-top:2px;font-variant-numeric:tabular-nums}
.tile .s{color:var(--muted);font-size:12px;margin-top:2px}
.grid2{display:grid;grid-template-columns:2fr 1fr;gap:16px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
@media (max-width:900px){.grid2,.grid3{grid-template-columns:1fr}}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}th{text-align:left;color:var(--muted);font-weight:500;font-size:12px;padding:6px 8px;border-bottom:1px solid var(--border)}
td{padding:7px 8px;border-bottom:1px solid var(--grid);vertical-align:top}td.n,th.n{text-align:right}
.scroll{overflow:auto;max-height:420px}.empty{color:var(--muted);padding:10px 0}
.pos{color:var(--gain)}.neg{color:var(--loss)}.tag{font-size:11px;padding:1px 7px;border-radius:99px;background:var(--wash);color:var(--text2);white-space:nowrap}
.meter{height:8px;border-radius:4px;background:var(--wash);overflow:hidden;margin:6px 0 2px}.meter i{display:block;height:100%;background:var(--accent);border-radius:4px}
.meter.hot i{background:var(--crit)}
.range{position:relative;height:18px;min-width:140px}.range .bar{position:absolute;top:8px;left:0;right:0;height:2px;background:var(--border)}
.range .m{position:absolute;top:3px;width:2px;height:12px}.range .px{position:absolute;top:2px;width:10px;height:14px;margin-left:-5px;border-radius:3px;background:var(--text)}
svg text{fill:var(--muted);font-size:11px}.tip{position:fixed;pointer-events:none;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:8px 10px;font-size:12px;box-shadow:0 4px 16px rgba(0,0,0,.12);display:none;z-index:9}
.tip b{font-size:14px}.note{color:var(--text2);font-size:12px}.why{color:var(--muted);font-size:12px}
button{font:inherit;background:var(--wash);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:3px 10px;cursor:pointer}
details summary{cursor:pointer;color:var(--text2);font-size:12px;margin-top:8px}
</style></head><body>
<header><div><h1>Paper Trading Desk <span class="pill" id="mode">paper</span></h1>
<div class="note">Real NSE prices, fake money, Zerodha's charges. Not financial advice.</div></div>
<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="pill"><span class="dot" id="live"></span><span id="eng">loading</span></span>
<span class="pill" id="mkt"></span><button id="theme" title="light / dark">theme</button></div></header>
<main>
<section class="tiles" id="tiles"></section>
<section class="grid2">
 <div class="card"><h2>Equity curve</h2><div id="equity"></div><details><summary>table view</summary><div class="scroll" id="equityTable"></div></details></div>
 <div class="card"><h2>Risk today</h2><div id="risk"></div><h2 style="margin-top:16px">Charges</h2><div id="charges"></div></div>
</section>
<section class="card"><h2>Open positions</h2><div id="positions"></div></section>
<section class="grid2">
 <div class="card"><h2>Today's calls: every trigger, entry and skip</h2><div class="scroll" id="decisions"></div></div>
 <div class="card"><h2>Watchlist today</h2><div class="scroll" id="watch"></div></div>
</section>
<section class="grid3">
 <div class="card"><h2>Daily P&amp;L</h2><div id="daily"></div></div>
 <div class="card"><h2>P&amp;L by entry hour</h2><div id="hours"></div></div>
 <div class="card"><h2>How trades ended</h2><div id="exits"></div></div>
</section>
<section class="card"><h2>Trade journal</h2><div class="scroll" id="journal"></div></section>
<section class="card"><h2>Notes</h2><div id="notes"></div></section>
</main><div class="tip" id="tip"></div>
<script>
"use strict";
const $=id=>document.getElementById(id);
const tip=$("tip");
function inr(x,dp){if(x==null||isNaN(x))return"-";const neg=x<0;x=Math.abs(x);let [w,f]=x.toFixed(dp==null?2:dp).split(".");
 if(w.length>3){let h=w.slice(0,-3),t=w.slice(-3),p=[];while(h.length>2){p.unshift(h.slice(-2));h=h.slice(0,-2)}if(h)p.unshift(h);w=p.join(",")+","+t}
 return(neg?"-":"")+"₹"+w+(f?"."+f:"")}
function signed(x){return(x>0?"+":"")+inr(x)}
function el(tag,attrs,text){const e=document.createElement(tag);for(const k in(attrs||{}))e.setAttribute(k,attrs[k]);if(text!=null)e.textContent=text;return e}
function cls(x){return x>0?"pos":x<0?"neg":""}
function showTip(ev,rows){tip.replaceChildren();rows.forEach((r,i)=>{const d=el("div");if(i===0){d.appendChild(el("b",null,r))}else d.textContent=r;tip.appendChild(d)});
 tip.style.display="block";const x=Math.min(ev.clientX+14,innerWidth-tip.offsetWidth-8);tip.style.left=x+"px";tip.style.top=(ev.clientY+14)+"px"}
function hideTip(){tip.style.display="none"}
function table(cols,rows,empty){if(!rows.length){return el("div",{class:"empty"},empty)}
 const t=el("table"),h=el("tr");cols.forEach(c=>h.appendChild(el("th",c.n?{class:"n"}:{},c.t)));t.appendChild(h);
 rows.forEach(r=>{const tr=el("tr");cols.forEach(c=>{const td=el("td",c.n?{class:"n"}:{});const v=c.f(r);if(v instanceof Node)td.appendChild(v);else td.textContent=v;if(c.c)td.className+=" "+c.c(r);tr.appendChild(td)});t.appendChild(tr)});return t}
const NS="http://www.w3.org/2000/svg";function s(tag,a){const e=document.createElementNS(NS,tag);for(const k in a)e.setAttribute(k,a[k]);return e}
function css(v){return getComputedStyle(document.documentElement).getPropertyValue(v).trim()}

function lineChart(box,pts,base){box.replaceChildren();if(pts.length<2){box.appendChild(el("div",{class:"empty"},"The curve starts with the first engine tick."));return}
 const W=box.clientWidth||640,H=240,L=64,R=12,T=10,B=26;const ys=pts.map(p=>p.equity).concat([base]);let lo=Math.min(...ys),hi=Math.max(...ys);if(hi-lo<1){hi+=1;lo-=1}const pad=(hi-lo)*.08;lo-=pad;hi+=pad;
 const X=i=>L+(W-L-R)*i/(pts.length-1),Y=v=>T+(H-T-B)*(1-(v-lo)/(hi-lo));const svg=s("svg",{width:W,height:H,role:"img","aria-label":"Equity curve"});
 for(let k=0;k<=4;k++){const v=lo+(hi-lo)*k/4,y=Y(v);svg.appendChild(s("line",{x1:L,x2:W-R,y1:y,y2:y,stroke:css("--grid")}));const t=s("text",{x:L-6,y:y+4,"text-anchor":"end"});t.textContent=inr(v,0);svg.appendChild(t)}
 const yb=Y(base);svg.appendChild(s("line",{x1:L,x2:W-R,y1:yb,y2:yb,stroke:css("--muted"),"stroke-dasharray":"3 3"}));const bt=s("text",{x:W-R,y:yb-4,"text-anchor":"end"});bt.textContent="start "+inr(base,0);svg.appendChild(bt);
 const days=[];pts.forEach((p,i)=>{const d=p.t.slice(0,10);if(!days.length||days[days.length-1].d!==d)days.push({d,i})});
 days.forEach((d,j)=>{if(days.length>8&&j%Math.ceil(days.length/8))return;const t=s("text",{x:X(d.i),y:H-8});t.textContent=d.d.slice(5);svg.appendChild(t)});
 svg.appendChild(s("path",{d:pts.map((p,i)=>(i?"L":"M")+X(i).toFixed(1)+" "+Y(p.equity).toFixed(1)).join(" "),fill:"none",stroke:css("--accent"),"stroke-width":2,"stroke-linejoin":"round"}));
 const hair=s("line",{y1:T,y2:H-B,stroke:css("--muted"),visibility:"hidden"}),dot=s("circle",{r:4,fill:css("--accent"),stroke:css("--surface"),"stroke-width":2,visibility:"hidden"});svg.appendChild(hair);svg.appendChild(dot);
 const hit=s("rect",{x:L,y:T,width:W-L-R,height:H-T-B,fill:"transparent"});svg.appendChild(hit);
 hit.addEventListener("pointermove",ev=>{const r=svg.getBoundingClientRect();const i=Math.max(0,Math.min(pts.length-1,Math.round((ev.clientX-r.left-L)/(W-L-R)*(pts.length-1))));
  const p=pts[i];hair.setAttribute("x1",X(i));hair.setAttribute("x2",X(i));dot.setAttribute("cx",X(i));dot.setAttribute("cy",Y(p.equity));hair.setAttribute("visibility","visible");dot.setAttribute("visibility","visible");
  showTip(ev,[inr(p.equity),p.t+" IST",signed(p.equity-base)+" vs start"])});
 hit.addEventListener("pointerleave",()=>{hair.setAttribute("visibility","hidden");dot.setAttribute("visibility","hidden");hideTip()});box.appendChild(svg)}

function barChart(box,items,label){box.replaceChildren();if(!items.length){box.appendChild(el("div",{class:"empty"},"No trades yet."));return}
 const W=box.clientWidth||300,H=180,L=56,R=8,T=10,B=26;const vs=items.map(d=>d.v);let hi=Math.max(0,...vs),lo=Math.min(0,...vs);if(hi===lo)hi=1;
 const Y=v=>T+(H-T-B)*(1-(v-lo)/(hi-lo)),bw=Math.max(4,Math.min(36,(W-L-R)/items.length-2));const svg=s("svg",{width:W,height:H,role:"img","aria-label":label});
 [lo,0,hi].forEach(v=>{const y=Y(v);svg.appendChild(s("line",{x1:L,x2:W-R,y1:y,y2:y,stroke:v===0?css("--muted"):css("--grid")}));const t=s("text",{x:L-6,y:y+4,"text-anchor":"end"});t.textContent=inr(v,0);svg.appendChild(t)});
 const step=(W-L-R)/items.length;items.forEach((d,i)=>{const x=L+step*i+(step-bw)/2,y0=Y(0),y1=Y(d.v),h=Math.max(1,Math.abs(y1-y0));
  const r=s("rect",{x,y:Math.min(y0,y1),width:bw,height:h,rx:Math.min(4,bw/2),fill:d.v>=0?css("--gain"):css("--loss"),tabindex:0});
  r.addEventListener("pointermove",ev=>showTip(ev,[signed(d.v),d.k,d.n!=null?d.n+" trade"+(d.n===1?"":"s"):""]));r.addEventListener("pointerleave",hideTip);svg.appendChild(r);
  if(items.length<=12){const t=s("text",{x:x+bw/2,y:H-8,"text-anchor":"middle"});t.textContent=d.short||d.k;svg.appendChild(t)}});box.appendChild(svg)}

function tile(k,v,sub,c){const d=el("div",{class:"card tile"});d.appendChild(el("div",{class:"k"},k));const vv=el("div",{class:"v"+(c?" "+c:"")},v);d.appendChild(vv);if(sub)d.appendChild(el("div",{class:"s"},sub));return d}
function meter(label,used,limit,fmt){const d=el("div");const pct=limit?Math.min(100,100*used/limit):0;d.appendChild(el("div",{class:"note"},label+": "+fmt(used)+" of "+fmt(limit)));
 const m=el("div",{class:"meter"+(pct>=80?" hot":"")});const i=el("i");i.style.width=pct+"%";m.appendChild(i);d.appendChild(m);return d}
function rangeBar(p,last){const w=el("div",{class:"range",title:"stop "+p.stop+" | entry "+p.entry+" | target "+p.target});w.appendChild(el("div",{class:"bar"}));
 const lo=p.stop,hi=p.target,pos=v=>Math.max(0,Math.min(100,100*(v-lo)/(hi-lo)))+"%";
 [[lo,css("--loss")],[p.entry,css("--muted")],[hi,css("--gain")]].forEach(([v,c])=>{const m=el("div",{class:"m"});m.style.left=pos(v);m.style.background=c;w.appendChild(m)});
 const px=el("div",{class:"px"});px.style.left=pos(last);w.appendChild(px);return w}

let st=null;
function render(){if(!st)return;const S=st.stats||{},A=st.account,E=st.engine||{};const today=(E.now_ist||"").slice(0,10);
 $("mode").textContent=st.mode==="replay"?"REPLAY":"paper";
 const fresh=E.last_tick&&(Date.now()-Date.parse(E.last_tick))<120000;$("live").className="dot"+(fresh&&E.status==="running"?" on":"");
 $("eng").textContent="engine "+(E.status||"?")+(E.now_ist?" · "+E.now_ist.slice(11,16)+" IST":"");$("mkt").textContent="market "+(E.market||"?");
 const day=(st.days||{})[today]||{};const dayTrades=st.trades.filter(t=>t.date===today);const dayPnl=dayTrades.reduce((a,t)=>a+t.pnl,0);
 const unreal=st.positions.reduce((a,p)=>a+p.qty*((st.prices[p.symbol]||p.entry)-p.entry),0);
 $("tiles").replaceChildren(
  tile("Equity",inr(S.equity),"started "+inr(A.start_capital,0)),
  tile("Total P&L",signed(S.total_pnl),(100*S.total_pnl/A.start_capital).toFixed(2)+"% · after charges",cls(S.total_pnl)),
  tile("Today",signed(dayPnl+unreal),dayTrades.length+" closed, "+st.positions.length+" open",cls(dayPnl+unreal)),
  tile("Win rate",S.trades?(100*S.win_rate).toFixed(0)+"%":"-",S.trades?S.wins+" won · "+S.losses+" lost":"no trades yet"),
  tile("Avg R",S.avg_r!=null?(S.avg_r>0?"+":"")+S.avg_r.toFixed(2)+"R":"-","per trade, after charges",cls(S.avg_r||0)),
  tile("Expectancy",S.trades?signed(S.expectancy):"-","average P&L a trade",cls(S.expectancy||0)),
  tile("Max drawdown",inr(-S.max_drawdown),"peak to trough"),
  tile("Cash",inr(A.cash),"free to trade"));
 const pts=st.equity.length>1500?st.equity.filter((p,i)=>i%Math.ceil(st.equity.length/1500)===0||i===st.equity.length-1):st.equity;
 lineChart($("equity"),pts,A.start_capital);
 $("equityTable").replaceChildren(table([{t:"time (IST)",f:r=>r.t},{t:"equity",n:1,f:r=>inr(r.equity)}],st.equity.slice(-60).reverse(),"No data yet."));
 const cfg=st.config||{};const rk=$("risk");rk.replaceChildren(
  meter("Loss at risk today",day.loss_at_risk||0,cfg.max_daily_loss_inr||0,inr),
  meter("Trades today",day.trades||0,cfg.max_trades_per_day||0,x=>String(x)));
 const av=Object.entries(day.avoid||{});rk.appendChild(el("div",{class:"note",style:"margin-top:10px"},av.length?"Avoided today: "+av.map(([s,r])=>s+" ("+r+")").join(", "):"Nothing avoided today."));
 const gross=S.gross||0;$("charges").replaceChildren(el("div",{class:"note"},"Paid "+inr(A.charges)+" in charges on "+S.trades+" trades."),
  el("div",{class:"why"},S.trades?"Gross P&L before charges "+signed(gross)+": charges took "+(gross>0?(100*A.charges/gross).toFixed(0)+"% of it":inr(A.charges)+" on top of the loss")+".":""));
 $("positions").replaceChildren(table([
  {t:"symbol",f:p=>p.symbol},{t:"qty",n:1,f:p=>p.qty},{t:"entry",n:1,f:p=>p.entry.toFixed(2)+" @ "+p.entry_time},
  {t:"last",n:1,f:p=>(st.prices[p.symbol]||p.entry).toFixed(2)},
  {t:"stop · price · target",f:p=>rangeBar(p,st.prices[p.symbol]||p.entry)},
  {t:"stop",n:1,f:p=>p.stop.toFixed(2)},{t:"target",n:1,f:p=>p.target.toFixed(2)},
  {t:"unrealised",n:1,f:p=>signed(p.qty*((st.prices[p.symbol]||p.entry)-p.entry)),c:p=>cls((st.prices[p.symbol]||p.entry)-p.entry)},
  {t:"max loss",n:1,f:p=>inr(p.planned_max_loss)},{t:"why",f:p=>el("span",{class:"why"},p.verdict||p.why)}],st.positions,"No open positions."));
 const dec=st.decisions.filter(d=>d.at.slice(0,10)===today).slice().reverse();
 $("decisions").replaceChildren(table([{t:"time",f:d=>d.at.slice(11)},{t:"symbol",f:d=>d.symbol||"-"},{t:"call",f:d=>el("span",{class:"tag"},d.action)},{t:"reason",f:d=>el("span",{class:"why"},d.reason)}],dec,"No calls yet today."));
 const w=Object.entries((st.watch||{})[today]||{}).map(([k,v])=>Object.assign({symbol:k},v));
 $("watch").replaceChildren(table([{t:"symbol",f:r=>r.symbol},{t:"at",f:r=>r.at},{t:"range",f:r=>r.or_low+" – "+r.or_high},{t:"status",f:r=>el("span",{class:"tag"},r.status)},{t:"note",f:r=>el("span",{class:"why"},r.reason||r.verdict||"")}],w,"No breakouts yet today."));
 barChart($("daily"),(S.daily||[]).map(d=>({k:d.date,short:d.date.slice(5),v:d.pnl})),"Daily P&L");
 barChart($("hours"),Object.entries(S.by_hour||{}).map(([k,v])=>({k:k+" entries",short:k.slice(0,2),v:v.pnl,n:v.n})),"P&L by entry hour");
 barChart($("exits"),Object.entries(S.by_exit||{}).map(([k,v])=>({k,short:k.slice(0,8),v:v.pnl,n:v.n})),"P&L by exit");
 $("journal").replaceChildren(table([{t:"date",f:t=>t.date},{t:"symbol",f:t=>t.symbol},{t:"in",f:t=>t.entry_time},{t:"out",f:t=>t.exit_time},{t:"qty",n:1,f:t=>t.qty},
  {t:"entry",n:1,f:t=>t.entry.toFixed(2)},{t:"exit",n:1,f:t=>t.exit.toFixed(2)},{t:"result",f:t=>el("span",{class:"tag"},t.exit_reason)},
  {t:"gross",n:1,f:t=>signed(t.gross),c:t=>cls(t.gross)},{t:"charges",n:1,f:t=>inr(t.charges)},{t:"P&L",n:1,f:t=>signed(t.pnl),c:t=>cls(t.pnl)},
  {t:"R",n:1,f:t=>(t.r>0?"+":"")+(t.r||0).toFixed(2),c:t=>cls(t.r)}],st.trades.slice().reverse(),"No trades yet."));
 $("notes").replaceChildren(table([{t:"time",f:n=>n.at},{t:"note",f:n=>n.text}],(st.notes||[]).slice().reverse(),"No notes yet. Claude writes its morning plan and evening review here."));}
const STATE_URL="__STATE_URL__";
async function load(){try{const u=STATE_URL+(STATE_URL.indexOf("?")<0?"?":"&")+"t="+Date.now();const r=await fetch(u,{cache:"no-store"});if(r.ok){st=await r.json();render()}}catch(e){$("eng").textContent="dashboard cannot reach the state file"}}
$("theme").onclick=()=>{const d=document.documentElement,cur=d.dataset.theme||(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");d.dataset.theme=cur==="dark"?"light":"dark";render()};
addEventListener("resize",render);load();setInterval(load,__POLL_MS__);
</script></body></html>"""


def page(state_url, poll_ms):
    return PAGE.replace("__STATE_URL__", state_url).replace("__POLL_MS__", str(int(poll_ms)))


def export(out_dir, state_url, poll_ms=60000):
    """A static copy for GitHub Pages: the page reads state_url (state.json with the config inside)."""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page(state_url, poll_ms))
    open(os.path.join(out_dir, ".nojekyll"), "w").close()


def bundle(state_path, config_path):
    """The state the page reads, with the few config numbers its meters need."""
    with open(state_path, encoding="utf-8") as fh:
        st = json.load(fh)
    cfg = {}
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    st["config"] = {k: cfg.get(k) for k in ("max_daily_loss_inr", "max_trades_per_day", "max_loss_per_trade_inr",
                                             "flat_by")}
    return st


def make_handler(state_path, config_path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, ctype):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                return self.send(200, page("/api/state", 5000), "text/html; charset=utf-8")
            if self.path.startswith("/api/state"):
                if not os.path.exists(state_path):
                    return self.send(404, json.dumps({"error": "no state at %s: run paper.py init" % state_path}),
                                     "application/json")
                return self.send(200, json.dumps(bundle(state_path, config_path), ensure_ascii=False),
                                 "application/json; charset=utf-8")
            self.send(404, "not found", "text/plain")
    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description="The paper trading dashboard (trader-paper).")
    ap.add_argument("--state", default="trader/paper/state.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--export", metavar="DIR", help="write a static page for GitHub Pages instead of serving")
    ap.add_argument("--state-url", help="with --export: where the page reads state.json")
    ap.add_argument("--bundle", metavar="FILE", help="write state.json with the config numbers, for the static page")
    a = ap.parse_args(argv)
    if a.bundle:
        with open(a.bundle, "w", encoding="utf-8") as fh:
            json.dump(bundle(a.state, a.config), fh, ensure_ascii=False)
        return 0
    if a.export:
        export(a.export, a.state_url or "state.json")
        print("Wrote a static dashboard to %s (reads %s every minute)." % (a.export, a.state_url or "state.json"))
        return 0
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(os.path.abspath(a.state), os.path.abspath(a.config)))
    print("Dashboard: http://127.0.0.1:%d  (reading %s; Ctrl-C stops it)" % (a.port, a.state), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
