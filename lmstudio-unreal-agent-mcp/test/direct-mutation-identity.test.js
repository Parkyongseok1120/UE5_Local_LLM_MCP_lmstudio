"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { createDirectRuntime } = require("../src/direct-server");
const { sha256Text } = require("../src/safe-write");
const { releasePathLock, tryAcquirePathLock } = require("../src/write-locks");

function payloadOf(result) {
  assert.ok(result?.structuredContent);
  return result.structuredContent;
}

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "direct-mutation-identity-"));
  const projectRoot = path.join(root, "Project");
  const projectPath = path.join(projectRoot, "Project.uproject");
  const stateRoot = path.join(root, "state");
  fs.mkdirSync(path.join(projectRoot, "Source"), { recursive: true });
  fs.writeFileSync(projectPath, '{"FileVersion":3}\n', "utf8");
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return { projectPath, projectRoot, root, stateRoot };
}

function runtimeFor(value, getActiveProject = () => value.projectPath) {
  return createDirectRuntime({
    workspaceRoot: value.root,
    stateRoot: value.stateRoot,
    configPath: path.join(value.root, "agent-mcp.json"),
    env: {
      AGENT_STATE_ROOT: value.stateRoot,
      ALLOW_SOURCE_DELETE: "1",
      ALLOW_WRITE: "1",
    },
    getActiveProject,
    validateMutationSemanticText: () => ({ ok: true, hits: [] }),
  });
}

function directoryLinkSupported(t, root) {
  const target = path.join(root, "LinkProbeTarget");
  const link = path.join(root, "LinkProbe");
  fs.mkdirSync(target);
  try {
    fs.symlinkSync(target, link, process.platform === "win32" ? "junction" : "dir");
  } catch (error) {
    if (["EACCES", "EPERM", "ENOTSUP"].includes(error.code)) {
      t.skip(`symlink/junction creation is unavailable: ${error.code}`);
      fs.rmdirSync(target);
      return false;
    }
    throw error;
  }
  fs.unlinkSync(link);
  fs.rmdirSync(target);
  return true;
}

function snapshotTree(root) {
  if (!fs.existsSync(root)) return [];
  const entries = [];
  function walk(current, relative) {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const child = path.join(current, entry.name);
      const childRelative = path.join(relative, entry.name).replace(/\\/gu, "/");
      if (entry.isDirectory()) {
        entries.push([childRelative, "directory"]);
        walk(child, childRelative);
      } else if (entry.isSymbolicLink()) {
        entries.push([childRelative, "link", fs.readlinkSync(child)]);
      } else {
        entries.push([childRelative, "file", fs.readFileSync(child).toString("base64")]);
      }
    }
  }
  walk(root, "");
  return entries;
}

function swapDirectoryForJunction(sourceDir, movedDir, alternateDir) {
  fs.renameSync(sourceDir, movedDir);
  fs.symlinkSync(
    alternateDir,
    sourceDir,
    process.platform === "win32" ? "junction" : "dir",
  );
}

function restoreSwappedDirectory(sourceDir, movedDir) {
  fs.unlinkSync(sourceDir);
  fs.renameSync(movedDir, sourceDir);
}

function assertLockReleasedAndReacquirable(target, stateRoot) {
  const lockDir = path.join(stateRoot, "locks");
  const lockFiles = fs.existsSync(lockDir)
    ? fs.readdirSync(lockDir).filter((name) => name.endsWith(".lock"))
    : [];
  assert.deepEqual(lockFiles, []);
  const acquired = tryAcquirePathLock(target, "identity-regression", { stateRoot });
  assert.equal(acquired.ok, true);
  releasePathLock(acquired);
  assert.deepEqual(
    fs.readdirSync(lockDir).filter((name) => name.endsWith(".lock")),
    [],
  );
}

