"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { atomicWriteText } = require("../src/atomic-io");
const { createDirectRuntime } = require("../src/direct-server");
const { createStrictRuntime } = require("../src/strict-server");
const { applyDirectEditBundle, validateBundleLimits } = require("../src/direct-edit-bundle");
const { sha256Text } = require("../src/safe-write");
const {
  createRuntimeTransaction,
  runtimeTransactionPaths,
  saveRuntimeTransaction,
  transactionBackupPath,
  transactionFilePath,
  updateRuntimeTransactionEntry,
} = require("../src/direct-transaction-store");

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "direct-transaction-owner-"));
  const stateRoot = path.join(root, "state");
  const projectRoot = path.join(root, "Project");
  const projectPath = path.join(projectRoot, "Project.uproject");
  fs.mkdirSync(path.join(projectRoot, "Config"), { recursive: true });
  fs.writeFileSync(projectPath, "{}", "utf8");
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return { root, stateRoot, projectRoot, projectPath };
}

function createInterruptedTransaction(value, owner, fileName, before, after) {
  const target = path.join(value.projectRoot, "Config", fileName);
  fs.writeFileSync(target, before, "utf8");
  const journal = createRuntimeTransaction({
    runtimeOwner: owner,
    stateRoot: value.stateRoot,
    projectRoot: value.projectRoot,
    projectPath: value.projectPath,
  });
  const relativePath = `Config/${fileName}`;
  const backup = transactionBackupPath(
    value.stateRoot,
    owner,
    journal.transactionId,
    relativePath,
  );
  atomicWriteText(backup, before, "utf8");
  updateRuntimeTransactionEntry(journal, {
    relativePath,
    canonicalAbsolutePath: target,
    operation: "patch",
    existedBefore: true,
    preHash: sha256Text(before),
    preContentBackupPath: backup,
    postHash: sha256Text(after),
    intendedPostHashes: [sha256Text(after)],
    writeStarted: true,
    writeCompleted: false,
    restored: false,
    rollbackSkippedReason: "",
  }, value.stateRoot);
  journal.status = "committing";
  saveRuntimeTransaction(journal, value.stateRoot);
  fs.writeFileSync(target, after, "utf8");
  return { journal, target };
}

function payloadOf(result) {
  assert.deepEqual(JSON.parse(result.content[0].text), result.structuredContent);
  return result.structuredContent;
}

