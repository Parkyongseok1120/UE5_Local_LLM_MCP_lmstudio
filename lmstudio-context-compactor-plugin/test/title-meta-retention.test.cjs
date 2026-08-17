"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { Chat } = require("@lmstudio/sdk");
const core = require("../src/compaction-core.js");

test("title meta prompt does not wipe objective or zero-tail the current turn", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-title-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    let captured = null;
    let toolCallbackCount = 0;
    const model = {
      identifier: "title-model",
      async applyPromptTemplate(chat) {
        return JSON.stringify(core.snapshotMessages(chat.getMessagesArray()));
      },
      async countTokens(value) { return Math.max(1, Math.floor(String(value || "").length / 4)); },
      async getContextLength() { return 55_040; },
      respond(chat, opts) {
        captured = core.snapshotMessages(chat.getMessagesArray());
        opts.onPredictionFragment({ content: "<title>Project Structure</title>" });
        return { async result() { return { stopReason: "eosFound" }; } };
      },
    };
    const config = {
      enabled: true,
      observeOnly: false,
      strictToolControlPlane: false,
      bufferUntilPredictionComplete: true,
      rejectTruncatedPredictions: false,
      requireCheckpointPersistence: false,
      targetModel: "",
      softRemainingTokens: 50_000,
      hardRemainingTokens: 8_000,
      maxOutputReserve: 1024,
      safetyMarginTokens: 256,
      normalToolResultReserve: 512,
      buildToolResultReserve: 1024,
      recentCompleteTurns: 1,
      minimumTurnsBetweenCompactions: 0,
      targetRemainingTokensAfterCompaction: 24_000,
    };
    const title = (
      "Based on the conversation above, can you please come up with a 2-5 word title "
      + "for this conversation? Put your answer in <title> tags, like this: <title>Your Title Here</title>.\n\n"
      + "Do not explain anything. Just return the title in the specified format."
    );
    const controller = {
      client: { llm: { async listLoaded() { return [model]; }, async model() { return model; } } },
      abortSignal: new AbortController().signal,
      getPluginConfig() { return { get(key) { return config[key]; } }; },
      getWorkingDirectory() { return stateRoot; },
      getToolDefinitions() { return [{ type: "function", function: { name: "read_file", parameters: { type: "object" } } }]; },
      fragmentGenerated() {},
      toolCallGenerationStarted() { toolCallbackCount += 1; },
      toolCallGenerationNameReceived() { toolCallbackCount += 1; },
      toolCallGenerationArgumentFragmentGenerated() { toolCallbackCount += 1; },
      toolCallGenerationEnded() { toolCallbackCount += 1; },
      toolCallGenerationFailed() { toolCallbackCount += 1; },
    };

    const baseMessages = [
        { role: "system", content: [{ type: "text", text: "rules" }] },
        { role: "user", content: [{ type: "text", text: "현재 프로젝트 찾고 코드 구조 전체 적으로 확인해줘" }] },
        {
          role: "assistant",
          content: [{
            type: "toolCallRequest",
            toolCallRequest: { id: "a", type: "function", name: "list_directory", arguments: { path: "Source" } },
          }],
        },
        {
          role: "tool",
          content: [{ type: "toolCallResult", toolCallId: "a", content: JSON.stringify({ entries: ["Project_MJS"] }) }],
        },
        { role: "assistant", content: [{ type: "text", text: "Structure overview: Project_MJS ..." }] },
    ];
    await generate(controller, Chat.from({ messages: baseMessages }));
    const primaryDir = fs.readdirSync(stateRoot, { withFileTypes: true }).find((e) => e.isDirectory()).name;
    const primaryCheckpointPath = path.join(stateRoot, primaryDir, "active-checkpoint.json");
    const primaryBeforeMeta = fs.readFileSync(primaryCheckpointPath, "utf8");
    const callbacksBeforeMeta = toolCallbackCount;

    const history = Chat.from({
      messages: [
        ...baseMessages,
        {
          role: "assistant",
          content: [{ type: "toolCallRequest", toolCallRequest: {
            id: "status-control", type: "function", name: "unreal_task_status", arguments: {},
          } }],
        },
        {
          role: "tool",
          content: [{ type: "toolCallResult", toolCallId: "status-control", content: JSON.stringify({
            control: {
              version: 2, epoch: 7, taskSessionId: "active-task", routeHash: "route-7",
              phase: "evidence", disposition: "require_tool",
              requiredTool: { name: "read_file", args: { path: "Source/Project_MJS.cpp" } },
              allowedTools: ["read_file"], retryPolicy: { sameSemanticInput: "once" },
            },
          }) }],
        },
        { role: "user", content: [{ type: "text", text: title }] },
      ],
    });

    await generate(controller, history);

    const sessionDirs = fs.readdirSync(stateRoot, { withFileTypes: true }).filter((e) => e.isDirectory());
    const auxiliaryDir = sessionDirs.find((entry) => entry.name !== primaryDir).name;
    const checkpoint = JSON.parse(fs.readFileSync(primaryCheckpointPath, "utf8"));
    const events = fs.readFileSync(path.join(stateRoot, auxiliaryDir, "events.jsonl"), "utf8")
      .trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);
    const compaction = events.find((e) => e.type === "compaction_decision");

    const logPath = path.join(__dirname, "..", "..", "debug-49b048.log");
    fs.appendFileSync(logPath, `${JSON.stringify({
      sessionId: "49b048",
      runId: "title-meta-repro",
      hypothesisId: "H11",
      location: "test/title-meta-retention.test.cjs",
      message: "title meta objective and compaction gates",
      data: {
        objective: String(checkpoint.objective || "").slice(0, 120),
        answeringMeta: compaction?.answeringMeta,
        goalChangeCompact: compaction?.goalChangeCompact,
        zeroRetainedTurns: compaction?.zeroRetainedTurns,
        retainedTurns: compaction?.retainedTurns,
        lastCapturedRole: captured?.at(-1)?.role,
        lastCapturedPreview: String(captured?.at(-1)?.text || "").slice(0, 80),
        keptToolResults: captured?.flatMap((m) => m.toolResults || []).length,
      },
      timestamp: Date.now(),
    })}\n`);

    assert.match(checkpoint.objective, /코드 구조/);
    assert.doesNotMatch(checkpoint.objective, /2-5 word title/);
    assert.equal(compaction?.answeringMeta, true);
    assert.equal(compaction?.goalChangeCompact, false);
    assert.equal(compaction?.zeroRetainedTurns, false);
    assert.equal(captured?.at(-1)?.role, "user");
    assert.match(String(captured?.at(-1)?.text || ""), /2-5 word title/);
    assert.equal(captured?.flatMap((m) => m.toolResults || []).length, 2);
    assert.equal(toolCallbackCount, callbacksBeforeMeta);
    assert.equal(fs.readFileSync(primaryCheckpointPath, "utf8"), primaryBeforeMeta);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});
