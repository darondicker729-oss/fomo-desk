'use strict';
const $=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const TZ='America/New_York',now=()=>Date.now()/1000;
const usd=(n,d=2)=>n==null?'—':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:d}).format(n);
const signed=n=>n==null?'—':(n>=0?'+':'−')+usd(Math.abs(n));
const pct=(n,d=1)=>n==null?'—':(n>0?'+':'')+Number(n).toFixed(d)+'%';
const k=n=>n==null?'—':n>=1e6?'$'+(n/1e6).toFixed(1)+'M':n>=1e3?'$'+(n/1e3).toFixed(1)+'k':'$'+Number(n).toFixed(0);
const px=n=>n==null?'—':'$'+Number(n).toLocaleString('en-US',{maximumSignificantDigits:6});
const tone=n=>n<0?'neg':n>0?'pos':'';
const clock=t=>t?new Date(t*1000).toLocaleTimeString('en-US',{timeZone:TZ,hour:'numeric',minute:'2-digit'})+' ET':'—';
const stamp=t=>t?new Date(t*1000).toLocaleString('en-US',{timeZone:TZ,month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})+' ET':'—';
const ago=t=>{if(!t)return '—';const s=Math.max(0,Math.floor(now()-t));return s<60?s+'s ago':s<3600?Math.floor(s/60)+'m ago':Math.floor(s/3600)+'h '+Math.floor(s%3600/60)+'m ago'};
const dur=s=>{s=Math.max(0,Math.floor(s));return Math.floor(s/3600)+'h '+String(Math.floor(s%3600/60)).padStart(2,'0')+'m'};
const card=(title,body,sub='')=>`<section class="card"><div class="card-h"><h2>${title}</h2>${sub?`<span>${sub}</span>`:''}</div>${body}</section>`;
const stat=(label,val,cls='')=>`<div class="stat"><small>${label}</small><b class="${cls}">${val}</b></div>`;
const empty=(t,p)=>`<div class="empty"><b>${t}</b><p>${p}</p></div>`;
const chip=(ok,yes='PASS',no='REJECT')=>ok==null?'<em class="chip">—</em>':ok?`<em class="chip ok">${yes}</em>`:`<em class="chip bad">${no}</em>`;
let tab=localStorage.getItem('fomo-app-tab')||'home',S=null,D=null,M=null,err='';

