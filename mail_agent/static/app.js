'use strict';
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const patterns = {acknowledgement_only:'Receipt acknowledgement',periodic_digest:'Periodic digest',routine_success:'Routine success report',informational_reference:'Informational reference',unknown:'Unknown pattern'};
const autonomies = {silent:'No notification',notify:'Notify',ask:'Ask for approval',escalate:'Escalate'};
const actions = {archive:'Archive email',label:'Apply label',draft:'Save draft',send:'Send reply',none:'No mail action',pay:'Payment request',delete:'Deletion request'};
const statuses = {executing:'Gmail action queued',restoring:'Gmail restore queued',unknown:'Verification needed',pending:'Approval needed',executed:'Completed',blocked:'Blocked',escalated:'Needs your review',error:'Processing error',skipped:'Kept in inbox',rejected:'Rejected',corrected:'Restored to inbox'};
const events = {gmail_draft_saved:'Gmail draft saved',gmail_queued:'Gmail operation queued',gmail_started:'Gmail verification started',gmail_unknown:'Verification needed',gmail_error:'Gmail operation stopped',gmail_unverified:'Gmail state not confirmed',decision:'Agent decision',approved:'You approved the action',rejected:'You rejected the action',executed:'Action completed',notification:'Notification',preference_feedback:'Feedback saved',learned_permission:'Learned preference applied',archive_corrected:'Archive corrected',revised:'Reply revised'};
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
function currentRows(){return state.actions.map(a=>({...a,email:state.emails.find(e=>e.id===a.email_id),labelReview:(state.label_reviews||[]).find(r=>r.action_id===a.id)})).filter(a=>a.email).reverse()}
function badge(row){const replyState=row.reply?(row.status==='executing'?'Saving or sending Gmail reply':row.status==='pending'?'Gmail draft · approval needed':row.status==='executed'?'Sent via Gmail':null):null;const style=['blocked','error','unknown'].includes(row.status)?row.status:row.autonomy;return `<span class="badge ${esc(style)}">${esc(replyState||statuses[row.status]||autonomies[row.autonomy])}</span>`}
function render(){
  const demo=state.mode==='scripted';$('#mode').textContent=demo?'Sample cases · no AI':(state.gmail_enabled?'Qwen · Gmail live':'Qwen · Groq');
  $('#new-email').classList.toggle('hidden',demo);$('#load-demo').classList.toggle('hidden',!demo);
  $('#mode-note').textContent=demo?'Sample cases: emails and decisions are predefined; no AI model is called. All mail actions are simulated locally.':state.gmail_enabled?'Gmail live: new live imports can apply AI labels, archive and restore messages in Wajo-Test. Each decision shows its execution mode. Replies are saved as Gmail drafts. Sending requires approval of the displayed version.':'Local mode: simulation actions stay local. Live Gmail actions remain paused until the server is started without --local-simulation.';
  $('#nav-count').textContent=state.emails.length;renderMail();renderMemory();renderGmail();
}
function renderMail(){
  if(!state)return;
  const all=currentRows();$('#count-all').textContent=all.length;
  const labelRows=all.filter(r=>r.labelReview);const reviewed=labelRows.filter(r=>r.labelReview.status==='reviewed').length;
  $('#label-review-count').textContent=labelRows.length-reviewed;
  const reviewMode=['label_review','label_reviewed'].includes(filter);
  $('#label-review-banner').classList.toggle('hidden',!reviewMode);
  $('#label-review-progress').textContent=`${reviewed} of ${labelRows.length} reviewed`;
  $('#nav-labels').classList.toggle('active',reviewMode&&page==='mail');
  $('#nav-mail').classList.toggle('active',!reviewMode&&page==='mail');
  $('#count-pending').textContent=all.filter(r=>r.status==='pending').length;
  $('#count-archived').textContent=all.filter(r=>r.email.archived).length;
  $('#count-attention').textContent=all.filter(r=>['blocked','escalated','error','unknown'].includes(r.status)||(state.attention_items||[]).some(x=>x.action_id===r.id&&!x.seen)).length;
  const listScroll=$('#email-list').scrollTop;
  const search=$('#search').value.toLocaleLowerCase();
  const rows=all.filter(r=>(filter==='all'||(filter==='label_review'&&r.labelReview&&r.labelReview.status!=='reviewed')||(filter==='label_reviewed'&&r.labelReview?.status==='reviewed')||(filter==='pending'&&r.status==='pending')||(filter==='archived'&&r.email.archived)||(filter==='attention'&&(['blocked','escalated','error','unknown'].includes(r.status)||(state.attention_items||[]).some(x=>x.action_id===r.id&&!x.seen))))&&(`${r.email.subject} ${r.email.sender}`).toLocaleLowerCase().includes(search));
  $('#list-count').textContent=rows.length;
  document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('selected',b.dataset.filter===filter));
  $('#filter-label').classList.toggle('hidden',filter!=='attention');$('#filter-label').textContent='Showing emails you asked to see and actions needing review. Select All to reset.';
  if(!rows.some(r=>r.id===selected))selected=rows[0]?.id;
  $('#email-list').innerHTML=rows.length?rows.map(r=>`<button class="email-item ${r.id===selected?'selected':''}" data-id="${r.id}"><div class="email-top"><span class="email-sender">${esc(r.email.sender)}</span><span class="badge ${esc(r.autonomy)}">${esc(autonomies[r.autonomy])}</span></div><h3>${esc(r.email.subject)}</h3><p class="email-preview">${esc(r.email.body)}</p>${r.labelReview?`<span class="badge label-chip">${esc(r.labelReview.current_label)}</span> <span class="badge">${r.labelReview.status==='reviewed'?'✓ Reviewed':r.status==='executed'?'To review':esc(statuses[r.status]||r.status)}</span>`:badge(r)}</button>`).join(''):'<div class="empty-list">No matching emails.<br>Add an email or change the filter.</div>';
  $('#email-list').scrollTop=listScroll;
  $('#email-list').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{selected=Number(b.dataset.id);renderMail();$('#detail').scrollTop=0}));
  const jobs=state.jobs.filter(j=>j.status!=='done');
  $('#jobs').innerHTML=jobs.map(j=>{const e=JSON.parse(j.email);return `<div class="job">${esc(e.subject)}<br>${j.status==='error'?'Processing failed':j.status==='processing'?'The agent is reading…':'Queued for analysis'}${j.status==='error'?`<details><summary>Error details and attempts</summary><pre>${esc(JSON.stringify(JSON.parse(j.diagnostics),null,2))}</pre></details>`:''}</div>`}).join('');
  renderDetail(rows.find(r=>r.id===selected));
}
function renderDetail(row){
  const previousDetailScroll=$('#detail').scrollTop;
  if(!row){$('#detail').innerHTML='<div class="empty"><div class="empty-icon">✉</div><h2>Select an email</h2><p>The email, agent decision and action history will appear here.</p></div>';return}
  const p=row.proposal;const reply=row.reply;const history=state.audit.filter(a=>a.action_id===row.id);
  const basis=history.find(a=>a.event==='learned_permission');const pref=basis?JSON.parse(basis.details):row.preference;
  const count=pref.approval_ids?.length||0;
  const flags=[['requires_action','Reply or action required'],['has_deadline','Has a deadline'],['significant_change','Significant change'],['sensitive','Sensitive content'],['suspicious','Suspected prompt injection']].filter(([k])=>p[k]);
  let explanation=basis?`Based on: ${count} approvals for “${patterns[p.pattern]||p.pattern}”. ${pref.scope==='*'?'Experience shared across senders.':'Sender-specific experience.'}`:row.preference.mode==='keep'?'Your exception applies: keep in inbox.':p.action==='archive'?`Approvals in the applicable group: ${count} of 3. ${!row.learning_eligible?'This email is not eligible for automatic archiving.':'After three approvals, eligible emails are archived with a notification.'}`:'Autonomy follows the permitted-action policy.';
  const scope=`<label class="scope-label">Apply feedback to<select id="feedback-scope"><option value="general">This email pattern across all senders</option><option value="sender">This pattern only from ${esc(row.email.sender)}</option></select></label>`;
  let controls='';
  if(row.status==='pending')controls=`${p.action==='archive'?scope:''}<div class="decision-buttons"><button class="primary" id="approve">${reply?'Approve and send via Gmail':p.action==='send'?'Approve simulated send':(row.transport==='gmail'?'Approve Gmail archive':'Approve local archive')}</button><button class="secondary" id="reject">Reject</button></div>`;
  if(row.status==='executed'&&p.action==='archive'&&row.email.archived)controls=`${scope}<div class="decision-buttons"><button class="secondary" id="correct">Restore to inbox and correct</button></div>`;
  const gmailOps=(state.gmail_operations||[]).filter(o=>o.action_id===row.id);
  const operation=gmailOps.find(o=>['unknown','error'].includes(o.status));
  const transport=row.transport==='gmail'?'Gmail · real action':'Local simulation';
  const replyPreview=reply?`<div class="send-preview"><strong>Reply · version ${row.revision}</strong><p>From: ${esc(reply.sender)}<br>To: ${esc(reply.recipient)}<br>Subject: ${esc(reply.subject)}</p><p>${esc(reply.text)}</p>${row.status==='rejected'?'<p>Sending rejected. The unsent draft remains in Gmail.</p>':''}</div>`:'';
  const canEdit=row.status==='pending'&&(reply||p.action==='send');
  const editor=canEdit?`<details class="send-editor"><summary>Edit recipient, subject and body</summary><label>Recipient<input id="edit-recipient" type="email"></label>${reply?'<label>Subject<input id="edit-subject" maxlength="300"></label>':''}<label>Body<textarea id="edit-text" rows="5"></textarea></label><div class="decision-buttons"><button class="secondary" id="save-edit">Save new revision</button></div><p>Save changes and review the new version before approving.</p></details>`:'';

  if(operation)controls+=`<p role="alert">${esc(operation.error)}</p><button class="secondary" id="gmail-check">Check Gmail status (read only)</button>`;
  $('#detail').innerHTML=`<div class="detail-heading"><span>EMAIL # ${row.id}</span>${badge(row)}</div><h2>${esc(row.email.subject)}</h2><div class="sender-row"><span class="avatar">${esc(row.email.sender[0].toUpperCase())}</span><div>${esc(row.email.sender)}<small>${row.transport==='gmail'?'Source: connected Gmail':'Source: local inbox copy'}</small></div></div><p class="email-body">${esc(row.email.body)}</p><div class="decision"><p><strong>${esc(transport)}</strong></p><p>${esc(row.reason)}</p><div class="decision-title">✦ Agent decision · ${esc(actions[p.action]||p.action)}</div><p>${esc(p.reason)}</p><div class="decision-meta">${esc(explanation)}<br>Pattern: ${esc(patterns[p.pattern]||p.pattern)}${p.label?`<br>Label: ${esc(p.label)}`:''}${flags.length&&p.pattern_evidence?`<br>${esc(flags.map(f=>f[1]).join(' · '))}`:''}</div>${p.pattern_evidence?`<blockquote class="reason-quote">${esc(p.pattern_evidence)}</blockquote>`:''}${replyPreview}${!reply&&p.text?`<div class="send-preview">${p.recipient?`Recipient: ${esc(p.recipient)}<br>`:''}${esc(p.text)}</div>`:''}${controls}${editor}${labelReviewForm(row)}${attentionForm(row)}<div class="decision-buttons"><button class="secondary" id="keep-sender">Always keep mail from this sender</button></div></div><details class="history"><summary>Decision history · ${history.length} entries</summary>${history.map(a=>`<div class="history-item">${esc(events[a.event]||a.event)}<small>${esc(new Date(a.created_at).toLocaleString('en-US'))}</small><details><summary>Details</summary><pre>${esc(JSON.stringify(JSON.parse(a.details),null,2))}</pre></details></div>`).join('')}</details>`;
  $('#detail').scrollTop=previousDetailScroll;
  bindLabelReview(row);bindAttention(row);
  $('#gmail-check')?.addEventListener('click',async()=>{if(await post('/api/gmail-check',{operation_id:operation.id}))notify('Gmail status checked. Review the result above.')});
  const scopeValue=()=>$('#feedback-scope')?.value||'general';
  for(const [id,route,message] of [['approve','approve','Action approved'],['reject','reject','Action rejected'],['correct','correct','Email restored. Correction saved']]){
    $(`#${id}`)?.addEventListener('click',async()=>{const r=await post(`/api/${route}`,{action_id:row.id,revision:row.revision,scope:scopeValue()});if(r)notify(['executing','restoring'].includes(r.status)?'Gmail operation queued. Waiting for verification.':message)})
  }
  $('#keep-sender').addEventListener('click',async()=>{const r=await post('/api/rule',{sender:row.email.sender,keep:true});if(r)notify('Mail from this sender will stay in the inbox')});
  if($('#save-edit')){
    $('#edit-recipient').value=reply?reply.recipient:p.recipient;$('#edit-text').value=reply?reply.text:p.text;
    if(reply)$('#edit-subject').value=reply.subject;
    // Unsaved edits must never be mistaken for the displayed, saved approval version.
    const disableApproval=()=>{$('#approve').disabled=true;$('#approve').textContent='Save changes before approving'};
    for(const id of ['edit-recipient','edit-subject','edit-text'])$('#'+id)?.addEventListener('input',disableApproval);
    $('#save-edit').addEventListener('click',async()=>{const r=await post('/api/edit',{action_id:row.id,revision:row.revision,recipient:$('#edit-recipient').value,text:$('#edit-text').value,...(reply?{subject:$('#edit-subject').value}:{})});if(r)notify('New revision queued. Wait for draft verification, then review before approving')})
  }
}