test("replace_in_file freezes the initial real identity before locked revalidation", async (t) => {
  const value = fixture(t);
  if (!directoryLinkSupported(t, value.root)) return;
  const sourceDir = path.join(value.projectRoot, "Source", "Swap");
  const movedDir = path.join(value.projectRoot, "Source", "Swap.original");
  const alternateDir = path.join(value.projectRoot, "Source", "Alternate");
  const target = path.join(sourceDir, "Victim.cpp");
  const originalTarget = path.join(movedDir, "Victim.cpp");
  const alternateTarget = path.join(alternateDir, "Victim.cpp");
  const before = "int Value = 0;\n";
  fs.mkdirSync(sourceDir, { recursive: true });
  fs.mkdirSync(alternateDir, { recursive: true });
  fs.writeFileSync(target, before, "utf8");
  fs.writeFileSync(alternateTarget, before, "utf8");

  let armed = false;
  let armedCalls = 0;
  const runtime = runtimeFor(value, () => {
    if (armed && ++armedCalls === 2) {
      swapDirectoryForJunction(sourceDir, movedDir, alternateDir);
    }
    return value.projectPath;
  });
  armed = true;
  const result = payloadOf(await runtime.callTool("replace_in_file", {
    path: "project://Source/Swap/Victim.cpp",
    oldText: "int Value = 0;",
    newText: "int Value = 1;",
    expectedOccurrences: 1,
    expectedHash: sha256Text(before),
  }));

  assert.equal(armedCalls, 2);
  assert.equal(result.ok, false);
  assert.equal(result.errorCode, "WRITE_TARGET_BLOCKED");
  assert.match(result.message, /real target or containment root changed/u);
  assert.equal(fs.readFileSync(originalTarget, "utf8"), before);
  assert.equal(fs.readFileSync(alternateTarget, "utf8"), before);
  restoreSwappedDirectory(sourceDir, movedDir);
  assertLockReleasedAndReacquirable(target, value.stateRoot);
});

test("write_file freezes a missing target identity before locked directory creation", async (t) => {
  const value = fixture(t);
  if (!directoryLinkSupported(t, value.root)) return;
  const sourceDir = path.join(value.projectRoot, "Source", "WriteSwap");
  const movedDir = path.join(value.projectRoot, "Source", "WriteSwap.original");
  const alternateDir = path.join(value.projectRoot, "Source", "WriteAlternate");
  const originalTarget = path.join(movedDir, "NewFile.cpp");
  const alternateTarget = path.join(alternateDir, "NewFile.cpp");
  fs.mkdirSync(sourceDir, { recursive: true });
  fs.mkdirSync(alternateDir, { recursive: true });

  let armedCalls = 0;
  const runtime = runtimeFor(value, () => {
    if (++armedCalls === 2) {
      swapDirectoryForJunction(sourceDir, movedDir, alternateDir);
    }
    return value.projectPath;
  });
  const result = payloadOf(await runtime.callTool("write_file", {
    path: "project://Source/WriteSwap/NewFile.cpp",
    content: "void NewFile() {}\n",
    createDirs: true,
  }));

  assert.equal(armedCalls, 2);
  assert.equal(result.ok, false);
  assert.equal(result.errorCode, "WRITE_TARGET_BLOCKED");
  assert.match(result.message, /real target or containment root changed/u);
  assert.equal(fs.existsSync(originalTarget), false);
  assert.equal(fs.existsSync(alternateTarget), false);
  restoreSwappedDirectory(sourceDir, movedDir);
  assertLockReleasedAndReacquirable(path.join(sourceDir, "NewFile.cpp"), value.stateRoot);
});

