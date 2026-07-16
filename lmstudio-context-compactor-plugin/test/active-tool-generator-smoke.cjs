"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const { Chat, LMStudioClient } = require("@lmstudio/sdk");

function entropyBlock(turn, length = 8_000) {
  let value = "";
  for (let index = 0; value.length < length; index += 1) {
    value += crypto.createHash("sha256").update(`${turn}:${index}:context-overflow-smoke`).digest("hex");
  }
  return value.slice(0, length);
}

async function main() {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-active-tool-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const generatedRequests = [];
    const failures = [];
    const config = {
      enabled: true,
      observeOnly: false,
      strictToolControlPlane: false,
      targetModel: process.env.LMS_CONTEXT_COMPACTOR_TARGET_MODEL || "",
      softRemainingTokens: 10_000,
      hardRemainingTokens: 5_000,
      maxOutputReserve: 256,
      normalToolResultReserve: 256,
      buildToolResultReserve: 512,
      recentCompleteTurns: 1,
      minimumTurnsBetweenCompactions: 0,
      targetRemainingTokensAfterCompaction: 20_000,
    };
    const client = new LMStudioClient();
    const controller = {
      client,
      abortSignal: new AbortController().signal,
      getPluginConfig() {
        return { get(key) { return config[key]; } };
      },
      getWorkingDirectory() { return stateRoot; },
      getToolDefinitions() {
        return [{
          type: "function",
          function: {
            name: "context_probe",
            description: "Required release smoke-test tool. Call it exactly once with the requested value.",
            parameters: {
              type: "object",
              properties: { value: { type: "string" } },
              required: ["value"],
              additionalProperties: false,
            },
          },
        }];
      },
      fragmentGenerated() {},
      toolCallGenerationStarted() {},
      toolCallGenerationNameReceived() {},
      toolCallGenerationArgumentFragmentGenerated() {},
      toolCallGenerationEnded(request) { generatedRequests.push(request); },
      toolCallGenerationFailed(error) { failures.push(error); },
    };
    const history = Chat.empty();
    history.append("system", "Follow the latest user request. Use the supplied tool when explicitly required.");
    history.append("user", "This session validates tool calling after context compaction.");

    const model = config.targetModel
      ? await client.llm.model(config.targetModel)
      : (await client.llm.listLoaded())[0];
    assert.ok(model, "No loaded underlying LLM was available for the active overflow test");
    const contextLength = await model.getContextLength();
    let preCompactionInputTokens = 0;
    for (let index = 0; index < 128 && preCompactionInputTokens <= contextLength; index += 1) {
      history.append("assistant", `discardable old answer ${index}\n${entropyBlock(index)}`);
      history.append("user", `old request ${index}`);
      const formatted = await model.applyPromptTemplate(history);
      preCompactionInputTokens = await model.countTokens(formatted);
    }
    assert.ok(
      preCompactionInputTokens > contextLength,
      `Could not construct an actual tokenizer overflow: ${preCompactionInputTokens} <= ${contextLength}`,
    );
    history.append(
      "user",
      "You must call context_probe exactly once with value POST_COMPACTION_TOOL_OK. Do not answer with plain text.",
    );

    await generate(controller, history);

    assert.equal(failures.length, 0, failures[0]?.message || "Tool generation failed");
    assert.equal(generatedRequests.length, 1, `Expected one tool call, received ${generatedRequests.length}`);
    assert.equal(generatedRequests[0].name, "context_probe");
    assert.equal(generatedRequests[0].arguments?.value, "POST_COMPACTION_TOOL_OK");

    const sessionDirs = fs.readdirSync(stateRoot, { withFileTypes: true })
      .filter((entry) => entry.isDirectory());
    assert.equal(sessionDirs.length, 1, "Expected one isolated checkpoint session");
    const eventPath = path.join(stateRoot, sessionDirs[0].name, "events.jsonl");
    const events = fs.readFileSync(eventPath, "utf8").trim()
      .split(/\r?\n/).filter(Boolean).map(JSON.parse);
    const measurement = events.find((event) => event.type === "context_measurement");
    const compaction = events.find((event) => event.type === "compaction_decision");
    assert.ok(measurement.inputTokens > measurement.contextLength, "The generator did not receive an over-limit history");
    assert.equal(measurement.decision.action, "hard_compact");
    assert.equal(compaction?.applied, true, "Active compaction was not applied before the tool call");
    assert.ok(compaction.postRemainingTokens >= 5_000, "Post-compaction hard margin was not met");
    console.log(JSON.stringify({
      ok: true,
      active: true,
      toolCall: generatedRequests[0],
      preCompactionInputTokens: measurement.inputTokens,
      contextLength: measurement.contextLength,
      decision: measurement.decision.action,
      postRemainingTokens: compaction.postRemainingTokens,
      retainedTurns: compaction.retainedTurns,
    }));
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error?.stack || error);
  process.exitCode = 1;
});
