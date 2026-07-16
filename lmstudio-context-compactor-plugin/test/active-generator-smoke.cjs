"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { Chat, LMStudioClient } = require("@lmstudio/sdk");

async function main() {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-active-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  const { generate } = require("../dist/generator.js");
  const fragments = [];
  const config = {
    enabled: true,
    observeOnly: false,
    targetModel: process.env.LMS_CONTEXT_COMPACTOR_TARGET_MODEL || "",
    softRemainingTokens: 100000,
    hardRemainingTokens: 5000,
    maxOutputReserve: 128,
    normalToolResultReserve: 128,
    buildToolResultReserve: 256,
    recentCompleteTurns: 2,
    minimumTurnsBetweenCompactions: 3,
    targetRemainingTokensAfterCompaction: 20000,
  };
  const controller = {
    client: new LMStudioClient(),
    abortSignal: new AbortController().signal,
    getPluginConfig() {
      return { get(key) { return config[key]; } };
    },
    getWorkingDirectory() { return stateRoot; },
    getToolDefinitions() { return []; },
    fragmentGenerated(content) { fragments.push(String(content || "")); },
    toolCallGenerationStarted() { throw new Error("Unexpected tool call in active smoke test"); },
    toolCallGenerationNameReceived() {},
    toolCallGenerationArgumentFragmentGenerated() {},
    toolCallGenerationEnded() {},
    toolCallGenerationFailed(error) { throw error; },
  };
  const history = Chat.empty();
  history.append("system", "This is an activation smoke test. Answer the latest request briefly.");
  history.append("user", "Verify that the active context compactor can proxy one response.");
  for (let index = 0; index < 4; index += 1) {
    history.append("assistant", `old answer ${index}`);
    history.append("user", `old request ${index}`);
  }
  history.append("user", "Reply with ACTIVE_TEST_OK.");

  await generate(controller, history);
  assert.ok(fragments.join("").trim().length > 0, "The underlying model returned no streamed content");
  const eventFiles = [];
  for (const sessionName of fs.readdirSync(stateRoot)) {
    const eventPath = path.join(stateRoot, sessionName, "events.jsonl");
    if (fs.existsSync(eventPath)) eventFiles.push(eventPath);
  }
  assert.equal(eventFiles.length, 1, "Expected exactly one isolated checkpoint session");
  const events = fs.readFileSync(eventFiles[0], "utf8").trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);
  const compaction = events.find((event) => event.type === "compaction_decision");
  assert.equal(compaction?.applied, true, "Active compaction was not applied");
  assert.ok(compaction.postRemainingTokens >= 5000, "Post-compaction hard margin was not met");
  assert.ok(events.some((event) => event.type === "target_model_auto_selected") || config.targetModel);
  console.log(JSON.stringify({
    ok: true,
    active: true,
    streamedCharacters: fragments.join("").length,
    postRemainingTokens: compaction.postRemainingTokens,
    retainedTurns: compaction.retainedTurns,
  }));
  fs.rmSync(stateRoot, { recursive: true, force: true });
}

main().catch((error) => {
  console.error(error?.stack || error);
  process.exitCode = 1;
});
