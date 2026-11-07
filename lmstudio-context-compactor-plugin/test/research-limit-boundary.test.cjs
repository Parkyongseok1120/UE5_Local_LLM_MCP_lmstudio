"use strict";
// Revised plan 2026-11-07 §F2/F1. Conversation 1793984074550 reached the
// round limit, then a replan reopened 38 tools for two more read rounds and a
// second limit; read recovery also skipped the limit check entirely.
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { fakeController, handlePredictionLoop } = require("./helpers/prediction-loop-fixture.cjs");
const { researchLimitTrigger, researchRoundNotice } = require("../dist/recovery-coordinator.js");
const { ExecutionState } = require("../dist/execution-state.js");
const { finalPhaseInstruction, RESEARCH_LIMIT_FINAL_INSTRUCTION, BOUNDED_AUDIT_FINAL_INSTRUCTION,
  FINAL_REPORT_CORRECTION_INSTRUCTION } = require("../dist/execution-instructions.js");

test("the research boundary counts tool-allowed predictions and closes tools for good", () => {
  assert.equal(researchLimitTrigger(2, 3, null), null);
  assert.equal(researchLimitTrigger(3, 3, null), "research_round_limit");
  assert.equal(researchLimitTrigger(99, 0, null), null, "0 means no round limit");
  assert.equal(researchLimitTrigger(0, 0, 10, 11), "research_time_limit");
  const execution = new ExecutionState();
  execution.startRecovery("context_budget");
  for (let i = 0; i < 3; i++) execution.beginResearchPrediction();
  const boundary = execution.researchBoundary(3, null);
  assert.equal(boundary.trigger, "research_round_limit");
  execution.finishResearch(boundary.trigger);
  assert.equal(execution.toolsClosed, true);
  assert.throws(() => execution.startReplan(), /ILLEGAL_EXECUTION_TRANSITION/u);
  assert.deepEqual(execution.researchBoundary(3, null), { trigger: null, notice: null }, "final rounds are past the boundary");
});

test("the remaining-rounds notice names when it was issued and appears once", () => {
  assert.equal(researchRoundNotice(47, 60), null);
  assert.match(researchRoundNotice(48, 60), /when 48 research rounds were complete, 12 of the 60-round limit remained/u);
  assert.equal(researchRoundNotice(60, 60), null);
  assert.equal(researchRoundNotice(5, 0), null);
  assert.match(researchRoundNotice(0, 3), /3 of the 3-round limit/u, "a small limit warns from the start");
  const execution = new ExecutionState();
  for (let i = 0; i < 48; i++) execution.beginResearchPrediction();
  assert.ok(execution.researchBoundary(60, null).notice);
  execution.beginResearchPrediction();
  assert.equal(execution.researchBoundary(60, null).notice, null);
});

test("a round-limit final report says so instead of claiming a bounded audit", () => {
  assert.deepEqual(finalPhaseInstruction("research_round_limit", { reviewing: false, bounded: false, correction: null }),
    [RESEARCH_LIMIT_FINAL_INSTRUCTION]);
  assert.deepEqual(finalPhaseInstruction("research_round_limit", { reviewing: false, bounded: true, correction: null }),
    [BOUNDED_AUDIT_FINAL_INSTRUCTION]);
  assert.deepEqual(finalPhaseInstruction("research_round_limit", { reviewing: false, bounded: false, correction: "raw_tool_intent" }),
    [RESEARCH_LIMIT_FINAL_INSTRUCTION, FINAL_REPORT_CORRECTION_INSTRUCTION]);
});

const read = { name: "read_file", pluginIdentifier: "mcp/unreal-agent", description: "Read source",
  parametersJsonSchema: { type: "object", properties: { path: { type: "string" } } } };
const write = { name: "write_file", pluginIdentifier: "mcp/unreal-agent", description: "Write source",
  parametersJsonSchema: { type: "object", properties: { path: { type: "string" }, content: { type: "string" } } } };
async function emitRead(opts, index) {
  const request = { type: "function", id: `limit-${index}`, name: "read_file", arguments: { path: `project://Source/Test${index}.cpp` } };
  let effective;
  await opts.guardToolCall(0, index, { toolCallRequest: request, allow() { effective = request.arguments; },
    allowAndOverrideParameters(args) { effective = args; }, deny(reason) { throw new Error(reason); } });
  opts.onPredictionCompleted({ stats: { stopReason: "toolCalls" } });
  opts.onMessage(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: { ...request, arguments: effective } }] }));
  opts.onMessage(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: request.id,
    content: JSON.stringify({ ok: true, kind: "workspace_file_observation", path: request.arguments.path,
      canonicalProject: "C:/Game/Game.uproject", sha256: String(index % 10).repeat(64), startLine: 1, endLine: 1, totalLines: 1,
      content: `void Fresh${index}() {}` }) }] }));
  opts.onRoundEnd();
  throw opts.signal.reason;
}
function emitText(opts, text) {
  opts.onPredictionCompleted({ stats: { stopReason: "eosFound" } });
  opts.onMessage(ChatMessage.create("assistant", text));
}
const prompt = async (chat, options = {}) => JSON.stringify({ text: chat.toString(),
  tools: (options.toolDefinitions || []).map(tool => tool.function.name) });
