"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  attachControlEnvelope,
  conciseControlText,
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