function attentionForm(row){
 const item=(state.attention_items||[]).find(x=>x.action_id===row.id&&!x.seen);
 const scopes=row.attention_scopes||[];
 return `<form id="attention-form" class="label-review-form"><label class="attention-toggle"><input type="checkbox" id="attention-enabled" ${scopes.length?'checked':''}> Always bring this to my attention</label><p>Keep these emails visible in Needs attention. Matching future emails will not be archived automatically.</p><label>Apply attention preference to<select id="attention-scope"><option value="email">This email only · mark for attention now</option>${state.label_kinds[row.proposal.label_kind]?`<option value="similar">Future emails of this kind · ${esc(state.label_kinds[row.proposal.label_kind])}</option><option value="sender">This kind from ${esc(row.email.sender)}</option>`:''}</select></label><button class="secondary">Save attention preference</button>${item?'<button type="button" class="secondary" id="attention-seen">Mark as seen</button>':''}</form>`;
}
function bindAttention(row){
 const scopes=row.attention_scopes||[];
 if($('#attention-scope')){
   $('#attention-scope').value=scopes.includes('sender')?'sender':scopes.includes('similar')?'similar':'email';
   $('#attention-scope').addEventListener('change',()=>{$('#attention-enabled').checked=scopes.includes($('#attention-scope').value)});
 }
 $('#attention-form')?.addEventListener('submit',async e=>{e.preventDefault();if(await post('/api/attention',{action_id:row.id,enabled:$('#attention-enabled').checked,scope:$('#attention-scope').value}))notify('Attention preference saved')});
 $('#attention-seen')?.addEventListener('click',()=>post('/api/attention-seen',{action_id:row.id}));
}
function labelReviewForm(row){
  const r=row.labelReview;if(!r||row.status!=='executed')return '';
  const kind=state.label_kinds[r.kind];
  const names=[...new Set([...(state.labels||[]).map(x=>x.label),...(state.label_rules||[]).map(x=>x.label)])].sort();
  return `<form id="label-review-form" class="label-review-form"><div class="eyebrow">${r.status==='reviewed'?'REVIEWED · EDIT YOUR CHOICE':'YOUR LABEL REVIEW'}</div><h3>${esc(r.current_label)}</h3><p>${esc(r.basis)}. ${kind?`Situation: ${esc(kind)}.`:'The situation type is uncertain; this review applies to this email only.'}</p><label>Choose a label or type a new name<input id="review-label" list="available-labels" maxlength="100" required aria-label="Label name"><datalist id="available-labels">${names.map(n=>`<option value="${esc(n)}"></option>`).join('')}</datalist></label><small>Custom names are saved with the AI: prefix. Other Gmail labels, including human ready, stay in place.</small><label>Use this choice for<select id="review-scope"><option value="email">This email only</option>${kind?`<option value="similar">Future similar emails · ${esc(kind)}</option><option value="sender">This situation from ${esc(row.email.sender)} only</option>`:''}</select></label><p class="review-scope-note" id="review-scope-note">Only this email changes. No preference will be saved.</p><button class="primary" id="save-label-review">Confirm label &amp; continue →</button><p class="review-save-note">${row.transport==='gmail'?'Saved to Gmail and verified before the review is marked complete.':'This is a local simulation.'}</p></form>`;
}
function bindLabelReview(row){
  if(!$('#label-review-form'))return;
  $('#review-label').value=row.labelReview.current_label;
  $('#review-label').addEventListener('input',()=>{$('#save-label-review').textContent=$('#review-label').value.trim()===row.labelReview.current_label?'Confirm label & continue →':'Save label & continue →'});
  $('#review-scope').addEventListener('change',()=>{$('#review-scope-note').textContent=$('#review-scope').value==='email'?'Only this email changes. No preference will be saved.':'Save an explicit preference for this situation. Existing emails will not be relabeled automatically; sending and archiving permissions stay unchanged.'});
  $('#label-review-form').addEventListener('submit',async e=>{
    e.preventDefault();const button=$('#save-label-review');button.disabled=true;
    const result=await post('/api/label-review',{action_id:row.id,revision:row.revision,label:$('#review-label').value,scope:$('#review-scope').value});
    if(result){
      if(['label_review','label_reviewed'].includes(filter)){
        const next=currentRows().filter(x=>x.labelReview?.status==='needs_review'&&x.status==='executed'&&x.id!==row.id).sort((a,b)=>a.id-b.id)[0];
        selected=next?.id;filter='label_review';renderMail();
      }
      notify(result.status==='executing'?'Saving your label in Gmail. The review completes after verification.':'Label reviewed. Your choice has been saved.');
    }else{button.disabled=false}
  });
}