function home(){
 const p=D.paper||{},t=now(),eight=p.eight_hours_at,elig=p.eligible_at,c=D.caps||{};
 const run=t-(p.start||t),toEight=eight-t,toElig=elig-t;
 const prog=Math.min(100,Math.max(0,run/28800*100));
 const stuck=D.stuck||[];
 return (stuck.length?card('Needs attention',`<div class="list">${stuck.map(i=>`<div class="item"><div><b class="neg">${esc(i.kind==='sell'?'SELL NOT CONFIRMED':'BUY NOT CONFIRMED')}</b><small>${esc(i.text)}</small></div></div>`).join('')}</div><p class="note">No automatic retry. Check Phantom, then reconcile.</p>`,'LIVE'):'')
 +card('Account',`<div class="hero"><small>Equity · ${esc(D.mode)}</small><b>${usd(D.equity)}</b><span class="${tone(D.total_pnl)}">${signed(D.total_pnl)} total</span><span class="${tone(D.today_pnl)}">${signed(D.today_pnl)} today (${pct(D.today_pct,2)})</span></div>
 <div class="grid3">${stat('Realized',signed(D.realized),tone(D.realized))}${stat('Unrealized',signed(D.unrealized),tone(D.unrealized))}${stat('Open',(D.positions||[]).length+' / '+(c.positions_limit??'—'))}</div>${D.mode==='LIVE'?'<p class="note">Starting stack plus P&amp;L; not a live wallet balance.</p>':''}`,D.mode==='PAPER'?'Simulated money':'LIVE')
 +card('PAPER clock',`<div class="bar"><i style="width:${prog}%"></i></div>
 <div class="rows"><div><span>Session start</span><b>${stamp(p.start)}</b></div>
 <div><span>Running for</span><b>${dur(run)}</b></div>
 <div><span>8h PAPER mark</span><b>${toEight>0?'in '+dur(toEight)+' · '+clock(eight):'✓ reached '+clock(eight)}</b></div>
 <div><span>LIVE eligible (report)</span><b>${toElig>0?'in '+dur(toElig)+' · '+stamp(elig):'✓ eligible since '+stamp(elig)}</b></div>
 <div><span>Full PAPER nights</span><b>${p.full_nights??0}</b></div></div>
 <p class="note">LIVE only switches when you type LIVE to the desk bot. This app cannot change modes.</p>`)
 +card('Safety',`<div class="rows"><div><span>Kill switch</span><b class="${D.kill?'neg':'pos'}">${D.kill?'ON · no new entries':'OFF · entries allowed'}</b></div>
 <div><span>PAPER pause</span><b class="${D.paper_pause?'neg':'pos'}">${D.paper_pause?'ON · paper entries blocked':'OFF · paper entries allowed'}</b></div>
 <div><span>Daily loss breaker</span><b class="${D.breaker?'neg':''}">${D.breaker?'TRIPPED today':'Not tripped'}</b></div>
 <div><span>Auto mode / buy / sell-on-stop</span><b>${['enabled','buy','sell'].map(x=>D.auto?.[x]?'ON':'off').join(' / ')}</b></div>
 <div><span>Auto buys today</span><b>${c.buys??0} / ${c.buys_limit??'—'}</b></div>
 <div><span>Daily loss used</span><b>${usd(c.loss)} / ${usd(c.loss_limit)}</b></div>
 <div><span>Exposure</span><b>${usd(c.exposure)} / ${usd(c.exposure_limit)}</b></div></div>
 <div class="btns"><button class="btn danger" data-cmd="/kill">Kill switch · /kill</button><button class="btn" data-cmd="/resume">Resume · /resume</button><button class="btn danger" data-cmd="/paperpause">PAPER pause · /paperpause</button><button class="btn" data-cmd="/paperresume">PAPER resume · /paperresume</button></div>
 <p class="note">Buttons copy the command and open your desk bot in Telegram (owner-locked). Paste &amp; send it there. The app itself never trades.</p>`)
 +card('Open positions',(D.positions||[]).length?`<div class="list">${D.positions.map(x=>`<div class="item"><div><b>${esc(x.symbol)}</b><small>${x.source==='majors'?'Majors':'Memecoin'} · ${usd(x.size)} · stop ${px(x.stop)}</small></div><div class="r"><b class="${tone(x.unrealized)}">${signed(x.unrealized)}</b><small>${px(x.entry)} → ${px(x.current)}</small></div></div>`).join('')}</div>`:empty('No open positions','Entries need a Jev pass and every safety gate.'),'')
}
function scan(){
 const f=D.funnel||{},rows=M?.scans||[],last=M?.last_hour||[],n=v=>(last.find(x=>x.verdict===v)||{}).n||0;
 return card('Funnel · this PAPER session',`<div class="funnel">${[['Seen',f.seen],['Sent to Jev',f.sent_jev],['Jev passed',f.jev_passed],['Traded',f.traded]].map(([l,v])=>`<div><b>${(v??0).toLocaleString()}</b><small>${l}</small><i style="width:${Math.max(3,f.seen?(v||0)/f.seen*100:0)}%"></i></div>`).join('')}</div>
 <p class="note">Liquidity floor ${k(M?.min_liquidity)} · last hour: ${n('kill')} rejected, ${last.filter(x=>x.verdict!=='kill').reduce((a,x)=>a+x.n,0)} advanced · ${f.rejected??0} distinct coins rejected this session.</p>`)
 +card('Top rejection reasons',(f.rejections||[]).length?`<div class="list">${f.rejections.slice(0,10).map(r=>`<div class="item"><span>${esc(r.reason||'Unspecified')}</span><b>${r.n}</b></div>`).join('')}</div>`:empty('No rejections yet',''))
 +card('Recent coins',rows.length?`<div class="list">${rows.slice(0,40).map(r=>`<div class="item"><div><b>${esc(r.symbol)}</b><small>${ago(r.ts)} · liq ${k(r.liq)} · 1h ${pct(r.h1,0)}${r.top10!=null?' · top10 '+Number(r.top10).toFixed(0)+'%':''}</small>${r.verdict==='kill'?`<small class="why">✕ ${esc(r.reason)}</small>`:''}</div><div class="r">${chip(r.verdict!=='kill')}</div></div>`).join('')}</div>`:empty('No scans yet','New migrations appear here.'),(rows.length?rows.length+' latest':''))
 +card('Helius on-chain checks',((M?.checks||[]).length||(M?.audits||[]).length)?`<div class="list">${[...(M.checks||[]).map(r=>({ts:r.ts,symbol:r.symbol,ok:r.ok,txt:r.reason})),...(M.audits||[]).map(r=>({ts:r.ts,symbol:r.symbol,ok:r.ok,txt:r.check_name+': '+r.value}))].sort((a,b)=>b.ts-a.ts).slice(0,25).map(r=>`<div class="item"><div><b>${esc(r.symbol)}</b><small>${ago(r.ts)} · ${esc(r.txt)}</small></div>${chip(!!r.ok)}</div>`).join('')}</div>`:empty('No coin has reached the on-chain checks yet','Snipers ≥15%, bundles ≥10%, top-10 ≥25%, dev ≥5% and live mint/freeze authority are rejected once a coin clears the liquidity floor.'))
}
function signals(){
 const v=M?.verdicts||[],g=M?.signals||[],feed=D.feed||[];
 return card('Jev verdicts',v.length?`<div class="list">${v.map(r=>`<div class="item"><div><b>${esc(r.symbol)}</b><small>${ago(r.ts)} · score ${r.jev??'—'}</small><small class="why">${esc(r.reason)}</small></div>${chip(!!r.ok)}</div>`).join('')}</div>`:empty('No Jev verdicts yet','Coins reach Jev only after passing the scanner and on-chain checks.'))
 +card('Signals',g.length?`<div class="list">${g.map(r=>`<div class="item"><div><b>${esc(r.symbol)}</b><small>${ago(r.ts)} · ${usd(r.size)} @ ${px(r.price)} · ${esc(r.mode)}</small>${r.reason?`<small class="why">${esc(r.reason)}</small>`:''}</div><em class="chip">${esc(r.status)}</em></div>`).join('')}</div>`:empty('No signals yet','Ready, not rushing.'))
 +card('Auto-trade feed',feed.length?`<div class="list">${feed.map(r=>`<div class="item"><div><b>${r.kind==='buy'?'AUTO BUY':'AUTO SELL'} · ${esc(r.symbol)}</b><small>${stamp(r.ts)} · ${usd(r.size)} · ${esc(r.status)} · ${esc(r.mode)}</small></div><b class="${tone(r.profit)}">${r.kind==='sell'?signed(r.profit):''}</b></div>`).join('')}</div>`:empty('No automatic fills yet','PAPER auto buys and stop exits appear here.'),`${D.caps?.buys??0}/${D.caps?.buys_limit??'—'} today`)
}
function smart(){
 const sm=D.smart_money||{},h=sm.hits||[],w=sm.tracked||[];
 return card('Smart-wallet engine',`<div class="grid3">${stat('Qualified wallets',w.length)}${stat('Rule',(sm.min_wallets??2)+' in '+(sm.window_min??30)+'m')}${stat('Hits',h.length)}</div><p class="note">${esc(sm.provider_status||'')}${sm.refreshed_at?' · refreshed '+ago(sm.refreshed_at):''}</p>`)
 +card('Hits',h.length?`<div class="list">${h.map(r=>`<div class="item"><div><b>${esc(r.symbol||'?')}</b><small>${ago(r.ts)} · ${r.buyers} wallets · ${esc(r.status)}${r.sell_warnings?' · ⚠ '+r.sell_warnings+' sells':''}</small></div><div class="r"><small>${r.paper_entries||0} PAPER entries</small><b class="${tone(r.paper_realized)}">${r.paper_realized!=null?signed(r.paper_realized):''}</b></div></div>`).join('')}</div>`:empty('No smart-wallet hits yet','An alert fires when 2+ tracked wallets buy the same coin within 30 minutes; it then runs the normal checks.'))
 +card('Tracked wallets',w.length?`<div class="list">${w.slice(0,20).map(x=>`<div class="item"><div><b>${esc(x.wallet.slice(0,4)+'…'+x.wallet.slice(-4))}</b><small>${x.stats?.win_rate!=null?'win '+Math.round(x.stats.win_rate*(x.stats.win_rate<=1?100:1))+'% · ':''}${x.stats?.trades??'—'} trades</small></div><small>${ago(x.refreshed_at)}</small></div>`).join('')}</div>`:empty('None qualified yet','Discovery runs within the Helius daily budget.'))
}
function shadow(){
 const s=D.shadow||{},r=M?.shadow_recent||[],bands=s.by_liquidity||[];
 return card('Rejected-coin shadow tracking',`<div class="grid3">${stat('Tracking now',s.active_n??0)}${stat('Recorded',s.total_recorded??0)}${stat('Capacity',(s.max_active??200)+' max')}</div><p class="note">Every rejected coin is re-checked at +15m, +1h, +3h and +6h. ${s.early_data?'Early data: first 6h outcomes land about 6 hours after tracking began.':''}</p>`)
 +card('By liquidity band',`<div class="table"><div class="th"><span>Band</span><span>Coins</span><span>Done</span><span>Survive</span><span>Med 6h</span></div>${bands.map(b=>`<div><span>${esc(b.band)}</span><span>${b.cohort_n}</span><span>${b.completed_n}</span><span>${b.survival_pct==null?'—':Math.round(b.survival_pct)+'%'}</span><span class="${tone(b.median_return_pct)}">${pct(b.median_return_pct,0)}</span></div>`).join('')}</div>`)
 +card('Recent rejected coins',r.length?`<div class="list">${r.slice(0,30).map(x=>`<div class="item"><div><b>${esc(x.symbol)}</b><small>${ago(x.first_seen)} · ${esc(x.band||'?')} · ${x.checkpoints??0}/4 checks</small><small class="why">${esc(x.reason||'')}</small></div><div class="r"><em class="chip ${x.outcome==='survived'?'ok':x.outcome?'bad':''}">${esc(x.outcome||x.status)}</em>${x.ret6h!=null?`<small class="${tone(x.ret6h)}">6h ${pct(x.ret6h,0)}</small>`:''}</div></div>`).join('')}</div>`:empty('Nothing tracked yet',''))
}
function majors(){
 const m=D.majors||{},icons={BTC:'₿',ETH:'Ξ',SOL:'◎'};
 return card('BTC · ETH · SOL',`<div class="majors">${['BTC','ETH','SOL'].map(x=>{const p=m.prices?.[x];return `<div><i>${icons[x]}</i><b>${x}</b><strong>${p?px(p.price):'—'}</strong><small>${p?ago(p.ts)+' · '+esc(p.dex||''):'no data'}</small></div>`}).join('')}</div><p class="note">Majors agent ${m.enabled?'enabled':'disabled'} · last poll ${ago(m.last_poll)} · feeds OK ${m.data_ok??'—'}/3</p>`)
 +card('Latest decisions',(m.decisions||[]).length?`<div class="list">${m.decisions.map(x=>`<div class="item"><div><small>${stamp(x.ts)}</small><small class="why">${esc(x.reason)}</small></div><em class="chip">${esc(x.status)}</em></div>`).join('')}</div>`:empty('No decisions yet',''))
}
function health(){
 const hb=(D.heartbeats||[]).slice().sort((a,b)=>a.agent.localeCompare(b.agent)),stale=D.stale_s||120,h=D.helius||{};
 const age=S?now()-S.generated_at:null;
 return card('Snapshot',`<div class="rows"><div><span>Desk data generated</span><b>${clock(S?.generated_at)} · ${ago(S?.generated_at)}</b></div><div><span>Publisher cadence</span><b>every ~${Math.round((M?.publish_every_s||390)/60)} min</b></div>${(M?.pushes||[]).slice(-3).reverse().map(x=>`<div><span>Push</span><b class="${/FAIL/.test(x)?'neg':'pos'}">${esc(x.replace(/^\d{4}-\d\d-\d\d /,''))}</b></div>`).join('')}</div><p class="note">${age>1200?'⚠ Data is older than 20 minutes: the PC, publisher or GitHub Pages may be behind.':'Freshness normal.'}</p>`)
 +card('Agent heartbeats',`<div class="hb">${hb.map(x=>{const a=now()-x.ts-(age||0);return `<div class="${a>stale?'bad':'ok'}"><i></i><span>${esc(x.agent)}</span><small>${ago(x.ts)}</small></div>`}).join('')}</div><p class="note">Red = heartbeat older than ${stale}s when the snapshot was taken.</p>`)
 +card('Helius',`<div class="rows"><div><span>Credits today</span><b>${(h.used_today??0).toLocaleString()} / ${(h.daily_cap??0).toLocaleString()}</b></div><div><span>Rolling 31 days</span><b>${(h.rolling_31d_used??0).toLocaleString()} / ${(h.rolling_31d_cap??0).toLocaleString()}</b></div>${Object.entries(h.worker_states||{}).map(([a,b])=>`<div><span>${esc(a)}</span><b>${esc(b||'—')}</b></div>`).join('')}${h.holders_exhausted?`<div><span>Holder bucket</span><b class="neg">${esc(h.holders_exhausted)}</b></div>`:''}<div><span>Last error</span><b class="${h.last_error?'neg':''}">${esc(h.last_error?(typeof h.last_error==='string'?h.last_error:JSON.stringify(h.last_error)).slice(0,90):'none')}</b></div></div>`)
 +card('Coin feed',`<div class="rows"><div><span>Reconnects (last hour)</span><b class="${(M?.reconnects_1h||0)>10?'neg':''}">${M?.reconnects_1h??'—'}</b></div><div><span>Last reconnect</span><b>${ago(M?.last_reconnect)}</b></div><div><span>Last desk event</span><b>${ago(M?.last_event)}</b></div></div>`)
 +card('This device',`<p class="note">${window.DeskClient.standalone?'Running as the installed FOMO Desk app.':'Running in the browser. To install: Share → Add to Home Screen.'}</p><div class="btns"><button class="btn" id="forget">Lock app (forget key)</button></div>`)
}
const VIEWS={home,scan,signals,smart,shadow,majors,health};

