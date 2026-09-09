'use strict';
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const patterns = {acknowledgement_only:'Receipt acknowledgement',periodic_digest:'Periodic digest',routine_success:'Routine success report',informational_reference:'Informational reference',unknown:'Unknown pattern'};
const autonomies = {silent:'Silent',notify:'Notify',ask:'Ask for approval',escalate:'Escalate'};
const actions = {archive:'Archive email',label:'Apply label',draft:'Save draft',send:'Send reply',none:'No mail action',pay:'Payment request',delete:'Deletion request'};
const statuses = {executing:'Gmail action queued',restoring:'Gmail restore queued',unknown:'Gmail result unknown',pending:'Approval needed',executed:'Completed',blocked:'Blocked',escalated:'Needs your review',error:'Processing error',skipped:'Kept in inbox',rejected:'Rejected',corrected:'Restored to inbox'};
const events = {gmail_queued:'Gmail operation queued',gmail_started:'Gmail verification started',gmail_unknown:'Gmail result unknown',gmail_error:'Gmail operation stopped',gmail_unverified:'Gmail state not confirmed',decision:'Agent decision',approved:'You approved the action',rejected:'You rejected the action',executed:'Action completed',notification:'Notification',preference_feedback:'Feedback saved',learned_permission:'Learned preference applied',archive_corrected:'Archive corrected',revised:'Reply revised'};
let state, selected, filter='all', page='mail', signature='', busy=false;
function notify(text){$('#toast').textContent=text;$('#toast').classList.remove('hidden');setTimeout(()=>$('#toast').classList.add('hidden'),5000)}
function error(text){$('#error').textContent=text;$('#error').classList.toggle('hidden',!text)}
async function post(path, data){
  if(busy) return null;
  busy=true;error('');
  try {
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify(data)});
    const result=await response.json();if(!response.ok)throw Error(result.error||'Operation failed');
    await refresh(true);return result;
  }catch(e){error(e.message);return null}finally{busy=false}
}
async function refresh(force=false){
  try{
    const response=await fetch('/api/state');if(!response.ok)throw Error('Server unavailable');
    const next=await response.json();const sig=JSON.stringify(next);
    if(!force && ($('#detail').contains(document.activeElement) && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)))return;
    state=next;
    if(force||sig!==signature){signature=sig;render()}
  }catch(e){error('Could not refresh the inbox. Check that the local server is running.')}
}
function setFilter(value){filter=value;renderMail()}
function currentRows(){return state.actions.map(a=>({...a,email:state.emails.find(e=>e.id===a.email_id)})).filter(a=>a.email).reverse()}
function badge(row){const style=['blocked','error','unknown'].includes(row.status)?row.status:row.autonomy;return `<span class="badge ${esc(style)}">${esc(statuses[row.status]||autonomies[row.autonomy])}</span>`}
function render(){
  const demo=state.mode==='scripted';$('#mode').textContent=demo?'Sample cases · no AI':(state.gmail_enabled?'Qwen · Gmail live':'Qwen · Groq');
  $('#new-email').classList.toggle('hidden',demo);$('#load-demo').classList.toggle('hidden',!demo);
  $('#mode-note').textContent=demo?'Sample cases: emails and decisions are predefined; no AI model is called. All mail actions are simulated locally.':state.gmail_enabled?'Gmail live: new live imports can apply AI labels, archive and restore messages in Wajo-Test. Each decision shows its execution mode. Gmail drafts and sending are unavailable.':'Local mode: simulation actions stay local. Live Gmail actions remain paused until the server is started with --gmail-live.';
  $('#nav-count').textContent=state.emails.length;renderMail();renderMemory();
}
function renderMail(){
  if(!state)return;
  const all=currentRows();$('#count-all').textContent=all.length;
  $('#count-pending').textContent=all.filter(r=>r.status==='pending').length;
  $('#count-archived').textContent=all.filter(r=>r.email.archived).length;
  $('#count-attention').textContent=all.filter(r=>['blocked','escalated','error','unknown'].includes(r.status)).length;
  const search=$('#search').value.toLocaleLowerCase();
  const rows=all.filter(r=>(filter==='all'||(filter==='pending'&&r.status==='pending')||(filter==='archived'&&r.email.archived)||(filter==='attention'&&['blocked','escalated','error','unknown'].includes(r.status)))&&(`${r.email.subject} ${r.email.sender}`).toLocaleLowerCase().includes(search));
  $('#list-count').textContent=rows.length;
  document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('selected',b.dataset.filter===filter));
  $('#filter-label').classList.toggle('hidden',filter!=='attention');$('#filter-label').textContent='Showing blocked, failed or escalated actions. Select All to reset.';
  if(!rows.some(r=>r.id===selected))selected=rows[0]?.id;
  $('#email-list').innerHTML=rows.length?rows.map(r=>`<button class="email-item ${r.id===selected?'selected':''}" data-id="${r.id}"><div class="email-top"><span class="email-sender">${esc(r.email.sender)}</span><span class="badge ${esc(r.autonomy)}">${esc(autonomies[r.autonomy])}</span></div><h3>${esc(r.email.subject)}</h3><p class="email-preview">${esc(r.email.body)}</p>${badge(r)}</button>`).join(''):'<div class="empty-list">No matching emails.<br>Add an email or change the filter.</div>';
  $('#email-list').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{selected=Number(b.dataset.id);renderMail()}));
  const jobs=state.jobs.filter(j=>j.status!=='done');
  $('#jobs').innerHTML=jobs.map(j=>{const e=JSON.parse(j.email);return `<div class="job">${esc(e.subject)}<br>${j.status==='error'?'Processing failed':j.status==='processing'?'The agent is reading…':'Queued for analysis'}${j.status==='error'?`<details><summary>Error details and attempts</summary><pre>${esc(JSON.stringify(JSON.parse(j.diagnostics),null,2))}</pre></details>`:''}</div>`}).join('');
  renderDetail(rows.find(r=>r.id===selected));
}
function renderDetail(row){
  if(!row){$('#detail').innerHTML='<div class="empty"><div class="empty-icon">✉</div><h2>Select an email</h2><p>The email, agent decision and action history will appear here.</p></div>';return}
  const p=row.proposal;const history=state.audit.filter(a=>a.action_id===row.id);
  const basis=history.find(a=>a.event==='learned_permission');const pref=basis?JSON.parse(basis.details):row.preference;
  const count=pref.approval_ids?.length||0;
  const flags=[['requires_action','Reply or action required'],['has_deadline','Has a deadline'],['significant_change','Significant change'],['sensitive','Sensitive content'],['suspicious','Suspected prompt injection']].filter(([k])=>p[k]);
  let explanation=basis?`Based on: ${count} approvals for “${patterns[p.pattern]||p.pattern}”. ${pref.scope==='*'?'Experience shared across senders.':'Sender-specific experience.'}`:row.preference.mode==='keep'?'Your exception applies: keep in inbox.':p.action==='archive'?`Approvals in the applicable group: ${count} of 3. ${!row.learning_eligible?'This email is not eligible for automatic archiving.':'After three approvals, eligible emails are archived with a notification.'}`:'Autonomy follows the permitted-action policy.';
  const scope=`<label class="scope-label">Apply feedback to<select id="feedback-scope"><option value="general">This email pattern across all senders</option><option value="sender">This pattern only from ${esc(row.email.sender)}</option></select></label>`;
  let controls='';
  if(row.status==='pending')controls=`${p.action==='archive'?scope:''}<div class="decision-buttons"><button class="primary" id="approve">${p.action==='send'?'Approve simulated send':(row.transport==='gmail'?'Approve Gmail archive':'Approve local archive')}</button><button class="secondary" id="reject">Reject</button></div>`;
  if(row.status==='executed'&&p.action==='archive'&&row.email.archived)controls=`${scope}<div class="decision-buttons"><button class="secondary" id="correct">Restore to inbox and correct</button></div>`;
  const gmailOps=(state.gmail_operations||[]).filter(o=>o.action_id===row.id);
  const operation=gmailOps.find(o=>['unknown','error'].includes(o.status));
  const transport=row.transport==='gmail'?'Gmail · real action':'Local simulation';
  if(operation)controls+=`<p role="alert">${esc(operation.error)}</p><button class="secondary" id="gmail-check">Check Gmail status (read only)</button>`;
  $('#detail').innerHTML=`<div class="detail-heading"><span>EMAIL # ${row.id}</span>${badge(row)}</div><h2>${esc(row.email.subject)}</h2><div class="sender-row"><span class="avatar">${esc(row.email.sender[0].toUpperCase())}</span><div>${esc(row.email.sender)}<small>${row.transport==='gmail'?'Source: connected Gmail':'Source: local inbox copy'}</small></div></div><p class="email-body">${esc(row.email.body)}</p><div class="decision"><p><strong>${esc(transport)}</strong></p><p>${esc(row.reason)}</p><div class="decision-title">✦ Agent decision · ${esc(actions[p.action]||p.action)}</div><p>${esc(p.reason)}</p><div class="decision-meta">${esc(explanation)}<br>Pattern: ${esc(patterns[p.pattern]||p.pattern)}${p.label?`<br>Label: ${esc(p.label)}`:''}${flags.length&&p.pattern_evidence?`<br>${esc(flags.map(f=>f[1]).join(' · '))}`:''}</div>${p.pattern_evidence?`<blockquote class="reason-quote">${esc(p.pattern_evidence)}</blockquote>`:''}${p.text?`<div class="send-preview">${p.recipient?`Recipient: ${esc(p.recipient)}<br>`:''}${esc(p.text)}</div>`:''}${controls}${p.action==='send'&&row.status==='pending'?'<details class="send-editor"><summary>Edit reply and recipient</summary><label>Recipient<input id="edit-recipient" type="email"></label><label>Body<textarea id="edit-text" rows="4"></textarea></label><div class="decision-buttons"><button class="secondary" id="save-edit">Save new revision</button></div></details>':''}<div class="decision-buttons"><button class="secondary" id="keep-sender">Always keep mail from this sender</button></div></div><details class="history"><summary>Decision history · ${history.length} entries</summary>${history.map(a=>`<div class="history-item">${esc(events[a.event]||a.event)}<small>${esc(new Date(a.created_at).toLocaleString('en-US'))}</small><details><summary>Details</summary><pre>${esc(JSON.stringify(JSON.parse(a.details),null,2))}</pre></details></div>`).join('')}</details>`;
  $('#gmail-check')?.addEventListener('click',async()=>{if(await post('/api/gmail-check',{operation_id:operation.id}))notify('Gmail status checked. Review the result above.')});
  const scopeValue=()=>$('#feedback-scope')?.value||'general';
  for(const [id,route,message] of [['approve','approve','Action approved'],['reject','reject','Action rejected'],['correct','correct','Email restored. Correction saved']]){
    $(`#${id}`)?.addEventListener('click',async()=>{const r=await post(`/api/${route}`,{action_id:row.id,revision:row.revision,scope:scopeValue()});if(r)notify(['executing','restoring'].includes(r.status)?'Gmail operation queued. Waiting for verification.':message)})
  }
  $('#keep-sender').addEventListener('click',async()=>{const r=await post('/api/rule',{sender:row.email.sender,keep:true});if(r)notify('Mail from this sender will stay in the inbox')});
  if($('#save-edit')){
    $('#edit-recipient').value=p.recipient;$('#edit-text').value=p.text;
    $('#save-edit').addEventListener('click',async()=>{const r=await post('/api/edit',{action_id:row.id,revision:row.revision,recipient:$('#edit-recipient').value,text:$('#edit-text').value});if(r)notify('New revision saved. Review it before approving')})
  }
}
function renderMemory(){
  if(!state)return;
  const groups=new Map();for(const f of state.preference_feedback){const key=JSON.stringify([f.scope,f.pattern]);if(!groups.has(key))groups.set(key,{scope:f.scope,pattern:f.pattern,count:0});const group=groups.get(key);group.count=f.positive?group.count+1:0}
  $('#memory-groups').innerHTML=groups.size?[...groups.values()].map(g=>`<div class="memory-card"><h2>${esc(patterns[g.pattern]||g.pattern)}</h2><p class="scope-name">${g.scope==='*'?'General experience · all senders':esc(g.scope)}</p><strong>${g.count} <small>/ 3</small></strong><p>${g.count>=3?'Enough experience for eligible emails. Exceptions and risk signals are checked separately.':'The agent will keep asking. Explicit approvals are needed.'}</p></div>`).join(''):'<div class="memory-card"><h2>Learning your preferences</h2><p>Approve or correct archiving decisions. Experience for each semantic pattern will appear here.</p></div>';
  $('#rules').innerHTML=state.archive_rules.map((r,i)=>`<div class="rule-row"><span>${esc(r.scope==='*'?'All senders':r.scope)} · ${esc(r.pattern==='*'?'All email patterns':patterns[r.pattern])}</span><button class="secondary" data-remove-rule="${i}">Remove exception</button></div>`).join('');
  $('#rules').querySelectorAll('button').forEach(b=>b.addEventListener('click',async()=>{const r=state.archive_rules[Number(b.dataset.removeRule)];if(await post('/api/rule',{sender:r.scope,pattern:r.pattern,keep:false}))notify('Exception removed')}));
}
function navigate(next){page=next;$('#mail-view').classList.toggle('hidden',page!=='mail');$('#memory-view').classList.toggle('hidden',page!=='memory');$('#nav-mail').classList.toggle('active',page==='mail');$('#nav-memory').classList.toggle('active',page==='memory')}
$('#nav-mail').addEventListener('click',()=>navigate('mail'));$('#back-mail').addEventListener('click',()=>navigate('mail'));$('#nav-memory').addEventListener('click',()=>navigate('memory'));
document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>setFilter(b.dataset.filter)));
$('#search').addEventListener('input',renderMail);$('#refresh').addEventListener('click',()=>refresh(true));
$('#new-email').addEventListener('click',()=>$('#compose').showModal());$('#close-compose').addEventListener('click',()=>$('#compose').close());
$('#compose-form').addEventListener('submit',async e=>{e.preventDefault();$('#submit-email').disabled=true;try{const r=await post('/api/ingest',Object.fromEntries(new FormData(e.target)));if(r){$('#compose').close();e.target.reset();navigate('mail');setFilter('all');notify('Email queued for agent analysis')}}finally{$('#submit-email').disabled=false}});
$('#load-demo').addEventListener('click',async()=>{if(await post('/api/demo',{}))notify('Sample cases loaded. Loading again does not duplicate actions')});
$('#rule-form').addEventListener('submit',async e=>{e.preventDefault();const sender=new FormData(e.target).get('sender');if(await post('/api/rule',{sender,keep:true})){e.target.reset();notify('Exception saved')}});
refresh();setInterval(()=>refresh(),2500);
