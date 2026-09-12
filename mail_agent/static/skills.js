'use strict';
let skillReview=null;
const skillDialog=document.querySelector('#skill-dialog');
async function skillRequest(path,payload){
  const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify(payload)});
  const result=await response.json();
  if(!response.ok)throw new Error(result.error||'Could not update the preview');
  return result;
}
async function openSkillReview(payload){
  const saved=(state.skills||[]).find(s=>s.id===payload.skill_id);
  $('#skill-title').textContent=saved&&saved.status!=='suggested'?'Review skill changes':'Review suggested skill';
  $('#skill-dialog .eyebrow').textContent=saved&&saved.status!=='suggested'?'CHANGES ARE NOT SAVED YET':'SUGGESTED SKILL · NOT ACTIVE YET';
  skillReview={payload:{...payload,exclusions:(saved?.examples||[]).filter(e=>e.excluded).map(e=>e.action_id)},index:0,reviewed:new Set(),data:null,loading:false};
  skillDialog.showModal();
  await reloadSkillPreview();
}
async function reloadSkillPreview(){
  const current=skillReview;
  current.loading=true;$('#skill-error').textContent='';renderSkill();
  try{
    const result=await skillRequest('/api/skills/preview',current.payload);
    if(skillReview!==current)return;
    current.data=result;current.reviewed.clear();
    current.index=0;
  }catch(e){if(skillReview===current){current.data=null;$('#skill-error').textContent=e.message}}
  finally{if(skillReview===current){current.loading=false;renderSkill()}}
}
function renderSkill(){
  const s=skillReview;if(!s)return;
  $('#skill-scope').value=s.payload.scope;$('#skill-scope').disabled=true;
  $('#skill-scope-title').textContent=s.payload.scope==='sender'?'This sender only · existing Skill':'Future similar emails';
  $('#skill-scope-note').textContent=s.payload.scope==='sender'?'This older narrow scope is preserved and is not expanded automatically.':'Meaning is primary; subject, subtopic and sender are supporting context.';
  $('#skill-rule-title').textContent=s.data?.title||'Checking your preference';
  $('#skill-cue').textContent=s.data?.cue||'';
  $('#skill-back').disabled=s.loading||!s.data||s.index===0;
  $('#skill-next').disabled=s.loading||!s.data;
  $('#skill-save').disabled=s.loading||!s.data;
  $('#skill-next').classList.remove('hidden');$('#skill-save').classList.add('hidden');
  if(s.loading||!s.data){$('#skill-content').innerHTML='<p class="skill-loading" role="status">'+(s.loading?'Checking examples without changing your mail…':'Preview unavailable. Change the scope to retry or close this window.')+'</p>';$('#skill-progress').textContent='';return}
  const examples=s.data.examples;
  $('#skill-refinements')?.remove();
  if(s.payload.skill_id){
    const c=s.data.config, kinds=s.data.family==='attention'?state.attention_cues:s.data.family==='archive'?patterns:state.label_kinds;
    const section=document.createElement('details');section.id='skill-refinements';section.className='skill-refinements';
    section.innerHTML=`<summary>Refine this skill</summary><form id="skill-refine-form"><label>Meaning<select name="kind">${Object.entries(kinds).filter(([k])=>!['none','unknown'].includes(k)).map(([k,v])=>`<option value="${esc(k)}" ${k===c.kind?'selected':''}>${esc(v)}</option>`).join('')}</select></label><div class="field-pair"><label>Body must contain<input name="contains" maxlength="200" value="${esc(c.contains)}"></label><label>Body must not contain<input name="excludes" maxlength="200" value="${esc(c.excludes)}"></label></div><small>Optional literal phrases, case-insensitive. Not instructions to the AI. Changing a condition rechecks every example.</small>${s.data.family==='labels'?`<label>Additional labels (one per line, maximum two)<textarea name="labels" rows="2">${esc(c.labels.join('\n'))}</textarea></label>`:''}${s.data.family==='organization'?`<div class="field-pair"><label>Topic<input name="topic" maxlength="60" value="${esc(c.topic)}"></label><label>Subtype<input name="subtype" maxlength="60" value="${esc(c.subtype)}"></label></div><label><input type="checkbox" name="important" ${c.important?'checked':''}>Important</label>`:''}${s.data.family==='attention'?`<label><input type="checkbox" name="enabled" ${c.enabled?'checked':''}>Keep in Needs attention</label>`:''}${s.data.family==='archive'?`<label>Result<select name="mode"><option value="archive" ${c.mode==='archive'?'selected':''}>Archive after three approvals</option><option value="keep" ${c.mode==='keep'?'selected':''}>Keep in inbox</option></select></label>`:''}${s.data.family==='draft'?['length','greeting','signoff'].map(k=>`<label>${esc(k)}<select name="${k}">${(k==='length'?['concise','brief','standard']:['include','omit']).map(v=>`<option ${v===c[k]?'selected':''}>${v}</option>`).join('')}</select></label>`).join(''):''}<button class="secondary">Recheck examples</button></form>`;
    $('#skill-error').before(section);
    $('#skill-refine-form').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget, changes=Object.fromEntries(new FormData(form));for(const k of ['enabled','important'])if(form.elements[k])changes[k]=form.elements[k].checked;if(changes.labels)changes.labels=changes.labels.split('\n').map(x=>x.trim()).filter(Boolean);s.payload.changes=changes;await reloadSkillPreview()};
  }
  if(s.index===examples.length){
    $('#skill-content').innerHTML=`<section class="skill-summary"><h3>Ready to save</h3><p>${esc(s.data.title)} · ${esc(s.data.cue)}</p><p>Similar situations <small>Meaning first; subject, subtype and sender add context.</small></p><p>${examples.length} examples reviewed. ${s.payload.exclusions.length} email-only exceptions.</p><p>Future matching emails use this preference. Existing mail actions will not be repeated.</p><small>${esc(s.data.note)}</small></section>`;
    $('#skill-next').classList.add('hidden');$('#skill-save').classList.remove('hidden');
    $('#skill-save').disabled=s.reviewed.size!==examples.length;
    $('#skill-progress').textContent='Final review';return;
  }
  const e=examples[s.index], reviewed=s.reviewed.has(e.id);
  $('#skill-content').innerHTML=`<div class="skill-example-head"><span>Example ${s.index+1} of ${examples.length}</span><span>${reviewed?'Reviewed':'Check the result'}</span></div><div class="skill-desk"><article class="skill-mail"><small>Already processed email</small><p class="skill-sender">${esc(e.sender)}</p><h3>${esc(e.subject)}</h3><p class="skill-body">${esc(e.body)}</p></article><section class="skill-verdict"><small>${esc(s.data.family||'Attention')}</small><h3>${esc(e.outcome)}</h3><p>${esc(e.reason)}</p><p>${esc(e.result||s.data.title)}</p><button class="primary" id="skill-correct">${reviewed?'Reviewed':'Looks right'}</button><details class="skill-change"><summary>Change result</summary><p>Exclude this email only. Other similar emails keep the rule.</p><button class="secondary" id="skill-exclude">${e.excluded?'Remove this exclusion':'Exclude this email'}</button><p>Use “Refine this skill” to change its meaning, literal conditions or result, then recheck the examples.</p></details></section></div>`;
  $('#skill-progress').textContent=`${s.reviewed.size} of ${examples.length} reviewed`;
  $('#skill-next').textContent=s.index===examples.length-1?'Review and save':'Next example';
  $('#skill-next').disabled=!reviewed;
  $('#skill-correct').onclick=()=>{s.reviewed.add(e.id);renderSkill();$('#skill-next').focus()};
  $('#skill-exclude').onclick=async()=>{s.payload.exclusions=e.excluded?s.payload.exclusions.filter(id=>id!==e.id):[...s.payload.exclusions,e.id];await reloadSkillPreview()};
}
$('#skill-back').onclick=()=>{skillReview.index--;renderSkill()};
$('#skill-next').onclick=()=>{skillReview.index++;renderSkill();$('#skill-correct')?.focus()};
$('#skill-save').onclick=async()=>{
  const s=skillReview;s.loading=true;$('#skill-close').disabled=true;renderSkill();
  try{await skillRequest('/api/skills/save',{...s.payload,token:s.data.token,reviewed:[...s.reviewed]});skillDialog.close();skillReview=null;await refresh(true);notify('Skill activated. No historical mail actions were repeated.')}
  catch(e){s.loading=false;renderSkill();$('#skill-error').textContent=e.message}
  finally{$('#skill-close').disabled=false}
};
$('#skill-close').onclick=()=>skillDialog.close();
skillDialog.addEventListener('close',()=>{skillReview=null});
skillDialog.addEventListener('cancel',e=>{if($('#skill-close').disabled)e.preventDefault()});

