"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { Chat } = require("@lmstudio/sdk");
const core = require("../src/compaction-core.js");

/**
 * Reproduce live telemetry: soft compact collapses a long in-flight tool loop
 * down to ~empty context (postInputTokens ~700) instead of retaining tools.
 */
test("soft compact retains many in-flight tool pairs after one user goal", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-inflight-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    let captured = null;
    let postTokens = 0;
    const model = {
      identifier: "inflight-model",
      async applyPromptTemplate(chat) {
        return JSON.stringify(core.snapshotMessages(chat.getMessagesArray()));
      },
      async countTokens(value) {
        return Math.max(1, Math.floor(String(value || "").length / 4));
      },
      async getContextLength() { return 55_040; },
      respond(chat, opts) {
        captured = core.snapshotMessages(chat.getMessagesArray());
        postTokens = String(JSON.stringify(captured)).length;
        opts.onPredictionFragment({ content: "overview done" });
        return { async result() { return { stopReason: "eosFound" }; } };
      },
    };

    const config = {
      // This legacy retention regression intentionally exercises the explicit
      // unbounded-current-turn opt-out. Omitted values now migrate to the
      // safety default and are covered by the cap-specific compatibility tests.
      configVersion: 1,
      maxCurrentTurnMessages: 0,
      enabled: true,
      observeOnly: false,
      strictToolControlPlane: false,
      bufferUntilPredictionComplete: true,
      rejectTruncatedPredictions: false,
      requireCheckpointPersistence: false,
      targetModel: "",
      softRemainingTokens: 50_000,
      hardRemainingTokens: 8_000,
      maxOutputReserve: 4_096,
      safetyMarginTokens: 1_024,
      normalToolResultReserve: 3_000,
      buildToolResultReserve: 8_000,
      recentCompleteTurns: 1,
      minimumTurnsBetweenCompactions: 0,
      targetRemainingTokensAfterCompaction: 24_000,
    };

    const toolDefinitions = [{
      type: "function",
      function: {
        name: "list_directory",
        description: "list",
        parameters: { type: "object", properties: { path: { type: "string" } } },
      },
    }];

    const controller = {
      client: {
        llm: {
          async listLoaded() { return [model]; },
          async model() { return model; },
        },
      },
      abortSignal: new AbortController().signal,
      getPluginConfig() { return { get(key) { return config[key]; } }; },
      getWorkingDirectory() { return stateRoot; },
      getToolDefinitions() { return toolDefinitions; },
      fragmentGenerated() {},
      toolCallGenerationStarted() {},
      toolCallGenerationNameReceived() {},
      toolCallGenerationArgumentFragmentGenerated() {},
      toolCallGenerationEnded() {},
      toolCallGenerationFailed() {},
    };

    const messages = [
      { role: "system", content: [{ type: "text", text: "You are an Unreal agent. Prefer tools." }] },
      { role: "user", content: [{ type: "text", text: "현재 프로젝트 찾고 코드 구조 전체 적으로 확인해줘" }] },
    ];
    for (let i = 0; i < 20; i += 1) {
      const id = `call-${i}`;
      const payload = JSON.stringify({
        ok: true,
        entries: Array.from({ length: 40 }, (_v, n) => `File_${i}_${n}.h`),
        path: `Source/Dir${i}`,
        blob: "x".repeat(1_200),
      });
      messages.push({
        role: "assistant",
        content: [{
          type: "toolCallRequest",
          toolCallRequest: {
            id,
            type: "function",
            name: "list_directory",
            arguments: { path: `Source/Dir${i}` },
          },
        }],
      });
      messages.push({
        role: "tool",
        content: [{ type: "toolCallResult", toolCallId: id, content: payload }],
      });
    }

    const history = Chat.from({ messages });
    const before = core.snapshotMessages(history.getMessagesArray());
    const beforeTools = before.flatMap((m) => m.toolResults || []).length;
    assert.equal(beforeTools, 20);

    await generate(controller, history);

    assert.ok(captured, "model did not receive a chat");
    const afterTools = captured.flatMap((m) => m.toolResults || []).length;
    const afterCalls = captured.flatMap((m) => m.toolCalls || []).length;
    const sessionDirs = fs.readdirSync(stateRoot, { withFileTypes: true }).filter((e) => e.isDirectory());
    const events = fs.readFileSync(path.join(stateRoot, sessionDirs[0].name, "events.jsonl"), "utf8")
      .trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);
    const compaction = events.find((e) => e.type === "compaction_decision");
    const measurement = events.find((e) => e.type === "context_measurement");
    const logPath = path.join(__dirname, "..", "..", "debug-49b048.log");
    fs.appendFileSync(logPath, `${JSON.stringify({
      sessionId: "49b048",
      runId: "inflight-repro",
      hypothesisId: "H10",
      location: "test/inflight-tool-retention.test.cjs",
      message: "in-flight tool retention after soft compact",
      data: {
        beforeTools,
        afterTools,
        afterCalls,
        decision: measurement?.decision?.action,
        effectiveAction: compaction?.effectiveAction,
        applied: compaction?.applied,
        postInputTokens: compaction?.postInputTokens,
        retainedTurns: compaction?.retainedTurns,
        capturedRoles: captured.map((m) => m.role),
        postChars: postTokens,
        latestUser: captured.filter((m) => m.role === "user").map((m) => m.text).slice(-1)[0],
      },
      timestamp: Date.now(),
    })}\n`);

    assert.equal(compaction?.applied, true, "expected soft/hard compaction to apply");
    assert.ok(
      afterTools >= 15,
      `expected most in-flight tool results retained, got ${afterTools}/${beforeTools}`,
    );
    assert.equal(core.isCompleteToolPair(captured), true);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});