function renderMemory(){
  if(!state)return;
  $('#label-rules').innerHTML=(state.label_rules||[]).filter(r=>r.active).map(r=>`<div class="rule-row"><span><strong>${esc(r.label)}</strong><br>${esc(state.label_kinds[r.kind]||r.kind)} · ${esc(r.scope==='*'?'All senders':r.scope)}<br><small>Enabled by your review #${r.feedback_id}</small></span><button class="secondary" data-pause-label="${r.id}">Pause</button></div>`).join('')||'<p>No saved label preferences yet. Choose “Future similar emails” when reviewing a label.</p>';
  $('#label-rules').querySelectorAll('button').forEach(b=>b.addEventListener('click',async()=>{if(await post('/api/label-rule-pause',{rule_id:Number(b.dataset.pauseLabel)}))notify('Label preference paused. Review a similar email to set a new preference.')}));
  const groups=new Map();for(const f of state.preference_feedback){const key=JSON.stringify([f.scope,f.pattern]);if(!groups.has(key))groups.set(key,{scope:f.scope,pattern:f.pattern,count:0});const group=groups.get(key);group.count=f.positive?group.count+1:0}
  $('#memory-groups').innerHTML=groups.size?[...groups.values()].map(g=>`<div class="memory-card"><h2>${esc(patterns[g.pattern]||g.pattern)}</h2><p class="scope-name">${g.scope==='*'?'General experience · all senders':esc(g.scope)}</p><strong>${g.count} <small>/ 3</small></strong><p>${g.count>=3?'Enough experience for eligible emails. Exceptions and risk signals are checked separately.':'The agent will keep asking. Explicit approvals are needed.'}</p></div>`).join(''):'<div class="memory-card"><h2>Learning your preferences</h2><p>Approve or correct archiving decisions. Experience for each semantic pattern will appear here.</p></div>';
  $('#rules').innerHTML=state.archive_rules.map((r,i)=>`<div class="rule-row"><span>${esc(r.scope==='*'?'All senders':r.scope)} · ${esc(r.pattern==='*'?'All email patterns':patterns[r.pattern])}</span><button class="secondary" data-remove-rule="${i}">Remove exception</button></div>`).join('');
  $('#rules').querySelectorAll('button').forEach(b=>b.addEventListener('click',async()=>{const r=state.archive_rules[Number(b.dataset.removeRule)];if(await post('/api/rule',{sender:r.scope,pattern:r.pattern,keep:false}))notify('Exception removed')}));
}
function navigate(next){page=next;$('#mail-view').classList.toggle('hidden',page!=='mail');$('#memory-view').classList.toggle('hidden',page!=='memory');$('#nav-mail').classList.toggle('active',page==='mail');$('#nav-memory').classList.toggle('active',page==='memory')}
$('#nav-mail').addEventListener('click',()=>{navigate('mail');setFilter('all')});$('#back-mail').addEventListener('click',()=>navigate('mail'));$('#nav-memory').addEventListener('click',()=>navigate('memory'));
$('#nav-labels').addEventListener('click',()=>{navigate('mail');$('#search').value='';setFilter('label_review')});
$('#show-unreviewed').addEventListener('click',()=>setFilter('label_review'));
$('#show-reviewed').addEventListener('click',()=>setFilter('label_reviewed'));
document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>setFilter(b.dataset.filter)));
$('#search').addEventListener('input',renderMail);$('#refresh').addEventListener('click',()=>refresh(true));
$('#new-email').addEventListener('click',()=>$('#compose').showModal());$('#close-compose').addEventListener('click',()=>$('#compose').close());
$('#compose-form').addEventListener('submit',async e=>{e.preventDefault();$('#submit-email').disabled=true;try{const r=await post('/api/ingest',Object.fromEntries(new FormData(e.target)));if(r){$('#compose').close();e.target.reset();navigate('mail');setFilter('all');notify('Email queued for agent analysis')}}finally{$('#submit-email').disabled=false}});
$('#load-demo').addEventListener('click',async()=>{if(await post('/api/demo',{}))notify('Sample cases loaded. Loading again does not duplicate actions')});
$('#rule-form').addEventListener('submit',async e=>{e.preventDefault();const sender=new FormData(e.target).get('sender');if(await post('/api/rule',{sender,keep:true})){e.target.reset();notify('Exception saved')}});
let gmailContext='';
function renderGmail(){
  const g=state.gmail_connection;if(!g)return;
  $('#gmail-strip').classList.toggle('hidden',state.mode==='scripted');
  const running=Boolean(g.operation), connected=g.status==='connected';
  const context=JSON.stringify([g.account,g.live,g.access]);
  if(context!==gmailContext){$('#gmail-consent').checked=false;gmailContext=context}
  $('#gmail-summary').textContent=connected?g.account:g.token_present?'Gmail connection saved':'Connect your Gmail';
  $('#gmail-auth-link').classList.toggle('hidden',!g.auth_url);if(g.auth_url)$('#gmail-auth-link').href=g.auth_url;
  $('#gmail-summary-note').textContent=running?({connect:'Waiting for Google sign-in…',check:'Checking your connection…',sync:'Syncing selected emails…'}[g.operation]):connected?'Wajo-Test · '+(g.live?'Gmail actions enabled':'Local simulation'):'Connect an account and sync selected emails.';
  $('#open-gmail').textContent=connected?'Gmail settings':g.token_present?'Check Gmail':'Connect Gmail';
  $('#gmail-title').textContent=connected?'Your Gmail connection':'Connect Gmail';
  $('#gmail-account').textContent=connected?g.account:g.token_present?'Saved connection · verification needed':'No Gmail account connected';
  $('#gmail-access').textContent=connected?(g.access==='manage'?'Read and manage access':'Read-only access'):'Check your connection or sign in with Google.';
  $('#gmail-progress').textContent=running?({connect:'Continue in your system browser. Google sign-in can take up to three minutes.',check:'Verifying access and the Wajo-Test label…',sync:'Reading selected emails and adding new ones to the analysis queue…'}[g.operation]):g.checked_at&&connected?'Connection checked '+new Date(g.checked_at).toLocaleTimeString('en-US'):'';
  $('#gmail-error').textContent=g.error||'';$('#gmail-error').classList.toggle('hidden',!g.error);
  $('#gmail-connect').textContent=g.token_present?'Reconnect with Google':'Connect with Google';
  $('#gmail-connect').disabled=running||!g.client_ready;$('#gmail-status').disabled=running||!g.token_present;
  if(!g.client_ready&&!g.token_present)$('#gmail-setup').open=true;
  $('#gmail-mode').textContent=g.live?'Gmail · real actions':'Local simulation';
  $('#gmail-scope-note').textContent=connected?(g.label_ready?'Wajo-Test found. Only emails in this label will be read.':'Create the Wajo-Test label in Gmail, add synthetic emails, then check the connection again.'):'Verify your account to enable sync.';
  $('#gmail-effects').textContent=g.live?'New emails may receive AI labels, be archived, or have replies saved as Gmail drafts under your permissions. Sending always requires approval of the exact saved version.':'Agent actions on new imports are simulated locally. Gmail messages will not be changed.';
  if(connected&&g.live&&g.access!=='manage')$('#gmail-scope-note').textContent='Reconnect with Google to grant access for live mail actions.';
  $('#gmail-sync').disabled=running||!connected||!g.label_ready||(g.live&&g.access!=='manage')||!$('#gmail-consent').checked;
  $('#gmail-limit').disabled=running;$('#gmail-consent').disabled=running;
  const r=g.last_sync;$('#gmail-result').classList.toggle('hidden',!r);
  if(r)$('#gmail-result').textContent=`Last completed sync · ${new Date(r.completed_at).toLocaleString('en-US')}. ${r.queued} queued for analysis · ${r.already_imported} already imported · ${r.manual_review} need manual review.${r.more_available?' More emails exist beyond this page. Pagination is not available yet.':''} These counts describe import, not the quality of agent decisions.`;
}
$('#open-gmail').addEventListener('click',async()=>{
  $('#gmail-dialog').showModal();
  if(state.gmail_connection.token_present&&!state.gmail_connection.operation)await post('/api/gmail/status',{});
});
$('#close-gmail').addEventListener('click',()=>$('#gmail-dialog').close());
async function gmailPost(path,data){
  const result=await post(path,data);
  if(!result){$('#gmail-error').textContent=$('#error').textContent||'Please wait for the current operation to finish.';$('#gmail-error').classList.remove('hidden')}
  return result;
}
$('#gmail-connect').addEventListener('click',()=>gmailPost('/api/gmail/connect',{}));
$('#gmail-status').addEventListener('click',()=>gmailPost('/api/gmail/status',{}));
$('#gmail-consent').addEventListener('change',renderGmail);
$('#gmail-sync-form').addEventListener('submit',async e=>{
  e.preventDefault();const g=state.gmail_connection;
  await gmailPost('/api/gmail/sync',{allow_groq:$('#gmail-consent').checked,account:g.account,live:g.live,limit:Number($('#gmail-limit').value)});
});
refresh();setInterval(()=>refresh(),2500);
