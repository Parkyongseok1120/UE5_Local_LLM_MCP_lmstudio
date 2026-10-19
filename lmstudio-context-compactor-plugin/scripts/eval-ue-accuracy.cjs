#!/usr/bin/env node
"use strict";
// Plan §5 / W-A2: real-model accuracy on the synthetic UE normal/defect pairs.
// One variable per comparison: design guidance off vs auto on the same build.
// Usage: node scripts/eval-ue-accuracy.cjs --model <id> --reps 1 --output <file>
const fs = require("node:fs");
const path = require("node:path");
const { Chat, ChatMessage, LMStudioClient, rawFunctionTool } = require("@lmstudio/sdk");
const { createPredictionLoopHandler } = require("../dist/prediction-loop.js");
const fixture = require("./ue-accuracy-fixture.cjs");

const valueAfter = (flag, fallback) => { const i = process.argv.indexOf(flag); return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback; };
// The engine identity a real session gets from an engine_local lookup; the
// version-checked engine contract sections are admitted only when it is known.
const ENGINE_LOOKUP = symbol => ({ ok: true, canonicalProject: fixture.PROJECT_IDENTITY, sourceMode: "engine_local", evidenceSource: "installed_engine_source", symbol,
  observedEngineIdentity: { status: "observed", engineRoot: "C:/Program Files/Epic Games/UE_5.8", hashScope: "file",
    sha256: "c".repeat(64), version: "5.8" }, coverage: { completeness: "partial", scope: "declaration" } });
const VERDICT_LINE = "End your answer with one final line exactly in this form: VERDICT: <label> | <severity or none>";

function tools(recorder) {
  const listFiles = rawFunctionTool({
    name: "list_files",
    description: "List the files under one folder of the active synthetic project.",
    parametersJsonSchema: { type: "object", properties: { folder: { type: "string" } }, required: ["folder"], additionalProperties: false },
    implementation: async ({ folder }) => {
      const prefix = String(folder || "").replace(/^project:\/\//u, "").replace(/\/?$/u, "/");
      const entries = fixture.files.filter(file => file.path.startsWith(prefix)).map(file => `project://${file.path}`);
      recorder.calls.push({ tool: "list_files", folder });
      return { ok: true, folder: `project://${prefix}`, entries };
    },
  });
  const readRange = rawFunctionTool({
    name: "read_file_range",
    description: "Read lines startLine..endLine of one synthetic project file (at most 220 lines).",
    parametersJsonSchema: { type: "object", properties: { path: { type: "string" }, startLine: { type: "integer", minimum: 1 },
      endLine: { type: "integer", minimum: 1 } }, required: ["path", "startLine", "endLine"], additionalProperties: false },
    implementation: async args => {
      const payload = fixture.readPayload(args.path, args.startLine, args.endLine);
      recorder.calls.push({ tool: "read_file_range", args, ok: payload.ok });
      return payload;
    },
  });
  for (const tool of [listFiles, readRange]) tool.pluginIdentifier = "mcp/unreal-agent";
  const engineLookup = rawFunctionTool({
    name: "unreal_symbol_lookup",
    description: "Look up an Unreal Engine symbol in the installed engine source.",
    parametersJsonSchema: { type: "object", properties: { symbol: { type: "string" } }, required: ["symbol"], additionalProperties: false },
    implementation: async ({ symbol }) => { recorder.calls.push({ tool: "unreal_symbol_lookup", symbol }); return ENGINE_LOOKUP(symbol); },
  });
  engineLookup.pluginIdentifier = "mcp/unreal-rag";
  return [listFiles, readRange, engineLookup];
}

function controller(history, tokenSource, session, overrides) {
  const blocks = [], debugValues = [];
  const config = { projectEngine: "unreal", projectIdentity: fixture.PROJECT_IDENTITY, observeOnly: false, showDebugInfo: true,
    contextManagementMode: "hybrid", inputAvailabilityMode: "observe", reviewProgress: false, separateAttachments: false, ...overrides };
  return {
    abortSignal: AbortSignal.timeout(Number(overrides.evaluationTimeoutMs || 420000)),
    guardAbort() { if (this.abortSignal.aborted) throw this.abortSignal.reason; },
    getPluginConfig() { return { get: key => config[key] }; },
    getWorkingDirectory() { return ""; },
    async pullHistory() { return history; },
    async tokenSource() { return tokenSource; },
    async startToolUseSession() { return session; },
    async requestConfirmToolCall() { return { type: "allow" }; },
    debug(value) { debugValues.push(value); },
    createStatus() { return { setText() {}, setState() {}, remove() {} }; },
    createToolStatus() { return { setText() {}, setState() {}, setStatus() {}, remove() {} }; },
    createContentBlock(options) {
      const block = { options, text: "" };
      blocks.push(block);
      return { appendText(text) { block.text += text; }, appendToolRequest() {}, replaceToolRequest() {}, appendToolResult() {},
        setStyle(style) { block.options.style = style; } };
    },
    blocks, debugValues,
  };
}

function parseVerdict(answer) {
  const line = [...answer.matchAll(/VERDICT:\s*([a-z_]+)\s*\|\s*([A-Za-z0-9]+)/gu)].at(-1);
  if (!line) return { label: "invalid", severity: null, parsed: false };
  return { label: line[1], severity: /^P[0-3]$/u.test(line[2]) ? line[2] : null, parsed: true };
}

async function oneRun(model, entry, group, seed) {
  const recorder = { calls: [] };
  const history = Chat.empty();
  history.append("system", fixture.SYSTEM_PROMPT);
  // Earlier turn: the engine was identified, as in a real session.
  history.append("user", "Which Unreal Engine version does the active project use?");
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
    type: "function", id: "seed_engine", name: "unreal_symbol_lookup", arguments: { symbol: "AActor" } } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "seed_engine",
    content: JSON.stringify(ENGINE_LOOKUP("AActor")) }] }));
  history.append("assistant", "The installed engine source is Unreal Engine 5.8.");
  history.append("user", `${entry.question}\n${VERDICT_LINE}`);
  const fixedModel = { identifier: model.identifier, getContextLength: () => model.getContextLength(),
    applyPromptTemplate: (chat, options) => model.applyPromptTemplate(chat, options), countTokens: text => model.countTokens(text),
    act: (chat, actTools, options) => model.act(chat, actTools, { ...options, temperature: 0, seed }) };
  const session = { tools: tools(recorder), [Symbol.dispose]() {} };
  // "off" uses the product's own disable switch (a zero guidance budget).
  const ctl = controller(history, fixedModel, session, group === "off"
    ? { designGuidanceMode: "auto", designGuidanceMaxTokens: 0 } : { designGuidanceMode: "auto" });
  const started = Date.now();
  let error = null;
  try { await createPredictionLoopHandler()(ctl); } catch (caught) { error = String(caught?.message || caught); }
  const answer = ctl.blocks.filter(block => block.options?.style?.type !== "thinking").map(block => block.text.trim()).filter(Boolean).join("\n");
  const verdict = parseVerdict(answer);
  // P7: not_defect means the prior finding was withdrawn; there is no user rebuttal in this run.
  const graded = fixture.gradeCase(entry.id, { ...verdict, retractedPriorFinding: verdict.label === "not_defect",
    retractedAfterUserRebuttal: false, reappearedAfterRetraction: false });
  const guidance = ctl.debugValues.filter(value => value.event === "design_guidance_input");
  const rounds = ctl.debugValues.filter(value => value.event === "direct_round_observation");
  return { caseId: entry.id, group, seed, elapsedMs: Date.now() - started, error, verdict, graded,
    rounds: rounds.length, toolCalls: recorder.calls.length,
    promptTokens: rounds.reduce((sum, round) => sum + Number(round.predictionStats?.promptTokensCount || 0), 0),
    guidanceIncludedRounds: guidance.filter(value => value.outcome === "included").length,
    guidanceSelectedIds: [...new Set(guidance.flatMap(value => value.selectedIds || []))],
    guidanceTopics: [...new Set(guidance.map(value => `${value.primaryTopic || ""}/${value.supplementTopic || ""}`))],
    answerTail: answer.slice(-600) };
}

