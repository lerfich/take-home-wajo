'use strict';
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const patterns = {acknowledgement_only:'Подтверждение получения',periodic_digest:'Регулярная сводка',routine_success:'Штатный успешный отчёт',informational_reference:'Справочная информация',unknown:'Паттерн не определён'};
const autonomies = {silent:'Без уведомления',notify:'С уведомлением',ask:'С подтверждением',escalate:'Передано вам'};
const actions = {archive:'Архивировать письмо',label:'Поставить метку',draft:'Сохранить черновик',send:'Отправить ответ',none:'Без почтового действия',pay:'Запрос оплаты',delete:'Запрос удаления'};
const statuses = {pending:'Нужно подтверждение',executed:'Выполнено',blocked:'Остановлено',escalated:'Нужен ваш разбор',error:'Ошибка обработки',skipped:'Оставлено во входящих',rejected:'Вы отклонили',corrected:'Возвращено во входящие'};
const events = {decision:'Решение агента',approved:'Вы одобрили действие',rejected:'Вы отклонили действие',executed:'Действие выполнено',notification:'Уведомление',preference_feedback:'Обратная связь сохранена',learned_permission:'Применена память предпочтений',archive_corrected:'Архивирование исправлено',revised:'Текст ответа изменён'};
let state, selected, filter='all', page='mail', signature='', busy=false;
function notify(text){$('#toast').textContent=text;$('#toast').classList.remove('hidden');setTimeout(()=>$('#toast').classList.add('hidden'),5000)}
function error(text){$('#error').textContent=text;$('#error').classList.toggle('hidden',!text)}
async function post(path, data){
  if(busy) return null;
  busy=true;error('');
  try {
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify(data)});
    const result=await response.json();if(!response.ok)throw Error(result.error||'Ошибка операции');
    await refresh(true);return result;
  }catch(e){error(e.message);return null}finally{busy=false}
}
async function refresh(force=false){
  try{
    const response=await fetch('/api/state');if(!response.ok)throw Error('Сервер недоступен');
    const next=await response.json();const sig=JSON.stringify(next);
    if(!force && ($('#detail').contains(document.activeElement) && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)))return;
    state=next;
    if(force||sig!==signature){signature=sig;render()}
  }catch(e){error('Не удалось обновить ящик. Проверьте, запущен ли локальный сервер.')}
}
function setFilter(value){filter=value;renderMail()}
function currentRows(){return state.actions.map(a=>({...a,email:state.emails.find(e=>e.id===a.email_id)})).filter(a=>a.email).reverse()}
function badge(row){const style=['blocked','error'].includes(row.status)?row.status:row.autonomy;return `<span class="badge ${esc(style)}">${esc(statuses[row.status]||autonomies[row.autonomy])}</span>`}
function render(){
  const demo=state.mode==='scripted';$('#mode').textContent=demo?'Сценарное демо':'Qwen · Groq';
  $('#new-email').classList.toggle('hidden',demo);$('#load-demo').classList.toggle('hidden',!demo);
  $('#mode-note').textContent=demo?'Сценарное демо: решения заданы заранее, модель не вызывается. Все почтовые действия — локальная имитация.':'Анализ через Groq. Почтовый ящик локальный: отправка — имитация, адресат ничего не получит.';
  $('#nav-count').textContent=state.emails.length;renderMail();renderMemory();
}
function renderMail(){
  if(!state)return;
  const all=currentRows();$('#count-all').textContent=all.length;
  $('#count-pending').textContent=all.filter(r=>r.status==='pending').length;
  $('#count-archived').textContent=all.filter(r=>r.email.archived).length;
  $('#count-attention').textContent=all.filter(r=>['blocked','escalated','error'].includes(r.status)).length;
  const search=$('#search').value.toLocaleLowerCase();
  const rows=all.filter(r=>(filter==='all'||(filter==='pending'&&r.status==='pending')||(filter==='archived'&&r.email.archived)||(filter==='attention'&&['blocked','escalated','error'].includes(r.status)))&&(`${r.email.subject} ${r.email.sender}`).toLocaleLowerCase().includes(search));
  $('#list-count').textContent=rows.length;
  document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('selected',b.dataset.filter===filter));
  $('#filter-label').classList.toggle('hidden',filter!=='attention');$('#filter-label').textContent='Фильтр: остановлено, ошибка или нужен ваш разбор. Нажмите «Все» для сброса.';
  if(!rows.some(r=>r.id===selected))selected=rows[0]?.id;
  $('#email-list').innerHTML=rows.length?rows.map(r=>`<button class="email-item ${r.id===selected?'selected':''}" data-id="${r.id}"><div class="email-top"><span class="email-sender">${esc(r.email.sender)}</span><span class="badge ${esc(r.autonomy)}">${esc(autonomies[r.autonomy])}</span></div><h3>${esc(r.email.subject)}</h3><p class="email-preview">${esc(r.email.body)}</p>${badge(r)}</button>`).join(''):'<div class="empty-list">Писем пока нет.<br>Добавьте письмо или измените фильтр.</div>';
  $('#email-list').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{selected=Number(b.dataset.id);renderMail()}));
  const jobs=state.jobs.filter(j=>j.status!=='done');
  $('#jobs').innerHTML=jobs.map(j=>{const e=JSON.parse(j.email);return `<div class="job">${esc(e.subject)}<br>${j.status==='error'?'Не удалось обработать':j.status==='processing'?'Агент читает письмо…':'В очереди на анализ'}${j.status==='error'?`<details><summary>Описание ошибки и попытки</summary><pre>${esc(JSON.stringify(JSON.parse(j.diagnostics),null,2))}</pre></details>`:''}</div>`}).join('');
  renderDetail(rows.find(r=>r.id===selected));
}
function renderDetail(row){
  if(!row){$('#detail').innerHTML='<div class="empty"><div class="empty-icon">✉</div><h2>Выберите письмо</h2><p>Здесь появятся текст, решение агента и история ваших действий.</p></div>';return}
  const p=row.proposal;const history=state.audit.filter(a=>a.action_id===row.id);
  const basis=history.find(a=>a.event==='learned_permission');const pref=basis?JSON.parse(basis.details):row.preference;
  const count=pref.approval_ids?.length||0;
  const flags=[['requires_action','Нужен ответ или действие'],['has_deadline','Есть срок'],['significant_change','Есть значимое изменение'],['sensitive','Чувствительная ситуация'],['suspicious','Подозрение на вмешательство']].filter(([k])=>p[k]);
  let explanation=basis?`Основание: ${count} одобрения для «${patterns[p.pattern]||p.pattern}». ${pref.scope==='*'?'Опыт общий для разных отправителей.':'Опыт этого отправителя.'}`:row.preference.mode==='keep'?'Действует ваше исключение: оставлять во входящих.':p.action==='archive'?`Одобрений в применимой группе: ${count} из 3. ${!row.learning_eligible?'Это письмо не подходит для самостоятельного архивирования.':'После трёх одобрений подходящие письма архивируются с уведомлением.'}`:'Самостоятельность выбирается по правилам допустимых действий.';
  const scope=`<label class="scope-label">К чему применить обратную связь<select id="feedback-scope"><option value="general">К этому типу писем от любых отправителей</option><option value="sender">Только к этому типу от ${esc(row.email.sender)}</option></select></label>`;
  let controls='';
  if(row.status==='pending')controls=`${p.action==='archive'?scope:''}<div class="decision-buttons"><button class="primary" id="approve">${p.action==='send'?'Подтвердить локальную отправку':'Одобрить архивирование'}</button><button class="secondary" id="reject">Отклонить</button></div>`;
  if(row.status==='executed'&&p.action==='archive'&&row.email.archived)controls=`${scope}<div class="decision-buttons"><button class="secondary" id="correct">Вернуть во входящие и исправить</button></div>`;
  $('#detail').innerHTML=`<div class="detail-heading"><span>ПИСЬМО № ${row.id}</span>${badge(row)}</div><h2>${esc(row.email.subject)}</h2><div class="sender-row"><span class="avatar">${esc(row.email.sender[0].toUpperCase())}</span><div>${esc(row.email.sender)}<small>Кому: ваш локальный ящик</small></div></div><p class="email-body">${esc(row.email.body)}</p><div class="decision"><div class="decision-title">✦ Решение агента · ${esc(actions[p.action]||p.action)}</div><p>${esc(p.reason)}</p><div class="decision-meta">${esc(explanation)}<br>Паттерн: ${esc(patterns[p.pattern]||p.pattern)}${p.label?`<br>Метка: ${esc(p.label)}`:''}${flags.length&&p.pattern_evidence?`<br>${esc(flags.map(f=>f[1]).join(' · '))}`:''}</div>${p.pattern_evidence?`<blockquote class="reason-quote">${esc(p.pattern_evidence)}</blockquote>`:''}${p.text?`<div class="send-preview">${p.recipient?`Получатель: ${esc(p.recipient)}<br>`:''}${esc(p.text)}</div>`:''}${controls}${p.action==='send'&&row.status==='pending'?'<details class="send-editor"><summary>Изменить текст и получателя</summary><label>Получатель<input id="edit-recipient" type="email"></label><label>Текст<textarea id="edit-text" rows="4"></textarea></label><div class="decision-buttons"><button class="secondary" id="save-edit">Сохранить новую версию</button></div></details>':''}<div class="decision-buttons"><button class="secondary" id="keep-sender">Всегда оставлять от этого отправителя</button></div></div><details class="history"><summary>История решения · ${history.length} записей</summary>${history.map(a=>`<div class="history-item">${esc(events[a.event]||a.event)}<small>${esc(new Date(a.created_at).toLocaleString('ru-RU'))}</small><details><summary>Подробности</summary><pre>${esc(JSON.stringify(JSON.parse(a.details),null,2))}</pre></details></div>`).join('')}</details>`;
  const scopeValue=()=>$('#feedback-scope')?.value||'general';
  for(const [id,route,message] of [['approve','approve','Действие подтверждено'],['reject','reject','Действие отклонено'],['correct','correct','Письмо возвращено. Исправление сохранено']]){
    $(`#${id}`)?.addEventListener('click',async()=>{const r=await post(`/api/${route}`,{action_id:row.id,revision:row.revision,scope:scopeValue()});if(r)notify(message)})
  }
  $('#keep-sender').addEventListener('click',async()=>{const r=await post('/api/rule',{sender:row.email.sender,keep:true});if(r)notify('Письма этого отправителя будут оставаться во входящих')});
  if($('#save-edit')){
    $('#edit-recipient').value=p.recipient;$('#edit-text').value=p.text;
    $('#save-edit').addEventListener('click',async()=>{const r=await post('/api/edit',{action_id:row.id,revision:row.revision,recipient:$('#edit-recipient').value,text:$('#edit-text').value});if(r)notify('Сохранена новая версия. Проверьте её перед подтверждением')})
  }
}
function renderMemory(){
  if(!state)return;
  const groups=new Map();for(const f of state.preference_feedback){const key=JSON.stringify([f.scope,f.pattern]);if(!groups.has(key))groups.set(key,{scope:f.scope,pattern:f.pattern,count:0});const group=groups.get(key);group.count=f.positive?group.count+1:0}
  $('#memory-groups').innerHTML=groups.size?[...groups.values()].map(g=>`<div class="memory-card"><h2>${esc(patterns[g.pattern]||g.pattern)}</h2><p class="scope-name">${g.scope==='*'?'Общий опыт · любые отправители':esc(g.scope)}</p><strong>${g.count} <small>/ 3</small></strong><p>${g.count>=3?'Достаточно опыта для подходящих писем. Исключения и признаки риска проверяются отдельно.':'Агент продолжает спрашивать. Нужны явные одобрения.'}</p></div>`).join(''):'<div class="memory-card"><h2>Знакомимся с вашими предпочтениями</h2><p>Одобряйте или исправляйте архивирование. Здесь появится история опыта по смысловым паттернам.</p></div>';
  $('#rules').innerHTML=state.archive_rules.map((r,i)=>`<div class="rule-row"><span>${esc(r.scope==='*'?'Все отправители':r.scope)} · ${esc(r.pattern==='*'?'Все типы писем':patterns[r.pattern])}</span><button class="secondary" data-remove-rule="${i}">Убрать исключение</button></div>`).join('');
  $('#rules').querySelectorAll('button').forEach(b=>b.addEventListener('click',async()=>{const r=state.archive_rules[Number(b.dataset.removeRule)];if(await post('/api/rule',{sender:r.scope,pattern:r.pattern,keep:false}))notify('Исключение удалено')}));
}
function navigate(next){page=next;$('#mail-view').classList.toggle('hidden',page!=='mail');$('#memory-view').classList.toggle('hidden',page!=='memory');$('#nav-mail').classList.toggle('active',page==='mail');$('#nav-memory').classList.toggle('active',page==='memory')}
$('#nav-mail').addEventListener('click',()=>navigate('mail'));$('#back-mail').addEventListener('click',()=>navigate('mail'));$('#nav-memory').addEventListener('click',()=>navigate('memory'));
document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>setFilter(b.dataset.filter)));
$('#search').addEventListener('input',renderMail);$('#refresh').addEventListener('click',()=>refresh(true));
$('#new-email').addEventListener('click',()=>$('#compose').showModal());$('#close-compose').addEventListener('click',()=>$('#compose').close());
$('#compose-form').addEventListener('submit',async e=>{e.preventDefault();$('#submit-email').disabled=true;try{const r=await post('/api/ingest',Object.fromEntries(new FormData(e.target)));if(r){$('#compose').close();e.target.reset();navigate('mail');setFilter('all');notify('Письмо добавлено. Агент начал обработку')}}finally{$('#submit-email').disabled=false}});
$('#load-demo').addEventListener('click',async()=>{if(await post('/api/demo',{}))notify('Сценарные письма загружены. Повторная загрузка не дублирует действия')});
$('#rule-form').addEventListener('submit',async e=>{e.preventDefault();const sender=new FormData(e.target).get('sender');if(await post('/api/rule',{sender,keep:true})){e.target.reset();notify('Исключение сохранено')}});
refresh();setInterval(()=>refresh(),2500);
