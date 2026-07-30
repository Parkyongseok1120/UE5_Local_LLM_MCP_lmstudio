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

test("required pre-write gates fail closed until completed", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-state-"));
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  const state = {
    ...authorization,
    status: "running",
    writeGate: { writesAllowed: true },
    requiredBeforeWrite: ["unreal_code_sketch_claim_validate"],
    requiredGateSetHash: "gate-set",
    completedGates: {},
  };
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify(state));
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const blocked = validateMutationAuth(
      workspace,
      { taskAuthorization: authorization },
      { requireAll: true }
    );
    assert.strictEqual(blocked.ok, false);
    assert.strictEqual(blocked.errorCode, "REQUIRED_GATE_INCOMPLETE");

    state.completedGates.unreal_code_sketch_claim_validate = {
      status: "completed",
      gateSetHash: "gate-set",
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
      targetSnapshots: [],
    };
    fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify(state));
    assert.strictEqual(
      validateMutationAuth(workspace, { taskAuthorization: authorization }, { requireAll: true }).ok,
      true
    );
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("active background task blocks concurrent manual mutations", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-state-"));
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
    ...authorization,
    status: "running",
    activeJobId: "job_12345678",
    writeGate: { writesAllowed: true },
  }));
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const result = validateMutationAuth(
      workspace,
      { taskAuthorization: authorization },
      { requireAll: true }
    );
    assert.strictEqual(result.ok, false);
    assert.strictEqual(result.errorCode, "TASK_JOB_IN_PROGRESS");
    assert.strictEqual(result.activeJobId, "job_12345678");
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("corrupt canonical task state fails closed without throwing", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-state-"));
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  fs.writeFileSync(path.join(taskDir, "state.json"), '{"taskSessionId":');
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const result = validateMutationAuth(
      workspace,
      { taskAuthorization: authorization },
      { requireAll: true }
    );
    assert.strictEqual(result.ok, false);
    assert.strictEqual(result.errorCode, "TASK_STATE_CORRUPT");
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("corrupt legacy task state fails closed without throwing", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-state-"));
  const legacyDir = path.join(
    workspace,
    ".agent",
    "tasks",
    authorization.taskSessionId
  );
  fs.mkdirSync(legacyDir, { recursive: true });
  fs.writeFileSync(path.join(legacyDir, "state.json"), "[]");
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const result = validateMutationAuth(
      workspace,
      { taskAuthorization: authorization },
      { requireAll: true }
    );
    assert.strictEqual(result.ok, false);
    assert.strictEqual(result.errorCode, "TASK_STATE_CORRUPT");
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("task authorization rejects mismatched persisted task identity", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-state-"));
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
    ...authorization,
    taskSessionId: "different_task_id",
    status: "running",
    writeGate: { writesAllowed: true },
  }));
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const result = validateMutationAuth(
      workspace,
      { taskAuthorization: authorization },
      { requireAll: true }
    );
    assert.strictEqual(result.ok, false);
    assert.strictEqual(result.errorCode, "TASK_STATE_ID_MISMATCH");
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("code-generation gate binds writes to validated target hash", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-state-"));
  const project = path.join(workspace, "Demo");
  const target = path.join(project, "Source", "Demo", "Thing.cpp");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(path.join(project, "Demo.uproject"), "{}");
  fs.writeFileSync(target, "before");
  const fileHash = require("crypto").createHash("sha1").update("before").digest("hex");
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  const state = {
    ...authorization,
    status: "running",
    projectFile: path.join(project, "Demo.uproject"),
    writeGate: { writesAllowed: true },
    requiredBeforeWrite: ["unreal_code_sketch_claim_validate"],
    requiredGateSetHash: "gate-set",
    completedGates: {
      unreal_code_sketch_claim_validate: {
        status: "completed",
        gateSetHash: "gate-set",
        expiresAt: new Date(Date.now() + 60_000).toISOString(),
        targetSnapshots: [{ absolutePath: target, exists: true, fileHash }],
      },
    },
  };
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify(state));
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const valid = validateMutationAuth(
      workspace,
      { taskAuthorization: authorization, path: "Source/Demo/Thing.cpp" },
      { requireAll: true }
    );
    assert.strictEqual(valid.ok, true);

    const wrongPath = validateMutationAuth(
      workspace,
      { taskAuthorization: authorization, path: "Source/Demo/Other.cpp" },
      { requireAll: true }
    );
    assert.strictEqual(wrongPath.errorCode, "GATE_TARGET_MISMATCH");

    fs.writeFileSync(target, "changed");
    const stale = validateMutationAuth(
      workspace,
      { taskAuthorization: authorization, path: "Source/Demo/Thing.cpp" },
      { requireAll: true }
    );
    assert.strictEqual(stale.errorCode, "GATE_TARGET_STALE");
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});
