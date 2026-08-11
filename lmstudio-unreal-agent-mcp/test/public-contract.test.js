"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { sanitizeModelPayload } = require("../src/public-contract.js");

test("model payload exposes only stable task ownership fields", () => {
  const payload = sanitizeModelPayload({
    taskAuthorization: {
      taskSessionId: "task-1",
      ownerCapability: "owner-1",
      authToken: "rotating-token",
      planId: "plan-1",
      planRevision: 7,
      activeSliceId: "slice-1",
      routeHash: "route-1",
      routePhase: "executor",
    },
    nested: {
      nextActionArgs: {
        taskAuthorization: {
          taskSessionId: "task-1",
          ownerCapability: "owner-1",
          authToken: "new-token",
        },
      },
    },
    routeHash: "diagnostic-route-hash",
  });

  assert.deepStrictEqual(payload.taskAuthorization, {
    taskSessionId: "task-1",
    ownerCapability: "owner-1",
  });
  assert.deepStrictEqual(payload.nested.nextActionArgs.taskAuthorization, {
    taskSessionId: "task-1",
    ownerCapability: "owner-1",
  });
  assert.strictEqual(payload.routeHash, "diagnostic-route-hash");
});