test("Direct startup recovery ignores byte-identical legacy and Strict journals", async (t) => {
  const value = fixture(t);
  const direct = createInterruptedTransaction(value, "direct", "Direct.ini", "direct-before", "direct-after");
  const strict = createInterruptedTransaction(value, "strict", "Strict.ini", "strict-before", "strict-after");

  const legacyTarget = path.join(value.projectRoot, "Config", "Legacy.ini");
  fs.writeFileSync(legacyTarget, "legacy-after", "utf8");
  const legacyDir = path.join(value.stateRoot, "transactions");
  const legacyFile = path.join(legacyDir, "legacy-awaiting-build.json");
  fs.mkdirSync(legacyDir, { recursive: true });
  const legacyBytes = Buffer.from(JSON.stringify({
    transactionId: "legacy-awaiting-build",
    status: "awaiting_build",
    taskSessionId: "foreign-task",
    requiresAtomicCheckpoint: true,
    entries: [{ canonicalAbsolutePath: legacyTarget, postHash: sha256Text("legacy-after") }],
  }, null, 2));
  fs.writeFileSync(legacyFile, legacyBytes);

  const strictJournalPath = transactionFilePath(
    value.stateRoot,
    "strict",
    strict.journal.transactionId,
  );
  const strictBytes = fs.readFileSync(strictJournalPath);
  const strictInDirectFile = path.join(
    runtimeTransactionPaths(value.stateRoot, "direct").pending,
    "strict-in-direct.json",
  );
  const strictInDirectBytes = Buffer.from(`${JSON.stringify({
    ...strict.journal,
    transactionId: "strict-in-direct",
  }, null, 2)}\n`);
  fs.writeFileSync(strictInDirectFile, strictInDirectBytes);
  const runtime = createDirectRuntime({
    runtimeOwner: "direct",
    stateRoot: value.stateRoot,
    workspaceRoot: value.root,
    configPath: path.join(value.root, "agent-mcp.json"),
    env: { AGENT_STATE_ROOT: value.stateRoot },
    getActiveProject: () => value.projectPath,
  });
  const report = await runtime.recoverTransactions();

  assert.equal(runtime.runtimeOwner, "direct");
  assert.equal(report.runtimeOwner, "direct");
  assert.equal(report.scanned, 2);
  assert.equal(report.recovered.length, 1);
  assert.equal(report.recoveryRequired.length, 1);
  assert.match(report.recoveryRequired[0].error, /owner mismatch/u);
  assert.equal(fs.readFileSync(direct.target, "utf8"), "direct-before");
  assert.equal(fs.readFileSync(strict.target, "utf8"), "strict-after");
  assert.equal(fs.readFileSync(legacyTarget, "utf8"), "legacy-after");
  assert.deepEqual(fs.readFileSync(legacyFile), legacyBytes);
  assert.deepEqual(fs.readFileSync(strictJournalPath), strictBytes);
  assert.deepEqual(fs.readFileSync(strictInDirectFile), strictInDirectBytes);
  assert.equal(fs.existsSync(transactionFilePath(
    value.stateRoot,
    "direct",
    direct.journal.transactionId,
  )), false);
  assert.equal(fs.existsSync(path.join(
    runtimeTransactionPaths(value.stateRoot, "direct").archive,
    `${direct.journal.transactionId}.json`,
  )), true);
});

test("Direct transaction schema and bundle API reject legacy workflow metadata", async (t) => {
  const value = fixture(t);
  const journal = createRuntimeTransaction({
    runtimeOwner: "direct",
    stateRoot: value.stateRoot,
    projectRoot: value.projectRoot,
    projectPath: value.projectPath,
  });
  for (const [field, payload] of [
    ["taskSessionId", "foreign"],
    ["requiresAtomicCheckpoint", true],
    ["checkpointRequired", true],
    ["mutationGeneration", 7],
    ["deferFinalization", true],
  ]) {
    assert.throws(
      () => saveRuntimeTransaction({ ...journal, [field]: payload }, value.stateRoot),
      new RegExp(`unsupported field: ${field}`, "u"),
    );
    assert.throws(
      () => createRuntimeTransaction({
        runtimeOwner: "direct",
        stateRoot: value.stateRoot,
        projectRoot: value.projectRoot,
        projectPath: value.projectPath,
        [field]: payload,
      }),
      new RegExp(`unsupported field: ${field}`, "u"),
    );
    await assert.rejects(
      () => applyDirectEditBundle(
        { files: [{ path: "Config/NeverWritten.ini", content: "x" }], patches: [] },
        async () => ({
          ok: true,
          absolutePath: path.join(value.projectRoot, "Config", "NeverWritten.ini"),
        }),
        {
          runtimeOwner: "direct",
          stateRoot: value.stateRoot,
          projectRoot: value.projectRoot,
          projectPath: value.projectPath,
          [field]: payload,
        },
      ),
      new RegExp(`unsupported option: ${field}`, "u"),
    );
  }
  assert.throws(
    () => saveRuntimeTransaction({ ...journal, status: "awaiting_build" }, value.stateRoot),
    /Unsupported transaction status/u,
  );
  assert.throws(
    () => validateBundleLimits({
      files: [{ path: "Config/New.ini", content: "x" }],
      patches: [],
      checkpointRequired: true,
    }),
    /unsupported field: checkpointRequired/u,
  );

  const runtime = createDirectRuntime({
    stateRoot: value.stateRoot,
    workspaceRoot: value.root,
    configPath: path.join(value.root, "agent-mcp.json"),
    env: { AGENT_STATE_ROOT: value.stateRoot, ALLOW_WRITE: "1" },
    getActiveProject: () => value.projectPath,
  });
  for (const field of [
    "taskSessionId",
    "requiresAtomicCheckpoint",
    "checkpointRequired",
    "mutationGeneration",
    "deferFinalization",
  ]) {
    const rejected = payloadOf(await runtime.callTool("apply_edit_bundle", {
      files: [{ path: "project://Config/NeverWritten.ini", content: "x" }],
      [field]: true,
    }));
    assert.equal(rejected.errorCode, "INVALID_ARGUMENT");
    assert.match(rejected.message, new RegExp(field, "u"));
  }
  assert.equal(fs.existsSync(path.join(value.stateRoot, "direct-transactions")), true);
  assert.equal(fs.readdirSync(runtimeTransactionPaths(value.stateRoot, "direct").pending).length, 1);
  assert.equal(fs.existsSync(path.join(value.projectRoot, "Config", "NeverWritten.ini")), false);
});

