// Pure UI guards; no browser, credentials, network, or Gmail writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../mail_agent/static/app.js'), 'utf8');
function extract(start, end) { return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start))); }
const context = vm.createContext({});
vm.runInContext(extract('function exactEditedReply(', 'async function saveAndSendEditedReply('), context);
const expected = {action_id: 7, sender:'owner@example.test', recipient:'sender@example.test', subject:'Re: Update', text:'Received, thank you.'};
const saved = {id:7, revision:4, reply:{sender:expected.sender, recipient:expected.recipient, subject:expected.subject, text:expected.text}};
assert.equal(context.exactEditedReply(saved, expected, 4), true);
for (const field of ['sender','recipient','subject','text']) {
  assert.equal(context.exactEditedReply({...saved, reply:{...saved.reply,[field]:'changed'}}, expected, 4), false, field);
}
assert.equal(context.exactEditedReply({...saved, revision:5}, expected, 4), false);
assert.equal(context.exactEditedReply({...saved, id:8}, expected, 4), false);
assert.equal(context.exactEditedReply({...saved, reply:null}, expected, 4), false);
vm.runInContext('const expandedBodies=new Set();const esc=x=>String(x);' + extract('function emailBody(', 'function bindEmailBody('), context);
const body = 'A '.repeat(47) + 'extracting details from the message';
const collapsed = context.emailBody({email:{id:'one', body}});
assert.ok(collapsed.includes('…'));
assert.ok(!collapsed.includes('extr…'));
assert.ok(collapsed.includes('Read full email'));
vm.runInContext("expandedBodies.add('one')", context);
assert.ok(context.emailBody({email:{id:'one', body}}).includes(body));
assert.ok(!context.emailBody({email:{id:'short', body:'Short email'}}).includes('Read full email'));
console.log('Review UI guards passed: exact edit approval and whole-word previews.');
async function testEditedApproval(status, mutate, accountChanged=false) {
  const calls=[],errors=[];
  const action={...saved,status,reply:{...saved.reply,...mutate}};
  const flow=vm.createContext({
    state:{gmail_connection:{account:'owner@example.test'}},approvalInProgress:false,replyEditing:true,detailDirty:true,
    $:()=>({querySelectorAll:()=>[]}),notify:()=>{},error:message=>errors.push(message),
    currentRows:()=>[action],refresh:async()=>{},setTimeout,
    post:async(route,payload)=>{calls.push([route,payload]);if(accountChanged)flow.state.gmail_connection.account='other@example.test';return {}},
  });
  vm.runInContext(extract('function exactEditedReply(', 'function renderDetail('),flow);
  await flow.saveAndSendEditedReply({id:7,revision:3},{sender:expected.sender},expected);
  assert.equal(flow.approvalInProgress,false);
  return {calls,errors};
}
(async()=>{
  const ok=await testEditedApproval('pending');
  assert.deepEqual(ok.calls.map(x=>x[0]),['/api/edit','/api/approve']);
  assert.equal(ok.calls[1][1].revision,4);
  for(const [status,mutation,changedAccount] of [['error',{},false],['pending',{text:'rewritten'},false],['pending',{},true]]){
    const stopped=await testEditedApproval(status,mutation,changedAccount);
    assert.deepEqual(stopped.calls.map(x=>x[0]),['/api/edit']);
    assert.equal(stopped.errors.length,1);
  }
  console.log('Edited approval flow passed: exact saved draft only; error, rewrite and account change stop sending.');
})().catch(error=>{console.error(error);process.exitCode=1});
