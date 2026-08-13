"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");

const {
  authorizeActiveRouteTool,
  authorizeTaskRouteTool,
  cancelActiveTask,
  checkpointMutationViaPython,
  completeTaskAfterBuildViaPython,
  discoverActiveTaskContext,
  discardTaskAuthorizationWithoutActiveRoute,
  expandCompactTaskAuthorization,
  featureIntentTargetHash,
  requiredFields,
  requestedMutationPaths,
  reserveRouteCall,
  commitRouteReservation,
  rollbackRouteReservation,
  selectionBindingForState,
  validateMutationAuth,
} = require("../src/task-auth");

test("mutation auth normalizes workspace-prefixed active-project paths", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "task-path-normalize-"));
  const projectRoot = path.join(root, "Git", "Demo");
  try {
    const projectFile = path.join(projectRoot, "Demo.uproject");
    const prefixed = requestedMutationPaths(
      { patches: [{ path: "Git/Demo/Source/Demo/Thing.cpp" }] },
      { projectFile },
    );
    const alreadyRelative = requestedMutationPaths(
      { patches: [{ path: "Source/Demo/Thing.cpp" }] },
      { projectFile },
    );
    const expected = path.resolve(projectRoot, "Source", "Demo", "Thing.cpp");

    assert.deepEqual(prefixed, [expected]);
    assert.deepEqual(alreadyRelative, [expected]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

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

test("inspection calls discard invented compact auth when no task route exists", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-no-route-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-no-route-state-"));
  const projectFile = path.join(workspace, "Demo.uproject");
  fs.writeFileSync(projectFile, "{}");
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const result = discardTaskAuthorizationWithoutActiveRoute(
      workspace,
      {
        path: "Source/Demo/Foo.cpp",
        taskAuthorization: {
          taskSessionId: "invented-chat-id",
          ownerCapability: "invented-capability",
        },
      },
      { activeProject: projectFile }
    );
    assert.strictEqual(result.discarded, true);
    assert.strictEqual(result.args.path, "Source/Demo/Foo.cpp");
    assert.strictEqual(result.args.taskAuthorization, undefined);
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("requiredFields normalizes an integer plan revision from local tool callers", () => {
  assert.deepStrictEqual(
    requiredFields({ taskAuthorization: { ...authorization, planRevision: 1 } }),
    authorization
  );
});

test("successful build bridge completes task state and releases its lease", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-build-complete-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-build-complete-state-"));
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
    },
  }));
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const result = completeTaskAfterBuildViaPython(
      workspace,
      { taskAuthorization: authorization },
      {
        proofLevel: "Built",
        mutationGeneration: 4,
        buildLogPath: ".agent/logs/latest-build.log",
      }
    );
    assert.strictEqual(result.ok, true);
    assert.strictEqual(result.status, "completed");
    const state = JSON.parse(fs.readFileSync(path.join(taskDir, "state.json"), "utf8"));
    assert.strictEqual(state.status, "completed");
    assert.strictEqual(state.continuity.lease.status, "released");
    assert.strictEqual(state.completionEvidence.mutationGeneration, 4);
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
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

