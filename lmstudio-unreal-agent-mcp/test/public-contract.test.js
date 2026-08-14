"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  sanitizeModelPayload,
  withCompactTaskAuthorization,
} = require("../src/public-contract.js");

test("cross-server recovery args preserve stable task ownership only", () => {
  const args = withCompactTaskAuthorization(
    { targetFiles: ["Source/Demo/Foo.cpp"] },
    {
      taskSessionId: "task-1",
      ownerCapability: "owner-1",
      authToken: "rotating-secret",
      routeHash: "rotating-route",
    }
  );

  assert.deepStrictEqual(args, {
    targetFiles: ["Source/Demo/Foo.cpp"],
    taskAuthorization: {
      taskSessionId: "task-1",
      ownerCapability: "owner-1",
    },
  });
});

test("recovery args do not invent task ownership", () => {
  assert.deepStrictEqual(
    withCompactTaskAuthorization({ path: "Source/Demo/Foo.cpp" }, null),
    { path: "Source/Demo/Foo.cpp" }
  );
});

test("public payload removes speculative expiry routes recursively", () => {
  const payload = sanitizeModelPayload({
    toolRoute: {
      phase: "executor",
      expiryTransition: {
        at: "2099-01-01T00:00:00Z",
        route: { phase: "planner", activeTools: ["read_file"] },
      },
    },
  });
  assert.deepStrictEqual(payload, { toolRoute: { phase: "executor" } });
});
