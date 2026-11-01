"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const { ChatMessage } = require("@lmstudio/sdk");
const { ReviewRegister, ReviewEvidenceIndex } = require("../dist/review-register");
const { fileCoverageNotice } = require("../dist/quote-anchoring");
const { ledgerView } = require("./helpers/returned-sources.cjs");
function read(id, path, body) {
  return [ChatMessage.from({role:"assistant",content:[{type:"toolCallRequest",toolCallRequest:{type:"function",id,name:"read_file",arguments:{path}}}]}),
    ChatMessage.from({role:"tool",content:[{type:"toolCallResult",toolCallId:id,content:JSON.stringify({ok:true,path,startLine:1,sha256:"ab".repeat(32),content:body})}]})];
}
function register(subjects) {
  const r = new ReviewRegister(), index = new ReviewEvidenceIndex();
  for (const [i, [name,path]] of subjects.entries()) {
    index.observe(read(`r${i}`,path,`float ${name.split("::").at(-1)};`));
    r.observe([ChatMessage.create("assistant",`\`${name}\` unused, at ${path}:1`)],i+1);
  }
  r.attach(index); return r;
}
test("Q01 unique qualified finding is covered by its exact leaf",()=>{
  const r=register([["FWeaponStats::HeadshotMultiplier","project://Source/Stats.h"]]);
  assert.equal(r.missing("HeadshotMultiplier는 현재 사용처가 확인되지 않았다.").length,0);
});
test("Q02 duplicate leaves require a qualified name or file context",()=>{
  const r=register([["FWeaponStats::HeadshotMultiplier","project://Source/Weapon.h"],["FCharacterStats::HeadshotMultiplier","project://Source/Character.h"]]);
  assert.equal(r.missing("HeadshotMultiplier unused.").length,2);
  assert.equal(r.missing("FWeaponStats::HeadshotMultiplier unused.").length,1);
});
test("Q03 tokens cannot be covered by a longer identifier",()=>{
  const r=register([["HealthRegenRate","project://Source/Stats.h"]]);
  assert.equal(r.missing("OtherHealthRegenRateSuffix unused.").length,1);
});
test("Q04 needs_recheck requires a disposition, not a name only",()=>{
  const r=register([["FWeaponStats::HeadshotMultiplier","project://Source/Stats.h"]]);
  r.rows[0].status="needs_recheck";
  assert.equal(r.missing("HeadshotMultiplier.").length,1);
  assert.equal(r.missing("HeadshotMultiplier는 재검토가 필요하다.").length,0);
});
const sources=()=>ledgerView(read("r","project://Source/A.cpp","void F() {}"));
test("Q05 current overclaim still receives a coverage notice",()=>assert.ok(fileCoverageNotice(sources(),"전체 19개 파일을 확인했다.")));
test("Q06 withdrawn quoted overclaim does not become a current claim",()=>{
  assert.equal(fileCoverageNotice(sources(),"앞서 ‘전체 19개 파일 확인’이라고 쓴 표현은 철회한다. 검색 목록은 19개, 본문은 1개를 읽었다."),null);
});
test("Q07 correction wording cannot hide a renewed current overclaim",()=>{
  assert.ok(fileCoverageNotice(sources(),"정정한다. 그래도 전체 19개 파일을 검토한 것은 맞다."));
});
