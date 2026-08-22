"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { createDirectRuntime } = require("../src/direct-server.js");
const { FileSnapshotRegistry } = require("../src/file-snapshot-registry.js");

function payloadOf(result) {
  assert.ok(result?.structuredContent);
  return result.structuredContent;
}

function makeProject(root, folder, projectName = folder) {
  const projectRoot = path.join(root, folder);
  const projectFile = path.join(projectRoot, `${projectName}.uproject`);
  const configDir = path.join(projectRoot, "Config");
  fs.mkdirSync(configDir, { recursive: true });
  fs.writeFileSync(projectFile, JSON.stringify({
    FileVersion: 3,
    EngineAssociation: "5.4",
    Modules: [{ Name: projectName, Type: "Runtime" }],
  }), "utf8");
  return { projectRoot, projectFile, configDir };
}

function fixture(t, options = {}) {
  const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), "file-snapshot-flow-"));
  const first = makeProject(workspaceRoot, "FirstProject");
  let activeProject = first.projectFile;
  const stateRoot = path.join(workspaceRoot, "state");
  const runtime = createDirectRuntime({
    workspaceRoot,
    configPath: path.join(workspaceRoot, "agent-mcp.json"),
    stateRoot,
    env: {
      AGENT_STATE_ROOT: stateRoot,
      ALLOW_WRITE: "1",
      ALLOW_SOURCE_DELETE: options.allowDelete ? "1" : "0",
      ALLOW_COMMANDS: "0",
      ALLOW_UNREAL_BUILD: "0",
    },
    getActiveProject: () => activeProject,
    setActiveProject: async (_workspace, _config, args) => {
      activeProject = args.projectPath ? path.resolve(args.projectPath) : null;
      return { ok: true, activeProject };
    },
    ...(options.fileSnapshots ? { fileSnapshots: options.fileSnapshots } : {}),
  });
  t.after(() => fs.rmSync(workspaceRoot, { recursive: true, force: true }));
  return {
    runtime,
    workspaceRoot,
    first,
    setActiveProject(value) { activeProject = value; },
  };
}

function writeConfig(project, name, value) {
  const file = path.join(project.configDir, name);
  fs.writeFileSync(file, `[Snapshot]\nValue=${value}\n`, "utf8");
  return file;
}

test("snapshot scenario 1: same-session mutation still requires explicit version evidence", async (t) => {
  const { runtime, first } = fixture(t);
  const target = writeConfig(first, "One.ini", "alpha");
  const owner = { sessionId: "conversation-a" };
  const read = payloadOf(await runtime.callTool("read_file", {
    path: "project://Config/One.ini",
  }, owner));
  assert.match(read.fileVersionReceipt, /^fvr1_[A-Za-z0-9_-]+$/u);

  const missing = payloadOf(await runtime.callTool("replace_in_file", {
    path: "project://Config/One.ini",
    oldText: "Value=alpha",
    newText: "Value=beta",
    expectedOccurrences: 1,
  }, owner));
  assert.equal(missing.ok, false);
  assert.equal(missing.errorCode, "FILE_SNAPSHOT_REQUIRED");
  assert.match(fs.readFileSync(target, "utf8"), /Value=alpha/u);

  const patch = payloadOf(await runtime.callTool("replace_in_file", {
    path: "project://Config/One.ini",
    oldText: "Value=alpha",
    newText: "Value=beta",
    expectedOccurrences: 1,
    fileVersionReceipt: read.fileVersionReceipt,
  }, owner));
  assert.equal(patch.ok, true);
  assert.equal(patch.hashSource, "file_version_receipt");
  assert.match(patch.fileVersionReceipt, /^fvr1_/u);
  assert.match(fs.readFileSync(target, "utf8"), /Value=beta/u);
});

