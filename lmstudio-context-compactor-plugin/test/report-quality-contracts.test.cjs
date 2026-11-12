"use strict";
const test = require('node:test'), assert = require('node:assert/strict');
const { ChatMessage } = require('@lmstudio/sdk');
const { DeliveredPrefixEchoFilter } = require('../dist/prediction-stream');
const { ReviewRegister } = require('../dist/review-register');
const { cleanVerdictScopeGap } = require('../dist/report-coverage');
const say = text => [ChatMessage.create('assistant', text)];
const report = ['# Architecture review', ...Array.from({length:12}, (_,i)=>`- Checked relation ${i}: definition, consumer and cleanup were inspected.`),
  '## Conclusion', '- Source: A.cpp @abcdef, P3, 0 defects. 이 규모에서 문제 없음.', '- Disposition: cleared.', '- Remaining scope: static inference only.'].join('\n');
function render(prefix, text, size) {
  const filter = new DeliveredPrefixEchoFilter(prefix); let out='';
  for(let i=0;i<text.length;i+=size) out+=filter.push(text.slice(i,i+size));
  return out+filter.finish();
}
for(const size of [1,7,10000]) test(`report corrections survive duplicate suppression (${size})`,()=>{
  for(const [before,after] of [['문제 없음','문제 있음'],['P3','P1'],['0 defects','1 defect'],['A.cpp','B.cpp'],['abcdef','abcdeg'],['cleared','not cleared']]) {
    const changed=report.replace(before,after), out=render(report,changed,size);
    assert.ok(out.includes(after),`${before} -> ${after}`);
    assert.ok(out.includes('## Conclusion'),'correction retains its context');
  }
  const deleted=report.replace('- Disposition: cleared.\n','');
  assert.equal(render(report,deleted,size),deleted,'a removed finding cannot disappear as an empty delta');
  const shortened=report.slice(0,report.lastIndexOf('\n'));
  assert.equal(render(report,shortened,size),shortened,'deleting the final line also changes the report');
  const code=report+'\n```python\nif alive:\n    release()\n```';
  const changed=code.replace('    release()','release()');
  assert.equal(render(code,changed,size),changed,'code whitespace can change meaning');
  assert.equal(render(report,report+'\nAdditional evidence.',size),'\nAdditional evidence.');
  const inline=report+'\nUse `value === "a b"` for matching.';
  const inlineChanged=inline.replace('"a b"','"a  b"');
  assert.equal(render(inline,inlineChanged,size),inlineChanged);
  const scoped=report+'\n`CheckAccess` is safe.';
  const retracted=scoped.replace('`CheckAccess` is safe.','The following claim is retracted:\n`CheckAccess` is safe.');
  assert.equal(render(scoped,retracted,size),retracted,'insertions need their referenced context');
  const sentence=report+'\n`Foo` is safe';
  const qualified=sentence+' only after fixing its caller.';
  assert.equal(render(sentence,qualified,size),qualified,'same-sentence qualifications retain their subject');
});

test('conditional or negated retractions do not clear a claim',()=>{
  for(const text of ['`Foo` cannot be cleared yet.','`Foo` is not fully resolved.','`Foo` may be resolved if callers are changed.',
    '`Foo` 정상 아님.','`Foo` has no leak only on the successful path; cancellation leaks.',
    '`Foo` will be resolved after fixing the caller.','Is `Foo` resolved?']) {
    const register=new ReviewRegister();
    register.observe(say('`Foo` P2 bug at Source/Alpha.cpp:10.'),1);
    register.observe(say(text),2);
    assert.ok(register.candidates().some(row=>row.identifier==='Foo'),text);
  }
});
test('explicit scope misses and later sentences never clear a different owner',()=>{
  const one=new ReviewRegister();
  one.observe(say('`Foo` P2 bug at Source/Alpha.cpp:10.'),1);
  one.observe(say('`Foo` in Beta is cleared.'),2);
  assert.equal(one.rows.find(row=>row.sourcePath==='Source/Alpha.cpp').status,'found');
  for(const text of ['`Foo` of Beta is cleared.','`Foo`는 Beta에서 정상입니다.']) {
    one.observe(say(text),3);
    assert.notEqual(one.rows.find(row=>row.sourcePath==='Source/Alpha.cpp').status,'cleared',text);
  }
  const two=new ReviewRegister();
  two.observe(say('`TickServer` P2 bug: calls `DisableMovement` but never restores movement. A.cpp:10.'),1);
  two.observe(say('In B, `DisableMovement` is resolved. `A` still needs recheck.'),2);
  assert.equal(two.rows.find(row=>row.identifier==='TickServer').status,'found');
  two.observe(say('`DisableMovement` in A is correct.'),3);
  assert.equal(two.rows.find(row=>row.identifier==='TickServer').status,'needs_recheck','co-occurrence cannot certify the same issue was resolved');
});

