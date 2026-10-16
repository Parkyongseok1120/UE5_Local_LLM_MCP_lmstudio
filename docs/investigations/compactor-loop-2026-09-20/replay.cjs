"use strict";

// Read-only investigation. Replays saved observations; never executes project tools.
// node replay.cjs <conversation.json> [--live-template] [--output result.json]
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const assert = require("node:assert/strict");
const { createRequire } = require("node:module");
const root = path.resolve(__dirname, "../../..");
const plugin = path.join(root, "lmstudio-context-compactor-plugin");
const core = require(path.join(plugin, "src/direct-compaction-core.js"));
const conversationPath = process.argv[2];
const conversation = JSON.parse(fs.readFileSync(conversationPath, "utf8"));
const selected = conversation.messages.map(m => m.versions[m.currentlySelected]);
const userIndex = selected.findLastIndex(m => m.role === "user");
const steps = selected[userIndex + 1].steps;
const rounds = [];
const uniqueRequests = new Map();
let round;
for (const [stepIndex, step] of steps.entries()) {
  if (step.type === "debugInfoBlock") {
    let debug;
    try { debug = JSON.parse(step.debugInfo); } catch { continue; }
    if (debug.event !== "direct_context_measurement") continue;
    round = { debug, requests: new Map(), results: [], texts: [], thoughts: [], stepIndex };
    rounds.push(round);
  }
  if (!round) continue;
  for (const part of step.content || []) {
    if (part.type === "toolCallRequest") {
      const id = part.toolCallRequestId || String(part.callId);
      round.requests.set(id, { id, type: "function", name: part.name, arguments: part.parameters });
      uniqueRequests.set(id, part);
    } else if (part.type === "toolCallResult") {
      round.results.push({ content: part.content, toolCallId: part.toolCallRequestId });
    } else if (part.type === "text") {
      (step.style?.type === "thinking" ? round.thoughts : round.texts).push(part.text);
    }
  }
}
function unwrapObservedSingleText(content) {
  const value = JSON.parse(content);
  // This is an experimental input adapter, NOT a proposed production parser.
  assert.ok(Array.isArray(value) && value.length === 1 && value[0].type === "text");
  return value[0].text;
}
const msg = (role, text, extra = {}) => ({ role, text, hasFiles: false, toolRequests: [], toolResults: [], ...extra });
const actualResults = rounds.flatMap(r => r.results);
const payloads = actualResults.map(r => JSON.parse(unwrapObservedSingleText(r.content)));
const wrappedParsed = actualResults.map(r => core.parseToolResult(r.content));
const plainParsed = actualResults.map(r => core.parseToolResult(unwrapObservedSingleText(r.content)));
const fileCoverage = new Map();
for (const p of payloads) {
  if (!p.returnedLineCount) continue;
  let f = fileCoverage.get(p.path);
  if (!f) { f = { lines: new Set(), readCalls: 0, repeatedLines: 0, hashes: new Set() }; fileCoverage.set(p.path, f); }
  f.readCalls++; f.hashes.add(p.hash);
  for (let line = p.startLine; line <= p.endLine; line++) {
    if (f.lines.has(line)) f.repeatedLines++;
    f.lines.add(line);
  }
}
function probeCompaction(unwrap) {
  // Actual read responses from round 2 are omitted; round 3 is the newest exchange.
  const history = [msg("user", "Review the C refactor against the supplied guide.")];
  for (const r of rounds.filter(r => r.debug.roundIndex === 2 || r.debug.roundIndex === 3)) {
    history.push(msg("assistant", r.texts.join(""), { toolRequests: [...r.requests.values()] }));
    history.push(msg("tool", "", { toolResults: r.results.map(v => ({ ...v, content: unwrap ? unwrapObservedSingleText(v.content) : v.content })) }));
  }
  const cp = core.buildCheckpoint(history, { recentCompleteTurns: 0, maxCurrentTurnMessages: 2, maxCheckpointChars: 22000 });
  const originalBody = JSON.parse(unwrapObservedSingleText(rounds[2].results[0].content)).text;
  const survivingInput = JSON.stringify({ checkpoint: cp.checkpoint, assistantCheckpoint: cp.assistantCheckpoint,
    retained: cp.retainedIndexes.map(i => history[i]) });
  return {
    omittedMessageCount: cp.omittedMessageCount,
    retainedIndexes: cp.retainedIndexes,
    files: cp.memory.currentWorkStatus.modifiedOrObservedFiles.map(f => ({ path: f.path, observedLineRanges: f.observedLineRanges, readCoverageState: f.readCoverageState })),
    outcomes: cp.memory.currentWorkStatus.recentToolOutcomes,
    entireEarlierReadBodyRetained: survivingInput.includes(JSON.stringify(originalBody).slice(1, -1)),
  };
}
function replayBoundedRounds(unwrap) {
  let history = [msg("user", "Review the C refactor against the supplied guide.")];
  const result = [];
  for (const r of rounds) {
    if (r.debug.compacted) {
      const cp = core.buildCheckpoint(history, { recentCompleteTurns: 0, maxCurrentTurnMessages: 2, maxCheckpointChars: 22000 });
      const keep = cp.retainedIndexes.map(i => history[i]);
      history = [msg("system", cp.checkpoint), ...(cp.assistantCheckpoint ? [msg("assistant", cp.assistantCheckpoint)] : []), ...keep];
      result.push({ round: r.debug.roundIndex, fileCount: cp.memory.currentWorkStatus.modifiedOrObservedFiles.length,
        ranges: cp.memory.currentWorkStatus.modifiedOrObservedFiles.map(f => ({ path: f.path, ranges: f.observedLineRanges })) });
    }
    if (r.requests.size || r.texts.length) history.push(msg("assistant", r.texts.join(""), { toolRequests: [...r.requests.values()] }));
    if (r.results.length) history.push(msg("tool", "", { toolResults: r.results.map(v => ({ ...v, content: unwrap ? unwrapObservedSingleText(v.content) : v.content })) }));
  }
  return result;
}
async function main() {
  const result = {
    sourceSha256: crypto.createHash("sha256").update(fs.readFileSync(conversationPath)).digest("hex"),
    roundCount: rounds.length, compactedRounds: rounds.filter(r => r.debug.compacted).map(r => r.debug.roundIndex),
    uniqueCalls: uniqueRequests.size, toolResults: actualResults.length,
    recordedFilesEmptyAtEveryCompaction: rounds.filter(r => r.debug.compacted).every(r => r.debug.compactionDetails.observedFiles.length === 0),
    minimumRecordedRemainingTokens: Math.min(...rounds.map(r => r.debug.remainingTokens)),
    enrichedLatestUserChars: selected[userIndex].preprocessed.content.filter(c => c.type === "text").map(c => c.text).join("").length,
    actualLatestUserChars: selected[userIndex].content.filter(c => c.type === "text").map(c => c.text).join("").length,
    attachmentCountInEveryRound: [...new Set(rounds.map(r => r.debug.attachmentCount))],
    explicitContinuityFooters: rounds.filter(r => r.texts.join("").includes("<!-- direct-continuity-note-v1 -->")).length,
    inputTokensBeforeFirstCompaction: rounds.find(r => r.debug.compacted).debug.inputTokens,
    remainingTokensBeforeFirstCompaction: rounds.find(r => r.debug.compacted).debug.remainingTokens,
    recordedRoundMetadata: rounds.map(r => ({ round: r.debug.roundIndex, compacted: r.debug.compacted, inputTokens: r.debug.inputTokens, remainingTokens: r.debug.remainingTokens, messageCount: r.debug.messageCount,
      retainedIndexes: r.debug.compactionDetails?.retainedMessageIndexes })),
    fileCoverage: [...fileCoverage].map(([file, f]) => ({ file, readCalls: f.readCalls, uniqueLines: f.lines.size, repeatedLines: f.repeatedLines, uniqueHashes: f.hashes.size })),
    parser: {
      wrappedEmpty: wrappedParsed.filter(p => p.summary === "tool result contained no retained factual fields").length,
      wrappedWithPath: wrappedParsed.filter(p => p.path).length,
      wrappedWithHash: wrappedParsed.filter(p => p.sha256).length,
      plainWithPath: plainParsed.filter(p => p.path).length,
      plainWithHash: plainParsed.filter(p => p.sha256).length,
    },
    compactionProbe: { wrapped: probeCompaction(false), unwrapped: probeCompaction(true) },
    replay: { wrapped: replayBoundedRounds(false), unwrapped: replayBoundedRounds(true) },
    limits: ["Replay uses actual calls/results and recorded compaction decisions, but a short replacement user message. It proves parser/retention data flow, not the original complete rendered prompt.", "The unwrapped path is retained as a comparison oracle; production now decodes the observed single-text MCP envelope itself."]
  };
  assert.equal(result.parser.wrappedEmpty, 0);
  assert.equal(result.parser.wrappedWithPath, result.parser.plainWithPath);
  assert.equal(result.parser.wrappedWithHash, result.parser.plainWithHash);
  assert.deepEqual(result.compactionProbe.wrapped.files, result.compactionProbe.unwrapped.files);
  assert.equal(result.compactionProbe.wrapped.files.length, 2);
  assert.deepEqual(result.replay.wrapped, result.replay.unwrapped);
  if (process.argv.includes("--live-template")) {
    const { LMStudioClient, Chat, ChatMessage } = createRequire(path.join(plugin, "package.json"))("@lmstudio/sdk");
    const models = await new LMStudioClient().llm.listLoaded();
    const model = models.find(m => m.identifier === conversation.lastUsedModel.identifier);
    assert.ok(model, "The recorded model must already be loaded; do not load or switch automatically.");
    // Match SDK 1.5.0 PredictionLoopHandlerController.tokenSource() configuration wiring.
    // This changes only this diagnostic handle, not the GUI or the loaded model.
    model.internalIgnoreServerSessionConfig = true;
    model.internalKVConfigStack = { layers: [{ layerName: "conversationSpecific", config: conversation.perChatPredictionConfig }] };
    const sep = "__LM_STUDIO_INTERNAL_LSEP_SYNTHETIC_REASONING_END_f4e9a8d2c6b14d0c9e5f3a7b8c1d2e6a__";
    const probe = async raw => {
      const h = Chat.empty(); h.append("user", "Review A and B.");
      h.append(ChatMessage.from({ role: "assistant", content: [
        { type: "text", text: (raw ? "PRIOR_JUDGMENT_SENTINEL" + sep : "") + "Read A." },
        { type: "toolCallRequest", toolCallRequest: { id: "a", type: "function", name: "read_file", arguments: { path: "A.cs" } } },
      ] }));
      h.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "a", content: "A_RESULT_SENTINEL" }] }));
      const prompt = await model.applyPromptTemplate(h);
      return { thoughtPresent: prompt.includes("PRIOR_JUDGMENT_SENTINEL"), toolResultPresent: prompt.includes("A_RESULT_SENTINEL"), emptyThink: prompt.includes("<think>\n\n</think>") };
    };
    result.liveTemplate = { model: model.identifier, contextLength: await model.getContextLength(), raw: await probe(true), visible: await probe(false), generatedTokens: 0 };
  }
  const outputIndex = process.argv.indexOf("--output");
  if (outputIndex >= 0) fs.writeFileSync(process.argv[outputIndex + 1], JSON.stringify(result, null, 2) + "\n");
  console.log(JSON.stringify({ ...result, replay: { wrapped: result.replay.wrapped.at(-1), unwrapped: result.replay.unwrapped.at(-1) } }, null, 2));
}
main().catch(e => { console.error(e); process.exitCode = 1; });