test("delete_file freezes the approved target identity before its locked rename", async (t) => {
  const value = fixture(t);
  if (!directoryLinkSupported(t, value.root)) return;
  const sourceDir = path.join(value.projectRoot, "Source", "DeleteSwap");
  const movedDir = path.join(value.projectRoot, "Source", "DeleteSwap.original");
  const alternateDir = path.join(value.projectRoot, "Source", "DeleteAlternate");
  const target = path.join(sourceDir, "Victim.cpp");
  const originalTarget = path.join(movedDir, "Victim.cpp");
  const alternateTarget = path.join(alternateDir, "Victim.cpp");
  const before = "void Victim() {}\n";
  fs.mkdirSync(sourceDir, { recursive: true });
  fs.mkdirSync(alternateDir, { recursive: true });
  fs.writeFileSync(target, before, "utf8");
  fs.writeFileSync(alternateTarget, before, "utf8");

  let armed = false;
  let armedCalls = 0;
  const runtime = runtimeFor(value, () => {
    if (armed && ++armedCalls === 2) {
      swapDirectoryForJunction(sourceDir, movedDir, alternateDir);
    }
    return value.projectPath;
  });
  const details = {
    completedEditsSummary: "Remove the obsolete victim implementation.",
    reason: "The replacement is already compiled.",
    ifNotDeleted: "A duplicate implementation remains.",
    ifDeleted: "Only the replacement remains.",
  };
  const proposal = payloadOf(await runtime.callTool("propose_file_deletions", {
    completedEditsSummary: details.completedEditsSummary,
    files: [{
      path: "project://Source/DeleteSwap/Victim.cpp",
      reason: details.reason,
      ifNotDeleted: details.ifNotDeleted,
      ifDeleted: details.ifDeleted,
    }],
  }));
  assert.equal(proposal.ok, true);

  armed = true;
  const result = payloadOf(await runtime.callTool("delete_file", {
    path: "project://Source/DeleteSwap/Victim.cpp",
    approvalToken: proposal.proposals[0].approvalToken,
    userApproved: true,
    fileVersionReceipt: proposal.proposals[0].fileVersionReceipt,
    ...details,
  }));

  assert.equal(armedCalls, 2);
  assert.equal(result.ok, false);
  assert.equal(result.errorCode, "DELETE_TARGET_BLOCKED");
  assert.match(result.message, /real target or containment root changed/u);
  assert.equal(fs.readFileSync(originalTarget, "utf8"), before);
  assert.equal(fs.readFileSync(alternateTarget, "utf8"), before);
  assert.equal(fs.existsSync(path.join(value.projectRoot, ".agent-trash")), false);
  restoreSwappedDirectory(sourceDir, movedDir);
  assertLockReleasedAndReacquirable(target, value.stateRoot);
});

test("delete_file rejects an existing trash junction before creating outside directories", async (t) => {
  const value = fixture(t);
  if (!directoryLinkSupported(t, value.root)) return;
  const target = path.join(value.projectRoot, "Source", "Victim.cpp");
  const outside = path.join(value.root, "OutsideTrash");
  const trashLink = path.join(value.projectRoot, ".agent-trash");
  const before = "void Victim() {}\n";
  fs.writeFileSync(target, before, "utf8");
  fs.mkdirSync(outside);
  fs.writeFileSync(path.join(outside, "sentinel.txt"), "outside must remain byte-identical\n", "utf8");
  const runtime = runtimeFor(value);
  const details = {
    completedEditsSummary: "Remove the obsolete victim implementation.",
    reason: "The replacement is already compiled.",
    ifNotDeleted: "A duplicate implementation remains.",
    ifDeleted: "Only the replacement remains.",
  };
  const proposal = payloadOf(await runtime.callTool("propose_file_deletions", {
    completedEditsSummary: details.completedEditsSummary,
    files: [{
      path: "project://Source/Victim.cpp",
      reason: details.reason,
      ifNotDeleted: details.ifNotDeleted,
      ifDeleted: details.ifDeleted,
    }],
  }));
  assert.equal(proposal.ok, true);
  fs.symlinkSync(outside, trashLink, process.platform === "win32" ? "junction" : "dir");
  const outsideBefore = snapshotTree(outside);

  const result = payloadOf(await runtime.callTool("delete_file", {
    path: "project://Source/Victim.cpp",
    approvalToken: proposal.proposals[0].approvalToken,
    userApproved: true,
    fileVersionReceipt: proposal.proposals[0].fileVersionReceipt,
    ...details,
  }));

  assert.equal(result.ok, false);
  assert.equal(result.errorCode, "DELETE_TARGET_BLOCKED");
  assert.match(result.message, /trash ancestor escapes/u);
  assert.equal(fs.readFileSync(target, "utf8"), before);
  assert.deepEqual(snapshotTree(outside), outsideBefore);
});
