"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  failure,
  normalizeDirectPayload,
  success,
  toMcpResult,
} = require("../src/direct-response.js");

test("Direct response removes workflow control without reconstructing a legacy suggestion", () => {
  const legacyVariants = [
    {
      requiredNextTool: "search_files",
      requiredNextToolArgs: { query: "Demo.cpp", ownerCapability: "nested-secret" },
    },
    {
      requiredNextTool: {
        tool: "search_files",
        args: { query: "Demo.cpp", ownerCapability: "nested-secret" },
      },
    },
    {
      nextAction: "read_file",
      nextActionArgs: { path: "project://Source/Demo.cpp" },
      nextActionIsTool: true,
    },
  ];

  for (const legacy of legacyVariants) {
    const payload = normalizeDirectPayload({
      ok: false,
      errorCode: "READ_TARGET_NOT_FOUND",
      error: "Target file was not found.",
      retryable: true,
      doNotRetry: ["read_file"],
      ...legacy,
      control: { disposition: "continue", allowedTools: ["read_file"] },
      taskAuthorization: {
        taskSessionId: "foreign-task",
        ownerCapability: "owner-secret",
      },
      routeHash: "route-secret",
      claimLedger: [{ claim: "large internal state" }],
    });

    assert.deepStrictEqual(payload.retry, {
      allowed: true,
      mode: "different_arguments",
    });
    assert.strictEqual(payload.suggestion, undefined);
    const rendered = JSON.stringify(payload);
    for (const forbidden of [
      "doNotRetry",
      "requiredNextTool",
      "nextAction",
      "nextActionArgs",
      "nextActionIsTool",
      "taskAuthorization",
      "ownerCapability",
      "routeHash",
      "claimLedger",
      "allowedTools",
    ]) {
      assert.doesNotMatch(rendered, new RegExp(forbidden));
    }
  }
});

test("non-retryable Direct response cannot suggest the blocked tool again", () => {
  const raw = failure("READ_REPEAT", "No new information.", {
    retryAllowed: false,
    suggestion: { tool: "read_file", args: { path: "project://Source/Demo.cpp" } },
  });
  const result = toMcpResult(raw, { currentTool: "read_file" });

  assert.strictEqual(result.isError, true);
  assert.deepStrictEqual(result.structuredContent.retry, {
    allowed: false,
    mode: "none",
  });
  assert.strictEqual(result.structuredContent.suggestion, undefined);
  assert.strictEqual(JSON.parse(result.content[0].text).suggestion, undefined);
});

test("non-retryable Direct response retains one explicit current-contract advisory suggestion", () => {
  const result = toMcpResult({
    ok: false,
    errorCode: "READ_TARGET_NOT_FOUND",
    message: "Target file was not found.",
    retry: { allowed: false, mode: "none" },
    suggestion: {
      tool: "search_files",
      args: { query: "Demo.cpp", path: "project://Source" },
    },
  }, { currentTool: "read_file" });

  assert.deepStrictEqual(result.structuredContent.suggestion, {
    tool: "search_files",
    args: { query: "Demo.cpp", path: "project://Source" },
  });
  assert.strictEqual(result.structuredContent.requiredNextTool, undefined);
  assert.strictEqual(result.structuredContent.agentInstruction, undefined);
});

test("Direct MCP errors remain bounded and structured/text projections agree", () => {
  const result = toMcpResult({
    ok: false,
    errorCode: "TOOL_FAILED",
    message: "x".repeat(20_000),
    control: { evidenceBundle: "y".repeat(50_000) },
    ownerCapability: "must-not-leak",
    retry: { allowed: false, mode: "none" },
  }, { currentTool: "read_file", maxChars: 4096 });

  assert.strictEqual(result.isError, true);
  assert.ok(result.content[0].text.length <= 4096);
  assert.deepStrictEqual(JSON.parse(result.content[0].text), result.structuredContent);
  assert.doesNotMatch(result.content[0].text, /ownerCapability|must-not-leak|evidenceBundle/);
});

test("successful Direct response has no retry directive", () => {
  assert.deepStrictEqual(success({ value: 1 }), { value: 1, ok: true });
});

test("oversized success becomes a retryable error instead of silently dropping cursor data", () => {
  const result = toMcpResult(success({
    content: "x".repeat(20_000),
    nextOffsetBytes: 20_000,
    hasMore: false,
  }), { currentTool: "read_file", maxChars: 4096 });

  assert.strictEqual(result.isError, true);
  assert.strictEqual(result.structuredContent.errorCode, "OUTPUT_LIMIT_EXCEEDED");
  assert.strictEqual(result.structuredContent.nextOffsetBytes, undefined);
  assert.deepStrictEqual(result.structuredContent.retry, { allowed: true, mode: "different_arguments" });
  assert.deepStrictEqual(JSON.parse(result.content[0].text), result.structuredContent);
});
