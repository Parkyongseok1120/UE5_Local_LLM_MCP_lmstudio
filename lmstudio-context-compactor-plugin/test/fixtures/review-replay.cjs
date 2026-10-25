"use strict";

// Offline replay of the 2026-10-25 review (conversation 1792866474332): the
// same 13 source files at their logged line counts, read whole, then a run of
// search rounds whose progress text cites findings, in the same context window
// (84224 tokens, target 40000, trigger 56000, 8192 output reserve). A scripted
// model cannot decide to re-read, so the replay measures what the real model
// lacked: how many read bodies stay visible, how often the input is rewritten
// (a non-prefix change discards the server's reusable prefix), and how much
// runtime data each input carries.

const path = require("node:path");

const FILES = [
  ["Public/LatencyMultiCombatCharacter.h", 130], ["Private/LatencyMultiCombatCharacter.cpp", 420],
  ["Public/Data/CombatTypes.h", 124], ["Public/Data/UCharacterDataAsset.h", 31], ["Public/Data/UWeaponDataAsset.h", 31],
  ["Public/LatencyMultiCombatGameMode.h", 45], ["Private/LatencyMultiCombatGameMode.cpp", 146],
  ["Public/LatencyMultiCombatPlayerState.h", 69], ["Private/LatencyMultiCombatPlayerState.cpp", 97],
  ["Public/LatencyMultiCombatGameState.h", 55], ["Private/LatencyMultiCombatGameState.cpp", 65],
  ["Public/LatencyMultiCombatPlayerController.h", 43], ["Private/LatencyMultiCombatPlayerController.cpp", 98],
].map(([file, lines], index) => ({ path: `project://Source/Latency_MultiCombat/${file}`, lines,
  name: path.basename(file), marker: `REPLAY_BODY_END_${index}`, sha: index.toString(16).padStart(2, "0").repeat(32) }));

const body = file => Array.from({ length: file.lines }, (_, i) => i === file.lines - 1 ? `// ${file.marker}`
  : `    const int32 ${file.name.replace(/\W/g, "")}Value${i} = ComputeReplicatedState(${i}); // line ${i + 1}`).join("\n");

function readResult(file) {
  return { ok: true, path: file.path, canonicalProject: "C:/Game/Latency_MultiCombat.uproject", sha256: file.sha,
    startLine: 1, endLine: file.lines, totalLines: file.lines, lineNumbers: false, content: body(file) };
}

function searchResult(round) {
  return { ok: true, query: `ReplicatedUsing_${round}`, coverage: "complete", total: 6,
    matches: Array.from({ length: 6 }, (_, i) => ({ path: FILES[(round + i) % FILES.length].path, line: 10 + i,
      text: `UPROPERTY(ReplicatedUsing = OnRep_Value${round}_${i}) int32 ReplicatedValue${round}_${i}; ${"x".repeat(640)}` })) };
}

/** Every role, text, request and result of an input, as the template would see them. */
const serialize = chat => chat.getMessagesArray().map(message => [message.getRole(), message.getText(),
  JSON.stringify(message.getToolCallRequests()), ...message.getToolCallResults().map(result => String(result.content))].join("\n")).join("\n<|turn|>\n");

