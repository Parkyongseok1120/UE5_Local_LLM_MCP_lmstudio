"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  attachControlEnvelope,
  conciseControlText,
  modelVisibleControlText,
} = require("../src/control-envelope");

test("control envelope preserves tool-vs-non-tool recovery semantics", () => {
  const payload = attachControlEnvelope({
    ok: false,
    errorCode: "TASK_AUTH_MISMATCH",
    nextAction: "unreal_agent_plan",
    nextActionIsTool: true,
    retryable: false,
    taskAuthorization: {
      taskSessionId: "task-1",
      ownerCapability: "owner-secret",
    },
  }, "replace_in_file");

  assert.strictEqual(payload.control.taskId, "task-1");
  assert.strictEqual(payload.control.phase, "replace_in_file");
  assert.strictEqual(payload.control.nextAction, "unreal_agent_plan");
  assert.strictEqual(payload.control.nextActionIsTool, true);
  assert.strictEqual(payload.control.retryPolicy, "forbidden");
  assert.strictEqual(JSON.stringify(payload.control).includes("owner-secret"), false);
});

test("requiredNextTool object becomes one canonical action name", () => {
  const payload = attachControlEnvelope({
    ok: false,
    requiredNextTool: { server: "unreal-rag", name: "unreal_set_active_project" },
  }, "build_unreal_project");

  assert.strictEqual(payload.control.nextAction, "unreal_set_active_project");
  assert.strictEqual(payload.control.nextActionIsTool, true);
});

test("tool-shaped nextAction is marked executable without a duplicate hint", () => {
  const payload = attachControlEnvelope({
    ok: false,
    errorCode: "SLICE_PLAN_REQUIRED",
    nextAction: "unreal_task_define_slices",
    retryable: true,
  }, "unreal_feature_intent_resolve");

  assert.strictEqual(payload.control.nextAction, "unreal_task_define_slices");
  assert.strictEqual(payload.control.nextActionIsTool, true);
});

test("exported tool-action classifier keeps route wrappers executable", () => {
  const { looksLikeToolAction } = require("../src/control-envelope");
  assert.strictEqual(looksLikeToolAction("unreal_task_checkpoint"), true);
  assert.strictEqual(looksLikeToolAction("get_active_project"), true);
  assert.strictEqual(looksLikeToolAction("static_validate_project"), true);
  assert.strictEqual(looksLikeToolAction("continue_with_current_tool_route"), false);
  assert.strictEqual(looksLikeToolAction("enable_or_call_unreal_agent_plan"), false);
});

test("prose-like nextAction remains non-tool", () => {
  const payload = attachControlEnvelope({
    ok: false,
    nextAction: "answer_feature_questions",
  }, "unreal_feature_intent_resolve");
  assert.strictEqual(payload.control.nextActionIsTool, false);
});

test("existing server control survives a second envelope pass", () => {
  const payload = attachControlEnvelope({
    ok: false,
    control: {
      version: 1,
      taskId: "task-existing",
      phase: "recovery",
      status: "NeedsAction",
      nextAction: "search_files",
      nextActionIsTool: true,
      retryPolicy: "once",
      blockerFingerprint: "existing-fingerprint",
    },
  }, "bridge");
  assert.deepStrictEqual(payload.control, {
    version: 1,
    taskId: "task-existing",
    phase: "recovery",
    status: "NeedsAction",
    nextAction: "search_files",
    nextActionIsTool: true,
    retryPolicy: "once",
    blockerFingerprint: "existing-fingerprint",
  });
});

test("concise text points to structured content instead of duplicating payload", () => {
  const payload = attachControlEnvelope({
    ok: false,
    errorCode: "DEMO_ERROR",
    error: "x".repeat(5000),
    rows: Array.from({ length: 100 }, () => ({ value: "y".repeat(1000) })),
  }, "demo_tool");
  const text = conciseControlText(payload);

  assert.ok(text.length < 1200);
  assert.match(text, /structuredContent/);
  assert.match(text, /DEMO_ERROR/);
});

test("LM Studio text fallback mirrors actionable structured data", () => {
  const payload = attachControlEnvelope({
    ok: true,
    path: { uri: "project://Source" },
    entries: [{ name: "O_Mock", type: "dir" }],
  }, "list_directory");

  const text = modelVisibleControlText(payload, "lmstudio");

  assert.match(text, /O_Mock/);
  assert.match(text, /project:\/\/Source/);
  assert.match(text, /"control"/);
  assert.doesNotMatch(text, /Detailed result is available/);
});

test("non-LM Studio clients keep concise structured-content pointer", () => {
  const payload = attachControlEnvelope({
    ok: true,
    entries: [{ name: "x".repeat(5000) }],
  }, "list_directory");

  const text = modelVisibleControlText(payload, "cline");

  assert.ok(text.length < 1200);
  assert.match(text, /structuredContent/);
});

test("LM Studio bounded fallback preserves blocker controls", () => {
  const payload = attachControlEnvelope({
    ok: false,
    errorCode: "EVIDENCE_STAGNATION_REPEAT",
    retryable: false,
    stopCurrentWorkflow: true,
    agentInstruction: "Do not call another evidence tool.",
    rows: Array.from({ length: 100 }, () => ({ text: "x".repeat(4000) })),
  }, "search_files");

  const text = modelVisibleControlText(payload, "lmstudio", 2500);
  const visible = JSON.parse(text);

  assert.ok(text.length <= 2500);
  assert.strictEqual(visible.errorCode, "EVIDENCE_STAGNATION_REPEAT");
  assert.strictEqual(visible.control.retryPolicy, "forbidden");
  assert.strictEqual(visible.stopCurrentWorkflow, true);
  assert.strictEqual(visible.agentInstruction, "Do not call another evidence tool.");
});

test("LM Studio bounded fallback preserves evidence phase boundary", () => {
  const payload = attachControlEnvelope({
    ok: false,
    errorCode: "EVIDENCE_STAGNATION",
    retryable: false,
    stopCurrentWorkflow: false,
    stopCurrentPhase: true,
    phaseBoundary: "evidence",
    doNotRetry: ["read_file", "read_file_range", "read_symbol", "search_files"],
    doNotRetryTools: ["unreal_rag_search"],
    agentInstruction: "Continue with an evidence-supported mutation.",
  }, "search_files");
  const visible = JSON.parse(modelVisibleControlText(payload, "lmstudio", 2_000));
  assert.strictEqual(visible.stopCurrentWorkflow, false);
  assert.strictEqual(visible.stopCurrentPhase, true);
  assert.strictEqual(visible.phaseBoundary, "evidence");
  assert.deepStrictEqual(visible.doNotRetryTools, ["unreal_rag_search"]);
});

test("LM Studio extreme control strings remain valid bounded JSON", () => {
  const payload = attachControlEnvelope({
    ok: false,
    errorCode: "BLOCKED",
    retryable: false,
    stopCurrentWorkflow: true,
    agentInstruction: "stop ".repeat(10_000),
    control: {
      phase: "phase ".repeat(10_000),
      continuationToken: "token ".repeat(10_000),
    },
    rows: Array.from({ length: 100 }, () => ({ text: "x".repeat(10_000) })),
  }, "search_files");

  const text = modelVisibleControlText(payload, "lmstudio", 2000);
  const visible = JSON.parse(text);

  assert.ok(text.length <= 2000);
  assert.strictEqual(visible.errorCode, "BLOCKED");
  assert.strictEqual(visible.control.retryPolicy, "forbidden");
  assert.strictEqual(visible.stopCurrentWorkflow, true);
});
