"use strict";

const test = require("node:test");
const assert = require("node:assert");

const {
  stableMutationCallFingerprint,
  boundedRecoveryRead,
  exactMutationCallGuard,
  bundleFailureRecovery,
} = require("../src/mutation-recovery.js");

test("mutation call fingerprints are stable across auth rotation and object key order", () => {
  const first = stableMutationCallFingerprint("replace_in_file", {
    path: "Source\\Demo\\Thing.cpp",
    oldText: "before",
    newText: "after",
    expectedOccurrences: 1,
    taskAuthorization: { taskSessionId: "task-one", routeHash: "old" },
  });
  const second = stableMutationCallFingerprint("replace_in_file", {
    expectedOccurrences: 1,
    newText: "after",
    oldText: "before",
    path: "Source/Demo/Thing.cpp",
    taskAuthorization: { taskSessionId: "task-one", routeHash: "new" },
  });
  const changed = stableMutationCallFingerprint("replace_in_file", {
    path: "Source/Demo/Thing.cpp",
    oldText: "before",
    newText: "different",
    expectedOccurrences: 1,
  });
  assert.strictEqual(first, second);
  assert.notStrictEqual(first, changed);
  assert.match(first, /^[a-f0-9]{64}$/);
});

test("bundle fingerprints normalize nested paths and ignore nested transport authorization", () => {
  const first = stableMutationCallFingerprint("apply_edit_bundle", {
    projectRoot: "D:\\Portable\\Project",
    bundle: {
      patches: [{
        path: "Plugins\\Portable\\Source\\Portable\\Thing.cpp",
        oldText: "before",
        newText: "after",
        taskAuthorization: { taskSessionId: "rotating-one" },
      }],
    },
  });
  const second = stableMutationCallFingerprint("apply_edit_bundle", {
    projectRoot: "D:/Portable/Project",
    bundle: {
      patches: [{
        newText: "after",
        oldText: "before",
        path: "Plugins/Portable/Source/Portable/Thing.cpp",
        taskAuthorization: { taskSessionId: "rotating-two" },
      }],
    },
  });
  assert.strictEqual(first, second);
});

test("bounded recovery read anchors around the nearest partial source match", () => {
  const lines = Array.from({ length: 240 }, (_, index) => `line ${index + 1}`);
  lines[176] = "void UDemoComponent::RefreshInventory()";
  lines[177] = "{";
  lines[178] = "    CurrentCount = NewCount;";
  const read = boundedRecoveryRead(
    "project://Source/Demo/DemoComponent.cpp",
    lines.join("\n"),
    "void UDemoComponent::RefreshInventory()\n{\n    OldCount = NewCount;\n}"
  );
  assert.strictEqual(read.path, "project://Source/Demo/DemoComponent.cpp");
  assert.ok(read.startLine <= 177);
  assert.ok(read.endLine >= 179);
  assert.ok(read.endLine - read.startLine + 1 <= 80);
  assert.strictEqual(read.detailLevel, "compact");
});

test("exact mutation guard forbids only the failed call fingerprint", () => {
  const guard = exactMutationCallGuard("replace_in_file", {
    path: "Source/Demo/A.cpp",
    oldText: "a",
    newText: "b",
  });
  assert.deepStrictEqual(guard.forbiddenCallFingerprints, [guard.failedCallFingerprint]);
  assert.deepStrictEqual(guard.forbiddenCalls, [{
    tool: "replace_in_file",
    fingerprint: guard.failedCallFingerprint,
  }]);
  assert.strictEqual(Object.hasOwn(guard, "doNotRetry"), false);
});

test("bundle static rollback requires repair planning, not a terminal blocker", () => {
  const recovery = bundleFailureRecovery({
    ok: false,
    error: "static validation failed",
    rolledBack: true,
    rollbackIncomplete: false,
  }, {
    patches: [{ path: "Source/Demo/A.cpp" }],
  });
  assert.strictEqual(recovery.errorCode, "BUNDLE_STATIC_VALIDATION_FAILED");
  assert.strictEqual(recovery.status, "repair_planning_required");
  assert.strictEqual(recovery.requiredTool.name, "unreal_code_sketch_claim_validate");
  assert.deepStrictEqual(recovery.requiredTool.args.targetFiles, ["Source/Demo/A.cpp"]);
});

test("bundle incomplete rollback requires an exact checkpoint rebase", () => {
  const recovery = bundleFailureRecovery({
    ok: false,
    error: "rollback failed",
    rollback: {
      rolledBack: false,
      rollbackIncomplete: true,
      unrestoredPaths: ["Source/Demo/A.cpp"],
      externalChangeDetected: ["Source/Demo/A.cpp"],
    },
  }, {
    patches: [{ path: "Source\\Demo\\A.cpp" }],
  });
  assert.strictEqual(recovery.errorCode, "BUNDLE_EXTERNAL_CHANGE_DETECTED");
  assert.strictEqual(recovery.status, "checkpoint_rebase_required");
  assert.deepStrictEqual(recovery.requiredTool, {
    name: "unreal_task_checkpoint",
    args: {
      action: "rebase",
      acceptCurrentFiles: true,
      includeGitChanges: false,
    },
  });
  assert.deepStrictEqual(recovery.targetFiles, ["Source/Demo/A.cpp"]);
});

test("bundle lock contention never requests checkpoint rebase when no write occurred", () => {
  const recovery = bundleFailureRecovery({
    ok: false,
    lockFailure: true,
    rolledBack: false,
    rollbackIncomplete: false,
  }, {
    patches: [{ path: "Plugins/Portable/Source/Portable/A.cpp" }],
  });
  assert.strictEqual(recovery.errorCode, "BUNDLE_PATH_LOCKED");
  assert.strictEqual(recovery.status, "repair_planning_required");
  assert.strictEqual(recovery.requiredTool.name, "unreal_code_sketch_claim_validate");
  assert.strictEqual(recovery.rolledBack, true);
});
