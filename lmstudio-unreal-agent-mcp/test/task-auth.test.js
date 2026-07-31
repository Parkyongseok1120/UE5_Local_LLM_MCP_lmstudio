"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");

const {
  authorizeActiveRouteTool,
  authorizeTaskRouteTool,
  discoverActiveTaskContext,
  featureIntentTargetHash,
  requiredFields,
  selectionBindingForState,
  validateMutationAuth,
} = require("../src/task-auth");

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

test("explicit route authorization rejects a task bound to another active project", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-scope-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-scope-state-"));
  const projectA = path.join(workspace, "A", "A.uproject");
  const projectB = path.join(workspace, "B", "B.uproject");
  fs.mkdirSync(path.dirname(projectA), { recursive: true });
  fs.mkdirSync(path.dirname(projectB), { recursive: true });
  fs.writeFileSync(projectA, "{}");
  fs.writeFileSync(projectB, "{}");
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
    ...authorization,
    status: "running",
    projectFile: projectA,
    routeScope: { workspaceRoot: workspace, projectFile: projectA },
    writeGate: { writesAllowed: true },
    toolRoute: {
      status: "active",
      routeHash: "route",
      phase: "executor",
      activeTools: ["replace_in_file"],
      allowedPathScopes: ["Source/Demo/Foo.cpp"],
      maxToolCallsPerPhase: 3,
    },
    toolRouteUsage: { routeHash: "route", count: 0, calls: [] },
  }));
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const result = authorizeTaskRouteTool(
      workspace,
      "replace_in_file",
      {
        taskAuthorization: {
          ...authorization,
          routeHash: "route",
          routePhase: "executor",
        },
        path: "Source/Demo/Foo.cpp",
      },
      { consumeBudget: false, activeProject: projectB }
    );
    assert.strictEqual(result.ok, false);
    assert.strictEqual(result.errorCode, "TASK_PROJECT_MISMATCH");
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("expired continuity lease blocks mutation while legacy tasks remain compatible", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-state-"));
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  const state = {
    ...authorization,
    status: "running",
    writeGate: { writesAllowed: true },
    continuity: {
      lease: {
        status: "active",
        expiresAt: new Date(Date.now() - 1000).toISOString(),
      },
      recovery: { status: "not_required", conflicts: [] },
    },
  };
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify(state));
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const expired = validateMutationAuth(
      workspace,
      { taskAuthorization: authorization },
      { requireAll: true }
    );
    assert.strictEqual(expired.errorCode, "TASK_LEASE_EXPIRED");

    delete state.continuity;
    fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify(state));
    assert.strictEqual(
      validateMutationAuth(
        workspace,
        { taskAuthorization: authorization },
        { requireAll: true }
      ).ok,
      true
    );
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("checkpoint conflict blocks mutation", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-state-"));
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
    ...authorization,
    status: "running",
    writeGate: { writesAllowed: true },
    continuity: {
      lease: {
        status: "active",
        expiresAt: new Date(Date.now() + 60_000).toISOString(),
      },
      recovery: {
        status: "blocked_by_checkpoint_conflict",
        conflicts: [{ relativePath: "Source/Demo/Thing.cpp" }],
      },
    },
  }));
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const result = validateMutationAuth(
      workspace,
      { taskAuthorization: authorization },
      { requireAll: true }
    );
    assert.strictEqual(result.errorCode, "TASK_CHECKPOINT_CONFLICT");
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("autonomy supervisor blockers stop mutation while legacy tasks remain compatible", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-state-"));
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  const blockers = [{
    code: "repeated_error_no_progress",
    count: 3,
    limit: 3,
  }];
  const state = {
    ...authorization,
    status: "running",
    writeGate: { writesAllowed: true },
    autonomySupervisor: {
      status: "blocked",
      blockers,
      nextAction: "replan_autonomous_strategy",
    },
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
    assert.strictEqual(blocked.errorCode, "TASK_AUTONOMY_BLOCKED");
    assert.deepStrictEqual(blocked.blockers, blockers);
    assert.strictEqual(blocked.nextAction, "replan_autonomous_strategy");

    delete state.autonomySupervisor;
    fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify(state));
    assert.strictEqual(
      validateMutationAuth(
        workspace,
        { taskAuthorization: authorization },
        { requireAll: true }
      ).ok,
      true
    );
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

test("runtime candidate gate binds writes to selected target hash", () => {
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
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
    ...authorization,
    status: "running",
    projectFile: path.join(project, "Demo.uproject"),
    writeGate: { writesAllowed: true },
    requiredBeforeWrite: ["unreal_runtime_debug_session"],
    requiredGateSetHash: "gate-set",
    completedGates: {
      unreal_runtime_debug_session: {
        status: "completed",
        gateSetHash: "gate-set",
        expiresAt: new Date(Date.now() + 60_000).toISOString(),
        targetSnapshots: [{ absolutePath: target, exists: true, fileHash }],
      },
    },
  }));
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const valid = validateMutationAuth(
      workspace,
      { taskAuthorization: authorization, path: "Source/Demo/Thing.cpp" },
      { requireAll: true }
    );
    assert.strictEqual(valid.ok, true);
    const unrelated = validateMutationAuth(
      workspace,
      { taskAuthorization: authorization, path: "Source/Demo/Other.cpp" },
      { requireAll: true }
    );
    assert.strictEqual(unrelated.errorCode, "GATE_TARGET_MISMATCH");
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("feature intent gate binds selected contract to plan checkpoint and targets", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-state-"));
  const project = path.join(workspace, "Demo");
  const target = path.join(project, "Source", "Demo", "Thing.cpp");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(path.join(project, "Demo.uproject"), "{}");
  fs.writeFileSync(target, "before");
  const fileHash = require("crypto").createHash("sha1").update("before").digest("hex");
  const snapshots = [{
    path: "Source/Demo/Thing.cpp",
    absolutePath: target,
    exists: true,
    fileHash,
  }];
  const targetSnapshotHash = featureIntentTargetHash(snapshots);
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  const intentBinding = {
    selectedIntentId: "bounded_local",
    intentContractHash: "a".repeat(64),
    acceptanceOracleHash: "b".repeat(64),
    planRevision: authorization.planRevision,
    checkpointHash: "checkpoint-1",
    targetSnapshotHash,
  };
  const state = {
    ...authorization,
    status: "running",
    projectFile: path.join(project, "Demo.uproject"),
    writeGate: { writesAllowed: true },
    requiredBeforeWrite: ["unreal_feature_intent_resolve"],
    requiredGateSetHash: "gate-set",
    selectedIntentId: intentBinding.selectedIntentId,
    intentContractHash: intentBinding.intentContractHash,
    continuity: {
      planIdentityHash: "initial",
      checkpoint: { checkpointHash: "checkpoint-1" },
      recovery: { conflicts: [] },
    },
    featureIntent: { status: "resolved", ...intentBinding },
    completedGates: {
      unreal_feature_intent_resolve: {
        status: "completed",
        gateSetHash: "gate-set",
        expiresAt: new Date(Date.now() + 60_000).toISOString(),
        targetSnapshots: snapshots,
        ...intentBinding,
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

    state.continuity.checkpoint.checkpointHash = "checkpoint-2";
    fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify(state));
    const stale = validateMutationAuth(
      workspace,
      { taskAuthorization: authorization, path: "Source/Demo/Thing.cpp" },
      { requireAll: true }
    );
    assert.strictEqual(stale.errorCode, "FEATURE_INTENT_BINDING_STALE");

    state.continuity.checkpoint.checkpointHash = "checkpoint-1";
    delete state.featureIntent;
    fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify(state));
    const missing = validateMutationAuth(
      workspace,
      { taskAuthorization: authorization, path: "Source/Demo/Thing.cpp" },
      { requireAll: true }
    );
    assert.strictEqual(missing.errorCode, "FEATURE_INTENT_STATE_MISSING");
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("feature intent fields do not affect legacy tasks when gate is not required", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-state-"));
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
    ...authorization,
    status: "running",
    writeGate: { writesAllowed: true },
    featureIntent: { status: "pending" },
  }));
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    assert.strictEqual(
      validateMutationAuth(
        workspace,
        { taskAuthorization: authorization },
        { requireAll: true }
      ).ok,
      true
    );
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("feature intent target hash matches the Python canonical contract", () => {
  const snapshots = [
    {
      path: "Source/A  File.cpp",
      absolutePath: "C:/프로젝트/Source/A  File.cpp",
      exists: true,
      fileHash: "abc",
    },
    {
      path: "Source/New.cpp",
      absolutePath: "C:/프로젝트/Source/New.cpp",
      exists: false,
      fileHash: "",
    },
  ];
  assert.strictEqual(
    featureIntentTargetHash(snapshots),
    "adca89573a6b4f15a86f3b2ba2285bbdd2c7988d876663ab7fb87fa70cd18ded"
  );
});