test("Direct edit bundle commits in only the Direct owner store", async (t) => {
  const value = fixture(t);
  const first = path.join(value.projectRoot, "Config", "First.ini");
  const second = path.join(value.projectRoot, "Config", "Second.ini");
  fs.writeFileSync(first, "First=before\n", "utf8");
  fs.writeFileSync(second, "Second=before\n", "utf8");
  const runtime = createDirectRuntime({
    stateRoot: value.stateRoot,
    workspaceRoot: value.root,
    configPath: path.join(value.root, "agent-mcp.json"),
    env: { AGENT_STATE_ROOT: value.stateRoot, ALLOW_WRITE: "1" },
    getActiveProject: () => value.projectPath,
  });
  const result = await runtime.callTool("apply_edit_bundle", {
    patches: [
      {
        path: "project://Config/First.ini",
        oldText: "First=before",
        newText: "First=after",
        expectedOccurrences: 1,
        expectedHash: sha256Text("First=before\n"),
      },
      {
        path: "project://Config/Second.ini",
        oldText: "Second=before",
        newText: "Second=after",
        expectedOccurrences: 1,
        expectedHash: sha256Text("Second=before\n"),
      },
    ],
  });
  const payload = payloadOf(result);
  assert.equal(result.isError, false);
  assert.equal(payload.ok, true);
  assert.equal(fs.readFileSync(first, "utf8"), "First=after\n");
  assert.equal(fs.readFileSync(second, "utf8"), "Second=after\n");
  const paths = runtimeTransactionPaths(value.stateRoot, "direct");
  assert.equal(fs.readdirSync(paths.pending).length, 0);
  const archived = fs.readdirSync(paths.archive);
  assert.equal(archived.length, 1);
  const journal = JSON.parse(fs.readFileSync(path.join(paths.archive, archived[0]), "utf8"));
  assert.equal(journal.runtimeOwner, "direct");
  assert.equal(journal.status, "completed");
  assert.equal(fs.existsSync(path.join(value.stateRoot, "transactions")), false);
  assert.equal(fs.existsSync(path.join(value.stateRoot, "strict-transactions")), false);
});