function stocksPanel(){const s=D.stocks||{},p=s.positions||[],g=s.signals||[];return card('Stocks (PAPER)',`<p class="note">${esc(s.state||'Starting')} - ${s.enabled?'enabled':'disabled'}. Separate simulated $200 stack; cannot go LIVE.</p><div class="grid3">${stat('Equity',usd(s.equity??200))}${stat('Realized',signed(s.realized??0))}${stat('Unrealized',signed(s.unrealized??0))}</div><p class="note">10% ticket / 30% exposure / 3 positions / 10% daily loss. 5m EMA9/21 bullish cross. Stop -1.5%, target +3%. Yahoo free data; stale prices veto fills. Regular US market sessions only.</p>${p.length?'<div class="list">'+p.map(x=>`<div class="item"><div><b>${esc(x.symbol)}</b><small>${usd(x.size)} at ${px(x.entry)} / mark ${px(x.mark)}</small><small>Stop ${px(x.stop)} / target ${px(x.target)}</small></div></div>`).join('')+'</div>':empty('No open stock positions','Market closed on weekends and holidays.')}<div class="list">${g.slice(0,5).map(x=>`<div class="item"><div><b>${esc(x.symbol)} - ${esc(x.status)}</b><small>${esc(x.reason)}</small></div></div>`).join('')}</div><p class="note">${s.telegram_authorized?'PAPER STOCKS trade notices enabled':'Stock Telegram pings held pending your approval.'}</p>`)}