test("snapshot scenario 2: range read receipt supports a hashless stdio-style patch", async (t) => {
  const { runtime, first } = fixture(t);
  const target = writeConfig(first, "Range.ini", "one");
  const read = payloadOf(await runtime.callTool("read_file_range", {
    path: "project://Config/Range.ini",
    startLine: 1,
    endLine: 2,
  }));
  const patch = payloadOf(await runtime.callTool("replace_in_file", {
    path: "project://Config/Range.ini",
    oldText: "Value=one",
    newText: "Value=two",
    expectedOccurrences: 1,
    fileVersionReceipt: read.fileVersionReceipt,
  }));
  assert.equal(patch.ok, true);
  assert.equal(patch.hashSource, "file_version_receipt");
  assert.match(fs.readFileSync(target, "utf8"), /Value=two/u);
});

test("snapshot scenario 3: malformed hash never falls back to same-session state", async (t) => {
  const { runtime, first } = fixture(t);
  const target = writeConfig(first, "Malformed.ini", "before");
  const owner = { sessionId: "conversation-malformed" };
  const read = payloadOf(await runtime.callTool("read_file", {
    path: "project://Config/Malformed.ini",
  }, owner));
  const rejected = payloadOf(await runtime.callTool("replace_in_file", {
    path: "project://Config/Malformed.ini",
    oldText: "Value=before",
    newText: "Value=after",
    expectedOccurrences: 1,
    expectedHash: "55-character-model-transcription",
  }, owner));
  assert.equal(rejected.ok, false);
  assert.equal(rejected.errorCode, "FILE_SNAPSHOT_REQUIRED");
  assert.match(fs.readFileSync(target, "utf8"), /Value=before/u);

  const accepted = payloadOf(await runtime.callTool("replace_in_file", {
    path: "project://Config/Malformed.ini",
    oldText: "Value=before",
    newText: "Value=after",
    expectedOccurrences: 1,
    fileVersionReceipt: read.fileVersionReceipt,
  }, owner));
  assert.equal(accepted.ok, true);
  assert.equal(accepted.hashSource, "file_version_receipt");
  assert.match(fs.readFileSync(target, "utf8"), /Value=after/u);
});

test("snapshot scenario 4: external modification produces FILE_VERSION_CONFLICT", async (t) => {
  const { runtime, first } = fixture(t);
  const target = writeConfig(first, "Conflict.ini", "read");
  const owner = { sessionId: "conversation-conflict" };
  const read = payloadOf(await runtime.callTool("read_file", {
    path: "project://Config/Conflict.ini",
  }, owner));
  fs.writeFileSync(target, "[Snapshot]\nValue=external\n", "utf8");
  const conflict = payloadOf(await runtime.callTool("replace_in_file", {
    path: "project://Config/Conflict.ini",
    oldText: "Value=read",
    newText: "Value=patched",
    expectedOccurrences: 1,
    fileVersionReceipt: read.fileVersionReceipt,
  }, owner));
  assert.equal(conflict.ok, false);
  assert.equal(conflict.errorCode, "FILE_VERSION_CONFLICT");
  assert.match(fs.readFileSync(target, "utf8"), /Value=external/u);
});

test("snapshot scenario 5: two consecutive edits need no intervening read", async (t) => {
  const { runtime, first } = fixture(t);
  const target = writeConfig(first, "Consecutive.ini", "zero");
  const owner = { sessionId: "conversation-consecutive" };
  const read = payloadOf(await runtime.callTool("read_file", {
    path: "project://Config/Consecutive.ini",
  }, owner));
  const firstPatch = payloadOf(await runtime.callTool("replace_in_file", {
    path: "project://Config/Consecutive.ini",
    oldText: "Value=zero",
    newText: "Value=one",
    expectedOccurrences: 1,
    fileVersionReceipt: read.fileVersionReceipt,
  }, owner));
  const staleReuse = payloadOf(await runtime.callTool("replace_in_file", {
    path: "project://Config/Consecutive.ini",
    oldText: "Value=one",
    newText: "Value=unsafe",
    expectedOccurrences: 1,
    fileVersionReceipt: read.fileVersionReceipt,
  }, owner));
  const secondPatch = payloadOf(await runtime.callTool("replace_in_file", {
    path: "project://Config/Consecutive.ini",
    oldText: "Value=one",
    newText: "Value=two",
    expectedOccurrences: 1,
    fileVersionReceipt: firstPatch.fileVersionReceipt,
  }, owner));
  assert.equal(firstPatch.ok, true);
  assert.equal(staleReuse.ok, false);
  assert.equal(staleReuse.errorCode, "FILE_VERSION_CONFLICT");
  assert.equal(secondPatch.ok, true);
  assert.equal(firstPatch.hashSource, "file_version_receipt");
  assert.equal(secondPatch.hashSource, "file_version_receipt");
  assert.notEqual(firstPatch.fileVersionReceipt, read.fileVersionReceipt);
  assert.notEqual(secondPatch.fileVersionReceipt, firstPatch.fileVersionReceipt);
  assert.match(fs.readFileSync(target, "utf8"), /Value=two/u);
});

