'use strict';
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const patterns = {acknowledgement_only:'Receipt acknowledgement',periodic_digest:'Periodic digest',routine_success:'Routine success report',informational_reference:'Informational reference',unknown:'Unknown pattern'};
const autonomies = {silent:'No notification',notify:'Notify',ask:'Ask for approval',escalate:'Escalate'};
const actions = {archive:'Archive email',label:'Apply label',draft:'Save draft',send:'Send reply',none:'No mail action',pay:'Payment request',delete:'Deletion request'};
const statuses = {executing:'Gmail action queued',restoring:'Gmail restore queued',unknown:'Verification needed',pending:'Approval needed',executed:'Completed',blocked:'Blocked',escalated:'Needs your review',error:'Processing error',skipped:'Kept in inbox',rejected:'Rejected',corrected:'Restored to inbox'};
const events = {gmail_draft_saved:'Gmail draft saved',gmail_queued:'Gmail operation queued',gmail_started:'Gmail verification started',gmail_unknown:'Verification needed',gmail_error:'Gmail operation stopped',gmail_unverified:'Gmail state not confirmed',decision:'Agent decision',approved:'You approved the action',rejected:'You rejected the action',executed:'Action completed',notification:'Notification',preference_feedback:'Feedback saved',learned_permission:'Learned preference applied',archive_corrected:'Archive corrected',revised:'Reply revised',organization_reviewed:'Organization reviewed',organization_preference_applied:'Organization preference applied',organization_rule_paused:'Organization preference paused',draft_style_saved:'Draft style saved',draft_style_applied:'Draft style applied',draft_style_fallback:'Draft style rewrite unavailable',draft_style_rule_paused:'Draft style paused'};
let state, selected, selectedAutosent, filter='all', page='mail', signature='', busy=false, detailOpen=false;
let calendarCursor=new Date(new Date().getFullYear(),new Date().getMonth(),1),modelValidation={mode:null,key:null,valid:false,token:null},modelValidationTimer,modelValidationRequest=0,modelsInitialized=false;
function notify(text){$('#toast').textContent=text;$('#toast').classList.remove('hidden');setTimeout(()=>$('#toast').classList.add('hidden'),5000)}
function error(text){$('#error').textContent=text;$('#error').classList.toggle('hidden',!text)}
async function post(path, data){
  if(busy) return null;
  busy=true;error('');
  try {
    const feedbackPaths=['/api/attention','/api/organization','/api/label-review','/api/draft-style','/api/approve','/api/reject','/api/correct','/api/edit'];
    const previousSkills=new Set((state.skills||[]).map(s=>s.id));
    const payload={...data};if(feedbackPaths.includes(path))payload.propose_skill=true;
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify(payload)});
    const result=await response.json();if(!response.ok)throw Error(result.error||'Operation failed');
    await refresh(true);
    if(feedbackPaths.includes(path)){
      const suggestion=(state.skills||[]).find(s=>s.source_id===data.action_id&&s.status==='suggested'&&!previousSkills.has(s.id));
      if(suggestion)await openSkillReview({skill_id:suggestion.id,scope:'similar'});
    }
    return result;
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
function setFilter(value){filter=value;detailOpen=false;renderMail()}
function mailTime(emailId){const message=(state.gmail_messages||[]).find(m=>`gmail:${m.account}:${m.message_id}`===emailId);return Number(message?.internal_date)||Date.parse((state.jobs||[]).find(j=>j.id===emailId)?.created_at)||0}
function newestFirst(a,b){return b.timestamp-a.timestamp||String(a.email.id).localeCompare(String(b.email.id))}
function currentRows(){return state.actions.map(a=>({...a,email:state.emails.find(e=>e.id===a.email_id),timestamp:mailTime(a.email_id),labelReview:(state.label_reviews||[]).find(r=>r.action_id===a.id),threadId:a.thread_id||a.email_id})).filter(a=>a.email).sort(newestFirst)}
function waitingRows(actions){const processed=new Set(actions.map(a=>a.email_id));return (state.jobs||[]).filter(j=>j.status!=='done'&&!processed.has(j.id)).map(job=>{const email=JSON.parse(job.email),message=(state.gmail_messages||[]).find(m=>`gmail:${m.account}:${m.message_id}`===job.id);return {id:'job:'+job.id,email,job,status:job.status,account:message?.account||'local',threadId:message?.thread_id||job.id,timestamp:mailTime(job.id)}})}
function jobStatus(row){return row.status==='error'?'Analysis failed':row.status==='processing'?'Reading email…':'Queued for analysis'}
function needsDecision(row){return row.status==='pending'||Boolean(row.awaiting_event_decision)}
function groupThreads(rows){const groups=[];for(const row of rows){const key=`${row.account||'local'}:${row.threadId}`;let group=groups.find(x=>x.key===key);if(!group){group={key,threadId:row.threadId,representative:row,members:[]};groups.push(group)}group.members.push(row)}return groups}
function badge(row){const replyState=row.reply?(row.status==='executing'?'Saving or sending Gmail reply':row.status==='pending'?'Gmail draft · approval needed':row.status==='executed'?'Sent via Gmail':null):null;const eventState=row.awaiting_event_decision?'Awaiting event decision':null;const style=['blocked','error','unknown'].includes(row.status)?row.status:row.autonomy;return `<span class="badge ${esc(style)}">${esc(eventState||replyState||statuses[row.status]||autonomies[row.autonomy])}</span>`}
function render(){
  const demo=state.mode==='scripted',provider=modelConfig().label||'Bundled free Groq';$('#mode').textContent=demo?'Sample cases · no AI':`${provider}${state.gmail_enabled?' · Gmail live':' · local'}`;
  $('#new-email').classList.toggle('hidden',demo);$('#load-demo').classList.toggle('hidden',!demo);
  $('#mode-note').textContent=demo?'Sample cases: emails and decisions are predefined; no AI model is called. All mail actions are simulated locally.':state.gmail_enabled?`Gmail live · ${provider}: synchronized incoming mail can receive AI labels, be archived, or have replies saved as Gmail drafts. Sending follows the separate approval and Superpowers policy.`:`Local mode · ${provider}: model requests use the selected provider. Gmail writes remain paused until the server is started without --local-simulation.`;
  $('#compose-provider-note').textContent=`The sender, subject and body will be sent to ${provider} for analysis. Use synthetic data.`;
  $('#gmail-consent-copy').textContent=`I allow the sender, subject and body of mail in this scope to be sent to ${provider} for analysis. Email remains local except for selected model requests.`;
  $('#nav-count').textContent=state.emails.length;
  const suggestions=(state.skills||[]).filter(s=>s.status==='suggested').length;
  $('#skill-suggestion-count').textContent=suggestions;
  $('#skill-suggestion-count').classList.toggle('hidden',!suggestions);
  renderMail();renderMemory();renderGmail();renderCalendar();renderModels();renderSuperpowers();
}
function renderMail(){
  if(!state)return;
  const actions=currentRows(),all=[...actions,...waitingRows(actions)].sort(newestFirst);$('#count-all').textContent=all.length;
  const labelRows=all.filter(r=>r.labelReview);const reviewed=labelRows.filter(r=>r.labelReview.status==='reviewed').length;
  $('#label-review-count').textContent=labelRows.length-reviewed;
  const reviewMode=['label_review','label_reviewed'].includes(filter);
  $('#label-review-banner').classList.toggle('hidden',!reviewMode);
  $('#label-review-progress').textContent=`${reviewed} of ${labelRows.length} reviewed`;
  $('#nav-labels').classList.toggle('active',reviewMode&&page==='mail');
  $('#nav-mail').classList.toggle('active',!reviewMode&&page==='mail');
  $('#count-pending').textContent=all.filter(needsDecision).length;
  $('#count-archived').textContent=all.filter(r=>r.email.archived).length;
  const attentionIds=new Set((state.attention_items||[]).filter(x=>!x.seen).map(x=>x.action_id));
  $('#count-attention').textContent=all.filter(r=>attentionIds.has(r.id)).length;
  $('#count-escalated').textContent=all.filter(r=>r.status==='escalated').length;
  const listScroll=$('#email-list').scrollTop;
  const search=$('#search').value.toLocaleLowerCase();
  const rows=all.filter(r=>(filter==='all'||(filter==='errors'&&['error','unknown'].includes(r.status))||(filter==='label_review'&&r.labelReview&&r.labelReview.status!=='reviewed')||(filter==='label_reviewed'&&r.labelReview?.status==='reviewed')||(filter==='pending'&&needsDecision(r))||(filter==='archived'&&r.email.archived)||(filter==='attention'&&attentionIds.has(r.id))||(filter==='escalated'&&r.status==='escalated'))&&(`${r.email.subject} ${r.email.sender}`).toLocaleLowerCase().includes(search));
  const threads=groupThreads(rows),incomplete=(state.gmail_messages||[]).filter(x=>x.state==='incomplete');
  $('#list-count').textContent=threads.length;
  document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('selected',b.dataset.filter===filter));
  $('#filter-label').classList.toggle('hidden',!['attention','escalated'].includes(filter));$('#filter-label').textContent=filter==='attention'?'Showing emails matched by your explicit visibility preferences. Importance and escalation are separate.':filter==='escalated'?'Showing situations where the agent cannot safely continue without human judgment.':' ';
  if(!rows.some(r=>r.id===selected)){selected=threads[0]?.representative.id;detailOpen=false}
  $('.mailbox').classList.toggle('detail-open',detailOpen);
  const cards=threads.map(group=>{const r=group.representative;const pending=group.members.filter(x=>needsDecision(x)||['escalated','unknown','error'].includes(x.status)).length;return {timestamp:r.timestamp,html:`<button class="email-item ${r.id===selected?'selected':''}" data-id="${esc(r.id)}"><div class="email-top"><span class="email-sender">${esc(r.email.sender)}</span><span class="thread-count">${group.members.length} message${group.members.length===1?'':'s'}${pending?' · '+pending+' need review':''}</span></div><h3>${esc(r.email.subject)}</h3>${r.timestamp?`<small>${esc(new Date(r.timestamp).toLocaleString('en-US'))}</small>`:''}${r.organization?`<p class="organization-line">${esc(r.organization.topic)} · ${esc(r.organization.subtype)}${r.organization.important?' · Important':''}</p>`:''}<p class="email-preview">${esc(r.email.body)}</p>${r.job?`<span class="badge ${r.status==='error'?'error':''}">${jobStatus(r)}</span>`:r.labelReview?`<span class="badge label-chip">${esc(r.labelReview.current_label)}</span> <span class="badge">${r.labelReview.status==='reviewed'?'✓ Reviewed':r.status==='executed'?'To review':esc(statuses[r.status]||r.status)}</span>`:badge(r)}</button>`}});
  const broken=['all','errors'].includes(filter)&&!search?incomplete.map(x=>({timestamp:Number(x.internal_date)||0,html:`<div class="email-item incomplete-email" aria-disabled="true"><div class="email-top"><span class="email-sender">! Incomplete Gmail message</span><span class="thread-count">Retry after ${x.retry_at?esc(new Date(x.retry_at).toLocaleTimeString('en-US')):'the next sync'}</span></div><h3>Message unavailable</h3><p class="email-preview">Wajo could not fully load this message. It cannot be opened or analyzed yet.</p></div>`})):[];
  $('#list-count').textContent=threads.length+broken.length;
  $('#email-list').innerHTML=[...cards,...broken].sort((a,b)=>b.timestamp-a.timestamp).map(c=>c.html).join('')||'<div class="empty-list">No matching conversations.<br>Add an email or change the filter.</div>';
  $('#email-list').scrollTop=listScroll;
  $('#email-list').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{selected=b.dataset.id.startsWith('job:')?b.dataset.id:Number(b.dataset.id);detailOpen=true;renderMail();$('.mailbox').scrollIntoView({block:'start'})}));
  const row=all.find(r=>r.id===selected);
  if(row?.job){$('#detail').innerHTML=`<button class="secondary detail-back" id="back-conversations">← All conversations</button><h2>${esc(row.email.subject)}</h2><p>${esc(row.email.sender)}</p><span class="badge">${jobStatus(row)}</span>${conversationView(row)}<p class="email-body">${esc(row.email.body)}</p>${row.status==='error'?`<details><summary>Error details and attempts</summary><pre>${esc(row.job.diagnostics)}</pre></details>`:''}`;$('#back-conversations').addEventListener('click',()=>{detailOpen=false;renderMail()})}else renderDetail(row);
  $('#detail').querySelectorAll('[data-conversation-id]').forEach(button=>button.addEventListener('click',()=>{const id=button.dataset.conversationId;selected=id.startsWith('job:')?id:Number(id);renderMail()}));
}
function conversationView(row){
  const actions=currentRows();const members=[...actions,...waitingRows(actions)].filter(x=>x.threadId===row.threadId&&x.account===row.account).sort(newestFirst).reverse();
  const context=(state.gmail_messages||[]).filter(x=>x.thread_id===row.threadId&&x.account===row.account&&['sent','draft'].includes(x.role));
  if(members.length+context.length<=1)return '';
  const items=[...members.map(x=>({key:'action-'+x.id,timestamp:x.timestamp,role:'incoming',sender:x.email.sender,subject:x.email.subject,body:x.email.body,open:x.id===row.id||['pending','escalated','unknown','error'].includes(x.status),id:x.id,note:x.job?jobStatus(x):statuses[x.status]||autonomies[x.autonomy]})),...context.map(x=>({key:x.role+'-'+x.message_id,timestamp:Number(x.internal_date)||0,role:x.role,sender:x.sender,subject:x.subject,body:x.body,open:false,note:x.role==='draft'?(x.managed?'Draft prepared by Wajo':'Your Gmail draft · context only'):'Sent message · context only'}))].sort((a,b)=>a.timestamp-b.timestamp);
  return `<section class="conversation"><h3>Conversation · ${items.length} messages</h3>${items.map(x=>`<details ${x.open?'open':''}><summary><span>${esc(x.sender)}</span><strong>${esc(x.note)}</strong></summary><small>${esc(x.subject)}</small><p>${esc(x.body)}</p>${x.id!==undefined&&x.id!==row.id?`<button class="secondary" data-conversation-id="${esc(x.id)}">Open message and status</button>`:''}</details>`).join('')}</section>`;
}
function eventProposalBlocks(row){
  const proposals=(state.event_proposals||[]).filter(item=>String(item.source_email_id)===String(row.email_id));
  return proposals.map(item=>{
    const status=item.status==='awaiting_confirmation'?'Awaiting your decision':item.status==='needs_clarification'?'Needs a clear date':item.status==='approved'?'Added to calendar':item.status==='rejected'?'Not added':item.status;
    const timed=item.local_start||item.start_utc,when=item.all_day?(item.local_date||'Date not resolved'):(timed?new Date(timed).toLocaleString('en-US',{weekday:'long',year:'numeric',month:'long',day:'numeric',hour:'numeric',minute:'2-digit'}):'Date and time not resolved');
    const decision=item.status==='awaiting_confirmation'?`<div class="event-proposal-actions"><button class="primary" data-event-approve="${esc(item.id)}" data-revision="${esc(item.revision)}" aria-label="Add ${esc(item.title)} to calendar">✓ Add to calendar</button><button class="secondary" data-event-reject="${esc(item.id)}" data-revision="${esc(item.revision)}" aria-label="Do not add ${esc(item.title)} to calendar">× Don’t add</button></div>`:'';
    const clarification=item.status==='needs_clarification'?`<form class="event-clarify" data-event-clarify="${esc(item.id)}" data-revision="${esc(item.revision)}"><p class="event-ambiguity">${esc(item.ambiguity_reason||'Choose an exact date and time.')}</p><label class="attention-toggle"><input type="checkbox" name="all_day"> All-day event</label><div class="field-pair"><label>Starts<input type="datetime-local" name="start_at" required></label><label>Ends · optional<input type="datetime-local" name="end_at"></label></div><label>Time zone<input name="timezone" required value="${esc(Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC')}"></label><button class="secondary">Use this date</button><small>You will still confirm the revised event separately.</small></form>`:'';
    return `<section class="event-proposal"><div class="event-proposal-head"><div><div class="eyebrow">EVENT FOUND</div><h3>${esc(item.title)}</h3></div><span class="badge ${item.status==='needs_clarification'?'escalate':'notify'}">${esc(status)}</span></div><p>${esc(item.original_text||item.evidence||'')}</p><strong>${esc(when)}</strong>${clarification}${decision}</section>`;
  }).join('');
}
function bindEventProposals(){
  $('#detail').querySelectorAll('[data-event-approve]').forEach(button=>button.addEventListener('click',async()=>{if(await post('/api/events/approve',{proposal_id:Number(button.dataset.eventApprove),revision:Number(button.dataset.revision)}))notify('Event added to your local calendar.')}));
  $('#detail').querySelectorAll('[data-event-reject]').forEach(button=>button.addEventListener('click',async()=>{if(await post('/api/events/reject',{proposal_id:Number(button.dataset.eventReject),revision:Number(button.dataset.revision)}))notify('This event was not added. Wajo will keep asking in similar situations.')}));
  $('#detail').querySelectorAll('[data-event-clarify]').forEach(form=>{
    const allDay=form.elements.all_day,start=form.elements.start_at,end=form.elements.end_at,timezone=form.elements.timezone;
    const syncType=()=>{start.type=allDay.checked?'date':'datetime-local';end.type=allDay.checked?'date':'datetime-local';timezone.disabled=allDay.checked};allDay.addEventListener('change',syncType);
    form.addEventListener('submit',async event=>{event.preventDefault();const payload={proposal_id:Number(form.dataset.eventClarify),revision:Number(form.dataset.revision),all_day:allDay.checked};if(allDay.checked){payload.local_date=start.value;payload.local_end_date=end.value}else{payload.start_at=start.value;payload.end_at=end.value;payload.timezone=timezone.value}if(await post('/api/events/clarify',payload))notify('Date clarified. Review it once more before adding it.')});
  });
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
  const readState=row.transport==='gmail'?(row.initial_unread?' · Unread when synchronized':' · Already read in Gmail'):'';
  $('#detail').innerHTML=`<div class="detail-heading"><span>EMAIL # ${row.id}${readState}</span>${badge(row)}</div><h2>${esc(row.email.subject)}</h2>${conversationView(row)}<div class="organization-summary"><span>${esc(row.organization.topic)}</span><span>${esc(row.organization.subtype)}</span>${row.organization.important?'<span class="important-chip">Important</span>':''}<small>${esc(row.organization.source)}</small></div><div class="sender-row"><span class="avatar">${esc(row.email.sender[0].toUpperCase())}</span><div>${esc(row.email.sender)}<small>${row.transport==='gmail'?'Source: connected Gmail':'Source: local inbox copy'}</small></div></div><p class="email-body">${esc(row.email.body)}</p>${eventProposalBlocks(row)}<div class="decision"><p><strong>${esc(transport)}</strong></p><p>${esc(row.reason)}</p><div class="decision-title">✦ Agent decision · ${esc(actions[p.action]||p.action)}</div><p>${esc(p.reason)}</p><div class="decision-meta">${esc(explanation)}<br>Pattern: ${esc(patterns[p.pattern]||p.pattern)}${p.label?`<br>Label: ${esc(p.label)}`:''}${flags.length&&p.pattern_evidence?`<br>${esc(flags.map(f=>f[1]).join(' · '))}`:''}</div>${p.pattern_evidence?`<blockquote class="reason-quote">${esc(p.pattern_evidence)}</blockquote>`:''}${replyPreview}${!reply&&p.text?`<div class="send-preview">${p.recipient?`Recipient: ${esc(p.recipient)}<br>`:''}${esc(p.text)}</div>`:''}${controls}${editor}${draftStyleForm(row)}${organizationForm(row)}${labelReviewForm(row)}${attentionForm(row)}<div class="decision-buttons"><button class="secondary" id="keep-sender">Always keep mail from this sender</button></div></div><details class="history"><summary>Decision history · ${history.length} entries</summary>${history.map(a=>`<div class="history-item">${esc(events[a.event]||a.event)}<small>${esc(new Date(a.created_at).toLocaleString('en-US'))}</small><details><summary>Details</summary><pre>${esc(JSON.stringify(JSON.parse(a.details),null,2))}</pre></details></div>`).join('')}</details>`;
  $('#detail').scrollTop=previousDetailScroll;
  $('#detail').insertAdjacentHTML('afterbegin','<button class="secondary detail-back" id="back-conversations">← All conversations</button>');
  $('#back-conversations').addEventListener('click',()=>{detailOpen=false;renderMail();$('.mailbox').scrollIntoView({block:'start'})});
  bindEventProposals();bindDraftStyle(row);bindOrganization(row);bindLabelReview(row);bindAttention(row);
  renderLabelConflict(row);
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

function draftStyleForm(row){
  const p=row.draft_style_preview;
  if(!p)return row.draft_style_note?`<div class="send-preview"><strong>${row.draft_style_saved?'Draft style saved':'Draft style not learned'}</strong><br>${esc(row.draft_style_note)}</div>`:'';
  const confirmed=p.basis==='confirmed';
  const wording=confirmed?'':`<div class="style-example"><small>Agent wording</small><p>${esc(p.example_before)}</p><small>Your wording</small><p>${esc(p.example_after)}</p></div>`;
  return `<form id="draft-style-form" class="label-review-form"><div class="eyebrow">${confirmed?'CONFIRM DRAFT STYLE':'LEARN FROM YOUR EDIT'}</div><h3>${confirmed?'Does this style work for you?':'Your draft style'}</h3><p>${esc(p.summary)}</p>${wording}<small>${confirmed?'The body matches the agent’s suggestion. Save this only if its writing style is what you want for similar drafts.':'Wajo will also use this wording change as an example of your tone. Situation-specific facts, recipients and promises are not copied.'} Sending always requires approval.</small><label>Use this style for<select id="draft-style-scope"><option value="similar">Future drafts for this kind of email</option><option value="sender">This kind of email from ${esc(row.email.sender)} only</option></select></label><button class="secondary">${confirmed?'This style works for me':'Use this style for future drafts'}</button></form>`;
}
function bindDraftStyle(row){
  $('#draft-style-form')?.addEventListener('submit',async e=>{
    e.preventDefault();
    const existing=(state.skills||[]).find(s=>s.family==='draft'&&s.source_id===row.id&&s.status==='suggested');
    if(existing){await openSkillReview({skill_id:existing.id,scope:$('#draft-style-scope').value});return}
    const r=await post('/api/draft-style',{action_id:row.id,revision:row.revision,scope:$('#draft-style-scope').value});
    if(r)notify('Draft style suggestion is ready for review. Every send still requires approval.');
  });
}

function organizationForm(row){
  const known=Boolean(state.label_kinds[row.proposal.label_kind]);
  return `<details class="organization-editor"><summary>Organize this email</summary><form id="organization-form" class="compact-form"><div class="field-pair"><label>Topic<input id="organization-topic" maxlength="60" required></label><label>Subtype<input id="organization-subtype" maxlength="60" required></label></div><label class="attention-toggle"><input type="checkbox" id="organization-important"> Mark as important</label><small>Importance is your separate marker. It does not approve an action or turn on attention alerts.</small><label>Use this organization for<select id="organization-scope"><option value="email">This email only</option>${known?`<option value="similar">Future emails of this kind · ${esc(state.label_kinds[row.proposal.label_kind])}</option><option value="sender">This kind from ${esc(row.email.sender)} only</option>`:''}</select></label><button class="secondary">Save organization</button></form></details>`;
}
function bindOrganization(row){
  if(!$('#organization-form'))return;
  $('#organization-topic').value=row.organization.topic;
  $('#organization-subtype').value=row.organization.subtype;
  $('#organization-important').checked=row.organization.important;
  $('#organization-form').addEventListener('submit',async e=>{
    e.preventDefault();
    const result=await post('/api/organization',{action_id:row.id,topic:$('#organization-topic').value,subtype:$('#organization-subtype').value,important:$('#organization-important').checked,scope:$('#organization-scope').value});
    if(result)notify('This email was updated. Future behavior stays inactive until you save the suggested skill.');
  });
}

function attentionForm(row){
 const item=(state.attention_items||[]).find(x=>x.action_id===row.id&&!x.seen);
 const effective=row.attention_effective_rule;
 const scopes=effective?.enabled?[effective.scope]:[];
 const cue=!['none','unknown'].includes(row.attention_cue)?state.attention_cues?.[row.attention_cue]:null;
 return `<form id="attention-form" class="label-review-form"><div class="eyebrow">VISIBILITY PREFERENCE</div><label class="attention-toggle"><input type="checkbox" id="attention-enabled" ${scopes.length?'checked':''}> Keep this in Needs attention</label><p>This controls what you want to see. It does not mark the email Important, create a notification, change escalation, or approve an action. Matching future emails will not be archived automatically.</p><label>Use this visibility preference for<select id="attention-scope"><option value="email">This email only</option>${cue?`<option value="similar">Future emails with this reason · ${esc(cue)}</option><option value="sender">This reason from ${esc(row.email.sender)}</option>`:''}</select></label><button class="secondary">Save visibility preference</button>${item?'<button type="button" class="secondary" id="attention-seen">Clear from Needs attention</button>':''}</form>`;
}
function bindAttention(row){
 const rule=row.attention_effective_rule;
 const scopes=rule?[rule.scope==='*'?'similar':rule.scope.startsWith('email:')?'email':'sender']:[];
 if($('#attention-scope')){
   $('#attention-scope').value=scopes.includes('sender')?'sender':scopes.includes('similar')?'similar':'email';
 }
 $('#attention-form')?.addEventListener('submit',async e=>{
   e.preventDefault();
   const payload={action_id:row.id,enabled:$('#attention-enabled').checked,scope:$('#attention-scope').value};
   if(await post('/api/attention',payload))notify('This email was updated. Future behavior stays inactive until you save the suggested skill.');
 });
 $('#attention-seen')?.addEventListener('click',()=>post('/api/attention-seen',{action_id:row.id}));
}
function labelReviewForm(row){
  const r=row.labelReview;if(!r||row.status!=='executed')return '';
  const kind=state.label_kinds[r.kind];
  const names=[...new Set([...(state.labels||[]).map(x=>x.label),...(state.label_rules||[]).map(x=>x.label)])].sort();
  return `<form id="label-review-form" class="label-review-form"><div class="eyebrow">${r.status==='reviewed'?'REVIEWED · EDIT YOUR CHOICE':'YOUR LABEL REVIEW'}</div><h3>${esc(r.current_label)}</h3><p>${esc(r.basis)}. ${kind?`Situation: ${esc(kind)}.`:'The situation type is uncertain; this review applies to this email only.'}</p><p>Current labels: ${esc((state.labels||[]).filter(x=>x.email_id===row.email_id).map(x=>x.label).join(' · '))}</p><label>Review action<select id="review-mode"><option value="replace">Replace / confirm current label</option><option value="add">Add another label · keep existing labels</option></select></label><p id="review-mode-note">Replaces ${esc(r.current_label)} only. Other labels stay.</p><label>Choose a label or type a new name<input id="review-label" list="available-labels" maxlength="100" required aria-label="Label name"><datalist id="available-labels">${names.map(n=>`<option value="${esc(n)}"></option>`).join('')}</datalist></label><small>Custom names are saved with the AI: prefix. Other Gmail labels, including human ready, stay in place.</small><label>Use this choice for<select id="review-scope"><option value="email">This email only</option>${kind?`<option value="similar">Future similar emails · ${esc(kind)}</option><option value="sender">This situation from ${esc(row.email.sender)} only</option>`:''}</select></label><p class="review-scope-note" id="review-scope-note">Only this email changes. No preference will be saved.</p><button class="primary" id="save-label-review">Confirm label &amp; continue →</button><p class="review-save-note">${row.transport==='gmail'?'Saved to Gmail and verified before the review is marked complete.':'This is a local simulation.'}</p></form>`;
}
function bindLabelReview(row){
  if(!$('#label-review-form'))return;
  $('#review-label').value=row.labelReview.current_label;
  const updateReviewButton=()=>{$('#save-label-review').textContent=$('#review-mode').value==='add'?'Add label & continue →':$('#review-label').value.trim()===row.labelReview.current_label?'Confirm label & continue →':'Replace label & continue →'};
  $('#review-label').addEventListener('input',updateReviewButton);
  $('#review-mode').addEventListener('change',()=>{
    const add=$('#review-mode').value==='add';
    $('#review-label').value=add?'':row.labelReview.current_label;
    $('#review-scope').disabled=false;
    $('#review-mode-note').textContent=add?'Adds one more label. All existing labels stay.':'Replaces '+row.labelReview.current_label+' only. Other labels stay.';
    $('#review-scope-note').textContent='This email changes first. A suggested skill is inactive until you review and activate it.';
    $('#review-scope').value='email';updateReviewButton();
  });
  $('#review-scope').addEventListener('change',()=>{$('#review-scope-note').textContent=$('#review-scope').value==='email'?'Only this email changes. No preference will be saved.':'Save an explicit preference for this situation. Existing emails will not be relabeled automatically; sending and archiving permissions stay unchanged.'});
  $('#label-review-form').addEventListener('submit',async e=>{
    e.preventDefault();const button=$('#save-label-review');button.disabled=true;
    const result=await post('/api/label-review',{action_id:row.id,revision:row.revision,label:$('#review-label').value,scope:$('#review-scope').value,mode:$('#review-mode').value});
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
  renderSkills();
  const attentionRules=[...new Map((state.attention_rules||[]).map(r=>[`${r.account}|${r.cue}|${r.scope}`,r])).values()];
  $('#attention-rules').innerHTML=attentionRules.map(r=>`<div class="rule-row"><span><strong>${r.enabled?'Needs attention':'Attention off · exception'}</strong><br>${esc(state.attention_cues?.[r.cue]||'This email only')} · ${esc(r.scope==='*'?'All senders':r.scope.startsWith('email:')?'One email':r.scope)}<br><small>Visibility only. It does not change importance, escalation or permission.</small></span></div>`).join('')||'<p>No saved attention preferences yet. Choose an email and save a visibility preference.</p>';
  $('#draft-style-rules').innerHTML=(state.draft_style_rules||[]).filter(r=>r.active).map(r=>`<div class="rule-row"><span><strong>${esc(r.length)} · ${r.greeting==='include'?'greeting':'no greeting'} · ${r.signoff==='include'?'sign-off':'no sign-off'}</strong><br>${esc(state.label_kinds[r.kind]||r.kind)} · ${esc(r.scope==='*'?'All senders':r.scope)}<br><small>Improves draft wording only. Sending still requires approval.</small></span><button class="secondary" data-pause-draft-style="${r.id}">Pause</button></div>`).join('')||'<p>No confirmed draft style yet. Edit a generated draft, save it, then review the style Wajo found.</p>';
  $('#draft-style-rules').querySelectorAll('button').forEach(b=>b.addEventListener('click',async()=>{if(await post('/api/draft-style-rule-pause',{rule_id:Number(b.dataset.pauseDraftStyle)}))notify('Draft style preference paused.')}));
  $('#organization-rules').innerHTML=(state.organization_rules||[]).filter(r=>r.active).map(r=>`<div class="rule-row"><span><strong>${esc(r.topic)} · ${esc(r.subtype)}${r.important?' · Important':''}</strong><br>${esc(state.label_kinds[r.kind]||r.kind)} · ${esc(r.scope==='*'?'All senders':r.scope)}<br><small>Explicit preference #${r.feedback_id}. It does not grant action permission.</small></span><button class="secondary" data-pause-organization="${r.id}">Pause</button></div>`).join('')||'<p>No saved organization preferences yet. Choose a future scope while organizing an email.</p>';
  $('#organization-rules').querySelectorAll('button').forEach(b=>b.addEventListener('click',async()=>{if(await post('/api/organization-rule-pause',{rule_id:Number(b.dataset.pauseOrganization)}))notify('Organization preference paused.')}));
  $('#label-rules').innerHTML=(state.label_rules||[]).filter(r=>r.active).map(r=>`<div class="rule-row"><span><strong>${esc(r.label)}</strong><br>${esc(state.label_kinds[r.kind]||r.kind)} · ${esc(r.scope==='*'?'All senders':r.scope)}<br><small>Enabled by your review #${r.feedback_id}</small></span><button class="secondary" data-pause-label="${r.id}">Pause</button></div>`).join('')||'<p>No saved label preferences yet. Choose “Future similar emails” when reviewing a label.</p>';
  $('#label-rules').querySelectorAll('button').forEach(b=>b.addEventListener('click',async()=>{if(await post('/api/label-rule-pause',{rule_id:Number(b.dataset.pauseLabel)}))notify('Label preference paused. Review a similar email to set a new preference.')}));
  $('#event-skills').innerHTML=(state.event_skills||[]).filter(skill=>skill.status!=='deleted').map(skill=>{const context=skill.context||{},stateCopy=skill.status==='paused'?'Paused':skill.qualified?'Qualified · automatic local saving':`${skill.approval_streak||0} of 2 confirmations`;return `<div class="rule-row"><span><strong>${esc(context.semantic_kind||context.kind||'Similar calendar events')}</strong><br>${esc(stateCopy)}<br><small>Meaning-first matching · sender is supporting context only</small></span><span class="rule-actions">${skill.status==='paused'?`<button class="secondary" data-event-skill="${skill.id}" data-revision="${skill.revision}" data-operation="resume">Resume</button>`:`<button class="secondary" data-event-skill="${skill.id}" data-revision="${skill.revision}" data-operation="pause">Pause</button>`}<button class="secondary" data-event-skill="${skill.id}" data-revision="${skill.revision}" data-operation="delete">Delete</button></span></div>`}).join('')||'<p>No Event Skills yet. Confirm a date found in an email to start one.</p>';
  $('#event-skills').querySelectorAll('[data-event-skill]').forEach(button=>button.addEventListener('click',async()=>{const operation=button.dataset.operation;if(await post('/api/event-skills/manage',{skill_id:Number(button.dataset.eventSkill),revision:Number(button.dataset.revision),operation}))notify(operation==='delete'?'Event Skill deleted.':`Event Skill ${operation}d.`)}));
  const groups=new Map();for(const f of state.preference_feedback){const key=JSON.stringify([f.scope,f.pattern]);if(!groups.has(key))groups.set(key,{scope:f.scope,pattern:f.pattern,count:0});const group=groups.get(key);group.count=f.positive?group.count+1:0}
  $('#memory-groups').innerHTML=groups.size?[...groups.values()].map(g=>`<div class="memory-card"><h2>${esc(patterns[g.pattern]||g.pattern)}</h2><p class="scope-name">${g.scope==='*'?'General experience · all senders':esc(g.scope)}</p><strong>${g.count} <small>/ 3</small></strong><p>${g.count>=3?'Enough experience for eligible emails. Exceptions and risk signals are checked separately.':'The agent will keep asking. Explicit approvals are needed.'}</p></div>`).join(''):'<div class="memory-card"><h2>Learning your preferences</h2><p>Approve or correct archiving decisions. Experience for each semantic pattern will appear here.</p></div>';
  $('#rules').innerHTML=state.archive_rules.map((r,i)=>`<div class="rule-row"><span>${esc(r.scope==='*'?'All senders':r.scope)} · ${esc(r.pattern==='*'?'All email patterns':patterns[r.pattern])}</span><button class="secondary" data-remove-rule="${i}">Remove exception</button></div>`).join('');
  $('#rules').querySelectorAll('button').forEach(b=>b.addEventListener('click',async()=>{const r=state.archive_rules[Number(b.dataset.removeRule)];if(await post('/api/rule',{sender:r.scope,pattern:r.pattern,keep:false}))notify('Exception removed')}));
}
function eventStart(item){const value=item.local_start||item.local_date||item.start_at||item.start||item.date||item.starts_at;if(/^\d{4}-\d{2}-\d{2}$/.test(value||'')){const [year,month,day]=value.split('-').map(Number);return new Date(year,month-1,day)}return new Date(value)}
function renderCalendar(){
  if(!state)return;
  const all=(state.events||[]).filter(item=>!['cancelled','canceled','deleted'].includes(String(item.status||'').toLowerCase())&&!Number.isNaN(eventStart(item).getTime()));
  $('#event-count').textContent=String(all.length);
  $('#calendar-month').textContent=calendarCursor.toLocaleDateString('en-US',{month:'long',year:'numeric'});
  const now=new Date(),minimum=new Date(now.getFullYear()-1,now.getMonth(),1),maximum=new Date(now.getFullYear()+3,now.getMonth(),1);
  $('#calendar-prev').disabled=calendarCursor<=minimum;$('#calendar-next').disabled=calendarCursor>=maximum;
  const year=calendarCursor.getFullYear(),month=calendarCursor.getMonth(),days=new Date(year,month+1,0).getDate(),offset=(new Date(year,month,1).getDay()+6)%7;
  const cells=[];
  for(let i=0;i<offset;i++)cells.push('<div class="calendar-day calendar-blank" role="gridcell" aria-hidden="true"></div>');
  for(let day=1;day<=days;day++){
    const date=new Date(year,month,day);
    const items=all.filter(item=>{const d=eventStart(item);return d.getFullYear()===year&&d.getMonth()===month&&d.getDate()===day});
    const today=date.toDateString()===now.toDateString();
    cells.push(`<div class="calendar-day ${today?'calendar-today':''}" role="gridcell" aria-label="${esc(date.toLocaleDateString('en-US',{weekday:'long',month:'long',day:'numeric'}))}"><div class="calendar-date"><span>${esc(date.toLocaleDateString('en-US',{weekday:'short'}))}</span><strong>${day}</strong></div>${items.map((item,index)=>{const d=eventStart(item),allDay=Boolean(item.all_day||item.is_all_day),time=allDay?'All day':d.toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit'}),palette=['teal','amber','violet','blue'],requested=String(item.color||''),color=palette.includes(requested)?requested:item.kind==='response_deadline'?'amber':palette[(Number(item.id)||index)%palette.length];return `<button class="event-card event-${color}" data-event-id="${esc(item.id)}" title="${esc(item.title||item.summary||'Untitled event')}"><small>${esc(time)}</small>${esc(item.title||item.summary||'Untitled event')}</button>`}).join('')}</div>`);
  }
  while(cells.length%7)cells.push('<div class="calendar-day calendar-blank" role="gridcell" aria-hidden="true"></div>');
  $('#calendar-grid').innerHTML=cells.join('');
  $('#calendar-grid').querySelectorAll('[data-event-id]').forEach(button=>button.addEventListener('click',()=>openEvent(button.dataset.eventId)));
}
function openEvent(id){
  const item=(state.events||[]).find(event=>String(event.id)===String(id));if(!item)return;
  const start=eventStart(item),end=item.local_end||item.local_end_date||item.end_at||item.end||item.ends_at,allDay=Boolean(item.all_day||item.is_all_day);
  const when=allDay?start.toLocaleDateString('en-US',{weekday:'long',year:'numeric',month:'long',day:'numeric'}):start.toLocaleString('en-US',{weekday:'long',year:'numeric',month:'long',day:'numeric',hour:'numeric',minute:'2-digit'});
  $('#event-dialog-title').textContent=item.title||item.summary||'Untitled event';
  $('#event-dialog-content').innerHTML=`<dl class="event-facts"><div><dt>When</dt><dd>${esc(when)}${end?` – ${esc(new Date(end).toLocaleString('en-US'))}`:''}</dd></div>${item.location?`<div><dt>Location</dt><dd>${esc(item.location)}</dd></div>`:''}${item.description||item.original_text?`<div><dt>Details</dt><dd>${esc(item.description||item.original_text)}</dd></div>`:''}</dl><div class="event-dialog-actions">${item.source_email_id||item.email_id||item.action_id?`<button class="primary" id="event-source-email">Open source email →</button>`:''}${item.automatic?'<button class="secondary danger-button" id="event-mistake">This shouldn’t have been added</button>':''}</div>`;
  $('#event-source-email')?.addEventListener('click',()=>{const source=item.action_id||(state.actions||[]).find(action=>String(action.email_id)===String(item.source_email_id||item.email_id))?.id;if(source!==undefined){filter='all';$('#search').value='';selected=source;detailOpen=true;$('#event-dialog').close();navigate('mail');renderMail()}});
  $('#event-mistake')?.addEventListener('click',async()=>{const result=await post('/api/events/mistake',{event_id:Number(item.id)});if(result){$('#event-dialog').close();notify('Event removed. Wajo will ask again in similar situations.')}});
  $('#event-dialog').showModal();
}
function modelConfig(){return state.models||state.model_settings||{mode:'bundled_groq',concurrency:3}}
function selectedModelMode(){return document.querySelector('input[name="model-mode"]:checked')?.value||'bundled_groq'}
function cleanModelError(message,key){let result=String(message||'The provider rejected this key.');if(key)result=result.split(key).join('[redacted]');return result.replace(/(?:sk-|gsk_)[A-Za-z0-9_-]{8,}/g,'[redacted]').replace(/Bearer\s+\S+/gi,'Bearer [redacted]').slice(0,240)}
function updateModelControls(){
  const mode=selectedModelMode(),bundled=mode==='bundled_groq',slider=$('#model-concurrency');
  slider.disabled=bundled;slider.value=bundled?'3':String(Math.max(3,Math.min(12,Number(slider.value)||3)));$('#model-concurrency-value').textContent=slider.value;
  $('#model-key-field').classList.toggle('hidden',bundled);
  $('#model-quota-copy').textContent=bundled?'The bundled free tier uses shared Groq limits, so processing may pause when its quota is busy. Concurrency is fixed at 3.':mode==='user_groq'?'Your Groq account limits and availability apply. Higher concurrency can reach your quota sooner.':'OpenAI API usage is billed to your account. Higher concurrency can increase spend and reach rate limits sooner.';
  $('.model-apply .risky').classList.toggle('hidden',mode!=='user_openai');
  $('#model-risk-copy').textContent=bundled?'Changing provider affects future AI analysis. The bundled option does not use a personal key.':mode==='user_openai'?'OpenAI is a paid external API. Applying this key can incur charges for future model requests.':'Your provider quota applies to future model requests. Keep this key private.';
  const currentKey=$('#model-key').value.trim();$('#model-apply').disabled=!bundled&&!(modelValidation.valid&&modelValidation.mode===mode&&modelValidation.key===currentKey);
}
function renderModels(){
  if(!state)return;const config=modelConfig();
  $('#model-active').textContent=config.label||({bundled_groq:'Bundled free Groq',user_groq:'Your Groq',user_openai:'Your OpenAI'}[config.mode]||'Bundled free Groq');
  if(!modelsInitialized){const mode=['bundled_groq','user_groq','user_openai'].includes(config.mode)?config.mode:'bundled_groq';document.querySelector(`input[name="model-mode"][value="${mode}"]`).checked=true;$('#model-concurrency').value=String(Math.max(3,Math.min(12,Number(config.concurrency)||3)));modelsInitialized=true}
  updateModelControls();
}
async function validateModelKey(){
  const mode=selectedModelMode(),key=$('#model-key').value.trim(),request=++modelValidationRequest;if(mode==='bundled_groq'||!key)return;
  $('#model-key-icon').textContent='…';$('#model-key-icon').className='checking';$('#model-key-status').textContent='Validating this key…';$('#model-apply').disabled=true;
  try{const response=await fetch('/api/models/validate',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify({mode,key})});const result=await response.json();if(request!==modelValidationRequest||mode!==selectedModelMode()||key!==$('#model-key').value.trim())return;modelValidation={mode,key,valid:response.ok&&Boolean(result.valid),token:result.token||null};$('#model-key-icon').textContent=modelValidation.valid?'✓':'×';$('#model-key-icon').className=modelValidation.valid?'valid':'invalid';$('#model-key-icon').title=modelValidation.valid?'':cleanModelError(result.error,key);$('#model-key-status').textContent=modelValidation.valid?'Key validated.':cleanModelError(result.error,key);$('#model-key-status').title=modelValidation.valid?'':cleanModelError(result.error,key)}catch(e){if(request!==modelValidationRequest)return;modelValidation={mode,key,valid:false,token:null};$('#model-key-icon').textContent='×';$('#model-key-icon').className='invalid';$('#model-key-icon').title='Could not validate the key.';$('#model-key-status').textContent='Could not validate the key. Check your connection and try again.'}updateModelControls();
}
function renderSuperpowers(){
  if(!state)return;
  const powers=state.superpowers||{enabled:false,available:false,special_powers:[],autosent:[],unread_count:0};
  const enabled=Boolean(powers.enabled),available=Boolean(powers.available);
  $('#open-superpowers').classList.toggle('hidden',!available);
  $('#open-superpowers').textContent=enabled?'Tune Wajo’s superpowers':'Want to give Wajo superpowers?';
  document.querySelectorAll('.superpowers-nav').forEach(node=>node.classList.toggle('hidden',!enabled));
  if(!enabled&&['special','autosent'].includes(page)){page='mail';selectedAutosent=undefined}
  $('#mail-view').classList.toggle('hidden',page!=='mail');
  $('#memory-view').classList.toggle('hidden',page!=='memory');
  $('#calendar-view').classList.toggle('hidden',page!=='calendar');
  $('#models-view').classList.toggle('hidden',page!=='models');
  $('#special-view').classList.toggle('hidden',page!=='special'||!enabled);
  $('#autosent-view').classList.toggle('hidden',page!=='autosent'||!enabled);
  for(const [name,id] of [['mail','nav-mail'],['calendar','nav-calendar'],['memory','nav-memory'],['models','nav-models'],['special','nav-special'],['autosent','nav-autosent']])$('#'+id).classList.toggle('active',page===name);
  const reviewMode=page==='mail'&&['label_review','label_reviewed'].includes(filter);
  $('#nav-labels').classList.toggle('active',reviewMode);$('#nav-mail').classList.toggle('active',page==='mail'&&!reviewMode);

  const unread=Number(powers.unread_count)||0;
  $('#autosent-count').textContent=String(unread);
  $('#autosent-count').classList.toggle('hidden',unread===0);
  $('#autosent-count').classList.toggle('unread-flash',unread>0);
  $('#autosent-account').textContent=powers.current_account||'Current Gmail account';
  const qualified=(powers.special_powers||[]).filter(item=>item.qualified!==false);
  $('#special-powers').innerHTML=qualified.map(item=>`<article class="special-card"><div class="special-card-head"><span class="special-spark" aria-hidden="true">✦</span><span class="badge notify">Ready to autosend</span></div><h2>${esc(item.title)}</h2><p>Qualified on ${esc(powers.current_account||'the current Gmail account')} for Skill revision ${esc(item.revision)}.</p><div class="qualification"><strong>${esc(item.confirmations)} confirmed sends</strong><small>Recipient, subject and text were unchanged</small></div><div class="decision-buttons"><button class="secondary" data-improve-power="${esc(item.skill_id)}" data-revision="${esc(item.revision)}">Improve Skill</button><button class="secondary danger-button" data-disable-power="${esc(item.skill_id)}" data-revision="${esc(item.revision)}">Disable auto-send</button></div></article>`).join('')||'<div class="empty special-empty"><div class="empty-icon">✦</div><h2>No qualified Draft Skills yet</h2><p>Send the same Skill’s drafts twice through Wajo without changing the recipient, subject or text. Qualification never bypasses safety checks.</p></div>';
  $('#special-powers').querySelectorAll('[data-disable-power]').forEach(button=>button.addEventListener('click',async()=>{button.disabled=true;const result=await post('/api/superpowers/disable',{skill_id:Number(button.dataset.disablePower),revision:Number(button.dataset.revision)});if(result)notify('Automatic sending disabled for this Skill revision.');else button.disabled=false}));
  $('#special-powers').querySelectorAll('[data-improve-power]').forEach(button=>button.addEventListener('click',async()=>{button.disabled=true;const skillId=Number(button.dataset.improvePower);const result=await post('/api/superpowers/improve',{skill_id:skillId,revision:Number(button.dataset.revision)});if(result){notify('Auto-send permission removed. Review changes before this Skill becomes active again.');navigate('memory');const skill=state.skills.find(x=>x.id===skillId);if(skill)await openSkillReview({skill_id:skill.id,scope:skill.config.scope})}else button.disabled=false}));

  const sent=powers.autosent||[];
  if(selectedAutosent!=null&&!sent.some(item=>String(item.id)===String(selectedAutosent)))selectedAutosent=undefined;
  $('#autosent-total').textContent=String(sent.length);
  $('#autosent-list').innerHTML=sent.map(item=>`<button class="autosent-item ${String(item.id)===String(selectedAutosent)?'selected':''} ${item.seen?'':'unread'}" data-autosent-id="${esc(item.id)}"><div class="email-top"><span class="email-sender">To: ${esc(item.recipient)}</span><span class="autosent-dot" aria-label="${item.seen?'Read':'Unread'}"></span></div><h3>${esc(item.subject||'(no subject)')}</h3><p class="email-preview">${esc(item.text)}</p><small>${esc(new Date(item.created_at).toLocaleString('en-US'))} · ${esc(item.delivery_status)}</small></button>`).join('')||'<div class="empty-list">No automatic replies have been sent.</div>';
  $('#autosent-list').querySelectorAll('[data-autosent-id]').forEach(button=>button.addEventListener('click',()=>openAutosent(button.dataset.autosentId)));
  renderAutosentDetail(sent.find(item=>String(item.id)===String(selectedAutosent)));
}
function renderAutosentDetail(item){
  if(!item){$('#autosent-detail').innerHTML='<div class="empty"><div class="empty-icon">↗</div><h2>Select an autosent email</h2><p>Its exact recipient, subject, text, permission reason and Gmail delivery status will appear here.</p></div>';return}
  $('#autosent-detail').innerHTML=`<div class="detail-heading"><span>AUTOMATIC REPLY</span><span>${esc(new Date(item.created_at).toLocaleString('en-US'))}</span></div><h2>${esc(item.subject||'(no subject)')}</h2><div class="autosent-facts"><div><small>Account</small><strong>${esc(item.account)}</strong></div><div><small>Recipient</small><strong>${esc(item.recipient)}</strong></div><div><small>Delivery status</small><strong>${esc(item.delivery_status)}</strong></div><div><small>Skill</small><strong>${esc(item.skill_title)}</strong></div></div><pre class="autosent-body">${esc(item.text)}</pre><div class="decision autosent-reason"><div class="decision-title">✦ Why Wajo was allowed to send</div><p>${esc(item.reason)}</p></div>`;
}
async function openAutosent(id){
  selectedAutosent=id;renderSuperpowers();
  const item=(state.superpowers?.autosent||[]).find(entry=>String(entry.id)===String(id));
  if(item&&!item.seen){const result=await post('/api/superpowers/seen',{id:item.id});if(!result)notify('Could not mark this email as reviewed.')}
}
function navigate(next){
  if(['special','autosent'].includes(next)&&!state?.superpowers?.enabled)return;
  page=next;
  $('#mail-view').classList.toggle('hidden',page!=='mail');$('#memory-view').classList.toggle('hidden',page!=='memory');$('#calendar-view').classList.toggle('hidden',page!=='calendar');$('#models-view').classList.toggle('hidden',page!=='models');$('#special-view').classList.toggle('hidden',page!=='special');$('#autosent-view').classList.toggle('hidden',page!=='autosent');
  for(const [name,id] of [['mail','nav-mail'],['calendar','nav-calendar'],['memory','nav-memory'],['models','nav-models'],['special','nav-special'],['autosent','nav-autosent']])$('#'+id).classList.toggle('active',page===name);
  const reviewMode=page==='mail'&&['label_review','label_reviewed'].includes(filter);
  $('#nav-labels').classList.toggle('active',reviewMode);$('#nav-mail').classList.toggle('active',page==='mail'&&!reviewMode);
}
$('#nav-mail').addEventListener('click',()=>{navigate('mail');setFilter('all')});$('#back-mail').addEventListener('click',()=>navigate('mail'));$('#nav-memory').addEventListener('click',()=>navigate('memory'));
$('#nav-calendar').addEventListener('click',()=>navigate('calendar'));
$('#nav-models').addEventListener('click',()=>navigate('models'));
$('#nav-labels').addEventListener('click',()=>{navigate('mail');$('#search').value='';setFilter('label_review')});
$('#nav-special').addEventListener('click',()=>navigate('special'));
$('#nav-autosent').addEventListener('click',()=>navigate('autosent'));
$('#special-settings').addEventListener('click',openSuperpowersDialog);
$('#open-superpowers').addEventListener('click',openSuperpowersDialog);
$('#close-superpowers').addEventListener('click',()=>$('#superpowers-dialog').close());
$('#superpowers-dialog').addEventListener('cancel',e=>{e.preventDefault();$('#superpowers-dialog').close()});
function openSuperpowersDialog(){
  const powers=state.superpowers||{};
  $('#superpowers-toggle').checked=Boolean(powers.enabled);
  $('#superpowers-toggle').disabled=!powers.available;
  $('#superpowers-toggle-note').textContent=powers.available?'Allow qualified Draft Skills to send matching replies':'Connect and verify Gmail to make Superpowers available';
  $('#superpowers-error').textContent='';$('#superpowers-error').classList.add('hidden');
  $('#superpowers-dialog').showModal();
}
function animateSuperpowersOff(){
  if(matchMedia('(prefers-reduced-motion: reduce)').matches)return Promise.resolve();
  const nodes=[$('#nav-special'),$('#nav-autosent'),$('#special-view'),$('#autosent-view')].filter(node=>!node.classList.contains('hidden'));
  nodes.forEach((node,index)=>node.classList.add(index%2?'power-fly-right':'power-fly-left'));
  return new Promise(resolve=>setTimeout(()=>{nodes.forEach(node=>node.classList.remove('power-fly-left','power-fly-right'));resolve()},520));
}
$('#superpowers-toggle').addEventListener('change',async event=>{
  const enabled=event.target.checked;event.target.disabled=true;
  $('#superpowers-error').classList.add('hidden');
  const result=await post('/api/superpowers/global',{enabled,reviewed_rules:true});
  if(result){notify(enabled?'Superpowers enabled. Qualified Skills can now autosend safely.':'Superpowers turned off. Every reply requires approval again.')}
  else{event.target.checked=!enabled;$('#superpowers-error').textContent='Could not update Superpowers. Nothing changed.';$('#superpowers-error').classList.remove('hidden')}
  event.target.disabled=false;
});
$('#calendar-prev').addEventListener('click',()=>{calendarCursor=new Date(calendarCursor.getFullYear(),calendarCursor.getMonth()-1,1);renderCalendar()});
$('#calendar-next').addEventListener('click',()=>{calendarCursor=new Date(calendarCursor.getFullYear(),calendarCursor.getMonth()+1,1);renderCalendar()});
$('#calendar-today').addEventListener('click',()=>{const now=new Date();calendarCursor=new Date(now.getFullYear(),now.getMonth(),1);renderCalendar()});
$('#close-event').addEventListener('click',()=>$('#event-dialog').close());
$('#event-dialog').addEventListener('cancel',event=>{event.preventDefault();$('#event-dialog').close()});
document.querySelectorAll('input[name="model-mode"]').forEach(input=>input.addEventListener('change',()=>{clearTimeout(modelValidationTimer);modelValidationRequest++;modelValidation={mode:null,key:null,valid:false,token:null};$('#model-key').value='';$('#model-key-icon').textContent='';$('#model-key-icon').title='';$('#model-key-status').textContent=selectedModelMode()==='bundled_groq'?'No personal key is used.':'Enter a key to validate it.';updateModelControls()}));
$('#model-concurrency').addEventListener('input',updateModelControls);
$('#model-key').addEventListener('input',()=>{clearTimeout(modelValidationTimer);modelValidationRequest++;modelValidation={mode:null,key:null,valid:false,token:null};$('#model-key-icon').textContent='';$('#model-key-icon').className='';$('#model-key-icon').title='';$('#model-key-status').textContent=$('#model-key').value.trim()?'Waiting to validate…':'Enter a key to validate it.';updateModelControls();if($('#model-key').value.trim())modelValidationTimer=setTimeout(validateModelKey,2000)});
$('#models-form').addEventListener('submit',async event=>{event.preventDefault();const mode=selectedModelMode(),key=$('#model-key').value.trim();if(mode!=='bundled_groq'&&!(modelValidation.valid&&modelValidation.mode===mode&&modelValidation.key===key))return;const result=await post('/api/models/apply',{mode,concurrency:Number($('#model-concurrency').value),...(mode==='bundled_groq'?{}:{key,validation_token:modelValidation.token})});if(result){modelValidation={mode:null,key:null,valid:false,token:null};$('#model-key').value='';notify('Model settings applied. Future requests will use this provider.')}});
$('#show-unreviewed').addEventListener('click',()=>setFilter('label_review'));
$('#show-reviewed').addEventListener('click',()=>setFilter('label_reviewed'));
document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>setFilter(b.dataset.filter)));
$('#search').addEventListener('input',renderMail);$('#refresh').addEventListener('click',()=>refresh(true));
const composeKey='wajo-unsent-test-email';
const composeNames=['sender','subject','body'];
function completeCompose(d){return d&&composeNames.every(name=>typeof d[name]==='string'&&d[name].trim())}
function savedCompose(){try{const d=JSON.parse(sessionStorage.getItem(composeKey)||'null');if(completeCompose(d))return d;sessionStorage.removeItem(composeKey)}catch{sessionStorage.removeItem(composeKey)}return null}
function composeData(){return Object.fromEntries(new FormData($('#compose-form')))}
function composeIsEmpty(){const d=composeData();return composeNames.every(name=>!d[name].trim())}
function updateComposeControls(){const form=$('#compose-form');$('#submit-email').disabled=!form.checkValidity();$('#restore-compose').classList.toggle('hidden',!savedCompose()||!composeIsEmpty())}
function storeCompleteCompose(){const d=composeData();if(completeCompose(d))sessionStorage.setItem(composeKey,JSON.stringify(d));updateComposeControls()}
function closeCompose(){storeCompleteCompose();$('#compose').close();$('#compose-form').reset();updateComposeControls()}
$('#new-email').addEventListener('click',()=>{$('#compose-form').reset();updateComposeControls();$('#compose').showModal()});
$('#close-compose').addEventListener('click',closeCompose);
$('#compose').addEventListener('cancel',e=>{e.preventDefault();closeCompose()});
$('#compose-form').addEventListener('input',storeCompleteCompose);
$('#restore-compose').addEventListener('click',()=>{const d=savedCompose();if(!d)return;for(const name of composeNames)$(`#compose-form [name="${name}"]`).value=d[name];updateComposeControls()});
$('#compose-form').addEventListener('submit',async e=>{e.preventDefault();$('#submit-email').disabled=true;try{const r=await post('/api/ingest',Object.fromEntries(new FormData(e.target)));if(r){sessionStorage.removeItem(composeKey);closeCompose();navigate('mail');setFilter('all');notify('Email queued for agent analysis')}}finally{$('#submit-email').disabled=false}});
$('#load-demo').addEventListener('click',async()=>{if(await post('/api/demo',{}))notify('Sample cases loaded. Loading again does not duplicate actions')});
$('#rule-form').addEventListener('submit',async e=>{e.preventDefault();const sender=new FormData(e.target).get('sender');if(await post('/api/rule',{sender,keep:true})){e.target.reset();notify('Exception saved')}});
let gmailContext='',gmailLabelSignature='';
function renderGmail(){
  const g=state.gmail_connection;if(!g)return;
  $('#gmail-strip').classList.toggle('hidden',state.mode==='scripted');
  const running=Boolean(g.operation), choosing=g.status==='account_choice', connected=g.status==='connected',sync=state.gmail_sync?.settings;
  const context=JSON.stringify([g.account,g.live,g.access]);
  if(context!==gmailContext){$('#gmail-consent').checked=false;gmailContext=context}
  const labelSignature=JSON.stringify([g.account,g.labels,sync?.history_labels]);
  if(labelSignature!==gmailLabelSignature){
    gmailLabelSignature=labelSignature;
    const selected=new Set(sync?.history_labels||[]);
    $('#gmail-label-options').innerHTML=(g.labels||[]).map(label=>`<label><input type="checkbox" value="${esc(label.id)}" ${selected.has(label.id)?'checked':''}><span>${esc(label.name)}</span></label>`).join('')||'<p>No Gmail labels are available.</p>';
    $('#gmail-label-options').querySelectorAll('input').forEach(input=>input.addEventListener('change',updateGmailLabelLimit));
    if(sync)document.querySelector(`input[name="history"][value="${sync.history_mode}"]`).checked=true;
  }
  updateGmailLabelLimit();
  $('#gmail-summary').textContent=(connected||choosing)?g.account:g.token_present?'Gmail connection saved':'Connect your Gmail';
  $('#gmail-auth-link').classList.toggle('hidden',!g.auth_url);if(g.auth_url)$('#gmail-auth-link').href=g.auth_url;
  $('#gmail-summary-note').textContent=running?({connect:'Waiting for Google sign-in…',check:'Checking your connection…',sync:'Synchronizing mail…',poll:'Checking for new mail…'}[g.operation]):choosing?'Choose what to do with data from the previous account.':connected?'Connected · '+(sync?.sync_enabled?'new mail sync on':'new mail sync paused')+' · '+(g.live?'Gmail actions enabled':'Local simulation'):'Connect an account and choose the initial history.';
  $('#open-gmail').textContent=connected?'Gmail settings':choosing?'Review account change':g.token_present?'Check Gmail':'Connect Gmail';
  $('#gmail-title').textContent=choosing?'Review account change':connected?'Gmail connected':g.token_present?'Your Gmail connection':'Connect Gmail';
  $('#gmail-intro').textContent=choosing?'Google connected a different Gmail account. Wajo will not synchronize it until you choose what happens to previous local data.':connected?'Your account is connected. You can sync emails below.':g.token_present?'A saved connection is available. Check its status before signing in again.':'Connect your account through Google. Wajo never asks for your Gmail password.';
  $('#gmail-account').textContent=(connected||choosing)?g.account:g.token_present?'Saved connection · verification needed':'No Gmail account connected';
  $('#gmail-access').textContent=(connected||choosing)?(g.access==='manage'?'Read and manage access':'Read-only access'):'Check your connection or sign in with Google.';
  $('#gmail-progress').textContent=running?({connect:'Continue in your system browser. Google sign-in can take up to three minutes.',check:'Verifying Gmail access and loading labels…',sync:'Loading unique messages. You can pause after the current small page.',poll:'Checking the saved Gmail cursor for new mail…'}[g.operation]):g.checked_at&&connected?'Connection checked '+new Date(g.checked_at).toLocaleTimeString('en-US'):'';
  $('#gmail-error').textContent=g.error||'';$('#gmail-error').classList.toggle('hidden',!g.error);
  const needsAccess=connected&&g.live&&g.access!=='manage';
  $('#gmail-connect').classList.toggle('hidden',choosing||(connected&&!needsAccess)||(g.token_present&&(g.status==='unchecked'||g.operation==='check')));
  $('#gmail-connect').textContent=needsAccess?'Grant Gmail action access':g.token_present?'Reconnect with Google':'Connect with Google';
  $('#gmail-setup').classList.toggle('hidden',(connected&&!needsAccess)||choosing);
  $('#gmail-connect').disabled=running||!g.client_ready;$('#gmail-status').disabled=running||!g.token_present;
  if(!g.client_ready&&!g.token_present)$('#gmail-setup').open=true;
  $('#gmail-mode').textContent=g.live?'Gmail · real actions':'Local simulation';
  $('#gmail-scope-note').textContent=connected?'Choose how much existing mail to load. Optional labels affect that initial history only; all later incoming mail is synchronized.':'Verify your account to enable synchronization.';
  $('#gmail-effects').textContent=g.live?'New emails may receive AI labels, be archived, or have replies saved as Gmail drafts under your permissions. Sending always requires approval of the exact saved version.':'Agent actions on new imports are simulated locally. Gmail messages will not be changed.';
  if(connected&&g.live&&g.access!=='manage')$('#gmail-scope-note').textContent='Reconnect with Google to grant access for live mail actions.';
  $('#gmail-account-choice').classList.toggle('hidden',!choosing);
  $('#gmail-connection-actions').classList.toggle('hidden',choosing);
  $('#gmail-sync-form').classList.toggle('hidden',choosing);
  if(choosing){const c=g.switch_counts||{};$('#gmail-account-choice-copy').textContent=`${g.previous_account} was active before ${g.account}. Choose once before synchronization.`;$('#gmail-fresh-counts').textContent=`Remove ${c.total_records||0} local records, including ${c.emails||0} emails, ${c.actions||0} actions, ${c.feedback||0} feedback records and ${c.skills||0} Skills. Nothing is deleted or changed in Gmail.`}
  $('#gmail-sync').disabled=running||!connected||(g.live&&g.access!=='manage')||!$('#gmail-consent').checked;
  $('#gmail-consent').disabled=running;
  $('#gmail-sync-controls').classList.toggle('hidden',!sync);
  if(sync){
    $('#sync-progress').value=sync.progress;$('#sync-progress-value').textContent=sync.progress+'%';
    $('#sync-progress-label').textContent=sync.history_status==='done'?'Initial history complete':sync.history_status==='paused'?'History import paused':'Importing initial history';
    $('#sync-progress-detail').textContent=`${sync.scanned} scanned · ${sync.imported} loaded · ${sync.current_incomplete} currently incomplete · ${sync.skipped} outside the selected history labels. Analysis continues separately until every loaded email has a saved decision.`;
    $('#gmail-import-pause').textContent=sync.history_status==='paused'?'Resume history import':'Pause history import';
    $('#gmail-import-pause').disabled=sync.history_status==='done'||(running&&g.operation!=='sync');
    $('#gmail-auto-sync').checked=sync.sync_enabled;$('#gmail-auto-sync').disabled=running;
  }
  const incomplete=state.gmail_sync?.incomplete?.length||0,r=g.last_poll||g.last_sync;
  $('#gmail-result').classList.toggle('hidden',!r&&!incomplete);
  if(r||incomplete)$('#gmail-result').textContent=`${r?.completed_at?'Last Gmail check · '+new Date(r.completed_at).toLocaleString('en-US')+'. ':''}${r?.added??0} new items loaded. ${incomplete} incomplete messages are locked and will be retried hourly. These counts describe synchronization, not decision quality.`;
}
function updateGmailLabelLimit(){
  const boxes=[...$('#gmail-label-options').querySelectorAll('input')],checked=boxes.filter(x=>x.checked);
  $('#gmail-label-count').textContent=`${checked.length} of 10 selected`;
  boxes.forEach(x=>x.disabled=!x.checked&&checked.length>=10);
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
$('#gmail-account-choice-form').addEventListener('submit',async e=>{e.preventDefault();const g=state.gmail_connection;const choice=new FormData(e.target).get('account-choice');if(await gmailPost('/api/gmail/account-choice',{account:g.account,previous_account:g.previous_account,choice}))notify(choice==='fresh'?'Previous local Wajo data removed. Gmail was not changed.':'Previous Wajo data kept in the shared timeline.')});
$('#gmail-consent').addEventListener('change',renderGmail);
$('#gmail-sync-form').addEventListener('submit',async e=>{
  e.preventDefault();const g=state.gmail_connection;
  const labels=[...$('#gmail-label-options').querySelectorAll('input:checked')].map(x=>x.value);
  await gmailPost('/api/gmail/sync',{allow_groq:$('#gmail-consent').checked,account:g.account,live:g.live,history_mode:new FormData(e.target).get('history'),label_ids:labels});
});
$('#gmail-import-pause').addEventListener('click',async()=>{const s=state.gmail_sync?.settings;if(!s)return;await gmailPost(s.history_status==='paused'?'/api/gmail/import-resume':'/api/gmail/import-pause',{account:state.gmail_connection.account})});
$('#gmail-auto-sync').addEventListener('change',async e=>{const enabled=e.target.checked;if(!await gmailPost('/api/gmail/sync-toggle',{account:state.gmail_connection.account,enabled}))e.target.checked=!enabled});
refresh();setInterval(()=>refresh(),2500);