function render(){
 if(!D)return;
 $('#mode').textContent=D.mode||'—';$('#mode').className='mode '+(D.mode==='LIVE'?'live':'paper');
 const y=scrollY;$('#view').innerHTML=VIEWS[tab]()+stocksPanel();scrollTo(0,y);
 document.querySelectorAll('#tabs button').forEach(b=>b.classList.toggle('on',b.dataset.tab===tab));
 fresh();
}
function fresh(){
 const el=$('#fresh');if(!S){el.hidden=true;return}
 const a=now()-S.generated_at,cls=a>1200?'bad':a>600?'warn':'ok';
 el.hidden=false;el.className='fresh '+cls;el.textContent=(err?'⚠ '+err+' · ':'')+'Desk data '+ago(S.generated_at).replace(' ago','')+' old · '+clock(S.generated_at)+' · updates ~6.5 min';
}
function locked(){
 $('#tabs').hidden=true;$('#fresh').hidden=true;
 $('#view').innerHTML=card('🔒 Desk locked',`<p>This app needs your private desk link (the one on your desktop shortcut / sent in Telegram). Open that link in Safari, then Share → Add to Home Screen.</p><p class="note">Nothing was decrypted. The key never leaves this phone.</p>`);
}
async function refresh(){
 try{S=await window.DeskClient.load();D=S.routes.dashboard;M=S.routes.mobile||null;err='';$('#tabs').hidden=false;render()}
 catch(e){if(e.locked){S=null;D=null;locked();return}err='Offline, retrying';fresh();if(!D)$('#view').innerHTML=card('Can’t reach the desk snapshot',`<p class="note">${esc(e.message)}. Retrying every 30 seconds.</p>`)}
}
$('#tabs').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;tab=b.dataset.tab;localStorage.setItem('fomo-app-tab',tab);scrollTo(0,0);render()});
document.addEventListener('click',async e=>{
 const c=e.target.closest('[data-cmd]');
 if(c){const cmd=c.dataset.cmd,bot=M?.bot||'Scrappy_grok_bot';try{await navigator.clipboard.writeText(cmd)}catch(_){}
  toast(`Copied ${cmd}. Paste & send it to @${bot}.`);setTimeout(()=>{location.href='https://t.me/'+bot},700);return}
 if(e.target.id==='forget'&&confirm('Forget the key on this phone? You will need the private link again.')){window.DeskClient.forget();locked()}
});
function toast(t){const el=$('#toast');el.textContent=t;el.hidden=false;clearTimeout(toast.t);toast.t=setTimeout(()=>el.hidden=true,3500)}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});
setInterval(refresh,30000);setInterval(fresh,1000);setInterval(()=>{if(D&&(tab==='home'))render()},30000);
if('serviceWorker' in navigator)navigator.serviceWorker.register('./sw.js',{scope:'./'}).catch(()=>{});
refresh();