test('a named disposition reaches a related claim only in its explicit source scope',()=>{
  const register=new ReviewRegister();
  register.observe(say('`TickServer` P2 at Source/Scenario.cpp:210: calls `DisableMovement` but never restores movement.'),1);
  register.observe(say('`TickOther` P2 at Source/Other.cpp:210: calls `DisableMovement` but never restores movement.'),1);
  register.observe(say('The P2 concern about `DisableMovement` in Scenario is resolved: RestoreParticipants restores movement.'),2);
  assert.equal(register.rows.find(r=>r.identifier==='TickServer').status,'needs_recheck');
  assert.equal(register.rows.find(r=>r.identifier==='TickOther').status,'found');
  assert.equal(register.rows.find(r=>r.identifier==='DisableMovement').status,'cleared');
});
test('a component verdict does not clear all members, and unresolved is not cleared',()=>{
  const register=new ReviewRegister();
  register.observe(say('`TickServer` P2 at Source/Scenario.cpp:210: calls `DisableMovement` but never restores movement.'),1);
  register.observe(say('`Scenario` is correct.'),2);
  assert.equal(register.rows.find(r=>r.identifier==='TickServer').status,'found');
  for(const text of ['`TickServer` is not resolved.','`TickServer` is not cleared.','`TickServer` correctness remains unknown.']) {
    register.observe(say(text),3);
    assert.notEqual(register.rows.find(r=>r.identifier==='TickServer').status,'cleared',text);
  }
});
test('source identity is extracted before the display excerpt is truncated',()=>{
  const register=new ReviewRegister();
  register.observe(say('`TickServer` P2 '+ 'evidence '.repeat(40)+' Source/Scenario.cpp:210'),1);
  register.observe(say('`TickServer` P2 at Source/Other.cpp:3'),2);
  register.observe(say('`TickServer` at Source/Scenario.cpp:210 is cleared.'),3);
  assert.ok(register.candidates().some(r=>r.evidence.includes('Source/Other.cpp')));
  assert.equal(register.candidates().length,1);
});

test('partial files alone do not invalidate a conclusion inferred from required relations',()=>{
  const source={listed:{overflow:false,directories:[{path:'Source',complete:true,subdirectories:[],files:2}],
    files:[{path:'Source/A.cpp',sourceKey:'A',coverage:'partial'},{path:'Source/B.h',sourceKey:'B',coverage:'unread'}]}};
  assert.equal(cleanVerdictScopeGap('## Conclusion\nNo defects were found. The required definitions and lifecycle relations support this assessment.',source),null);
  const gap=cleanVerdictScopeGap('Scope: All source files were read in full.\n## Conclusion\nNo defects were found.',source);
  assert.ok(gap,'a false claim of actually reading everything is still checked');
  assert.doesNotMatch(gap.message,/끝까지.*읽어|전부.*읽어/);
  for(const text of ['전체 파일을 모두 읽을 필요는 없습니다. 필요한 함수와 호출 관계를 확인했습니다.',
    'All files were not read in full.','다음 단계에서는 전체 파일을 전량 확인할 예정입니다.',
    'All files must be inspected in full.','TickServer 함수의 정의를 끝까지 읽었습니다.',
    '이번에 반환된 세 줄은 모두 확인했습니다.','> All source files were read in full.',
    '```text\nAll source files were read in full.\n```']) assert.equal(cleanVerdictScopeGap(text,source),null,text);
});

test('a blocked supplement cannot inherit the preceding report presentation',()=>{
  const { DeliveryController }=require('../dist/delivery-controller');
  const owner=new DeliveryController();
  owner.observe({messages:say('Report.'),finishReason:'eosFound',presentation:{generatedVisibleChars:7,deliveredChars:7,duplicateSuppressedChars:0}});
  owner.retainReportPrefix('Report.');owner.beginSupplement('report_review',true);owner.beginRound();owner.stop('context_budget');
  const outcome=owner.finish();
  assert.equal(outcome.supplement.state,'blocked');
  assert.equal(outcome.supplement.outputDisposition,undefined);
  assert.equal(outcome.reportDelivered,true);
});

test('bounded candidate text keeps both the original hypothesis and its related correction',()=>{
  const { reviewCandidateLines }=require('../dist/review-register');
  const row={identifier:'TickServer',status:'needs_recheck',source:'reasoning',round:3,
    evidence:'authority guard missing; '+ 'unverified context '.repeat(20),
    relatedDisposition:{identifier:'DisableMovement',evidence:'resolved via snapshot restoration'}};
  const lines=reviewCandidateLines([row]).join('\n');
  assert.match(lines,/원가설\(미검증\): authority guard missing/);
  assert.match(lines,/관련 정정\(DisableMovement\)/);
  assert.match(lines,/정정: resolved via snapshot/);
});

