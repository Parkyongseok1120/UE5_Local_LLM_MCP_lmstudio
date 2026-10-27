"use strict";

// When a round rewrites its input. The 2026-10-25 run rewrote it ten times in
// one review: every projection also cut the conversation to low water, and one
// round dropped only its retained instruction (a full prompt rebuild that
// reclaimed nothing) before the next round compacted anyway.

const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { WorkingContext } = require("../src/working-context.js");
const { readConfig } = require("../dist/execution-config");
const { ContextManager } = require("../dist/context-manager");
const { BudgetBroker } = require("../dist/budget-broker");
const { EvidenceManager } = require("../dist/evidence-manager");
const { prepareRoundInput } = require("../dist/round-input");

const CONTEXT = 65536;
const tokenSource = {
  async getContextLength() { return CONTEXT; },
  async applyPromptTemplate(chat) { return chat.toString(); },
  async countTokens(text) { return Math.ceil(text.length / 4); },
};

function configFor(overrides = {}) {
  return readConfig({ getPluginConfig: () => ({ get: key => ({
    projectEngine: "unreal", contextManagementMode: "hybrid", showDebugInfo: true,
    maxOutputReserve: 4096, designGuidanceMode: "off", toolResultProjectionChars: 512, ...overrides,
  })[key] }) });
}

function water(config) {
  return new BudgetBroker(config).watermarks({ contextLength: CONTEXT, outputReserve: 4096, inputTokens: 0 }, 0, true, 2048);
}

/** Assistant progress messages totalling about `tokens` deterministic tokens. */
function withProse(history, tokens, parts = 8) {
  for (let i = 0; i < parts; i++)
    history.append(ChatMessage.from({ role: "assistant", content: `progress ${i} ${"p".repeat(Math.floor(tokens * 4 / parts))}` }));
  return history;
}

async function prepare(config, history, store, extra = {}) {
  const events = [];
  const tools = [{ name: "read_file", pluginIdentifier: "mcp/unreal-agent" }];
  const result = await prepareRoundInput({
    ctl: { debug(event) { events.push(event); }, guardAbort() {} }, config, tokenSource,
    contextManager: new ContextManager(config, new BudgetBroker(config)),
    evidenceManager: new EvidenceManager(store, tools), workingContext: store,
    workingHistory: history, visibleHistory: history, preProjectionHistory: history, postProjectionHistory: history,
    roundTools: tools, modelTools: tools, roundScopeInstructions: [], scopeInstructions: [],
    roundOutputReserve: 4096, activeNote: null, noteEnabled: false, historicalAvailabilityLedger: [],
    semanticSummaryCooldownUntilRound: 99, objectiveFingerprint: "rewrite-policy",
    finalizing: false, boundedAudit: true, researchRecoveryEpisodeStarted: false,
    finalizationTrigger: null, projectionApplied: false, executionId: "rewrite-policy",
    modelInputId: "rewrite-policy:input-1", roundIndex: 3, ...extra,
  });
  return { result, events, byEvent: name => events.filter(event => event.event === name) };
}

function store() {
  return new WorkingContext({ conversation: "rewrite", lineage: `rewrite-${Math.random()}`, repository: "fixture", workspace: "fixture" });
}

test("v3 projectionExhausted is measured after low-water compaction", async () => {
  const config = configFor({ workingInputScaling: "fixed", workingInputTargetTokens: 4000, workingInputTriggerTokens: 6700 });
  const w = water(config);
  const history = withProse(Chat.from([{ role: "user", content: "Review the synthetic fixture" }]),
    w.effectiveHighWaterTokens + 2000, 12);
  const wc = store();
  try {
    const { result, byEvent } = await prepare(config, history, wc);
    assert.equal(result.kind, "ready");
    assert.ok(byEvent("working_context_projection").some(e => e.pinsReleased));
    assert.ok(result.finalMeasurement.inputTokens < w.effectiveHighWaterTokens);
    assert.equal(result.projectionExhausted, false);
  } finally { wc.close(); }
});