test("Strict edit bundle is stamped and persisted only as Strict-owned", async (t) => {
  const value = fixture(t);
  const target = path.join(value.projectRoot, "Config", "StrictBundle.ini");
  fs.writeFileSync(target, "Value=before\n", "utf8");
  const runtime = createStrictRuntime({
    workspaceRoot: value.root,
    configPath: path.join(value.root, "agent-mcp.json"),
    env: { AGENT_STATE_ROOT: value.stateRoot, ALLOW_WRITE: "1" },
    getActiveProject: () => value.projectPath,
  });
  assert.equal(runtime.stateRoot, path.resolve(value.stateRoot));
  const session = payloadOf(await runtime.callTool("strict_begin", {
    conversationId: "strict-owner-test",
    objective: "Apply one atomic configuration edit",
  })).strictSession;
  const result = await runtime.callTool("apply_edit_bundle", {
    strictSessionId: session.id,
    conversationId: session.conversationId,
    patches: [{
      path: "project://Config/StrictBundle.ini",
      oldText: "Value=before",
      newText: "Value=after",
      expectedOccurrences: 1,
      expectedHash: sha256Text("Value=before\n"),
    }],
  });
  const payload = payloadOf(result);
  assert.equal(result.isError, false);
  assert.equal(payload.executionMode, "strict");
  assert.equal(fs.readFileSync(target, "utf8"), "Value=after\n");
  const strictPaths = runtimeTransactionPaths(value.stateRoot, "strict");
  assert.equal(fs.readdirSync(strictPaths.archive).length, 1);
  const archived = path.join(strictPaths.archive, fs.readdirSync(strictPaths.archive)[0]);
  assert.equal(JSON.parse(fs.readFileSync(archived, "utf8")).runtimeOwner, "strict");
  assert.equal(fs.existsSync(path.join(value.stateRoot, "direct-transactions")), false);
  assert.equal(fs.existsSync(path.join(value.stateRoot, "transactions")), false);
});

test("Direct edit bundle fault rolls every owned post-image back", async (t) => {
  const value = fixture(t);
  const first = path.join(value.projectRoot, "Config", "CrashOne.ini");
  const second = path.join(value.projectRoot, "Config", "CrashTwo.ini");
  fs.writeFileSync(first, "One=before\n", "utf8");
  fs.writeFileSync(second, "Two=before\n", "utf8");
  const runtime = createDirectRuntime({
    stateRoot: value.stateRoot,
    workspaceRoot: value.root,
    configPath: path.join(value.root, "agent-mcp.json"),
    env: { AGENT_STATE_ROOT: value.stateRoot, ALLOW_WRITE: "1" },
    getActiveProject: () => value.projectPath,
    transactionHooks: {
      afterDiskWrite: async ({ stageIndex }) => {
        if (stageIndex === 2) throw new Error("fault after second disk write");
      },
    },
  });
  const result = await runtime.callTool("apply_edit_bundle", {
    patches: [
      {
        path: "project://Config/CrashOne.ini",
        oldText: "One=before",
        newText: "One=after",
        expectedOccurrences: 1,
        expectedHash: sha256Text("One=before\n"),
      },
      {
        path: "project://Config/CrashTwo.ini",
        oldText: "Two=before",
        newText: "Two=after",
        expectedOccurrences: 1,
        expectedHash: sha256Text("Two=before\n"),
      },
    ],
  });
  const payload = payloadOf(result);
  assert.equal(result.isError, true);
  assert.equal(payload.errorCode, "BUNDLE_FAILED");
  assert.equal(fs.readFileSync(first, "utf8"), "One=before\n");
  assert.equal(fs.readFileSync(second, "utf8"), "Two=before\n");
  const paths = runtimeTransactionPaths(value.stateRoot, "direct");
  assert.equal(fs.readdirSync(paths.pending).length, 0);
  const archived = path.join(paths.archive, fs.readdirSync(paths.archive)[0]);
  assert.equal(JSON.parse(fs.readFileSync(archived, "utf8")).status, "rolled_back");
});