function renderSkills(){
  let host=$('#skills-list');
  if(!host){host=document.createElement('section');host.id='skills-list';host.className='rule-section';$('#memory-view .memory-intro').after(host)}
  const familyNames={attention:'Needs attention',organization:'Organization',labels:'Additional labels',draft:'Draft style',archive:'Inbox cleanup'};
  const kindName=s=>s.family==='attention'?(state.attention_cues[s.config.kind]||s.config.kind):s.family==='archive'?(patterns[s.config.kind]||s.config.kind):(state.label_kinds[s.config.kind]||s.config.kind);
  host.innerHTML='<h2>Skills</h2><p>Suggested skills are not active. Review examples before activation. Pausing preserves the skill; deleting removes its learning examples, not your mail or action history.</p>'+(state.skills||[]).map(s=>`<article class="skill-card"><div><span class="badge">${esc(s.status[0].toUpperCase()+s.status.slice(1))}</span> <small>${esc(familyNames[s.family]||s.family)} · ${esc(s.account==='local_simulation'?'Local simulation':s.account)}</small><h3>${esc(s.title)}</h3><p>${esc(kindName(s))} · ${s.config.scope==='sender'?'Source sender only':'All senders'}</p><small>Source feedback: email #${s.source_id} · ${(s.examples||[]).length} reviewed examples · ${(s.examples||[]).filter(e=>e.excluded).length} exceptions</small>${s.config.contains||s.config.excludes?`<p>Contains: ${esc(s.config.contains||'any')} · Excludes: ${esc(s.config.excludes||'none')}</p>`:''}</div><div class="decision-buttons"><button class="primary" data-review-skill="${s.id}">${s.status==='suggested'?'Review suggestion':'Review / edit'}</button>${s.status!=='suggested'?`<button class="secondary" data-manage-skill="${s.id}" data-operation="${s.status==='active'?'pause':'resume'}">${s.status==='active'?'Pause':'Resume'}</button>`:''}<button class="secondary" data-delete-skill="${s.id}">Delete</button></div><div id="delete-skill-${s.id}" class="hidden"><p>Delete this skill and its learning examples? This cannot be undone. Your mail and action history stay.</p><button class="secondary" data-manage-skill="${s.id}" data-operation="delete">Delete permanently</button></div></article>`).join('');
  if(!(state.skills||[]).length)host.insertAdjacentHTML('beforeend','<p>Correct an email or edit a draft. Wajo will suggest a skill from your feedback.</p>');
  host.querySelectorAll('[data-review-skill]').forEach(b=>b.onclick=()=>{const s=state.skills.find(x=>x.id===Number(b.dataset.reviewSkill));openSkillReview({skill_id:s.id,scope:s.config.scope})});
  host.querySelectorAll('[data-delete-skill]').forEach(b=>b.onclick=()=>$('#delete-skill-'+b.dataset.deleteSkill).classList.toggle('hidden'));
  host.querySelectorAll('[data-manage-skill]').forEach(b=>b.onclick=async()=>{const s=state.skills.find(x=>x.id===Number(b.dataset.manageSkill));if(await post('/api/skills/manage',{skill_id:s.id,revision:s.revision,operation:b.dataset.operation}))notify(b.dataset.operation==='delete'?'Skill and learning examples deleted. Mail and action history preserved.':'Skill updated')});
}

