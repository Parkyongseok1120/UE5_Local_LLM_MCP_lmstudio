"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const { applyBundleTransaction } = require("../src/edit-bundle");
const {
  beginMutationJournal,
  recoverIncompleteJournals,
} = require("../src/transaction-journal");
const { sha256Text } = require("../src/safe-write");

function stateRoot() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "journal-writeahead-"));
  for (const sub of ["locks", "transactions", "tasks", "jobs", "backups"]) {
    fs.mkdirSync(path.join(root, sub), { recursive: true });
  }
  return root;
}

function setFileImage(filePath, content) {
  if (content == null) {
    fs.rmSync(filePath, { force: true });
    return;
  }
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, content, "utf8");
}

for (const fixture of [
  {
    name: "create",
    operation: "write_file",
    existedBefore: false,
    preContent: null,
    intendedPostContent: "created\n",
    deleteTarget: false,
    applyDisk: (target) => fs.writeFileSync(target, "created\n", "utf8"),
    assertRecovered: (target) => assert.strictEqual(fs.existsSync(target), false),
  },
  {
    name: "replace",
    operation: "replace_in_file",
    existedBefore: true,
    preContent: "before\n",
    intendedPostContent: "after\n",
    deleteTarget: false,
    applyDisk: (target) => fs.writeFileSync(target, "after\n", "utf8"),
    assertRecovered: (target) => assert.strictEqual(fs.readFileSync(target, "utf8"), "before\n"),
  },
  {
    name: "delete",
    operation: "delete_file",
    existedBefore: true,
    preContent: "before deletion\n",
    intendedPostContent: null,
    deleteTarget: true,
    applyDisk: (target) => fs.unlinkSync(target),
    assertRecovered: (target) => assert.strictEqual(fs.readFileSync(target, "utf8"), "before deletion\n"),
  },
]) {
  test(`single-file ${fixture.name} persists its intended post-image before disk I/O`, async () => {
    const root = stateRoot();
    process.env.AGENT_STATE_ROOT = root;
    const projectRoot = path.join(root, "Project");
    const relativePath = `Source/Demo/${fixture.name}.cpp`;
    const target = path.join(projectRoot, ...relativePath.split("/"));
    fs.mkdirSync(path.dirname(target), { recursive: true });
    if (fixture.existedBefore) fs.writeFileSync(target, fixture.preContent, "utf8");

    const journal = beginMutationJournal({
      operation: fixture.operation,
      projectRoot,
      relativePath,
      canonicalAbsolutePath: target,
      existedBefore: fixture.existedBefore,
      preContent: fixture.preContent,
      intendedPostContent: fixture.intendedPostContent,
      deleteTarget: fixture.deleteTarget,
      checkpointRequired: false,
    });
    const persisted = JSON.parse(fs.readFileSync(path.join(
      root,
      "transactions",
      `${journal.transactionId}.json`,
    ), "utf8"));
    const entry = persisted.entries[0];
    assert.strictEqual(entry.writeStarted, true);
    assert.strictEqual(entry.writeCompleted, false);
    assert.strictEqual(entry.deletedAfter, fixture.deleteTarget);
    if (!fixture.deleteTarget) {
      assert.strictEqual(entry.postHash, sha256Text(fixture.intendedPostContent));
    }

    fixture.applyDisk(target);
    const recovery = await recoverIncompleteJournals(root);
    assert.deepStrictEqual(recovery.recoveryRequired, []);
    fixture.assertRecovered(target);
    delete process.env.AGENT_STATE_ROOT;
  });
}