test("snapshot scenario 6: explicit receipts are conversation-isolated", async (t) => {
  const { runtime, first } = fixture(t);
  const target = writeConfig(first, "Conversation.ini", "alpha");
  const read = payloadOf(await runtime.callTool("read_file", {
    path: "project://Config/Conversation.ini",
  }, { sessionId: "conversation-owner" }));
  const rejected = payloadOf(await runtime.callTool("replace_in_file", {
    path: "project://Config/Conversation.ini",
    oldText: "Value=alpha",
    newText: "Value=other",
    expectedOccurrences: 1,
    fileVersionReceipt: read.fileVersionReceipt,
  }, { sessionId: "conversation-other" }));
  assert.equal(rejected.errorCode, "FILE_SNAPSHOT_SCOPE_MISMATCH");
  assert.match(fs.readFileSync(target, "utf8"), /Value=alpha/u);
});

test("snapshot scenario 7: same-name clone projects have distinct identities", async (t) => {
  const { runtime, workspaceRoot } = fixture(t);
  const left = makeProject(workspaceRoot, "CloneLeft", "Twin");
  const right = makeProject(workspaceRoot, "CloneRight", "Twin");
  writeConfig(left, "Twin.ini", "left");
  const rightTarget = writeConfig(right, "Twin.ini", "right");
  const owner = { sessionId: "clone-conversation" };
  const leftRead = payloadOf(await runtime.callTool("read_file", {
    project: left.projectFile,
    path: "project://Config/Twin.ini",
  }, owner));
  const rejected = payloadOf(await runtime.callTool("replace_in_file", {
    project: right.projectFile,
    path: "project://Config/Twin.ini",
    oldText: "Value=right",
    newText: "Value=wrong",
    expectedOccurrences: 1,
    fileVersionReceipt: leftRead.fileVersionReceipt,
  }, owner));
  assert.equal(rejected.errorCode, "FILE_SNAPSHOT_SCOPE_MISMATCH");
  assert.match(fs.readFileSync(rightTarget, "utf8"), /Value=right/u);
});

test("snapshot scenario 8: one stale file aborts an edit bundle before any write", async (t) => {
  const { runtime, first } = fixture(t);
  const firstTarget = writeConfig(first, "BundleA.ini", "a0");
  const secondTarget = writeConfig(first, "BundleB.ini", "b0");
  const owner = { sessionId: "bundle-conversation" };
  const firstRead = payloadOf(await runtime.callTool("read_file", {
    path: "project://Config/BundleA.ini",
  }, owner));
  const secondRead = payloadOf(await runtime.callTool("read_file", {
    path: "project://Config/BundleB.ini",
  }, owner));
  fs.writeFileSync(secondTarget, "[Snapshot]\nValue=external\n", "utf8");
  const result = payloadOf(await runtime.callTool("apply_edit_bundle", {
    patches: [
      {
        path: "project://Config/BundleA.ini",
        oldText: "Value=a0",
        newText: "Value=a1",
        expectedOccurrences: 1,
        fileVersionReceipt: firstRead.fileVersionReceipt,
      },
      {
        path: "project://Config/BundleB.ini",
        oldText: "Value=b0",
        newText: "Value=b1",
        expectedOccurrences: 1,
        fileVersionReceipt: secondRead.fileVersionReceipt,
      }
    ]
  }, owner));
  assert.equal(result.ok, false);
  assert.equal(result.errorCode, "FILE_VERSION_CONFLICT");
  assert.match(fs.readFileSync(firstTarget, "utf8"), /Value=a0/u);
  assert.match(fs.readFileSync(secondTarget, "utf8"), /Value=external/u);
});

