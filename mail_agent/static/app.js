'use strict';
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const patterns = {acknowledgement_only:'Receipt acknowledgement',periodic_digest:'Periodic digest',routine_success:'Routine success report',informational_reference:'Informational reference',unknown:'Unknown pattern'};
const autonomies = {silent:'No notification',notify:'Notify',ask:'Ask for approval',escalate:'Escalate'};
const actions = {archive:'Archive email',label:'Apply label',draft:'Save draft',send:'Send reply',none:'No mail action',pay:'Payment request',delete:'Deletion request'};
const statuses = {executing:'Gmail action queued',restoring:'Gmail restore queued',unknown:'Verification needed',pending:'Approval needed',executed:'Completed',blocked:'Blocked',escalated:'Needs your review',error:'Processing error',skipped:'Kept in inbox',rejected:'Rejected',corrected:'Restored to inbox'};
const events = {gmail_draft_saved:'Gmail draft saved',gmail_queued:'Gmail operation queued',gmail_started:'Gmail verification started',gmail_unknown:'Verification needed',gmail_error:'Gmail operation stopped',gmail_unverified:'Gmail state not confirmed',decision:'Agent decision',approved:'You approved the action',rejected:'You rejected the action',executed:'Action completed',notification:'Notification',preference_feedback:'Feedback saved',learned_permission:'Learned preference applied',archive_corrected:'Archive corrected',revised:'Reply revised',organization_reviewed:'Organization reviewed',organization_preference_applied:'Organization preference applied',organization_rule_paused:'Organization preference paused',draft_style_saved:'Draft style saved',draft_style_applied:'Draft style applied',draft_style_fallback:'Draft style rewrite unavailable',draft_style_rule_paused:'Draft style paused'};
let state, selected, filter='all', page='mail', signature='', busy=false;
function notify(text){$('#toast').textContent=text;$('#toast').classList.remove('hidden');setTimeout(()=>$('#toast').classList.add('hidden'),5000)}
function error(text){$('#error').textContent=text;$('#error').classList.toggle('hidden',!text)}
async function post(path, data){
  if(busy) return null;
  busy=true;error('');
  try {
    const feedbackPaths=['/api/attention','/api/organization','/api/label-review','/api/draft-style','/api/approve','/api/reject','/api/correct','/api/edit'];
    const previousSkills=new Set((state.skills||[]).map(s=>s.id));
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify({...data,propose_skill:feedbackPaths.includes(path)})});
    const result=await response.json();if(!response.ok)throw Error(result.error||'Operation failed');
    await refresh(true);
    if(feedbackPaths.includes(path)){
      const suggestion=(state.skills||[]).find(s=>s.source_id===data.action_id&&s.status==='suggested'&&!previousSkills.has(s.id));
      if(suggestion)await openSkillReview({skill_id:suggestion.id,scope:data.scope==='sender'?'sender':'similar'});
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
function setFilter(value){filter=value;renderMail()}
function currentRows(){return state.actions.map(a=>({...a,email:state.emails.find(e=>e.id===a.email_id),labelReview:(state.label_reviews||[]).find(r=>r.action_id===a.id)})).filter(a=>a.email).reverse()}
function badge(row){const replyState=row.reply?(row.status==='executing'?'Saving or sending Gmail reply':row.status==='pending'?'Gmail draft · approval needed':row.status==='executed'?'Sent via Gmail':null):null;const style=['blocked','error','unknown'].includes(row.status)?row.status:row.autonomy;return `<span class="badge ${esc(style)}">${esc(replyState||statuses[row.status]||autonomies[row.autonomy])}</span>`}
function render(){
  const demo=state.mode==='scripted';$('#mode').textContent=demo?'Sample cases · no AI':(state.gmail_enabled?'Qwen · Gmail live':'Qwen · Groq');
  $('#new-email').classList.toggle('hidden',demo);$('#load-demo').classList.toggle('hidden',!demo);
  $('#mode-note').textContent=demo?'Sample cases: emails and decisions are predefined; no AI model is called. All mail actions are simulated locally.':state.gmail_enabled?'Gmail live: new live imports can apply AI labels, archive and restore messages in Wajo-Test. Each decision shows its execution mode. Replies are saved as Gmail drafts. Sending requires approval of the displayed version.':'Local mode: simulation actions stay local. Live Gmail actions remain paused until the server is started without --local-simulation.';
  $('#nav-count').textContent=state.emails.length;
  const suggestions=(state.skills||[]).filter(s=>s.status==='suggested').length;
  $('#skill-suggestion-count').textContent=suggestions;
  $('#skill-suggestion-count').classList.toggle('hidden',!suggestions);
  renderMail();renderMemory();renderGmail();
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
  const attentionIds=new Set((state.attention_items||[]).filter(x=>!x.seen).map(x=>x.action_id));
  $('#count-attention').textContent=all.filter(r=>attentionIds.has(r.id)).length;
  $('#count-escalated').textContent=all.filter(r=>r.autonomy==='escalate').length;
  const listScroll=$('#email-list').scrollTop;
  const search=$('#search').value.toLocaleLowerCase();
  const rows=all.filter(r=>(filter==='all'||(filter==='label_review'&&r.labelReview&&r.labelReview.status!=='reviewed')||(filter==='label_reviewed'&&r.labelReview?.status==='reviewed')||(filter==='pending'&&r.status==='pending')||(filter==='archived'&&r.email.archived)||(filter==='attention'&&attentionIds.has(r.id))||(filter==='escalated'&&r.autonomy==='escalate'))&&(`${r.email.subject} ${r.email.sender}`).toLocaleLowerCase().includes(search));
  $('#list-count').textContent=rows.length;
  document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('selected',b.dataset.filter===filter));
  $('#filter-label').classList.toggle('hidden',!['attention','escalated'].includes(filter));$('#filter-label').textContent=filter==='attention'?'Showing emails matched by your explicit visibility preferences. Importance and escalation are separate.':filter==='escalated'?'Showing situations where the agent cannot safely continue without human judgment.':' ';
  if(!rows.some(r=>r.id===selected))selected=rows[0]?.id;
  $('#email-list').innerHTML=rows.length?rows.map(r=>`<button class="email-item ${r.id===selected?'selected':''}" data-id="${r.id}"><div class="email-top"><span class="email-sender">${esc(r.email.sender)}</span><span class="badge ${esc(r.autonomy)}">${esc(autonomies[r.autonomy])}</span></div><h3>${esc(r.email.subject)}</h3><p class="organization-line">${esc(r.organization.topic)} · ${esc(r.organization.subtype)}${r.organization.important?' · Important':''}</p><p class="email-preview">${esc(r.email.body)}</p>${r.labelReview?`<span class="badge label-chip">${esc(r.labelReview.current_label)}</span> <span class="badge">${r.labelReview.status==='reviewed'?'✓ Reviewed':r.status==='executed'?'To review':esc(statuses[r.status]||r.status)}</span>`:badge(r)}</button>`).join(''):'<div class="empty-list">No matching emails.<br>Add an email or change the filter.</div>';
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
  $('#detail').innerHTML=`<div class="detail-heading"><span>EMAIL # ${row.id}</span>${badge(row)}</div><h2>${esc(row.email.subject)}</h2><div class="organization-summary"><span>${esc(row.organization.topic)}</span><span>${esc(row.organization.subtype)}</span>${row.organization.important?'<span class="important-chip">Important</span>':''}<small>${esc(row.organization.source)}</small></div><div class="sender-row"><span class="avatar">${esc(row.email.sender[0].toUpperCase())}</span><div>${esc(row.email.sender)}<small>${row.transport==='gmail'?'Source: connected Gmail':'Source: local inbox copy'}</small></div></div><p class="email-body">${esc(row.email.body)}</p><div class="decision"><p><strong>${esc(transport)}</strong></p><p>${esc(row.reason)}</p><div class="decision-title">✦ Agent decision · ${esc(actions[p.action]||p.action)}</div><p>${esc(p.reason)}</p><div class="decision-meta">${esc(explanation)}<br>Pattern: ${esc(patterns[p.pattern]||p.pattern)}${p.label?`<br>Label: ${esc(p.label)}`:''}${flags.length&&p.pattern_evidence?`<br>${esc(flags.map(f=>f[1]).join(' · '))}`:''}</div>${p.pattern_evidence?`<blockquote class="reason-quote">${esc(p.pattern_evidence)}</blockquote>`:''}${replyPreview}${!reply&&p.text?`<div class="send-preview">${p.recipient?`Recipient: ${esc(p.recipient)}<br>`:''}${esc(p.text)}</div>`:''}${controls}${editor}${draftStyleForm(row)}${organizationForm(row)}${labelReviewForm(row)}${attentionForm(row)}<div class="decision-buttons"><button class="secondary" id="keep-sender">Always keep mail from this sender</button></div></div><details class="history"><summary>Decision history · ${history.length} entries</summary>${history.map(a=>`<div class="history-item">${esc(events[a.event]||a.event)}<small>${esc(new Date(a.created_at).toLocaleString('en-US'))}</small><details><summary>Details</summary><pre>${esc(JSON.stringify(JSON.parse(a.details),null,2))}</pre></details></div>`).join('')}</details>`;
  $('#detail').scrollTop=previousDetailScroll;
  bindDraftStyle(row);bindOrganization(row);bindLabelReview(row);bindAttention(row);
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
let gmailContext='';
function renderGmail(){
  const g=state.gmail_connection;if(!g)return;
  $('#gmail-strip').classList.toggle('hidden',state.mode==='scripted');
  const running=Boolean(g.operation), connected=g.status==='connected';
  const context=JSON.stringify([g.account,g.live,g.access]);
  if(context!==gmailContext){$('#gmail-consent').checked=false;gmailContext=context}
  $('#gmail-summary').textContent=connected?g.account:g.token_present?'Gmail connection saved':'Connect your Gmail';
  $('#gmail-auth-link').classList.toggle('hidden',!g.auth_url);if(g.auth_url)$('#gmail-auth-link').href=g.auth_url;
  $('#gmail-summary-note').textContent=running?({connect:'Waiting for Google sign-in…',check:'Checking your connection…',sync:'Syncing selected emails…'}[g.operation]):connected?'Connected · Wajo-Test · '+(g.live?'Gmail actions enabled':'Local simulation'):'Connect an account and sync selected emails.';
  $('#open-gmail').textContent=connected?'Gmail settings':g.token_present?'Check Gmail':'Connect Gmail';
  $('#gmail-title').textContent=connected?'Gmail connected':g.token_present?'Your Gmail connection':'Connect Gmail';
  $('#gmail-intro').textContent=connected?'Your account is connected. You can sync emails below.':g.token_present?'A saved connection is available. Check its status before signing in again.':'Connect your account through Google. Wajo never asks for your Gmail password.';
  $('#gmail-account').textContent=connected?g.account:g.token_present?'Saved connection · verification needed':'No Gmail account connected';
  $('#gmail-access').textContent=connected?(g.access==='manage'?'Read and manage access':'Read-only access'):'Check your connection or sign in with Google.';
  $('#gmail-progress').textContent=running?({connect:'Continue in your system browser. Google sign-in can take up to three minutes.',check:'Verifying access and the Wajo-Test label…',sync:'Reading selected emails and adding new ones to the analysis queue…'}[g.operation]):g.checked_at&&connected?'Connection checked '+new Date(g.checked_at).toLocaleTimeString('en-US'):'';
  $('#gmail-error').textContent=g.error||'';$('#gmail-error').classList.toggle('hidden',!g.error);
  const needsAccess=connected&&g.live&&g.access!=='manage';
  $('#gmail-connect').classList.toggle('hidden',(connected&&!needsAccess)||(g.token_present&&(g.status==='unchecked'||g.operation==='check')));
  $('#gmail-connect').textContent=needsAccess?'Grant Gmail action access':g.token_present?'Reconnect with Google':'Connect with Google';
  $('#gmail-setup').classList.toggle('hidden',connected&&!needsAccess);
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