function renderLabelConflict(row){
  const conflict=(state.label_conflicts||[]).find(c=>c.action_id===row.id);if(!conflict)return;
  const candidates=JSON.parse(conflict.candidates), existing=JSON.parse(conflict.existing_labels);
  const form=document.createElement('form');form.className='label-conflict';
  form.innerHTML=`<h3>Choose two additional labels</h3><p>This change would exceed the limit. Existing labels have not been replaced. Topic and Subtype are separate.</p>${candidates.map((name,i)=>`<label><input type="checkbox" name="choice" value="${i}" ${existing.includes(name)?'checked':''}>${esc(name)} ${existing.includes(name)?'<small>existing</small>':''}</label>`).join('')}<p class="conflict-count" role="status"></p><button class="primary">Apply these two labels</button><small>Only the displayed AI labels may be replaced. Other Gmail labels stay.</small>`;
  $('#detail .decision').prepend(form);
  const selected=()=>[...form.querySelectorAll('input:checked')].map(x=>candidates[Number(x.value)]);
  const update=()=>{form.querySelector('button').disabled=selected().length!==2;form.querySelector('.conflict-count').textContent=`${selected().length} of 2 selected`};
  form.onchange=update;update();
  form.onsubmit=async e=>{e.preventDefault();await post('/api/labels/resolve',{action_id:row.id,revision:conflict.revision,labels:selected()})};
}