test("snapshot scenario 9: active-project switch cannot reuse the previous project snapshot", async (t) => {
  const { runtime, workspaceRoot, first } = fixture(t);
  const second = makeProject(workspaceRoot, "SecondProject");
  writeConfig(first, "Switch.ini", "first");
  const secondTarget = writeConfig(second, "Switch.ini", "second");
  const owner = { sessionId: "switch-conversation" };
  const read = payloadOf(await runtime.callTool("read_file", {
    path: "project://Config/Switch.ini",
  }, owner));
  const switched = payloadOf(await runtime.callTool("set_active_project", {
    projectPath: second.projectFile,
  }, owner));
  assert.equal(switched.ok, true);
  const rejected = payloadOf(await runtime.callTool("replace_in_file", {
    path: "project://Config/Switch.ini",
    oldText: "Value=second",
    newText: "Value=wrong",
    expectedOccurrences: 1,
    fileVersionReceipt: read.fileVersionReceipt,
  }, owner));
  assert.equal(rejected.errorCode, "FILE_SNAPSHOT_SCOPE_MISMATCH");
  assert.match(fs.readFileSync(secondTarget, "utf8"), /Value=second/u);
});

test("snapshot scenario 10: registry applies bounded LRU eviction and TTL expiry", () => {
  let now = 1_000;
  let nonce = 0;
  const registry = new FileSnapshotRegistry({
    maxEntries: 2,
    ttlMs: 1_000,
    now: () => now,
    randomBytes: () => Buffer.alloc(16, ++nonce),
  });
  const stat = { dev: 1, ino: 1, size: 1, mtimeMs: 1 };
  const register = (name) => registry.register({
    projectPath: path.resolve("Project.uproject"),
    filePath: path.resolve(name),
    hash: String(nonce + 1).padStart(64, "a").slice(-64),
    stat,
    requestContext: { sessionId: "bounded" },
  });
  const first = register("A.ini");
  register("B.ini");
  register("C.ini");
  assert.equal(registry.stats().entries, 2);
  assert.equal(registry.resolve({
    projectPath: path.resolve("Project.uproject"),
    filePath: path.resolve("A.ini"),
    fileVersionReceipt: first.fileVersionReceipt,
    requestContext: { sessionId: "bounded" },
  }).errorCode, "FILE_SNAPSHOT_INVALID");
  now += 1_001;
  assert.equal(registry.stats().entries, 0);
});

test("recoverable delete accepts the proposal's opaque snapshot receipt", async (t) => {
  const { runtime, first } = fixture(t, { allowDelete: true });
  const target = path.join(first.projectRoot, "Source", "FirstProject", "Delete.cpp");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, "void DeleteMe() {}\n", "utf8");
  const common = {
    completedEditsSummary: "Replacement implementation is complete.",
    reason: "obsolete source",
    ifNotDeleted: "duplicate implementation remains",
    ifDeleted: "only the replacement remains",
  };
  const proposal = payloadOf(await runtime.callTool("propose_file_deletions", {
    completedEditsSummary: common.completedEditsSummary,
    files: [{
      path: "project://Source/FirstProject/Delete.cpp",
      reason: common.reason,
      ifNotDeleted: common.ifNotDeleted,
      ifDeleted: common.ifDeleted,
    }],
  }));
  const deleted = payloadOf(await runtime.callTool("delete_file", {
    path: "project://Source/FirstProject/Delete.cpp",
    approvalToken: proposal.proposals[0].approvalToken,
    fileVersionReceipt: proposal.proposals[0].fileVersionReceipt,
    userApproved: true,
    ...common,
  }));
  assert.equal(deleted.ok, true);
  assert.equal(deleted.operation, "moved_to_trash");
  assert.equal(fs.existsSync(target), false);
  assert.equal(fs.existsSync(deleted.restorePath), true);
});