test("mutation authorization blocks placeholder slice plans until concrete slices are registered", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-slice-plan-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-slice-plan-state-"));
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
    ...authorization,
    ownerCapability: "owner-capability",
    status: "running",
    slicePlanningRequired: true,
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
    assert.strictEqual(result.errorCode, "SLICE_PLAN_REQUIRED");
    assert.strictEqual(result.nextAction, "unreal_task_define_slices");
    assert.strictEqual(result.nextActionArgs.taskAuthorization.taskSessionId, authorization.taskSessionId);
    assert.strictEqual(result.nextActionArgs.taskAuthorization.ownerCapability, "owner-capability");
    assert.strictEqual(result.nextActionArgs.taskAuthorization.authToken, undefined);
    assert.strictEqual(result.nextActionArgs.taskAuthorization.activeSliceId, undefined);
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

test("runtime candidate gate is supporting and does not intersect write targets", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-auth-state-"));
  const project = path.join(workspace, "Demo");
  const target = path.join(project, "Source", "Demo", "Thing.cpp");
  const other = path.join(project, "Source", "Demo", "Other.cpp");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(path.join(project, "Demo.uproject"), "{}");
  fs.writeFileSync(target, "before");
  fs.writeFileSync(other, "other");
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
    // Supporting gates must not require intersection with every mutation path.
    const unrelated = validateMutationAuth(
      workspace,
      { taskAuthorization: authorization, path: "Source/Demo/Other.cpp" },
      { requireAll: true }
    );
    assert.strictEqual(unrelated.ok, true);
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
  const { getMcpConnectionId } = require("../src/mcp-connection");
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
    mcpConnectionId: getMcpConnectionId(),
    writesAllowed: true,
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

    const plannerState = routeState(projectFile);
    plannerState.toolRoute = {
      ...plannerState.toolRoute,
      phase: "planner",
      roleSession: "planner",
      activeTools: ["unreal_code_sketch_claim_validate"],
      pendingGates: ["unreal_code_sketch_claim_validate"],
    };
    writeRouteState(stateRoot, plannerState);
    const inactiveWrite = validateMutationAuth(
      workspace,
      {
        taskAuthorization: { ...routeAuthorization, routeHash: "stale" },
        path: "Source/Demo/Foo.cpp",
      },
      { requireAll: true, toolName: "replace_in_file" }
    );
    assert.strictEqual(inactiveWrite.errorCode, "TASK_TOOL_NOT_ACTIVE");
    assert.strictEqual(inactiveWrite.nextAction, "unreal_code_sketch_claim_validate");
    assert.strictEqual(inactiveWrite.taskAuthorization.routePhase, "planner");
    assert.notStrictEqual(
      inactiveWrite.nextAction,
      "retry_same_tool_with_returned_taskAuthorization"
    );
    writeRouteState(stateRoot, state);

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
      "unreal_agent_plan"
    );
    assert.strictEqual(mismatch.nextActionIsTool, true);
    assert.strictEqual(
      Object.prototype.hasOwnProperty.call(mismatch.taskAuthorization, "authToken"),
      false
    );
    assert.strictEqual(mismatch.taskAuthorization.planRevision, "1");
    assert.deepStrictEqual(mismatch.mismatchedFields, ["authToken"]);
    assert.strictEqual(
      mismatch.authorizationContext.planRevision,
      "1"
    );
    assert.notStrictEqual(
      mismatch.nextAction,
      "retry_same_tool_with_returned_taskAuthorization"
    );

    const ownedState = routeState(projectFile, { ownerCapability: "owner-capability" });
    writeRouteState(stateRoot, ownedState);
    const wrongOwner = validateMutationAuth(
      workspace,
      {
        taskAuthorization: {
          ...routeAuthorization,
          ownerCapability: "wrong-owner-capability",
        },
        path: "Source/Demo/Foo.cpp",
      },
      { requireAll: true, toolName: "replace_in_file" }
    );
    assert.strictEqual(wrongOwner.ok, false);
    assert.strictEqual(wrongOwner.errorCode, "TASK_ROUTE_CAPABILITY_MISMATCH");
    writeRouteState(stateRoot, state);

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
    assert.strictEqual(suffixEscape.nextAction, "unreal_code_sketch_claim_validate");
    assert.strictEqual(suffixEscape.taskAuthorization.routePhase, "executor");
    assert.strictEqual(suffixEscape.taskAuthorization.routeHash, "route-1");
    assert.strictEqual(suffixEscape.maxFilesPerSlice, 2);
    assert.strictEqual(suffixEscape.nextActionArgs.targetFileLimit, undefined);
    assert.strictEqual(
      suffixEscape.nextActionArgs.taskAuthorization.routeHash,
      undefined
    );
    assert.strictEqual(
      suffixEscape.nextActionArgs.taskAuthorization.ownerCapability,
      undefined
    );
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("cancel bridge uses the host python3 fallback when PYTHON_EXE is absent", {
  skip: process.platform === "win32",
}, () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-cancel-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-cancel-state-"));
  const projectFile = path.join(workspace, "Demo.uproject");
  fs.writeFileSync(projectFile, "{}");
  const state = routeState(projectFile);
  writeRouteState(stateRoot, state);
  const previousRoot = process.env.AGENT_STATE_ROOT;
  const previousPythonExe = process.env.PYTHON_EXE;
  const previousPython = process.env.PYTHON;
  process.env.AGENT_STATE_ROOT = stateRoot;
  delete process.env.PYTHON_EXE;
  delete process.env.PYTHON;
  try {
    const cancelled = cancelActiveTask(
      workspace,
      projectFile,
      authorization.taskSessionId,
      true
    );
    assert.strictEqual(cancelled.ok, true, JSON.stringify(cancelled));
    assert.strictEqual(cancelled.status, "cancelled");
  } finally {
    if (previousRoot === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previousRoot;
    if (previousPythonExe === undefined) delete process.env.PYTHON_EXE;
    else process.env.PYTHON_EXE = previousPythonExe;
    if (previousPython === undefined) delete process.env.PYTHON;
    else process.env.PYTHON = previousPython;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("mutation checkpoint bridge persists the written file", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-checkpoint-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-checkpoint-state-"));
  const projectFile = path.join(workspace, "Demo.uproject");
  const target = path.join(workspace, "Source", "Demo", "Foo.cpp");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(projectFile, "{}");
  fs.writeFileSync(target, "// written\n");
  const state = routeState(projectFile, {
    toolRouteUsage: {
      routeHash: "route-1",
      phase: "executor",
      roleSession: "executor",
      count: 1,
      calls: ["write_file"],
    },
  });
  writeRouteState(stateRoot, state);
  const previousRoot = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const result = checkpointMutationViaPython(
      workspace,
      { taskAuthorization: { ...authorization, routeHash: "route-1", routePhase: "executor" } },
      [target],
      { requiredNextAction: "continue_active_slice" }
    );
    assert.strictEqual(result.ok, true, JSON.stringify(result));
    assert.strictEqual(result.continuity.checkpoint.status, "recorded");
    assert.ok(result.continuity.checkpoint.modifiedFiles.includes("Source/Demo/Foo.cpp"));
    assert.strictEqual(result.taskAuthorization.authToken, authorization.authToken);
    assert.strictEqual(result.taskAuthorization.routeHash, result.toolRoute.routeHash);
    assert.strictEqual(result.taskAuthorization.routePhase, result.toolRoute.phase);
    const persisted = JSON.parse(fs.readFileSync(
      path.join(stateRoot, "tasks", authorization.taskSessionId, "state.json"),
      "utf8"
    ));
    assert.strictEqual(persisted.continuity.checkpoint.status, "recorded");
  } finally {
    if (previousRoot === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previousRoot;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("successful mutation checkpoint advances scope-gate hash for the next edit", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-checkpoint-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-checkpoint-state-"));
  const projectFile = path.join(workspace, "Demo.uproject");
  const target = path.join(workspace, "Source", "Demo", "Foo.cpp");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(projectFile, "{}");
  fs.writeFileSync(target, "// before\n");
  const beforeHash = require("crypto").createHash("sha1").update("// before\n").digest("hex");
  const gate = "unreal_code_sketch_claim_validate";
  const gateSetHash = require("crypto").createHash("sha256").update(JSON.stringify({
    activeSliceId: authorization.activeSliceId,
    planId: authorization.planId,
    planRevision: authorization.planRevision,
    projectFile,
    requiredBeforeWrite: [gate],
    taskSessionId: authorization.taskSessionId,
  })).digest("hex");
  const state = routeState(projectFile, {
    requiredBeforeWrite: [gate],
    requiredGateSetHash: gateSetHash,
    completedGates: {
      [gate]: {
        status: "completed",
        gateSetHash,
        expiresAt: new Date(Date.now() + 60_000).toISOString(),
        targetSnapshots: [{
          path: "Source/Demo/Foo.cpp",
          absolutePath: target,
          exists: true,
          fileHash: beforeHash,
        }],
      },
    },
    pendingGates: [],
    selectedTargetSnapshots: [{
      path: "Source/Demo/Foo.cpp",
      exists: true,
      fileHash: beforeHash,
    }],
  });
  writeRouteState(stateRoot, state);

  // This write represents a mutation that already passed authorization and
  // committed successfully; the automatic checkpoint must advance its CAS
  // baseline so a second bounded edit can proceed without rerunning the gate.
  fs.writeFileSync(target, "// after first authorized edit\n");
  const afterHash = require("crypto")
    .createHash("sha1")
    .update("// after first authorized edit\n")
    .digest("hex");

  const previousRoot = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const result = checkpointMutationViaPython(
      workspace,
      { taskAuthorization: { ...authorization, routeHash: "route-1", routePhase: "executor" } },
      [target],
      { requiredNextAction: "continue_active_slice" }
    );
    assert.strictEqual(result.ok, true, JSON.stringify(result));
    assert.deepStrictEqual(result.advancedGateSnapshots, ["Source/Demo/Foo.cpp"]);

    const persisted = JSON.parse(fs.readFileSync(
      path.join(stateRoot, "tasks", authorization.taskSessionId, "state.json"),
      "utf8"
    ));
    assert.strictEqual(
      persisted.completedGates[gate].targetSnapshots[0].fileHash,
      afterHash
    );

    const nextAuthorization = result.taskAuthorization;
    const secondEdit = validateMutationAuth(
      workspace,
      { taskAuthorization: nextAuthorization, path: "Source/Demo/Foo.cpp" },
      { requireAll: true }
    );
    assert.strictEqual(secondEdit.ok, true, JSON.stringify(secondEdit));

    fs.writeFileSync(target, "// external change\n");
    const externalChange = validateMutationAuth(
      workspace,
      { taskAuthorization: nextAuthorization, path: "Source/Demo/Foo.cpp" },
      { requireAll: true }
    );
    assert.strictEqual(externalChange.errorCode, "GATE_TARGET_STALE");
  } finally {
    if (previousRoot === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previousRoot;
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
    assert.strictEqual(exhausted.nextAction, "unreal_task_checkpoint");
    assert.deepStrictEqual(exhausted.nextActions, [
      "unreal_task_checkpoint",
      "unreal_task_status",
      "unreal_task_cancel",
    ]);
    assert.strictEqual(exhausted.nextActionArgs.action, "record");
    assert.strictEqual(exhausted.nextActionArgs.requiredNextAction, "read_file");
    assert.strictEqual(exhausted.nextActionArgs.includeGitChanges, false);

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
      "TASK_STATE_CORRUPT"
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
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    writeRouteState(stateRoot, routeState(projectFile));
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
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
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
      "TASK_ROUTE_BLOCKED"
    );
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("route budget reservation blocks concurrent over-limit calls before commit", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-budget-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-budget-state-"));
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
    ...authorization,
    ownerCapability: "owner-capability",
    status: "running",
    writeGate: { writesAllowed: true },
    toolRoute: {
      status: "active",
      routeHash: "route-budget",
      phase: "executor",
      activeTools: ["read_file"],
      allowedPathScopes: ["Source"],
      maxToolCallsPerPhase: 2,
    },
    toolRouteUsage: { routeHash: "route-budget", count: 0, reserved: 0, reservations: [], calls: [] },
    continuity: {
      lease: {
        status: "active",
        ttlSeconds: 1800,
        heartbeatAt: new Date(Date.now() - 60_000).toISOString(),
        expiresAt: new Date(Date.now() + 90_000).toISOString(),
      },
      recovery: { conflicts: [] },
    },
  }));
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  const fields = {
    routeHash: "route-budget",
    routePhase: "executor",
  };
  try {
    const first = reserveRouteCall(workspace, authorization.taskSessionId, fields, {}, "read_file");
    const second = reserveRouteCall(workspace, authorization.taskSessionId, fields, {}, "read_file");
    assert.strictEqual(first.ok, true);
    assert.strictEqual(second.ok, true);
    assert.ok(first.reservationId);
    assert.ok(second.reservationId);
    const blocked = reserveRouteCall(
      workspace,
      authorization.taskSessionId,
      fields,
      {},
      "read_file"
    );
    assert.strictEqual(blocked.ok, false);
    assert.strictEqual(blocked.errorCode, "TASK_PHASE_TOOL_BUDGET_EXHAUSTED");
    assert.strictEqual(blocked.nextAction, "unreal_task_checkpoint");
    assert.strictEqual(blocked.nextActionArgs.action, "record");
    assert.strictEqual(blocked.nextActionArgs.requiredNextAction, "read_file");
    assert.strictEqual(blocked.nextActionArgs.includeGitChanges, false);
    assert.deepStrictEqual(blocked.nextActionArgs.taskAuthorization, {
      taskSessionId: authorization.taskSessionId,
      ownerCapability: "owner-capability",
    });
    assert.match(blocked.agentInstruction, /action=record/);
    assert.strictEqual(
      commitRouteReservation(
        workspace,
        authorization.taskSessionId,
        fields,
        {},
        "read_file",
        first.reservationId
      ).ok,
      true
    );
    assert.strictEqual(
      rollbackRouteReservation(
        workspace,
        authorization.taskSessionId,
        fields,
        {},
        "read_file",
        second.reservationId
      ).ok,
      true
    );
    assert.strictEqual(
      commitRouteReservation(
        workspace,
        authorization.taskSessionId,
        fields,
        {},
        "read_file",
        "missing-reservation"
      ).errorCode,
      "TASK_RESERVATION_NOT_FOUND"
    );
    const state = JSON.parse(
      fs.readFileSync(path.join(taskDir, "state.json"), "utf8")
    );
    assert.strictEqual(state.toolRouteUsage.count, 1);
    assert.strictEqual(state.toolRouteUsage.reserved, 0);
    assert.deepStrictEqual(state.toolRouteUsage.reservations, []);
    assert.strictEqual(state.continuity.lease.renewalReason, "route_tool_activity");
    assert.ok(Date.parse(state.continuity.lease.expiresAt) > Date.now() + 20 * 60_000);
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("planner route honors an advertised eight-call budget", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-budget-eight-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-budget-eight-state-"));
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
    ...authorization,
    status: "running",
    writeGate: { writesAllowed: true },
    toolRoute: {
      status: "active",
      routeHash: "route-budget-eight",
      phase: "planner",
      activeTools: ["read_file"],
      allowedPathScopes: ["Source"],
      maxToolCallsPerPhase: 8,
    },
    toolRouteUsage: {
      routeHash: "route-budget-eight",
      count: 0,
      reserved: 0,
      reservations: [],
      calls: [],
    },
  }));
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  const fields = { routeHash: "route-budget-eight", routePhase: "planner" };
  try {
    for (let index = 0; index < 8; index += 1) {
      const reservation = reserveRouteCall(
        workspace,
        authorization.taskSessionId,
        fields,
        {},
        "read_file"
      );
      assert.strictEqual(reservation.ok, true);
      assert.strictEqual(
        commitRouteReservation(
          workspace,
          authorization.taskSessionId,
          fields,
          {},
          "read_file",
          reservation.reservationId
        ).ok,
        true
      );
    }
    const blocked = reserveRouteCall(
      workspace,
      authorization.taskSessionId,
      fields,
      {},
      "read_file"
    );
    assert.strictEqual(blocked.ok, false);
    assert.strictEqual(blocked.errorCode, "TASK_PHASE_TOOL_BUDGET_EXHAUSTED");
    assert.match(blocked.error, /8\/8/);
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("required first tool blocks speculative reads and unlocks after commit", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-first-tool-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-first-tool-state-"));
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
    ...authorization,
    status: "running",
    writeGate: { writesAllowed: true },
    toolRoute: {
      status: "active",
      routeHash: "route-first-tool",
      phase: "planner",
      activeTools: ["build_unreal_project", "read_file"],
      allowedPathScopes: ["Source"],
      maxToolCallsPerPhase: 8,
      requiredFirstTool: "build_unreal_project",
    },
    toolRouteUsage: {
      routeHash: "route-first-tool",
      count: 0,
      reserved: 0,
      reservations: [],
      calls: [],
    },
  }));
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  const fields = { routeHash: "route-first-tool", routePhase: "planner" };
  try {
    const prematureRead = reserveRouteCall(
      workspace,
      authorization.taskSessionId,
      fields,
      {},
      "read_file"
    );
    assert.strictEqual(prematureRead.ok, false);
    assert.strictEqual(prematureRead.errorCode, "TASK_REQUIRED_FIRST_TOOL");
    assert.strictEqual(prematureRead.nextAction, "build_unreal_project");

    const build = reserveRouteCall(
      workspace,
      authorization.taskSessionId,
      fields,
      {},
      "build_unreal_project"
    );
    assert.strictEqual(build.ok, true);
    const recoveryRead = reserveRouteCall(
      workspace,
      authorization.taskSessionId,
      fields,
      {},
      "read_file"
    );
    assert.strictEqual(recoveryRead.ok, true);
    assert.strictEqual(
      rollbackRouteReservation(
        workspace,
        authorization.taskSessionId,
        fields,
        {},
        "read_file",
        recoveryRead.reservationId
      ).ok,
      true
    );
    assert.strictEqual(
      commitRouteReservation(
        workspace,
        authorization.taskSessionId,
        fields,
        {},
        "build_unreal_project",
        build.reservationId
      ).ok,
      true
    );

    const read = reserveRouteCall(
      workspace,
      authorization.taskSessionId,
      fields,
      {},
      "read_file"
    );
    assert.strictEqual(read.ok, true);
    assert.strictEqual(
      rollbackRouteReservation(
        workspace,
        authorization.taskSessionId,
        fields,
        {},
        "read_file",
        read.reservationId
      ).ok,
      true
    );
    const state = JSON.parse(fs.readFileSync(path.join(taskDir, "state.json"), "utf8"));
    assert.strictEqual(
      state.routeFacts.requiredFirstToolAttempt.planRevision,
      authorization.planRevision
    );
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("stale legacy reserved counters are cleared on next budget mutate", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-budget-stale-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-budget-stale-state-"));
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
    ...authorization,
    status: "running",
    writeGate: { writesAllowed: true },
    toolRoute: {
      status: "active",
      routeHash: "route-stale",
      phase: "executor",
      activeTools: ["read_file"],
      allowedPathScopes: ["Source"],
      maxToolCallsPerPhase: 2,
    },
    // Crash-left counter without reservation IDs.
    toolRouteUsage: { routeHash: "route-stale", count: 0, reserved: 2, calls: [] },
  }));
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const reserved = reserveRouteCall(
      workspace,
      authorization.taskSessionId,
      { routeHash: "route-stale", routePhase: "executor" },
      {},
      "read_file"
    );
    assert.strictEqual(reserved.ok, true);
    const state = JSON.parse(fs.readFileSync(path.join(taskDir, "state.json"), "utf8"));
    assert.strictEqual(state.toolRouteUsage.reserved, 1);
    assert.strictEqual(state.toolRouteUsage.reservations.length, 1);
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("conversation-scoped tasks require ownerCapability for CallTool authorize", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-cap-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-cap-state-"));
  const projectFile = path.join(workspace, "Demo.uproject");
  fs.writeFileSync(projectFile, "{}");
  const previous = process.env.AGENT_STATE_ROOT;
  const previousBridge = process.env.MCP_BRIDGE_PAIR_ID;
  const previousClient = process.env.MCP_CLIENT_INSTANCE_ID;
  process.env.AGENT_STATE_ROOT = stateRoot;
  process.env.MCP_BRIDGE_PAIR_ID = "bridge-cap-test";
  process.env.MCP_CLIENT_INSTANCE_ID = "client-cap-test";
  try {
    const { getMcpConnectionId } = require("../src/mcp-connection");
    const {
      listToolsRouteContext,
      listActiveTasks,
      cancelActiveTask,
    } = require("../src/task-auth");
    const capA = "a".repeat(64);
    const capB = "b".repeat(64);
    const taskA = routeState(projectFile, {
      taskSessionId: "task_aaaaaaa1",
      conversationId: "conv-aaaa",
      ownerCapability: capA,
      mcpConnectionId: `${getMcpConnectionId()}:conv-aaaa`,
      request: "secret-a-request",
    });
    const taskB = routeState(projectFile, {
      taskSessionId: "task_bbbbbbb2",
      conversationId: "conv-bbbb",
      ownerCapability: capB,
      mcpConnectionId: `${getMcpConnectionId()}:conv-bbbb`,
      request: "secret-b-request",
    });
    taskA.toolRoute.pendingGates = ["unreal_code_sketch_claim_validate"];
    const dirA = path.join(stateRoot, "tasks", taskA.taskSessionId);
    const dirB = path.join(stateRoot, "tasks", taskB.taskSessionId);
    fs.mkdirSync(dirA, { recursive: true });
    fs.mkdirSync(dirB, { recursive: true });
    fs.writeFileSync(path.join(dirA, "state.json"), JSON.stringify(taskA));
    fs.writeFileSync(path.join(dirB, "state.json"), JSON.stringify(taskB));

    assert.strictEqual(
      discoverActiveTaskContext(workspace, projectFile).status,
      "ambiguous_or_corrupt"
    );
    const multi = discoverActiveTaskContext(workspace, projectFile);
    assert.strictEqual(multi.errorCode, "MULTIPLE_HEALTHY_ROUTE_TASKS");
    const catalog = listToolsRouteContext(workspace, projectFile);
    assert.strictEqual(catalog.status, "ambiguous_or_corrupt");
    assert.strictEqual(catalog.errorCode, "MULTIPLE_HEALTHY_ROUTE_TASKS");
    assert.strictEqual(catalog.catalogMode, "route_union");
    assert.ok(Array.isArray(catalog.route.activeTools));
    assert.ok(catalog.route.activeTools.includes("read_file"));

    assert.strictEqual(
      authorizeActiveRouteTool(
        workspace,
        "read_file",
        { conversationId: "conv-aaaa" },
        { activeProject: projectFile }
      ).errorCode,
      "TASK_ROUTE_OWNERSHIP_REQUIRED"
    );
    const omittedCapability = authorizeActiveRouteTool(
      workspace,
      "read_file",
      { conversationId: "conv-aaaa" },
      { activeProject: projectFile }
    );
    assert.strictEqual(omittedCapability.nextAction, "read_file");
    assert.strictEqual(omittedCapability.retryable, true);
    assert.strictEqual(
      omittedCapability.requiredArgument,
      "taskAuthorization"
    );
    assert.strictEqual(omittedCapability.nextActionArgs, undefined);
    assert.ok(String(omittedCapability.agentInstruction).includes("Do not recover or cancel"));

    const wrongCap = authorizeActiveRouteTool(
      workspace,
      "read_file",
      { ownerCapability: "0".repeat(64) },
      { activeProject: projectFile, consumeBudget: false }
    );
    assert.strictEqual(wrongCap.ok, false);
    assert.strictEqual(wrongCap.errorCode, "TASK_ROUTE_CAPABILITY_MISMATCH");
    assert.ok(String(wrongCap.nextAction || "").includes("ownerCapability"));

    const allowed = authorizeActiveRouteTool(
      workspace,
      "read_file",
      { taskAuthorization: { ownerCapability: capA } },
      { activeProject: projectFile, consumeBudget: false }
    );
    assert.strictEqual(allowed.ok, true);
    assert.strictEqual(allowed.taskSessionId, taskA.taskSessionId);

    const topLevel = authorizeActiveRouteTool(
      workspace,
      "read_file",
      { ownerCapability: capA },
      { activeProject: projectFile, consumeBudget: false }
    );
    assert.strictEqual(topLevel.ok, true);
    assert.strictEqual(topLevel.taskSessionId, taskA.taskSessionId);

    const foreign = authorizeActiveRouteTool(
      workspace,
      "read_file",
      { taskAuthorization: { ownerCapability: capB } },
      { activeProject: projectFile, consumeBudget: false }
    );
    assert.strictEqual(foreign.ok, true);
    assert.strictEqual(foreign.taskSessionId, taskB.taskSessionId);

    const listedA = listActiveTasks(workspace, projectFile, { ownerCapability: capA });
    const own = listedA.tasks.find((item) => item.taskSessionId === taskA.taskSessionId);
    const other = listedA.tasks.find((item) => item.taskSessionId === taskB.taskSessionId);
    assert.strictEqual(own.connectionMatches, true);
    assert.strictEqual(own.request, "secret-a-request");
    assert.strictEqual(other.connectionMatches, false);
    assert.strictEqual(other.request, "");
    assert.strictEqual(other.mcpConnectionId, "");
    assert.strictEqual(other.conversationId, undefined);
    assert.strictEqual(listedA.nextAction, "unreal_code_sketch_claim_validate");
    assert.strictEqual(listedA.nextActionIsTool, true);
    assert.notStrictEqual(listedA.nextAction, "cancel_active_task");

    const listedTop = listActiveTasks(workspace, projectFile, {});
    assert.strictEqual(
      listedTop.nextAction,
      "active_task_requires_explicit_user_decision"
    );
    assert.strictEqual(listedTop.nextActionIsTool, false);
    // Simulate parser via top-level capability through listActiveTasks options
    // (server routeOwnershipFromArgs now maps args.ownerCapability).
    const listedTopLevel = listActiveTasks(workspace, projectFile, {
      ownerCapability: capA,
    });
    assert.ok(listedTopLevel.tasks.some((item) => item.connectionMatches === true));
    assert.ok(listedTop.tasks.every((item) => item.connectionMatches !== true));

    const deniedCancel = cancelActiveTask(
      workspace,
      projectFile,
      taskA.taskSessionId,
      false,
      {}
    );
    assert.strictEqual(deniedCancel.errorCode, "TASK_OWNED_BY_ANOTHER_CONNECTION");

    const cancelledOwn = cancelActiveTask(
      workspace,
      projectFile,
      taskA.taskSessionId,
      false,
      { ownerCapability: capA }
    );
    assert.strictEqual(cancelledOwn.ok, true);
    assert.strictEqual(cancelledOwn.userMessageKo, "작업 취소됨");

    const corruptDir = path.join(stateRoot, "tasks", "corrupt_blocker");
    fs.mkdirSync(corruptDir, { recursive: true });
    fs.writeFileSync(path.join(corruptDir, "workspace-root.txt"), workspace);
    fs.writeFileSync(path.join(corruptDir, "state.json"), "{");
    const blockedCatalog = listToolsRouteContext(workspace, projectFile);
    assert.strictEqual(blockedCatalog.errorCode, "TASK_STATE_CORRUPT");
    assert.notStrictEqual(blockedCatalog.catalogMode, "route_union");
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    if (previousBridge === undefined) delete process.env.MCP_BRIDGE_PAIR_ID;
    else process.env.MCP_BRIDGE_PAIR_ID = previousBridge;
    if (previousClient === undefined) delete process.env.MCP_CLIENT_INSTANCE_ID;
    else process.env.MCP_CLIENT_INSTANCE_ID = previousClient;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("state root unreadable fails closed instead of legacy open", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-root-workspace-"));
  const badRoot = path.join(os.tmpdir(), `task-root-file-${Date.now()}`);
  fs.writeFileSync(badRoot, "not-a-directory");
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = badRoot;
  try {
    const context = discoverActiveTaskContext(workspace);
    assert.strictEqual(context.status, "blocked");
    assert.strictEqual(context.errorCode, "TASK_STATE_ROOT_UNAVAILABLE");
    const denied = authorizeActiveRouteTool(workspace, "read_file", {}, { consumeBudget: false });
    assert.strictEqual(denied.ok, false);
    assert.strictEqual(denied.errorCode, "TASK_STATE_ROOT_UNAVAILABLE");
    assert.notStrictEqual(denied.legacy, true);
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(badRoot, { force: true });
  }
});

test("explicit ownerCapability disables legacy connection ownership", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-legacy-cap-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-legacy-cap-state-"));
  const projectFile = path.join(workspace, "Demo.uproject");
  fs.writeFileSync(projectFile, "{}");
  const previous = process.env.AGENT_STATE_ROOT;
  const previousBridge = process.env.MCP_BRIDGE_PAIR_ID;
  const previousClient = process.env.MCP_CLIENT_INSTANCE_ID;
  process.env.AGENT_STATE_ROOT = stateRoot;
  process.env.MCP_BRIDGE_PAIR_ID = "bridge-legacy-cap-node";
  process.env.MCP_CLIENT_INSTANCE_ID = "client-legacy-cap-node";
  try {
    const { getMcpConnectionId, taskConnectionMatches } = require("../src/mcp-connection");
    const cap = "c".repeat(64);
    const scoped = routeState(projectFile, {
      taskSessionId: "task_scopedcap1",
      conversationId: "conv-scoped",
      ownerCapability: cap,
      mcpConnectionId: `${getMcpConnectionId()}:conv-scoped`,
    });
    const legacy = routeState(projectFile, {
      taskSessionId: "task_legacycap1",
      mcpConnectionId: getMcpConnectionId(),
    });
    delete legacy.conversationId;
    delete legacy.ownerCapability;
    for (const state of [scoped, legacy]) {
      const dir = path.join(stateRoot, "tasks", state.taskSessionId);
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, "state.json"), JSON.stringify(state));
    }
    assert.strictEqual(taskConnectionMatches(legacy), true);
    assert.strictEqual(taskConnectionMatches(legacy, "", cap), false);
    assert.strictEqual(taskConnectionMatches(scoped, "", cap), true);

    const listed = discoverActiveTaskContext(workspace, projectFile);
    assert.strictEqual(listed.errorCode, "MULTIPLE_HEALTHY_ROUTE_TASKS");

    const allowed = authorizeActiveRouteTool(
      workspace,
      "read_file",
      { ownerCapability: cap },
      { activeProject: projectFile, consumeBudget: false }
    );
    assert.strictEqual(allowed.ok, true);
    assert.strictEqual(allowed.taskSessionId, scoped.taskSessionId);
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    if (previousBridge === undefined) delete process.env.MCP_BRIDGE_PAIR_ID;
    else process.env.MCP_BRIDGE_PAIR_ID = previousBridge;
    if (previousClient === undefined) delete process.env.MCP_CLIENT_INSTANCE_ID;
    else process.env.MCP_CLIENT_INSTANCE_ID = previousClient;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("successful direct source reads persist bounded target evidence on commit", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-source-evidence-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-source-evidence-state-"));
  const taskDir = path.join(stateRoot, "tasks", authorization.taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
    ...authorization,
    ownerCapability: "owner-capability",
    status: "running",
    planRevision: "4",
    writeGate: { writesAllowed: true },
    toolRoute: {
      status: "active",
      routeHash: "route-source-evidence",
      phase: "planner",
      activeTools: ["read_file"],
      allowedPathScopes: ["Source"],
      maxToolCallsPerPhase: 2,
    },
    toolRouteUsage: {
      routeHash: "route-source-evidence",
      count: 0,
      reserved: 0,
      reservations: [],
      calls: [],
    },
  }));
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  const fields = { routeHash: "route-source-evidence", routePhase: "planner" };
  try {
    const reserved = reserveRouteCall(
      workspace,
      authorization.taskSessionId,
      fields,
      {},
      "read_file"
    );
    const contentHash = "a".repeat(64);
    const committed = commitRouteReservation(
      workspace,
      authorization.taskSessionId,
      fields,
      {},
      "read_file",
      reserved.reservationId,
      {
        directSourceEvidence: {
          projectRelativePath: "Source/Demo/RuleEngine.cpp",
          contentHash,
          lineRange: "1-80",
        },
      }
    );
    assert.strictEqual(committed.ok, true);
    const state = JSON.parse(fs.readFileSync(path.join(taskDir, "state.json"), "utf8"));
    assert.strictEqual(state.directSourceEvidence.planRevision, "4");
    assert.deepStrictEqual(
      state.directSourceEvidence.files["source/demo/ruleengine.cpp"],
      {
        path: "Source/Demo/RuleEngine.cpp",
        contentHash,
        sourceKind: "implementation",
        lineRanges: ["1-80"],
        tools: ["read_file"],
        recordedAt: state.directSourceEvidence.files["source/demo/ruleengine.cpp"].recordedAt,
      }
    );
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("compact task ownership expands to current full route authorization", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-compact-auth-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-compact-auth-state-"));
  const projectFile = path.join(workspace, "Demo.uproject");
  fs.writeFileSync(projectFile, "{}");
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const cap = "e".repeat(64);
    const scoped = routeState(projectFile, {
      taskSessionId: "task_compact1",
      conversationId: "conv-compact",
      ownerCapability: cap,
    });
    const dir = path.join(stateRoot, "tasks", scoped.taskSessionId);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "state.json"), JSON.stringify(scoped));

    const expanded = expandCompactTaskAuthorization(
      workspace,
      "read_file",
      {
        taskAuthorization: {
          taskSessionId: scoped.taskSessionId,
          ownerCapability: cap,
        },
      },
      { activeProject: projectFile }
    );

    assert.strictEqual(expanded.ok, true);
    assert.strictEqual(expanded.expanded, true);
    assert.strictEqual(expanded.authorizationBinding, "compact_owner_capability");
    assert.strictEqual(expanded.args.taskAuthorization.authToken, scoped.authToken);
    assert.strictEqual(expanded.args.taskAuthorization.planId, scoped.planId);
    assert.strictEqual(expanded.args.taskAuthorization.routeHash, scoped.toolRoute.routeHash);

    const mismatched = expandCompactTaskAuthorization(
      workspace,
      "read_file",
      {
        taskAuthorization: {
          taskSessionId: "task_wrong_1",
          ownerCapability: cap,
        },
      },
      { activeProject: projectFile }
    );
    assert.strictEqual(mismatched.ok, false);
    assert.strictEqual(mismatched.errorCode, "TASK_ROUTE_CAPABILITY_MISMATCH");
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("single scoped active route auto-binds complete server authorization", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-auto-auth-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-auto-auth-state-"));
  const projectFile = path.join(workspace, "Demo.uproject");
  fs.writeFileSync(projectFile, "{}");
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const cap = "d".repeat(64);
    const scoped = routeState(projectFile, {
      taskSessionId: "task_autoauth1",
      conversationId: "conv-auto-auth",
      ownerCapability: cap,
    });
    const dir = path.join(stateRoot, "tasks", scoped.taskSessionId);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "state.json"), JSON.stringify(scoped));

    const resolved = authorizeActiveRouteTool(
      workspace,
      "read_file",
      {},
      { activeProject: projectFile, consumeBudget: false }
    );

    assert.strictEqual(resolved.ok, true);
    assert.strictEqual(resolved.authorizationBinding, "single_active_route");
    assert.strictEqual(resolved.taskSessionId, scoped.taskSessionId);
    assert.strictEqual(resolved.taskAuthorization.taskSessionId, scoped.taskSessionId);
    assert.strictEqual(resolved.taskAuthorization.authToken, scoped.authToken);
    assert.strictEqual(resolved.taskAuthorization.ownerCapability, cap);
    assert.strictEqual(resolved.taskAuthorization.routeHash, scoped.toolRoute.routeHash);
    assert.strictEqual(resolved.taskAuthorization.routePhase, scoped.toolRoute.phase);
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("legacy-only task + arbitrary ownerCapability fails closed", () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "task-legacy-only-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "task-legacy-only-state-"));
  const projectFile = path.join(workspace, "Demo.uproject");
  fs.writeFileSync(projectFile, "{}");
  const previous = process.env.AGENT_STATE_ROOT;
  const previousBridge = process.env.MCP_BRIDGE_PAIR_ID;
  const previousClient = process.env.MCP_CLIENT_INSTANCE_ID;
  process.env.AGENT_STATE_ROOT = stateRoot;
  process.env.MCP_BRIDGE_PAIR_ID = "bridge-legacy-only-node";
  process.env.MCP_CLIENT_INSTANCE_ID = "client-legacy-only-node";
  try {
    const { getMcpConnectionId, taskConnectionMatches } = require("../src/mcp-connection");
    const { listActiveTasks } = require("../src/task-auth");
    const legacy = routeState(projectFile, {
      taskSessionId: "task_legacyonly1",
      mcpConnectionId: getMcpConnectionId(),
    });
    delete legacy.conversationId;
    delete legacy.ownerCapability;
    const dir = path.join(stateRoot, "tasks", legacy.taskSessionId);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "state.json"), JSON.stringify(legacy));

    assert.strictEqual(taskConnectionMatches(legacy), true);
    assert.strictEqual(taskConnectionMatches(legacy, "", "a".repeat(64)), false);

    const withoutCap = authorizeActiveRouteTool(
      workspace,
      "read_file",
      {},
      { activeProject: projectFile, consumeBudget: false }
    );
    assert.strictEqual(withoutCap.ok, true);
    assert.strictEqual(withoutCap.taskSessionId, legacy.taskSessionId);

    const denied = authorizeActiveRouteTool(
      workspace,
      "read_file",
      { ownerCapability: "b".repeat(64) },
      { activeProject: projectFile, consumeBudget: false }
    );
    assert.strictEqual(denied.ok, false);
    assert.notStrictEqual(denied.legacy, true);
    assert.strictEqual(denied.errorCode, "TASK_ROUTE_CAPABILITY_MISMATCH");
    assert.ok(String(denied.nextAction || "").includes("ownerCapability"));

    const badRoot = path.join(os.tmpdir(), `task-list-root-file-${Date.now()}`);
    fs.writeFileSync(badRoot, "not-a-directory");
    const previousRoot = process.env.AGENT_STATE_ROOT;
    process.env.AGENT_STATE_ROOT = badRoot;
    try {
      const listed = listActiveTasks(workspace, projectFile);
      assert.strictEqual(listed.ok, false);
      assert.strictEqual(listed.errorCode, "TASK_STATE_ROOT_UNAVAILABLE");
      assert.strictEqual(listed.nextAction, "check_agent_state_root");
    } finally {
      process.env.AGENT_STATE_ROOT = previousRoot;
      fs.rmSync(badRoot, { force: true });
    }

    fs.rmSync(dir, { recursive: true, force: true });
    const noTasks = listActiveTasks(workspace, projectFile);
    assert.strictEqual(noTasks.count, 0);
    assert.strictEqual(noTasks.nextAction, "enable_or_call_unreal_agent_plan");
    assert.strictEqual(noTasks.nextActionIsTool, false);
    assert.strictEqual(noTasks.requiredProvider, "mcp/unreal-rag");
    assert.strictEqual(noTasks.requiredTool, "unreal_agent_plan");
    assert.strictEqual(noTasks.doNotFabricateTaskAuthorization, true);
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    if (previousBridge === undefined) delete process.env.MCP_BRIDGE_PAIR_ID;
    else process.env.MCP_BRIDGE_PAIR_ID = previousBridge;
    if (previousClient === undefined) delete process.env.MCP_CLIENT_INSTANCE_ID;
    else process.env.MCP_CLIENT_INSTANCE_ID = previousClient;
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});
