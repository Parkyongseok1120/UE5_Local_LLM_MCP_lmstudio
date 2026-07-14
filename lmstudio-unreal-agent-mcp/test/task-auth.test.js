"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");

const { requiredFields, validateMutationAuth } = require("../src/task-auth");

const authorization = {
  taskSessionId: "task_12345678",
  authToken: "token",
  planId: "plan",
  planRevision: "1",
  activeSliceId: "slice-1",
};

test("requiredFields accepts nested taskAuthorization unchanged", () => {
  assert.deepStrictEqual(requiredFields({ taskAuthorization: authorization }), authorization);
});

test("nested taskAuthorization validates against task state", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-state-"));
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
    ...authorization,
    status: "running",
    writeGate: { writesAllowed: true },
  }));
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    assert.strictEqual(validateMutationAuth(workspace, { taskAuthorization: authorization }, { requireAll: true }).ok, true);
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});
