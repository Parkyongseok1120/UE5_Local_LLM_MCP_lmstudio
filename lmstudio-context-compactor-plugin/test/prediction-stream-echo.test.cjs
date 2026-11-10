'use strict';
const test=require('node:test'), assert=require('node:assert/strict');
const {DeliveredPrefixEchoFilter}=require('../dist/prediction-stream');
function run(prefix,text,fragmentSize) {
 const filter=new DeliveredPrefixEchoFilter(prefix);let output='';
 for(let i=0;i<text.length;i+=fragmentSize)output+=filter.push(text.slice(i,i+fragmentSize));
 return {output:output+filter.finish(),suppressed:filter.duplicateSuppressed,chars:filter.suppressedChars};
}
for(const size of [1,3,1000])test(`delivered prefix filtering preserves deltas and shortened reports (fragments=${size})`,()=>{
 const prefix='abcdefghij';
 assert.deepEqual(run(prefix,'abcdefghi',size),{output:'abcdefghi',suppressed:false,chars:0});
 assert.deepEqual(run(prefix,'abcdefgh',size),{output:'abcdefgh',suppressed:false,chars:0});
 assert.equal(run(prefix,'abcdefghX',size).output,'abcdefghX');
 assert.equal(run(prefix,'abcdefghiX',size).output,'abcdefghiX');
 assert.equal(run(prefix,prefix+'\n추가 결과',size).output,'\n추가 결과');
 assert.equal(run('  보고 A\n결론 B  ','\n보고   A\t결론 B\n\n추가😀',size).output,'\n\n추가😀');
 assert.equal(run('123456789보고 😀','123456789보고 😃',size).output,'123456789보고 😃');
 assert.equal(run('','ordinary output',size).output,'ordinary output');
 assert.equal(run(prefix,'unrelated response',size).output,'unrelated response');
});

// Conversation 1793526507401: the report review repeated the whole delivered
// report but fixed one typo ("해armless"->"harmless") at 14%, so the exact
// prefix match failed and the user saw the final report twice. Quality takes
// precedence: the filter cannot tell a typo from a changed verdict.
const REPORT=['# 리뷰 보고서','','## P2 — 수정 권장','','1. `ACombatGameState::SetScenario` 실패 시 부분 변경과 rewind 플래그 이중 SSOT. CombatGameState.cpp:72-81.',
 '**P3: 3건** (해armless한 중복 검사), 나머지는 정상.','','## P3 — 정리 후보 (해armless)','1. CombatTimeSyncComponent 오프셋 갱신이 단조가 아님.',
 '2. 벽 명중 시 ApplyPointDamage가 비캐릭터에도 호출됨.','','## 영역별 종합','- 네트워크 RPC 계약: Server/Client 지정자와 권한 가드 정합.',
 '- SSOT: 탄약·체력·실험 상태는 서버 단일 출처.','- 수명관리: TWeakObjectPtr와 EndPlay 정리 경로 적절.'].join('\n');
const FIXED=REPORT.replace(/해armless/g,'harmless');
for(const size of [1,7,1000])test(`a long report with a small edit preserves the revised context (fragments=${size})`,()=>{
 assert.ok(REPORT.length>=200);
 const echoed=run(REPORT,FIXED,size);
 assert.equal(echoed.output,FIXED);assert.equal(echoed.suppressed,false);
 // Edits, including apparently harmless typos, must remain visible.
 assert.equal(run(REPORT,FIXED+'\n\n',size).output,FIXED+'\n\n');
 // An edited report and its supplement preserve the revised context together.
 const supplement='\n\n## 검토 보완\n- InitNewPlayer 지적은 엔진 순서상 철회합니다.';
 assert.equal(run(REPORT,FIXED+supplement,size).output,FIXED+supplement);
 // A supplement alone, or a genuinely rewritten report, is not an echo.
 assert.equal(run(REPORT,supplement.trimStart(),size).output,supplement.trimStart());
 const rewritten=REPORT.replace(/\. /g,' 입니다. ').replace(/:/g,' →');
 assert.equal(run(REPORT,rewritten,size).output,rewritten);
});

// Conversation 1793532481549: the review rewrote the whole report and inserted a
// new section at 71%. Insertions can qualify following text, so retain context.
const SECTION=['### 6. 실험 RPC 계약','- 낙관적 동시성: 클라이언트가 ExpectedRevision을 보내고 서버가 불일치 시 거부.','- Pending 자동 해제: OnRep_Experiment 뒤 false.'].join('\n');
const INSERTED=REPORT.replace('## 영역별 종합',SECTION+'\n\n## 영역별 종합');
for(const size of [1,7,1000])test(`an inserted section retains the report context (fragments=${size})`,()=>{
 const out=run(REPORT,INSERTED,size);
 assert.equal(out.output,INSERTED);assert.equal(out.suppressed,false);
 // A supplement the model already headed is kept after the inserted section, without a second heading.
 const own='## 검토 보완\n- InitNewPlayer 지적은 엔진 순서상 철회합니다.';
 assert.equal(run(REPORT,INSERTED+'\n\n'+own,size).output,INSERTED+'\n\n'+own);
 // An answer that starts like the report but then covers little of it is shown whole.
 const different=REPORT.slice(0,REPORT.indexOf('## P3'))+'\n완전히 다른 결론: 이 보고서는 범위를 벗어났습니다.\n'.repeat(3);
 assert.equal(run(REPORT,different,size).output,different);
});

// Conversation 1794306949131, round 52: the review copied the whole data block
// the report was shown in (header, body without blank lines, end line), then
// went on. The frame lines and the echo are hidden; only what follows shows.
const { DELIVERED_REPORT_HEADER, DELIVERED_REPORT_END } = require('../dist/review-patterns.js');
for(const size of [1,7,1000])test(`an echoed delivered-report frame is hidden with its header and end line (fragments=${size})`,()=>{
 const body=REPORT.split('\n').filter(line=>line.trim()).join('\n');
 const framed=`${DELIVERED_REPORT_HEADER}\n${body}\n${DELIVERED_REPORT_END}\n`;
 assert.equal(run(REPORT,framed,size).output,'');
 const out=run(REPORT,framed+'## 검토 보완\n- 추가 확인 없음.',size);
 assert.equal(out.output.trim(),'## 검토 보완\n- 추가 확인 없음.');assert.equal(out.suppressed,true);
 assert.doesNotMatch(out.output,/Delivered report|End delivered report/);
 // Ordinary text that merely starts with a bracket is untouched.
 assert.equal(run(REPORT,'[참고] 다른 답변입니다.',size).output,'[참고] 다른 답변입니다.');
});
