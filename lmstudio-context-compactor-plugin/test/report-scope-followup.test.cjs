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
