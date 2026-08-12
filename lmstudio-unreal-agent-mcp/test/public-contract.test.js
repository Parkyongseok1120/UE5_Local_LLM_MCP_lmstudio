"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
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