test("Direct bundle CAS failure never overwrites a concurrent external change", async (t) => {
  const value = fixture(t);
  const target = path.join(value.projectRoot, "Config", "Concurrent.ini");
  const before = "Value=before\n";
  const external = "Value=external\n";
  fs.writeFileSync(target, before, "utf8");
  const runtime = createDirectRuntime({
    stateRoot: value.stateRoot,
    workspaceRoot: value.root,
    configPath: path.join(value.root, "agent-mcp.json"),
    env: { AGENT_STATE_ROOT: value.stateRoot, ALLOW_WRITE: "1" },
    getActiveProject: () => value.projectPath,
    transactionHooks: {
      afterWriteAhead: async () => {
        fs.writeFileSync(target, external, "utf8");
      },
    },
  });
  const result = await runtime.callTool("apply_edit_bundle", {
    patches: [{
      path: "project://Config/Concurrent.ini",
      oldText: "Value=before",
      newText: "Value=direct",
      expectedOccurrences: 1,
      expectedHash: sha256Text(before),
    }],
  });
  const payload = payloadOf(result);
  assert.equal(result.isError, true);
  assert.equal(payload.errorCode, "ROLLBACK_INCOMPLETE");
  assert.equal(payload.retry.allowed, false);
  assert.equal(fs.readFileSync(target, "utf8"), external);
  assert.deepEqual(
    payload.rollback.externalChangeDetected,
    ["project://Config/Concurrent.ini"],
  );
  const paths = runtimeTransactionPaths(value.stateRoot, "direct");
  assert.equal(fs.readdirSync(paths.archive).length, 0);
  const pending = fs.readdirSync(paths.pending);
  assert.equal(pending.length, 1);
  const pendingPath = path.join(paths.pending, pending[0]);
  const journal = JSON.parse(fs.readFileSync(pendingPath, "utf8"));
  assert.equal(journal.status, "rollback_incomplete");
  assert.equal(journal.entries[0].rollbackSkippedReason, "external_change_detected");

  // A later ABA-equal image is not proof that Direct still owns the file.
  // Automatic recovery must leave both it and the unresolved journal alone.
  fs.writeFileSync(target, "Value=direct\n", "utf8");
  const journalBytes = fs.readFileSync(pendingPath);
  const recovery = await runtime.recoverTransactions();
  assert.equal(recovery.scanned, 1);
  assert.equal(recovery.recovered.length, 0);
  assert.equal(recovery.recoveryRequired.length, 1);
  assert.equal(fs.readFileSync(target, "utf8"), "Value=direct\n");
  assert.deepEqual(fs.readFileSync(pendingPath), journalBytes);
});

test("Direct deletion still requires exact approval and moves source to recoverable trash", async (t) => {
  const value = fixture(t);
  const target = path.join(value.projectRoot, "Source", "Project", "Obsolete.cpp");
  const content = "void Obsolete() {}\n";
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, content, "utf8");
  const runtime = createDirectRuntime({
    stateRoot: value.stateRoot,
    workspaceRoot: value.root,
    configPath: path.join(value.root, "agent-mcp.json"),
    env: {
      AGENT_STATE_ROOT: value.stateRoot,
      ALLOW_WRITE: "1",
      ALLOW_SOURCE_DELETE: "1",
    },
    getActiveProject: () => value.projectPath,
  });
  const details = {
    completedEditsSummary: "Remove the obsolete isolated source",
    reason: "No references remain",
    ifNotDeleted: "Dead source stays in the target",
    ifDeleted: "The dead translation unit is removed",
  };
  const unapproved = payloadOf(await runtime.callTool("delete_file", {
    path: "project://Source/Project/Obsolete.cpp",
    approvalToken: "none",
    userApproved: false,
    expectedHash: sha256Text(content),
    ...details,
  }));
  assert.equal(unapproved.errorCode, "USER_APPROVAL_REQUIRED");

  const proposal = payloadOf(await runtime.callTool("propose_file_deletions", {
    completedEditsSummary: details.completedEditsSummary,
    files: [{
      path: "project://Source/Project/Obsolete.cpp",
      reason: details.reason,
      ifNotDeleted: details.ifNotDeleted,
      ifDeleted: details.ifDeleted,
    }],
  }));
  assert.equal(proposal.deletesNothing, true);
  assert.equal(fs.existsSync(target), true);
  const deleted = payloadOf(await runtime.callTool("delete_file", {
    path: "project://Source/Project/Obsolete.cpp",
    approvalToken: proposal.proposals[0].approvalToken,
    userApproved: true,
    expectedHash: sha256Text(content),
    ...details,
  }));
  assert.equal(deleted.ok, true);
  assert.equal(deleted.operation, "moved_to_trash");
  assert.equal(deleted.recoverable, true);
  assert.equal(fs.existsSync(target), false);
  assert.equal(fs.readFileSync(deleted.restorePath, "utf8"), content);
});