function routeState(projectFile, overrides = {}) {
  const routeAuthorization = {
    ...authorization,
    routeHash: "route-1",
    routePhase: "executor",
  };
  return {
    ...routeAuthorization,
    status: "running",
    workspaceRoot: path.dirname(projectFile),
    projectFile,
    writeGate: { writesAllowed: true },
    selectedHypothesisId: "",
    selectedCandidateId: "",
    selectedTargetSnapshots: [],
    runtimeDebugSession: {
      selectedHypothesisId: "",
      patchCandidateComparison: { selectedCandidateId: "", candidates: [] },
    },
    toolRoute: {
      routeHash: "route-1",
      phase: "executor",
      roleSession: "executor",
      activeTools: ["replace_in_file", "read_file"],
      maxToolCallsPerPhase: 2,
      maxFilesPerSlice: 2,
      selectedSlice: {
        sliceId: "slice-1",
        files: ["Source/Demo/Foo.cpp"],
      },
    },
    toolRouteUsage: {
      routeHash: "route-1",
      phase: "executor",
      roleSession: "executor",
      count: 0,
      calls: [],
    },
    ...overrides,
  };
}

function writeRouteState(stateRoot, state) {
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify(state));
}

test("route-aware auth rejects stale route and suffix path escape", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-route-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-route-state-"));
  const projectFile = path.join(workspace, "Demo.uproject");
  fs.writeFileSync(projectFile, "{}");
  const state = routeState(projectFile);
  writeRouteState(stateRoot, state);
  const routeAuthorization = {
    ...authorization,
    routeHash: "route-1",
    routePhase: "executor",
  };
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const stale = validateMutationAuth(
      workspace,
      {
        taskAuthorization: { ...routeAuthorization, routeHash: "stale" },
        path: "Source/Demo/Foo.cpp",
      },
      { requireAll: true, toolName: "replace_in_file" }
    );
    assert.strictEqual(stale.errorCode, "TASK_ROUTE_STALE");
    assert.strictEqual(
      stale.nextAction,
      "retry_same_tool_with_returned_taskAuthorization"
    );
    assert.strictEqual(stale.taskAuthorization.routeHash, "route-1");
    assert.strictEqual(stale.taskAuthorization.routePhase, "executor");

    const mismatch = validateMutationAuth(
      workspace,
      {
        taskAuthorization: { ...routeAuthorization, authToken: "wrong-token" },
        path: "Source/Demo/Foo.cpp",
      },
      { requireAll: true, toolName: "replace_in_file" }
    );
    assert.strictEqual(mismatch.errorCode, "TASK_AUTH_MISMATCH");
    assert.strictEqual(
      mismatch.nextAction,
      "replan_or_resume_with_returned_taskAuthorization"
    );
    assert.strictEqual(mismatch.taskAuthorization.authToken, authorization.authToken);
    assert.notStrictEqual(
      mismatch.nextAction,
      "retry_same_tool_with_returned_taskAuthorization"
    );

    const exact = validateMutationAuth(
      workspace,
      {
        taskAuthorization: routeAuthorization,
        path: "Source/Demo/Foo.cpp",
      },
      { requireAll: true, toolName: "replace_in_file" }
    );
    assert.strictEqual(exact.ok, true);

    const suffixEscape = validateMutationAuth(
      workspace,
      {
        taskAuthorization: routeAuthorization,
        path: "Source/Other/Source/Demo/Foo.cpp",
      },
      { requireAll: true, toolName: "replace_in_file" }
    );
    assert.strictEqual(suffixEscape.errorCode, "TASK_SLICE_TARGET_MISMATCH");
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("selection reselection mismatch and checkpoint drift fail closed", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-route-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-route-state-"));
  const projectFile = path.join(workspace, "Demo.uproject");
  fs.writeFileSync(projectFile, "{}");
  const state = routeState(projectFile, {
    selectedHypothesisId: "hyp-1",
    selectedCandidateId: "candidate-1",
    selectedTargetSnapshots: [
      { path: "Source/Demo/Foo.cpp", exists: false, fileHash: "" },
    ],
    runtimeDebugSession: {
      selectedHypothesisId: "hyp-1",
      patchCandidateComparison: {
        selectedCandidateId: "candidate-1",
        candidates: [{ id: "candidate-1" }, { id: "candidate-2" }],
      },
    },
    continuity: {
      checkpoint: { checkpointHash: "checkpoint-1" },
    },
  });
  state.selectionBinding = selectionBindingForState(state);
  writeRouteState(stateRoot, state);
  const routeAuthorization = {
    ...authorization,
    routeHash: "route-1",
    routePhase: "executor",
  };
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    state.selectedCandidateId = "candidate-2";
    writeRouteState(stateRoot, state);
    const mismatch = validateMutationAuth(
      workspace,
      {
        taskAuthorization: routeAuthorization,
        path: "Source/Demo/Foo.cpp",
      },
      { requireAll: true, toolName: "replace_in_file" }
    );
    assert.strictEqual(mismatch.errorCode, "TASK_SELECTION_STATE_MISMATCH");

    state.selectedCandidateId = "candidate-1";
    state.continuity.checkpoint.checkpointHash = "checkpoint-2";
    writeRouteState(stateRoot, state);
    const checkpointDrift = validateMutationAuth(
      workspace,
      {
        taskAuthorization: routeAuthorization,
        path: "Source/Demo/Foo.cpp",
      },
      { requireAll: true, toolName: "replace_in_file" }
    );
    assert.strictEqual(checkpointDrift.errorCode, "TASK_SELECTION_BINDING_STALE");
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("active route discovery is tri-state and all-call budget is fail closed", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-route-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-route-state-"));
  const projectFile = path.join(workspace, "Demo.uproject");
  fs.writeFileSync(projectFile, "{}");
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    assert.strictEqual(discoverActiveTaskContext(workspace).status, "none");
    assert.strictEqual(
      authorizeActiveRouteTool(workspace, "read_file").legacy,
      true
    );

    writeRouteState(stateRoot, routeState(projectFile));
    assert.strictEqual(
      discoverActiveTaskContext(workspace, projectFile).status,
      "active"
    );
    const routeOptions = { activeProject: projectFile };
    assert.strictEqual(
      authorizeActiveRouteTool(workspace, "read_file", {}, routeOptions).ok,
      true
    );
    assert.strictEqual(
      authorizeActiveRouteTool(workspace, "read_file", {}, routeOptions).ok,
      true
    );
    const exhausted = authorizeActiveRouteTool(
      workspace,
      "read_file",
      {},
      routeOptions
    );
    assert.strictEqual(
      exhausted.errorCode,
      "TASK_PHASE_TOOL_BUDGET_EXHAUSTED"
    );

    const corruptDir = path.join(stateRoot, "tasks", "corrupt_task");
    fs.mkdirSync(corruptDir, { recursive: true });
    fs.writeFileSync(
      path.join(corruptDir, "workspace-root.txt"),
      workspace
    );
    fs.writeFileSync(path.join(corruptDir, "state.json"), "{");
    assert.strictEqual(
      discoverActiveTaskContext(workspace, projectFile).status,
      "ambiguous_or_corrupt"
    );
    assert.strictEqual(
      authorizeActiveRouteTool(
        workspace,
        "read_file",
        {},
        routeOptions
      ).errorCode,
      "TASK_ROUTE_AMBIGUOUS_OR_CORRUPT"
    );
    assert.strictEqual(
      authorizeActiveRouteTool(
        workspace,
        "get_workspace_info",
        {},
        routeOptions
      ).recoveryOnly,
      true
    );

    fs.rmSync(corruptDir, { recursive: true, force: true });
    const second = {
      ...routeState(projectFile),
      taskSessionId: "task_87654321",
    };
    const secondDir = path.join(stateRoot, "tasks", second.taskSessionId);
    fs.mkdirSync(secondDir, { recursive: true });
    fs.writeFileSync(path.join(secondDir, "state.json"), JSON.stringify(second));
    assert.strictEqual(
      discoverActiveTaskContext(workspace, projectFile).status,
      "ambiguous_or_corrupt"
    );
    const primaryStatePath = path.join(
      stateRoot,
      "tasks",
      authorization.taskSessionId,
      "state.json"
    );
    const primaryState = JSON.parse(fs.readFileSync(primaryStatePath, "utf8"));
    primaryState.toolRouteUsage.count = 0;
    primaryState.toolRouteUsage.calls = [];
    fs.writeFileSync(primaryStatePath, JSON.stringify(primaryState));
    const explicitAuthorization = {
      ...authorization,
      routeHash: "route-1",
      routePhase: "executor",
    };
    const explicit = authorizeTaskRouteTool(
      workspace,
      "read_file",
      { taskAuthorization: explicitAuthorization }
    );
    assert.strictEqual(explicit.ok, true);
    assert.strictEqual(
      JSON.parse(fs.readFileSync(primaryStatePath, "utf8")).toolRouteUsage.count,
      1
    );
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("project identity bridges server workspaces without claiming unrelated context", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-route-workspace-"));
  const otherWorkspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-route-other-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-route-state-"));
  const projectFile = path.join(otherWorkspace, "Other.uproject");
  fs.writeFileSync(projectFile, "{}");
  writeRouteState(stateRoot, routeState(projectFile));
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    assert.strictEqual(
      discoverActiveTaskContext(otherWorkspace, projectFile).status,
      "active"
    );
    assert.strictEqual(discoverActiveTaskContext(workspace).status, "none");
    assert.strictEqual(
      discoverActiveTaskContext(workspace, projectFile).status,
      "active"
    );
    assert.strictEqual(
      authorizeActiveRouteTool(workspace, "read_file").legacy,
      true
    );
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(otherWorkspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("expired active route is blocked rather than treated as legacy", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-route-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-route-state-"));
  const projectFile = path.join(workspace, "Demo.uproject");
  fs.writeFileSync(projectFile, "{}");
  writeRouteState(
    stateRoot,
    routeState(projectFile, {
      continuity: {
        lease: {
          status: "active",
          expiresAt: new Date(Date.now() - 1000).toISOString(),
        },
        recovery: { conflicts: [] },
      },
    })
  );
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    assert.strictEqual(
      discoverActiveTaskContext(workspace, projectFile).status,
      "blocked"
    );
    assert.strictEqual(
      authorizeActiveRouteTool(
        workspace,
        "read_file",
        {},
        { activeProject: projectFile }
      ).errorCode,
      "TASK_ROUTE_AMBIGUOUS_OR_CORRUPT"
    );
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});
