'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {Chat}=require('@lmstudio/sdk');
const {reportScopeGaps,reportScopeReviewFact}=require('../dist/report-scope');
const {pair}=require('./helpers/v4.cjs');
const source={files:[{path:'Source/Public/FixtureHUD.h',sourceKey:'h',lines:new Map([[1,'virtual void DrawHUD() override;']]),scope:'execution',bodyReturned:true}],contractIds:new Set(),scope:'execution'};
test('unread listed implementation is not implementation absence; report corrections are bounded advisory data',()=>{
 const h=Chat.from([{role:'user',content:'검토해줘'}]);
 for(const m of pair('list_directory',{path:'Source/Private'},{ok:true,path:'Source/Private',truncated:false,entries:[{name:'FixtureHUD.cpp',type:'file'}]},'list'))h.append(m);
 const gaps=reportScopeGaps(h,'모든 소스 파일을 확인했습니다. DrawHUD() 구현 없음.',source);
 assert.ok(gaps.some(g=>g.kind==='implementation_scope'));assert.ok(gaps.some(g=>g.kind==='file_scope'));
 assert.match(reportScopeReviewFact(gaps),/no tools or new changes/);
 assert.equal(reportScopeGaps(h,'DrawHUD() 구현은 미확인입니다.',source).length,0);
});
test('structure-only verification overclaim joins existing report supplement obligations',()=>{
 const h=Chat.from([{role:'user',content:'감사해줘'}]);for(const m of pair('evidence_first_validate',{}, {ok:true,validationScope:'packet_structure_and_record_consistency',factualEvidenceVerified:false},'v'))h.append(m);
 assert.ok(reportScopeGaps(h,'검증 완료.',source).some(g=>g.kind==='verification_scope'));
});
test('follow-up changes account for prior rated items without treating them as source facts',()=>{
 const h=Chat.from([{role:'user',content:'감사해줘'},{role:'assistant',content:'## P1\n### 1. `RunSpeed` 구현 확인 필요\n## P2\n다른 사항'},{role:'user',content:'수정해볼래?'}]);
 const gaps=reportScopeGaps(h,'다른 버그를 수정했습니다.',source);assert.ok(gaps.some(g=>g.target==='RunSpeed'));assert.match(reportScopeReviewFact(gaps),/미검증/);
 assert.equal(reportScopeGaps(h,'RunSpeed는 원래 요구사항이 미확인이라 보류했습니다.',source).length,0);
});
// Conversation 1794469194476 wrote its P1 items as bold bullets under a bold
// heading, which the "## / ###" form alone did not see.
test('prior P1 items in bold, bullet or numbered form are followed up, nested evidence is not',()=>{
 const prior=['**P1 — 즉시 수정**','1. **상태 소유권** — `AEnemyBase`가 FSM을 소유','   - 근거: `Think` 호출 경로','**P2 — 정리**','- `OtherThing` 정리',
  '- **P1** `SpawnTable` 기본값 누락'].join('\n');
 const h=Chat.from([{role:'user',content:'검토해줘'},{role:'assistant',content:prior},{role:'user',content:'남은 것 다 수정해줘'}]);
 const targets=reportScopeGaps(h,'수정을 마쳤습니다.',source).filter(g=>g.kind==='prior_item_disposition').map(g=>g.target);
 assert.deepEqual(targets,['AEnemyBase','SpawnTable']);
});
// The same conversation: search_files read none of Source (coverage=partial, 0
// hits) and the report then stated that no spawner existed.
test('an absence claim backed only by a partial, empty search becomes a review item',()=>{
 const searched=(results,coverage)=>pair('search_files',{query:'Spawn',include:'**/*.h'},{ok:true,results,coverage,...(coverage==='partial'?{absenceVerified:false}:{})},'s'+results.length+coverage);
 const h=Chat.from([{role:'user',content:'적 스폰 구조 검토해줘'}]);for(const m of searched([],'partial'))h.append(m);
 const gaps=reportScopeGaps(h,'기존 Spawn 관리자가 없으므로 새 스포너가 필요합니다.',source);
 assert.ok(gaps.some(g=>g.kind==='partial_search_absence'&&g.target==='Spawn'),JSON.stringify(gaps));
 assert.equal(reportScopeGaps(h,'Spawn 경로는 CStageManager에 있습니다.',source).filter(g=>g.kind==='partial_search_absence').length,0,'no absence claim');
 const later=Chat.from(h);for(const m of searched([{uri:'project://Source/A.h',line:3,text:'Spawn'}],'partial'))later.append(m);
 assert.equal(reportScopeGaps(later,'기존 Spawn 관리자가 없으므로 새 스포너가 필요합니다.',source).filter(g=>g.kind==='partial_search_absence').length,0,
  'a later search that found the term settles it');
});
