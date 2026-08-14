"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  attachPostReadRouteControl,
  postReadGatePayload,
} = require("../src/post-read-route-control.js");

function recoveryCommit() {
  return {
    ok: true,
    state: {
      taskSessionId: "task_12345678",
      authToken: "auth-token",
      ownerCapability: "owner-capability",
      planId: "plan-1",
      planRevision: "1",
      activeSliceId: "local-play",
      requiredGateSetHash: "gate-set-local-play",
      mutationGeneration: 0,
      status: "running",
      controlEpoch: 5,
      pendingGates: [
        "unreal_feature_intent_resolve",
        "unreal_code_sketch_claim_validate",
      ],
      failedGateAttempts: {
        unreal_feature_intent_resolve: {
          validationErrorCode: "FEATURE_INTENT_DIRECT_SOURCE_EVIDENCE_REQUIRED",
          nextAction: "read_file",
          attemptCount: 1,
          gateSetHash: "gate-set-local-play",
          planRevision: "1",
          activeSliceId: "local-play",
          mutationGeneration: 0,
        },
      },
      toolRoute: {
        routeHash: "route-4",
        phase: "verifier",
        activeTools: ["read_file", "unreal_feature_intent_resolve"],
      },
    },
  };
}

test("successful required source read resumes the pending feature gate", () => {
  const payload = postReadGatePayload(recoveryCommit(), "read_file");
  assert.strictEqual(payload.nextAction, "unreal_feature_intent_resolve");
  assert.strictEqual(payload.controlEpoch, 5);

  const result = attachPostReadRouteControl(
    { content: [{ type: "text", text: "file body" }] },
    recoveryCommit(),
    "read_file"
  );
  assert.strictEqual(JSON.parse(result.content[0].text).fileContent, "file body");
  assert.strictEqual(result.structuredContent.control.version, 2);
  assert.strictEqual(result.structuredContent.control.epoch, 5);
  assert.strictEqual(
    result.structuredContent.control.requiredTool.name,
    "unreal_feature_intent_resolve"
  );
  assert.deepStrictEqual(
    result.structuredContent.control.allowedTools,
    ["unreal_feature_intent_resolve"]
  );
});

test("ordinary discovery reads do not invent a gate continuation", () => {
  const commit = recoveryCommit();
  commit.state.failedGateAttempts = {};
  const original = { content: [{ type: "text", text: "file body" }] };
  assert.strictEqual(
    attachPostReadRouteControl(original, commit, "read_file"),
    original
  );
});

test("authoritative v2 control cannot be overridden by a legacy pending gate", () => {
  const commit = recoveryCommit();
  commit.state.controlState = {
    version: 2,
    authoritative: true,
    epoch: 6,
    taskSessionId: "task_12345678",
    phase: "executor",
    disposition: "continue",
    requiredTool: null,
    allowedTools: ["read_file"],
  };
  commit.state.toolRoute.expiryTransition = {
    at: "2099-01-01T00:00:00Z",
    route: { phase: "planner", activeTools: ["unreal_feature_intent_resolve"] },
  };

  const payload = postReadGatePayload(commit, "read_file");
  assert.strictEqual(payload.nextAction, "use_authoritative_control");
  assert.strictEqual(payload.nextActionIsTool, false);

  const result = attachPostReadRouteControl(
    { content: [{ type: "text", text: "file body" }] },
    commit,
    "read_file"
  );
  const mirrored = JSON.parse(result.content[0].text);
  assert.strictEqual(mirrored.nextAction, "use_authoritative_control");
  assert.strictEqual(mirrored.nextActionIsTool, false);
  assert.strictEqual(mirrored.fileContent, "file body");
  assert.strictEqual(JSON.stringify(result.structuredContent).includes("expiryTransition"), false);
  assert.ok(result.content[0].text.length < 4_000);
});