/** Run the replay against a built dist directory; returns its measurements. */
async function runReviewReplay(distDir, { searchRounds = 36 } = {}) {
  const { Chat, ChatMessage } = require("@lmstudio/sdk");
  const { REASONING_SEPARATOR } = require(path.join(distDir, "continuity-text.js"));
  const { createPredictionLoopHandler } = require(path.join(distDir, "prediction-loop.js"));
  const handle = createPredictionLoopHandler(undefined, undefined, undefined, undefined, () => ({}));
  const tools = ["read_file", "search_files"].map(name => ({ name, description: name, pluginIdentifier: "mcp/unreal-agent",
    parametersJsonSchema: { type: "object", properties: { path: { type: "string" }, query: { type: "string" } } } }));
  const readRounds = Math.ceil(FILES.length / 2);
  const inputs = [];
  let calls = 0;
  const model = { identifier: "review-replay", async getContextLength() { return 84224; },
    async applyPromptTemplate(chat) { return serialize(chat); },
    async countTokens(text) { return Math.ceil(text.length / 3); },
    async act(chat, _tools, options) {
      inputs.push(serialize(chat));
      const round = calls++;
      if (round >= readRounds + searchRounds) {
        options.onMessage(ChatMessage.create("assistant", "최종 보고: `DeathCount`가 `OnRep_KillCount`를 공유합니다. `HeadshotMultiplier`는 소비 근거 부족으로 제외합니다."));
        options.onPredictionCompleted({ stats: { stopReason: "eosFound" } });
        return;
      }
      const requests = round < readRounds
        ? FILES.slice(round * 2, round * 2 + 2).map((file, i) => ({ id: `read-${round}-${i}`, type: "function",
          name: "read_file", arguments: { path: file.path } }))
        : [{ id: `search-${round}`, type: "function", name: "search_files", arguments: { query: `ReplicatedUsing_${round}` } }];
      const cited = FILES[round % FILES.length];
      const progress = `\`Value${round}\` in ${cited.name}:${10 + round % 7} is set only on the server (finding ${round}).`;
      const text = round === 0 ? "`HeadshotMultiplier` never used: reasoning-only finding.\n" + REASONING_SEPARATOR + progress : progress;
      options.onPredictionCompleted({ stats: { stopReason: "toolCalls" } });
      for (const [i, request] of requests.entries()) {
        await options.guardToolCall(0, 100 * round + i, { toolCallRequest: request, allow() {}, allowAndOverrideParameters() {}, deny() {} });
        options.onToolCallRequestFinalized(0, 100 * round + i, { toolCallRequest: request });
      }
      options.onMessage(ChatMessage.from({ role: "assistant", content: [{ type: "text", text },
        ...requests.map(toolCallRequest => ({ type: "toolCallRequest", toolCallRequest }))] }));
      options.onMessage(ChatMessage.from({ role: "tool", content: requests.map(request => ({ type: "toolCallResult",
        toolCallId: request.id, content: JSON.stringify(request.name === "read_file"
          ? readResult(FILES[round * 2 + Number(request.id.at(-1))]) : searchResult(round)) })) }));
      options.onRoundEnd(0);
      throw options.signal.reason;
    } };
  const config = { projectEngine: "unreal", contextManagementMode: "hybrid", observeOnly: false, showDebugInfo: true,
    workingInputTargetTokens: 40000, workingInputTriggerTokens: 56000, maxOutputReserve: 8192, safetyMarginTokens: 2048,
    designGuidanceMode: "off", toolStagnationAction: "off", auditCompletionMode: "off", maxResearchRounds: 200 };
  const debug = [];
  const ctl = { abortSignal: new AbortController().signal, guardAbort() {},
    getPluginConfig() { return { get: key => config[key] }; },
    async pullHistory() { return Chat.from([{ role: "user", content: "캐릭터와 RPC 계약, SSOT, 수명관리 문제를 찾아 보고해줘." }]); },
    async tokenSource() { return model; },
    async startToolUseSession() { return { tools, [Symbol.dispose]() {} }; },
    async requestConfirmToolCall() { return { type: "allow" }; },
    createToolStatus() { return { setStatus() {} }; },
    createStatus() { return { setText() {}, setState() {}, remove() {} }; },
    createContentBlock() { return { appendText() {}, appendToolRequest() {}, replaceToolRequest() {}, appendToolResult() {}, setStyle() {} }; },
    debug(value) { debug.push(value); } };
  await handle(ctl);
  const analysis = inputs.slice(readRounds);
  const visible = analysis.map(input => FILES.filter(file => input.includes(file.marker)).length);
  // A rewrite: the next input does not extend the previous one, so the server
  // reprocesses from the first changed message instead of the newest exchange.
  let rewrites = 0, rewrittenChars = 0;
  for (let i = 1; i < inputs.length; i++) {
    const previous = inputs[i - 1].split("\n<|turn|>\n"), current = inputs[i].split("\n<|turn|>\n");
    let same = 0;
    while (same < previous.length && same < current.length && previous[same] === current[same]) same++;
    if (same < previous.length - 2) { rewrites++; rewrittenChars += current.slice(same).join("").length; }
  }
  const measurements = debug.filter(event => event.event === "direct_context_measurement");
  return {
    modelCalls: inputs.length,
    rewrites, rewrittenChars,
    projections: debug.filter(event => event.event === "working_context_projection" && event.changed).length,
    compactions: debug.filter(event => event.event === "context_low_water" && event.disposition === "compacted_to_low").length,
    visibleBodiesMin: Math.min(...visible), visibleBodiesLast: visible.at(-1),
    maxRuntimeDataChars: Math.max(0, ...measurements.map(event => event.workingContext?.runtimeDataChars ?? 0)),
    promptChars: inputs.reduce((sum, input) => sum + input.length, 0),
    findingsInLastInput: (inputs.at(-1).match(/finding \d+/gu) || []).length,
    reasoningRegisterInLastInput: /Runtime review register[\s\S]*\[found\] HeadshotMultiplier \(reasoning, round 1\)/u.test(inputs.at(-1)),
    debug,
  };
}

module.exports = { FILES, runReviewReplay };

if (require.main === module) {
  runReviewReplay(path.resolve(process.argv[2] || path.join(__dirname, "..", "..", "dist"))).then(result => {
    const { debug, ...summary } = result;
    process.stdout.write(JSON.stringify(summary, null, 2) + "\n");
  }, error => { console.error(error); process.exitCode = 1; });
}