test("v3 61029 input keeps pins before optional 5523 guidance at high water 63626", async()=>{
 const config=configFor({workingInputScaling:'fixed',workingInputTargetTokens:48000,workingInputTriggerTokens:63626,designGuidanceMode:'core',designGuidanceMaxTokens:6000});
 const history=Chat.from([{role:'user',content:'Fix the found file'}]),wc=store();
 history.append(ChatMessage.from({role:'assistant',content:[{type:'toolCallRequest',toolCallRequest:{type:'function',id:'pin',name:'read_file',arguments:{path:'Source/Pinned.cpp'}}}]}));
 history.append(ChatMessage.from({role:'tool',content:[{type:'toolCallResult',toolCallId:'pin',content:JSON.stringify({ok:true,path:'Source/Pinned.cpp',sha256:'a'.repeat(64),startLine:1,endLine:1,totalLines:1,content:'void PinnedSource() {}'})}]}));
 const broker=new BudgetBroker(config);let pinDecisions=0,projections=0;
 const original=broker.pinnedWorkingSetChars.bind(broker);broker.pinnedWorkingSetChars=w=>{pinDecisions++;return original(w)};
 const cm=new ContextManager(config,broker),em=new EvidenceManager(wc,[{name:'read_file',pluginIdentifier:'mcp/unreal-agent'}]);
 const oldProject=em.project.bind(em);em.project=(...args)=>{projections++;return oldProject(...args)};
 const source={async getContextLength(){return 84224},async applyPromptTemplate(chat){return chat.toString()},async countTokens(text){return text.includes('GUIDANCE_SENTINEL')?66552:61029}};
 try{
  const {result,byEvent}=await prepare(config,history,wc,{tokenSource:source,contextManager:cm,evidenceManager:em,
   objective:{kind:'mutate',requiredEffects:new Set(['read','mutation']),mutationState:'none'},
   mutationTargets:[{path:'Source/Pinned.cpp'}],
   guidanceRetention:{kept:{ids:['fixture'],instruction:'GUIDANCE_SENTINEL',selectionScope:'unscoped',tokens:5523}}});
  assert.equal(result.kind,'ready');assert.equal(projections,0,'guidance alone cannot start pin release');assert.equal(pinDecisions,1);
  assert.match(result.modelInput.toString(),/void PinnedSource/);
  assert.equal(byEvent('design_guidance_input')[0].reason,'sticky_dropped');
  const base={measurement:result.finalMeasurement,modelInput:result.modelInput};
  const guide=await cm.addOptionalReferences(base,[{ids:['fixture'],instruction:'optional'}],async()=>({...base,measurement:{...base.measurement,inputTokens:66552}}),
    {maxAddedTokens:6000,hasReadTools:true,minimumReadTokens:4096,guardAbort(){},admissionWater:'high'});
  assert.notEqual(guide.telemetry.reason,'included');assert.equal(guide.assembled,base);
 }finally{wc.close();}
});

test("a projection that settles below the midpoint keeps the conversation", async () => {
  const config = configFor();
  const w = water(config);
  const history = Chat.from([{ role: "user", content: "Review the replication contracts" }]);
  // Between low water and the midpoint once the body is projected.
  withProse(history, w.effectiveLowWaterTokens + Math.floor((w.effectiveHighWaterTokens - w.effectiveLowWaterTokens) / 6));
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest:
    { type: "function", id: "big", name: "read_file", arguments: { path: "Source/Big.cpp" } } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "big",
    content: JSON.stringify({ ok: true, path: "Source/Big.cpp", sha256: "a".repeat(64),
      content: "void Big() {}\n".repeat(Math.ceil((w.effectiveHighWaterTokens - w.effectiveLowWaterTokens) * 4 / 14) + 2000) }) }] }));
  const wc = store();
  try {
    wc.captureReturned(history, () => true);
    wc.captureExposure(history, "previous-input", true);
    const { result, byEvent } = await prepare(config, history, wc);
    assert.equal(result.kind, "ready");
    const [projection] = byEvent("working_context_projection");
    assert.equal(projection.changed, true);
    assert.equal(projection.deepenLowWater, false);
    const [low] = byEvent("context_low_water");
    assert.equal(low.selectedCandidate, "unchanged", "no exchange is cut after a sufficient projection");
    assert.match(result.modelInput.toString(), /progress 0 /u, "earliest progress of the turn is kept");
  } finally { wc.close(); }
});

test("v3 optional retained instruction cannot force a working-set rewrite", async () => {
  const config = configFor();
  const w = water(config);
  const history = withProse(Chat.from([{ role: "user", content: "Review the replication contracts" }]),
    w.effectiveHighWaterTokens - 1500, 12);
  const wc = store();
  const guidanceRetention = { kept: { ids: ["core/proof"], instruction: "[Optional design reference]\nKeep proof.",
    selectionScope: "unscoped", tokens: 3000 } };
  try {
    const { result, byEvent } = await prepare(config, history, wc, { guidanceRetention });
    assert.equal(result.kind, "ready");
    assert.equal(byEvent("working_context_projection").length, 0);
    const [low] = byEvent("context_low_water");
    assert.equal(low.selectedCandidate, "unchanged");
    assert.match(result.modelInput.toString(), /progress 0 /u);
    assert.ok(result.finalMeasurement.inputTokens < w.effectiveHighWaterTokens);
  } finally { wc.close(); }
});
