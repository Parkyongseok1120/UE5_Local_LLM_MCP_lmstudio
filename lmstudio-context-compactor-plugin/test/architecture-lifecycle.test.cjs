"use strict";
const test=require("node:test"),assert=require("node:assert/strict");
const {ArchitectureState,binding,evidence,draft,issue,populated}=require("./helpers/architecture.cjs");
test("L01 changed dependency stales only its judgment",()=>{
  const state=populated(),refs=issue(state,[evidence({sourceKey:"B.cpp"})]);state.commit({upsert:[draft({id:"decision_b",refs:[...refs.evidence.keys()]})]},refs);
  state.invalidate([evidence({observedVersion:"2".repeat(64)})]);
  assert.deepEqual(state.snapshot().architecture.map(d=>d.applicability),["stale","same_observed_version"]);
});
test("L02 no applied change leaves the observed judgment intact",()=>{const state=populated();state.invalidate([]);assert.equal(state.snapshot().architecture[0].applicability,"same_observed_version");});
test("L03 unknown effect does not become applied or rolled back",()=>{const state=populated();state.invalidateUnknown();assert.equal(state.snapshot().architecture[0].applicability,"unknown");assert.equal(state.snapshot().architecture[0].state,"selected");});
test("L04 uncertain same-batch observations cannot choose a latest version",()=>{const state=populated();state.invalidate([evidence({applicability:"unknown"})]);assert.equal(state.snapshot().architecture[0].applicability,"unknown");});
test("L05 archival availability changes independently of source version",()=>{const state=populated();state.invalidate([evidence({availability:"archived"})]);assert.equal(state.snapshot().architecture[0].applicability,"same_observed_version");assert.equal(state.snapshot().architecture[0].evidenceRefs[0].availability,"archived");});
test("L06 catalog change invalidates pattern-dependent prior advice only",()=>{
  const {catalog}=require("./helpers/architecture.cjs"),state=new ArchitectureState(binding()),refs=issue(state,[evidence()],[catalog.patterns[0]]);
  state.commit({upsert:[draft({patternRefs:[...refs.patterns.keys()]})]},refs);state.syncBinding(binding({catalogHash:"b".repeat(64)}));assert.equal(state.snapshot().architecture[0].applicability,"stale");
  const unrelated=populated();unrelated.syncBinding(binding({catalogHash:"b".repeat(64)}));assert.equal(unrelated.snapshot().architecture[0].applicability,"same_observed_version");
});
test("L07 closed or stale-revision updates are rejected",()=>{
  const state=populated(),refs=issue(state);state.invalidateUnknown();assert.equal(state.commit({upsert:[draft()]},refs).disposition,"reject");state.close();assert.equal(state.commit({},issue(state)).disposition,"reject");
});
test("L08 uncommitted model planning cannot change durable judgments",()=>{
  const {RoundTransaction}=require("../dist/execution-state");const tx=new RoundTransaction({predictionCompleted:false,messages:[],finishReason:"maxPredictedTokensReached"},true);
  const state=populated(),before=state.snapshot();if(tx.planningCommitted)state.commit({retireIds:["decision_a"]},issue(state));assert.deepEqual(state.snapshot(),before);
});
test("L09 late callbacks cannot mutate closed state",()=>{const state=populated(),before=state.snapshot();state.close();state.invalidateUnknown();state.syncBinding(binding({projectIdentity:"other"}));assert.deepEqual(state.snapshot(),before);});
test("L10 a same-round change invalidates issued evidence before a new judgment",()=>{
  const state=new ArchitectureState(binding()),refs=issue(state);state.revalidateIssued(refs,[evidence({applicability:"stale"})]);
  assert.equal(state.commit({upsert:[draft({refs:[...refs.evidence.keys()]})]},refs).reason,"superseded_reference");
});
test("L11 project changes clear applicability without changing tool settings",()=>{const state=populated();state.syncBinding(binding({projectIdentity:"other"}));assert.equal(state.snapshot().architecture.length,0);});
test("L12 restored forks share no mutable judgment state",()=>{
  const parent=populated(),fork=new ArchitectureState(binding({executionId:"fork",lineage:"fork"}),parent.snapshot());
  fork.invalidateUnknown();assert.equal(parent.snapshot().architecture[0].applicability,"same_observed_version");
});
test("L13 terminal lease release stays once and preserves seal error",()=>{
  const {sealAndRelease}=require("../dist/terminal-persistence");let releases=0;
  assert.throws(()=>sealAndRelease({seal(){throw Error("seal");},close(){releases++;throw Error("close");}},null,null,[]),/seal/);assert.equal(releases,1);
});