test('recorded retractions remain dispositions through the final report',()=>{
  const fixture=require('./fixtures/review-dispositions-1794323874297.json');
  const register=new ReviewRegister();
  const { ReviewEvidenceIndex }=require('../dist/review-register');
  const { pair }=require('./helpers/v4.cjs');
  const index=new ReviewEvidenceIndex();
  index.observe(pair('read_file_range',{path:fixture.sourceObservation.path,startLine:210,endLine:210},fixture.sourceObservation));
  for(const entry of fixture.passages) { register.observe(say(entry.text),entry.step); register.attach(index); }
  for(const identifier of ['TickServer','DisableMovement']) {
    const rows=register.rows.filter(row=>row.identifier===identifier);
    assert.ok(rows.length,identifier);
    assert.ok(rows.every(row=>row.status===(identifier==='TickServer'?'needs_recheck':'cleared')),identifier);
  }
  const notice=require('../dist/review-register').reportReviewNotice(register.candidates(),'duplicate');
  assert.match(notice,/판단 연결 미확인/);
  assert.match(notice,/원래 미검증 가설:/);
  assert.match(notice,/관련 정정/);
  const fact=require('../dist/review-register').reportReviewFact(register.candidates(),[]);
  assert.match(fact,/원래 미검증 가설:/);
  assert.match(fact,/관련 정정/);
});

const { Chat } = require('@lmstudio/sdk');
const { fakeController, handlePredictionLoop } = require('./helpers/prediction-loop-fixture.cjs');
const { sourceHistory, tool } = require('./helpers/v4.cjs');
const { REASONING_SEPARATOR } = require('../src/continuity-text.js');
for(const streaming of [false,true]) for(const mode of ['empty','repeat','repeat_space','correction'])
test(`report delivery distinguishes ${mode} from generation and keeps prior output (stream=${streaming})`,async()=>{
  let calls=0;
  const repeat=mode.startsWith('repeat');
  const reply=mode==='empty'?'':repeat?report+(mode==='repeat_space'?'\n\n':''):report.replace('문제 없음','문제 있음');
  const model={identifier:'quality-replay',async getContextLength(){return 65536;},
    async applyPromptTemplate(chat){return chat.toString();},async countTokens(){return 1000;},
    async act(_chat,_tools,opts){
      calls++;
      const text=calls===1?'`ReloadSound` is unused.'+REASONING_SEPARATOR+report:reply;
      if(streaming && calls>1) for(let i=0;i<text.length;i+=7) opts.onPredictionFragment({content:text.slice(i,i+7),roundIndex:0,
        reasoningType:'none',isStructural:false,tokensCount:1,containsDrafted:false});
      opts.onMessage(ChatMessage.create('assistant',text));
      opts.onPredictionCompleted({stats:{stopReason:'eosFound'}});
    }};
  const ctl=fakeController(sourceHistory('Review architecture.',['ReloadSound']),model,
    {designGuidanceMode:'off',reportReviewMode:'on'},[tool('read_file')]);
  await handlePredictionLoop(ctl);
  assert.equal(calls,2,'one bounded supplement, no extra research');
  const outcome=ctl.debugValues.find(e=>e.event==='execution_outcome');
  assert.equal(outcome.reportDelivered,true);
  assert.notEqual(outcome.taskCompleted,true,'delivery never certifies the audit objective');
  // A near-verbatim copy with one change is shown as the changed section only.
  assert.equal(outcome.supplement.outputDisposition,mode==='empty'?'no_generation':repeat?'duplicate_only':'salvaged');
  const shown=ctl.blocks.filter(b=>b.options?.includeInContext!==false).map(b=>b.text).join('');
  assert.ok(shown.includes('문제 없음'),'the prior report remains available');
  if(mode==='correction') {
    assert.ok(shown.includes('문제 있음'),'the user receives the polarity correction');
    assert.equal(shown.split('Checked relation 0').length-1,1,'the unchanged report is not shown a second time');
    assert.match(shown,/## Conclusion\n- Source: A\.cpp @abcdef, P3, 0 defects\. 이 규모에서 문제 있음\./,'with the section it corrects');
    assert.match(shown,/앞선 보고서에서 바뀌거나 빠진 줄: - Source: A\.cpp @abcdef, P3, 0 defects\. 이 규모에서 문제 없음\./,'and the original claim');
  }
  if(repeat) {
    const event=ctl.debugValues.find(e=>e.event==='supplement_duplicate_suppressed');
    assert.equal(event.generatedVisibleChars,reply.length);
    assert.equal(event.deliveredChars,0);
    assert.ok(event.priorReportChars>0);
    assert.ok(ctl.statuses.some(status=>/중복된 출력/u.test(status.state?.text||'')));
    assert.doesNotMatch(shown,/내용을 생성하지 않았습니다/);
  }
});