async function main() {
  const identifier = valueAfter("--model", "swift-1.5-qwen3.8-27b");
  const reps = Number(valueAfter("--reps", "1"));
  const output = path.resolve(valueAfter("--output", "ue-accuracy-eval.json"));
  const only = valueAfter("--cases", "");
  const model = await new LMStudioClient().llm.model(identifier);
  const cases = fixture.CASES.filter(entry => entry.variant !== "scoring_rule" && (!only || only.split(",").includes(entry.id)));
  const runs = [];
  for (let rep = 1; rep <= reps; rep++) {
    for (const [index, entry] of cases.entries()) {
      // AB/BA alternation per case and repetition.
      const order = (index + rep) % 2 ? ["off", "auto"] : ["auto", "off"];
      for (const group of order) {
        const run = await oneRun(model, entry, group, 7000 + rep * 100 + index);
        runs.push({ rep, ...run });
        process.stdout.write(`${JSON.stringify({ rep, caseId: run.caseId, group, label: run.verdict.label, severity: run.verdict.severity,
          score: run.graded.score, guidanceIncludedRounds: run.guidanceIncludedRounds, rounds: run.rounds, elapsedMs: run.elapsedMs, error: run.error })}\n`);
        fs.writeFileSync(output, JSON.stringify({ model: identifier, runs }, null, 1));
      }
    }
  }
  const summary = Object.fromEntries(["off", "auto"].map(group => {
    const byRep = [...new Set(runs.map(run => run.rep))].map(rep => fixture.summarize(
      runs.filter(run => run.group === group && run.rep === rep).map(run => run.graded)).correctness);
    return [group, byRep];
  }));
  fs.writeFileSync(output, JSON.stringify({ model: identifier, summary, runs }, null, 1));
  process.stdout.write(`${JSON.stringify(summary)}\n`);
}

if (require.main === module) main().catch(error => { console.error(error); process.exit(1); });
module.exports = { parseVerdict };