test("Direct and Strict source closures exclude every legacy mutation workflow owner", () => {
  const forbidden = new Set([
    "transaction-journal.js",
    "edit-bundle.js",
    "mutation-generation.js",
    "validate-write.js",
    "validation-dirty.js",
    "state-root.js",
  ]);
  for (const entry of ["direct-server.js", "strict-server.js"]) {
    const target = path.resolve(__dirname, "../src", entry);
    const script = `
      const Module = require("module");
      const loaded = [];
      const original = Module._load;
      Module._load = function(request, parent, isMain) {
        try {
          const resolved = Module._resolveFilename(request, parent, isMain);
          if (typeof resolved === "string") loaded.push(resolved.replace(/\\\\/g, "/"));
        } catch {}
        return original.apply(this, arguments);
      };
      require(${JSON.stringify(target)});
      process.stdout.write(JSON.stringify(loaded));
    `;
    const child = spawnSync(process.execPath, ["-e", script], {
      cwd: path.dirname(target),
      encoding: "utf8",
    });
    assert.equal(child.status, 0, child.stderr);
    const loaded = JSON.parse(child.stdout).filter((file) => file.includes("/src/"));
    const violations = loaded.filter((file) => forbidden.has(path.posix.basename(file)));
    assert.deepEqual(violations, [], `${entry} loaded ${violations.join(", ")}`);
  }
});

test("Direct mutation and recovery responsibilities stay bounded and acyclic", () => {
  const sourceRoot = path.resolve(__dirname, "../src");
  const files = [
    "direct-mutation-capabilities.js",
    "direct-mutation-limits.js",
    "direct-mutation-scope.js",
    "direct-file-mutation-capabilities.js",
    "direct-bundle-capability.js",
    "direct-delete-capabilities.js",
    "direct-edit-bundle.js",
    "direct-edit-bundle-plan.js",
    "direct-edit-bundle-commit.js",
    "direct-transaction-store.js",
    "direct-transaction-recovery.js",
    "direct-static-validation.js",
    "mutation-semantic-guard.js",
    "python-executable.js",
    "runtime-state-root.js",
    "strict-session-domain.js",
    "strict-session-store.js",
    "strict-project-binding.js",
    "strict-lifecycle.js",
    "strict-server.js",
  ];
  const tighterLimits = {
    "strict-session-domain.js": 90,
    "strict-session-store.js": 140,
    "strict-project-binding.js": 90,
    "strict-lifecycle.js": 330,
    "strict-server.js": 230,
  };
  const graph = new Map();
  for (const file of files) {
    const source = fs.readFileSync(path.join(sourceRoot, file), "utf8");
    const lines = source.split(/\r?\n/u).length;
    const limit = tighterLimits[file] || 360;
    assert.ok(lines <= limit, `${file} is ${lines} lines (limit ${limit})`);
    const dependencies = [...source.matchAll(/require\("\.\/([^"]+)"\)/gu)]
      .map((match) => match[1].endsWith(".js") ? match[1] : `${match[1]}.js`)
      .filter((dependency) => files.includes(dependency));
    graph.set(file, dependencies);
  }
  const visiting = new Set();
  const visited = new Set();
  const visit = (file, pathToFile = []) => {
    if (visiting.has(file)) assert.fail(`import cycle: ${[...pathToFile, file].join(" -> ")}`);
    if (visited.has(file)) return;
    visiting.add(file);
    for (const dependency of graph.get(file) || []) visit(dependency, [...pathToFile, file]);
    visiting.delete(file);
    visited.add(file);
  };
  for (const file of files) visit(file);
});
