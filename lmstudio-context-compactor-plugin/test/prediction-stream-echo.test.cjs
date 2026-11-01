'use strict';
const test=require('node:test'), assert=require('node:assert/strict');
const {DeliveredPrefixEchoFilter}=require('../dist/prediction-stream');
function run(prefix,text,fragmentSize) {
 const filter=new DeliveredPrefixEchoFilter(prefix);let output='';
 for(let i=0;i<text.length;i+=fragmentSize)output+=filter.push(text.slice(i,i+fragmentSize));
 return {output:output+filter.finish(),suppressed:filter.duplicateSuppressed,chars:filter.suppressedChars};
}
for(const size of [1,3,1000])test(`delivered prefix filtering preserves deltas and the 90% boundary (fragments=${size})`,()=>{
 const prefix='abcdefghij';
 assert.deepEqual(run(prefix,'abcdefghi',size),{output:'',suppressed:true,chars:9});
 assert.deepEqual(run(prefix,'abcdefgh',size),{output:'abcdefgh',suppressed:false,chars:0});
 assert.equal(run(prefix,'abcdefghX',size).output,'abcdefghX');
 assert.equal(run(prefix,'abcdefghiX',size).output,'X');
 assert.equal(run(prefix,prefix+'\n추가 결과',size).output,'\n추가 결과');
 assert.equal(run('  보고 A\n결론 B  ','\n보고   A\t결론 B\n\n추가😀',size).output,'\n\n추가😀');
 assert.equal(run('123456789보고 😀','123456789보고 😃',size).output,'😃');
 assert.equal(run('','ordinary output',size).output,'ordinary output');
 assert.equal(run(prefix,'unrelated response',size).output,'unrelated response');
});

// Conversation 1793526507401: the report review repeated the whole delivered
// report but fixed one typo ("해armless"->"harmless") at 14%, so the exact
// prefix match failed and the user saw the final report twice.
const REPORT=['# 리뷰 보고서','','## P2 — 수정 권장','','1. `ACombatGameState::SetScenario` 실패 시 부분 변경과 rewind 플래그 이중 SSOT. CombatGameState.cpp:72-81.',
 '**P3: 3건** (해armless한 중복 검사), 나머지는 정상.','','## P3 — 정리 후보 (해armless)','1. CombatTimeSyncComponent 오프셋 갱신이 단조가 아님.',
 '2. 벽 명중 시 ApplyPointDamage가 비캐릭터에도 호출됨.','','## 영역별 종합','- 네트워크 RPC 계약: Server/Client 지정자와 권한 가드 정합.',
 '- SSOT: 탄약·체력·실험 상태는 서버 단일 출처.','- 수명관리: TWeakObjectPtr와 EndPlay 정리 경로 적절.'].join('\n');
const FIXED=REPORT.replace(/해armless/g,'harmless');
for(const size of [1,7,1000])test(`a long report echoed with a small edit is suppressed (fragments=${size})`,()=>{
 assert.ok(REPORT.length>=200);
 const echoed=run(REPORT,FIXED,size);
 assert.equal(echoed.output,'');assert.equal(echoed.suppressed,true);
 // A trailing newline after the echo is not an empty supplement.
 assert.equal(run(REPORT,FIXED+'\n\n',size).output,'');
 // An edited echo followed by the requested supplement keeps only the supplement.
 const supplement='\n\n## 검토 보완\n- InitNewPlayer 지적은 엔진 순서상 철회합니다.';
 assert.equal(run(REPORT,FIXED+supplement,size).output,supplement);
 // A supplement alone, or a genuinely rewritten report, is not an echo.
 assert.equal(run(REPORT,supplement.trimStart(),size).output,supplement.trimStart());
 const rewritten=REPORT.replace(/\. /g,' 입니다. ').replace(/:/g,' →');
 assert.equal(run(REPORT,rewritten,size).output,rewritten);
});
