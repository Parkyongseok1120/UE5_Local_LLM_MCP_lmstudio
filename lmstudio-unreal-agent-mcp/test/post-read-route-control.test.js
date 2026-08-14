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
