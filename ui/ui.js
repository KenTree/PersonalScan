/* All email content enters the DOM through textContent, never HTML. */
'use strict';
const token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
let state = null, category = 'All', signature = '';
const $ = id => document.getElementById(id);
function node(tag, text, cls) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if(cls) el.className=cls; return el; }
function date(value) { return new Date(value).toLocaleString(); }
function renderItems() {
  $('items').replaceChildren();
  const items = state.items.filter(i => (category === 'All' || i.category === category) && (!$('unread').checked || i.unread));
  if (!items.length) $('items').append(node('div', state.last_scan ? 'No notable messages in this view. Check the excluded list and scan coverage.' : 'Run a scan to see your digest.', 'empty'));
  for (const item of items) {
    const card=node('article', undefined, 'card'), top=node('div', undefined, 'card-top');
    top.append(node('span',item.category,'category'),node('span',item.unread?'Unread':'Read','unread-dot')); card.append(top);
    card.append(node('h2',item.subject),node('div',item.sender+' · '+date(item.received),'meta'));
    if(item.review) card.append(node('p','Needs review: possible application correspondence.','warning'));
    card.append(node('p',item.summary_type,'type'),node('p',item.summary,'summary'),node('p','Why included: '+item.reason,'reason'));
    if(item.action_quote) card.append(node('p','Requested action (source quote): '+item.action_quote));
    if(item.evidence) for(const quote of item.evidence) card.append(node('blockquote',quote));
    for(const warning of item.warnings) card.append(node('p',warning,'warning'));
    if(item.url){const link=node('a','Open original in Gmail ↗'); link.href=item.url;link.target='_blank';link.rel='noopener noreferrer';card.append(link);}
    const detail=node('details');detail.append(node('summary','Read thread source'));
    for(const mail of item.messages){const source=node('div',undefined,'message');source.append(node('strong',mail.sender+' · '+date(mail.received)),node('p',mail.body));if(mail.attachments.length)source.append(node('p','Attachments not read: '+mail.attachments.join(', ')));detail.append(source);}
    card.append(detail);$('items').append(card);
  }
  $('excluded').replaceChildren();$('audit-title').textContent=`Excluded messages (${state.excluded.length})`;
  for(const item of state.excluded){const row=node('div',undefined,'excluded-row');row.append(node('strong',item.subject),node('p',item.sender),node('p',item.reason));$('excluded').append(row);}
}
async function refresh(){
  try {
    const response=await fetch('/api/state',{headers:{'X-Scanner-Token':token}});
    if(!response.ok) throw new Error('Open the full dashboard URL printed in Terminal, including its token.');
    const firstLoad = state === null;
    state=await response.json();
    if(firstLoad) $('max-threads').value=state.max_threads;
    $('max-threads').disabled=state.busy;
    $('mode').textContent=state.demo?'FICTIONAL DEMO':'LIVE GMAIL';$('engine').textContent=state.engine;
    $('scope').textContent=state.demo?'Sample messages only · The contact’s demo address is fictional':`${state.account_email?state.account_email+' · ':''}Last ${state.lookback_days} days · Up to ${state.max_threads} threads · ${state.mentor_configured?'Mentor address configured':'Add The contact’s address in config.json'}`;
    $('scan').textContent=state.busy?'Scanning…':(state.demo?'Scan demo messages':'Scan Gmail');$('scan').disabled=state.busy;
    $('status').textContent=(state.incomplete?'INCOMPLETE · ':'')+state.status;$('error').textContent=state.error||'';
    $('last').textContent=state.last_scan?'Last finished scan: '+date(state.last_scan):'No completed scan yet.';
    const next=JSON.stringify([state.last_scan,state.counts,state.items.length]);
    if(next!==signature){signature=next;$('stats').replaceChildren();for(const [key,label] of [['scanned','Threads scanned'],['notable','Notable threads'],['excluded','Excluded threads'],['failed','Retrieval failures']]){const stat=node('div',undefined,'stat');stat.append(node('strong',state.counts[key]||0),node('span',label));$('stats').append(stat);}renderItems();}
  }catch(error){$('error').textContent=error.message;$('scan').disabled=true;}
}
$('scan').addEventListener('click',async()=>{
  const input=$('max-threads');
  const limit=Number(input.value);
  if(!input.reportValidity()) return;
  if(!Number.isInteger(limit)||limit<1||limit>150){$('error').textContent='Thread limit must be a whole number from 1 to 150.';return;}
  $('scan').disabled=true;input.disabled=true;
  try{
    const response=await fetch('/api/scan',{method:'POST',headers:{'X-Scanner-Token':token,'Content-Type':'application/json'},body:JSON.stringify({max_threads:limit})});
    if(!response.ok){const result=await response.json();throw new Error(result.error||'Could not start scan.');}
    await refresh();
  }catch(error){$('error').textContent=error.message;$('scan').disabled=false;input.disabled=false;}
});
$('filters').addEventListener('click',event=>{if(!event.target.dataset.category)return;category=event.target.dataset.category;document.querySelectorAll('.filter').forEach(button=>button.classList.toggle('active',button.dataset.category===category));if(state)renderItems();});
$('unread').addEventListener('change',()=>{if(state)renderItems();});
refresh();setInterval(refresh,1500);

const navigationButtons=document.querySelectorAll('.nav-links [data-scroll]');
function updateNavigation(){
  document.querySelector('.site-header').classList.toggle('scrolled',window.scrollY>24);
  let active='scan-section';
  for(const id of ['scan-section','digest-section','excluded-section']){
    if($(id).getBoundingClientRect().top<=window.innerHeight*.35) active=id;
  }
  for(const button of navigationButtons){
    const selected=button.dataset.scroll===active;
    button.classList.toggle('active',selected);
    if(selected) button.setAttribute('aria-current','location');else button.removeAttribute('aria-current');
  }
}
document.querySelector('.navigation').addEventListener('click',event=>{
  const button=event.target.closest('[data-scroll]');
  if(!button) return;
  const target=$(button.dataset.scroll);
  if(target.tagName==='DETAILS') target.open=true;
  target.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
});
window.addEventListener('scroll',updateNavigation,{passive:true});
window.addEventListener('resize',updateNavigation);
updateNavigation();