const events = (ctl, name) => ctl.debugValues.filter(event => event.event === name);

test("read recovery stops at the round limit like ordinary research", async () => {
  const surfaces = [];
  const model = { identifier: "limit-recovery", async getContextLength() { return 65536; }, applyPromptTemplate: prompt,
    // The full catalogue (with write_file) does not fit, so every round is read-only recovery.
    async countTokens(text) { return text.startsWith("[") ? 100 : text.includes("write_file") ? 62000 : 1000; },
    async act(_chat, tools, opts) {
      surfaces.push(tools.map(tool => tool.name));
      if (!tools.length) return emitText(opts, "Read source evidence; the requested change is not applied.");
      if (surfaces.length > 8) return emitText(opts, "runaway");
      await emitRead(opts, surfaces.length);
    } };
  const ctl = fakeController(Chat.from([{ role: "user", content: "Read the source and implement the requested fix." }]), model,
    { contextManagementMode: "deterministic", workingInputTargetTokens: 40000, workingInputTriggerTokens: 56000,
      maxResearchRounds: 3, toolStagnationAction: "off" }, [read, write]);
  await handlePredictionLoop(ctl);
  assert.equal(surfaces.filter(tools => tools.length).length, 3, "three tool-allowed predictions, then the report");
  assert.deepEqual(surfaces.at(-1), []);
  const limit = events(ctl, "research_round_limit");
  assert.equal(limit.length, 1);
  assert.equal(limit[0].rounds, 3);
  assert.notEqual(limit[0].phase, "RESEARCH", "the entry boundary caught a recovery-phase round");
});

test("after the limit, raw tool text in the report gets one tool-free correction, never reopened tools", async () => {
  const finalInputs = [];
  let toolCalls = 0;
  const model = { identifier: "limit-raw", async getContextLength() { return 65536; }, applyPromptTemplate: prompt,
    async countTokens() { return 1000; },
    async act(chat, tools, opts) {
      if (tools.length) { toolCalls++; return emitRead(opts, toolCalls); }
      finalInputs.push(chat.toString());
      emitText(opts, '<tool_call><function=read_file><parameter=path>project://Source/More.cpp</parameter></function></tool_call>');
    } };
  const ctl = fakeController(Chat.from([{ role: "user", content: "Review the damage implementation." }]), model,
    { maxResearchRounds: 2, toolStagnationAction: "off" }, [read]);
  await handlePredictionLoop(ctl);
  assert.equal(toolCalls, 2);
  assert.equal(finalInputs.length, 2, "one report attempt and one correction");
  assert.doesNotMatch(finalInputs[0], /printed tool-call text instead of a report/u);
  assert.match(finalInputs[1], /printed tool-call text instead of a report/u);
  assert.equal(events(ctl, "fresh_tool_planning_retry_scheduled").length, 0);
  assert.equal(events(ctl, "final_report_correction_scheduled").length, 1);
  assert.equal(events(ctl, "partial_report_delivery").length, 1, "a single partial report after the correction failed");
  assert.equal(ctl.blocks.filter(block => /부분 조사 보고/u.test(block.text)).length, 1);
});

test("a correction that writes the report completes it without a partial report", async () => {
  let finals = 0, toolCalls = 0;
  const model = { identifier: "limit-fixed", async getContextLength() { return 65536; }, applyPromptTemplate: prompt,
    async countTokens() { return 1000; },
    async act(_chat, tools, opts) {
      if (tools.length) { toolCalls++; return emitRead(opts, toolCalls); }
      finals++;
      emitText(opts, finals === 1 ? "<tool_call><function=read_file></function></tool_call>"
        : "## 결론\nFresh1 and Fresh2 were read; the rest of the scope is unverified.");
    } };
  const ctl = fakeController(Chat.from([{ role: "user", content: "Review the damage implementation." }]), model,
    { maxResearchRounds: 2, toolStagnationAction: "off" }, [read]);
  await handlePredictionLoop(ctl);
  assert.equal(finals, 2);
  assert.equal(events(ctl, "partial_report_delivery").length, 0);
  assert.equal(events(ctl, "execution_outcome").at(-1).reportCompleted, true);
});

test("the remaining-rounds notice reaches the model input once and leaves before the report", async () => {
  const inputs = [];
  let toolCalls = 0;
  const model = { identifier: "limit-notice", async getContextLength() { return 65536; }, applyPromptTemplate: prompt,
    async countTokens() { return 1000; },
    async act(chat, tools, opts) {
      inputs.push({ tools: tools.length, text: chat.toString() });
      if (tools.length) { toolCalls++; return emitRead(opts, toolCalls); }
      emitText(opts, "## 결론\nDone within the read scope.");
    } };
  const ctl = fakeController(Chat.from([{ role: "user", content: "Review the damage implementation." }]), model,
    { maxResearchRounds: 6, toolStagnationAction: "off" }, [read]);
  await handlePredictionLoop(ctl);
  const noticed = inputs.map(input => /research rounds were complete/u.test(input.text));
  assert.equal(toolCalls, 6);
  assert.equal(noticed[0], false);
  assert.equal(noticed[1], true, "issued when 1 of 6 rounds was complete (5 remained)");
  assert.equal(inputs.at(-1).tools, 0);
  assert.equal(noticed.at(-1), false, "the final report does not carry the notice");
});