test("bundle write-ahead snapshots recover every repeated-patch and create crash boundary", async () => {
  const root = stateRoot();
  process.env.AGENT_STATE_ROOT = root;
  const projectRoot = path.join(root, "Project");
  const patched = path.join(projectRoot, "Source", "Demo", "Repeated.cpp");
  const created = path.join(projectRoot, "Source", "Demo", "Created.cpp");
  fs.mkdirSync(path.dirname(patched), { recursive: true });
  fs.writeFileSync(patched, "alpha\nbeta\ngamma\n", "utf8");
  const snapshots = [];

  function capture(boundary, info) {
    const persisted = JSON.parse(fs.readFileSync(path.join(
      root,
      "transactions",
      `${info.journal.transactionId}.json`,
    ), "utf8"));
    const entry = persisted.entries.find((item) => item.relativePath === info.relativePath);
    assert.ok(entry, `${boundary} must be durable before the hook runs`);
    assert.strictEqual(persisted.requiresAtomicCheckpoint, true);
    assert.strictEqual(entry.postHash, info.postHash);
    assert.ok(entry.intendedPostHashes.includes(info.postHash));
    assert.strictEqual(entry.writeCompleted, false);
    snapshots.push({
      boundary,
      operation: info.operation,
      stageIndex: info.stageIndex,
      priorHash: info.priorHash,
      postHash: info.postHash,
      persisted,
      patchedContent: fs.existsSync(patched) ? fs.readFileSync(patched, "utf8") : null,
      createdContent: fs.existsSync(created) ? fs.readFileSync(created, "utf8") : null,
    });
  }

  const tx = await applyBundleTransaction(
    {
      patches: [
        { path: "Source/Demo/Repeated.cpp", oldText: "alpha", newText: "ALPHA", expectedOccurrences: 1 },
        { path: "Source/Demo/Repeated.cpp", oldText: "gamma", newText: "GAMMA", expectedOccurrences: 1 },
      ],
      files: [
        { path: "Source/Demo/Created.cpp", content: "created\n" },
      ],
    },
    async (relativePath) => ({
      ok: true,
      absolutePath: path.join(projectRoot, ...String(relativePath).split("/")),
    }),
    {
      maxFilesPerEdit: 2,
      deferFinalization: true,
      projectRoot,
      checkpointRequired: false,
      transactionHooks: {
        afterWriteAhead: async (info) => capture("write-ahead", info),
        afterDiskWrite: async (info) => capture("disk-write", info),
      },
    },
  );
  assert.strictEqual(tx.ok, true);
  assert.strictEqual(tx.journal.requiresAtomicCheckpoint, true);
  assert.strictEqual(snapshots.length, 6);

  const secondPatchWriteAhead = snapshots.find((item) => (
    item.boundary === "write-ahead" && item.stageIndex === 2
  ));
  assert.ok(secondPatchWriteAhead);
  assert.notStrictEqual(secondPatchWriteAhead.priorHash, sha256Text("alpha\nbeta\ngamma\n"));
  const repeatedEntry = secondPatchWriteAhead.persisted.entries.find(
    (item) => item.relativePath === "Source/Demo/Repeated.cpp",
  );
  assert.ok(repeatedEntry.intendedPostHashes.includes(secondPatchWriteAhead.priorHash));

  const activeJournalPath = path.join(root, "transactions", `${tx.transactionId}.json`);
  const archivedJournalPath = path.join(root, "transactions", "archive", `${tx.transactionId}.json`);
  for (const snapshot of snapshots) {
    setFileImage(patched, snapshot.patchedContent);
    setFileImage(created, snapshot.createdContent);
    fs.rmSync(activeJournalPath, { force: true });
    fs.rmSync(archivedJournalPath, { force: true });
    fs.writeFileSync(activeJournalPath, JSON.stringify(snapshot.persisted, null, 2), "utf8");

    const recovery = await recoverIncompleteJournals(root);
    assert.deepStrictEqual(
      recovery.recoveryRequired,
      [],
      `${snapshot.boundary} stage ${snapshot.stageIndex} must not be mistaken for an external edit`,
    );
    assert.strictEqual(fs.readFileSync(patched, "utf8"), "alpha\nbeta\ngamma\n");
    assert.strictEqual(fs.existsSync(created), false);
  }
  delete process.env.AGENT_STATE_ROOT;
});

for (const fixture of [
  {
    name: "create",
    bundle: {
      patches: [],
      files: [{ path: "Source/Demo/Created.cpp", content: "created\n" }],
    },
    seed: [],
    throwAtStage: 1,
  },
  {
    name: "first patch",
    bundle: {
      patches: [
        {
          path: "Source/Demo/Patched.cpp",
          oldText: "before",
          newText: "after",
          expectedOccurrences: 1,
        },
      ],
      files: [],
    },
    seed: [["Source/Demo/Patched.cpp", "before\n"]],
    throwAtStage: 1,
  },
  {
    name: "repeated patch",
    bundle: {
      patches: [
        {
          path: "Source/Demo/Repeated.cpp",
          oldText: "alpha",
          newText: "ALPHA",
          expectedOccurrences: 1,
        },
        {
          path: "Source/Demo/Repeated.cpp",
          oldText: "gamma",
          newText: "GAMMA",
          expectedOccurrences: 1,
        },
      ],
      files: [],
    },
    seed: [["Source/Demo/Repeated.cpp", "alpha\nbeta\ngamma\n"]],
    throwAtStage: 2,
  },
]) {
  test(`bundle ${fixture.name} post-write exception restores disk before reporting rollback`, async () => {
    const root = stateRoot();
    process.env.AGENT_STATE_ROOT = root;
    const projectRoot = path.join(root, "Project");
    const expected = new Map();
    for (const [relativePath, content] of fixture.seed) {
      const target = path.join(projectRoot, ...relativePath.split("/"));
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, content, "utf8");
      expected.set(relativePath, content);
    }

    const tx = await applyBundleTransaction(
      fixture.bundle,
      async (relativePath) => ({
        ok: true,
        absolutePath: path.join(projectRoot, ...String(relativePath).split("/")),
      }),
      {
        maxFilesPerEdit: 2,
        deferFinalization: true,
        projectRoot,
        checkpointRequired: false,
        transactionHooks: {
          afterDiskWrite: async ({ stageIndex }) => {
            if (stageIndex === fixture.throwAtStage) {
              throw new Error(`fault after disk write ${stageIndex}`);
            }
          },
        },
      },
    );

    assert.strictEqual(tx.ok, false);
    assert.strictEqual(tx.rolledBack, true);
    assert.strictEqual(tx.rollbackIncomplete, false);
    for (const relativePath of bundlePathsForTest(fixture.bundle)) {
      const target = path.join(projectRoot, ...relativePath.split("/"));
      if (expected.has(relativePath)) {
        assert.strictEqual(fs.readFileSync(target, "utf8"), expected.get(relativePath));
      } else {
        assert.strictEqual(fs.existsSync(target), false);
      }
    }
    const persisted = JSON.parse(fs.readFileSync(path.join(
      root,
      "transactions",
      `${tx.transactionId}.json`,
    ), "utf8"));
    assert.strictEqual(persisted.status, "rolled_back");
    assert.ok(persisted.entries.every((entry) => entry.restored === true));
    delete process.env.AGENT_STATE_ROOT;
  });
}

function bundlePathsForTest(bundle) {
  return [...new Set([
    ...(bundle.patches || []).map((item) => item.path),
    ...(bundle.files || []).map((item) => item.path),
  ])];
}
