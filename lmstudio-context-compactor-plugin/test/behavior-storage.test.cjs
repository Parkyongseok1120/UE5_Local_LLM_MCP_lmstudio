"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { WorkingContext, hash, serialize } = require("../src/working-context.js");
const { EvidenceArchive } = require("../src/evidence-archive.js");

const scope = {
  conversation: "behavior-storage-conversation",
  lineage: "behavior-storage-conversation",
  workspace: "behavior-storage-workspace",
  repository: "behavior-storage-repository",
};

function fixture(t, options = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "compactor-behavior-storage-"));
  const contexts = [];
  t.after(() => {
    for (const context of contexts) context.archive.close();
    const resolved = path.resolve(root);
    assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
    assert.ok(path.basename(resolved).startsWith("compactor-behavior-storage-"));
    fs.rmSync(resolved, { recursive: true, force: true });
  });
  return {
    root,
    context(lineage, parentLineage = null) {
      const context = new WorkingContext(scope, { root, durable: true, ...options, lineage, parentLineage });
      contexts.push(context);
      return context;
    },
  };
}

function historyWithRead(id, file = `${id}.cpp`) {
  const history = Chat.from([{ role: "user", content: "Inspect the requested source file." }]);
  history.append(ChatMessage.from({ role: "assistant", content: [{
    type: "toolCallRequest", toolCallRequest: {
      type: "function", id, name: "read_file", arguments: { path: file },
    },
  }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{
    type: "toolCallResult", toolCallId: id,
    content: JSON.stringify({ ok: true, path: file, content: `verified source: ${id}` }),
  }] }));
  return history;
}

function capture(context, id, file) {
  const history = historyWithRead(id, file);
  assert.equal(context.captureReturned(history, () => true), 1);
  const entries = context.archive.catalog({ path: file || `${id}.cpp`, maxChars: 8192 });
  assert.equal(entries.ok, true);
  assert.equal(entries.entries.length, 1);
  return { history, ...entries.entries[0] };
}

function read(context, record) {
  return context.archive.read(record.evidenceId, record.version, 0, 8192);
}

function catalogIds(context) {
  const catalog = context.archive.catalog({ maxChars: 8192 });
  assert.equal(catalog.ok, true);
  assert.equal(catalog.hasMore, false, "the fixture must fit into one catalog page");
  return catalog.entries.map(entry => entry.evidenceId).sort();
}

function commit(context, source, candidate = source) {
  assert.equal(context.commit(source, candidate, { exact: true, remainingTokens: 4096 },
    hash(serialize(source)), "test-model"), true, context.lastCommitReason);
}

test("sibling lineages cannot discover each other's observations in the catalog", t => {
  const f = fixture(t);
  const left = f.context("left"), right = f.context("right");
  const leftRecord = capture(left, "left-read"), rightRecord = capture(right, "right-read");

  assert.deepEqual(catalogIds(left), [leftRecord.evidenceId]);
  assert.deepEqual(catalogIds(right), [rightRecord.evidenceId]);
});

test("a known evidence ID does not bypass sibling lineage access checks", t => {
  const f = fixture(t);
  const left = f.context("left"), right = f.context("right");
  const rightRecord = capture(right, "right-read");

  assert.equal(read(right, rightRecord).ok, true);
  assert.equal(read(left, rightRecord).ok, false);
});

test("restoring a parent window inherits that snapshot but excludes later parent and sibling observations", t => {
  const f = fixture(t);
  const parent = f.context("parent");
  const inherited = capture(parent, "before-window");
  commit(parent, inherited.history);

  // This result exists before the child starts, but after the chosen parent
  // window was committed. A scan of the entire parent lineage would leak it.
  const future = capture(parent, "after-window");
  const sibling = f.context("sibling", "parent");
  const siblingRecord = capture(sibling, "sibling-read");
  const child = f.context("child", "parent");
  const childSource = Chat.from(inherited.history);
  childSource.append("user", "Continue from this parent window.");

  const restored = child.restore(childSource);
  assert.equal(restored.reason, "restored_remeasure_required");
  assert.equal(read(child, inherited).ok, true);
  assert.equal(read(child, future).ok, false);
  assert.equal(read(child, siblingRecord).ok, false);
  assert.deepEqual(catalogIds(child), [inherited.evidenceId]);
  assert.equal(restored.history.getMessagesArray().at(-1).getText(), "Continue from this parent window.");
});

test("an invalid parent prefix cannot grant archive membership", t => {
  const f = fixture(t);
  const parent = f.context("parent");
  const record = capture(parent, "parent-read");
  commit(parent, record.history);
  const child = f.context("child", "parent");
  const unrelated = Chat.from([{ role: "user", content: "A different conversation prefix." }]);
  for (const message of record.history.getMessagesArray().slice(1)) unrelated.append(message);

  const restored = child.restore(unrelated);
  assert.equal(restored.reason, "source_prefix_mismatch");
  assert.deepEqual(serialize(restored.history), serialize(unrelated));
  assert.deepEqual(catalogIds(child), []);
  assert.equal(read(child, record).ok, false);
});

test("one live instance cannot reclaim another instance's unconsumed observation under quota pressure", t => {
  const f = fixture(t, { maxRecords: 1 });
  const first = f.context("first"), second = f.context("second");
  const pending = capture(first, "pending-first");
  const secondHistory = historyWithRead("pending-second");

  assert.equal(second.captureReturned(secondHistory, () => true), 0,
    "quota exhaustion must refuse the new archive record while the first consumer is pending");
  assert.equal(read(first, pending).ok, true);
  assert.equal(first.returnedRefs.size, 1);
  commit(first, pending.history);
});

test("consumed observations become reclaimable after leaving the active input", t => {
  const f = fixture(t, { maxRecords: 1 });
  const first = f.context("first"), second = f.context("second");
  const old = capture(first, "consumed-first");
  first.captureExposure(old.history, "consuming-input", true);
  first.captureExposure(Chat.empty(), "next-input", false);

  const next = capture(second, "new-second");
  assert.equal(read(second, next).ok, true);
  assert.equal(read(first, old).ok, false, "membership is not a permanent retention pin");
});

test("consumed evidence omitted from compacted input remains discoverable through its committed membership", t => {
  const f = fixture(t);
  const parent = f.context("parent");
  const record = capture(parent, "consumed-before-summary");
  parent.captureExposure(record.history, "consuming-input", true);
  const summary = Chat.from([
    { role: "user", content: "Inspect the requested source file." },
    { role: "assistant", content: "The requested source file was read." },
  ]);
  commit(parent, record.history, summary);

  const child = f.context("child", "parent");
  const restored = child.restore(record.history);
  assert.equal(restored.reason, "restored_remeasure_required");
  assert.equal(restored.history.getMessagesArray().flatMap(message => message.getToolCallResults()).length, 0);
  assert.deepEqual(catalogIds(child), [record.evidenceId]);
  const page = read(child, record);
  assert.equal(page.ok, true);
  assert.equal(page.currentFile, false);
  assert.equal(page.grantsMutation, false);
  assert.match(page.content, /verified source: consumed-before-summary/);
});

test("closing an archive releases its execution pins for another instance", t => {
  const f = fixture(t, { maxRecords: 1 });
  const first = f.context("first"), second = f.context("second");
  const old = capture(first, "closed-first");
  first.archive.close();

  const next = capture(second, "new-second");
  assert.equal(read(second, next).ok, true);
  assert.equal(read(first, old).ok, false);
});

test("a first interrupted execution preserves the original request without fabricating a result when no window can be sealed", t => {
  const f = fixture(t);
  const first = f.context("first");
  const source = Chat.from([{ role: "user", content: "Apply the requested change." }]);
  source.append(ChatMessage.from({ role: "assistant", content: [{
    type: "toolCallRequest", toolCallRequest: {
      type: "function", id: "mutation-without-result", name: "replace_in_file",
      arguments: { path: "Character.cpp", oldText: "old", newText: "new" },
    },
  }] }));
  const priorSource = serialize(source);
  const candidate = Chat.from([{ role: "user", content: "Apply the requested change." }]);

  // This is the existing no-manifest fallback contract. It does not assert
  // whether the request reached the provider or whether any mutation occurred.
  assert.equal(first.seal(source, candidate), false);
  assert.deepEqual(serialize(source), priorSource);
  const next = f.context("next", "first");
  const restored = next.restore(source);
  assert.notEqual(restored.reason, "restored_remeasure_required");
  assert.deepEqual(serialize(restored.history), priorSource);
  assert.deepEqual(restored.history.getMessagesArray().flatMap(message => message.getToolCallRequests())
    .map(request => request.id), ["mutation-without-result"]);
  assert.equal(restored.history.getMessagesArray().flatMap(message => message.getToolCallResults()).length, 0);
});

test("legacy writers and their garbage collection cannot reach current archive records", t => {
  const f = fixture(t);
  const current = f.context("current");
  const record = capture(current, "current-record");
  // Old releases stored records directly under root/hash(scope). Their GC
  // cannot understand the active execution leases used by the new contract.
  const legacyDirectory = path.join(f.root, hash(scope));
  fs.mkdirSync(legacyDirectory, { recursive: true });
  const legacyFile = path.join(legacyDirectory, `${record.evidenceId}.json`);
  fs.writeFileSync(legacyFile, JSON.stringify({ legacyFixture: true }));

  assert.notEqual(path.resolve(current.archive.directory), path.resolve(legacyDirectory));
  for (const name of fs.readdirSync(legacyDirectory).filter(name => /^ev_[a-f0-9]{64}\.json$/u.test(name))) {
    fs.unlinkSync(path.join(legacyDirectory, name));
  }
  assert.equal(read(current, record).ok, true);
  assert.deepEqual(catalogIds(current), [record.evidenceId]);
});

test("a crashed stale-lock reclaimer does not permanently disable archive writes", t => {
  const f = fixture(t);
  const context = f.context("recovery");
  context.archive.ensureDirectory(true);
  const exited = spawnSync(process.execPath, ["-e", "process.stdout.write(String(process.pid))"], { encoding: "utf8" });
  assert.equal(exited.status, 0);
  const deadPid = Number(exited.stdout);
  assert.ok(Number.isSafeInteger(deadPid) && deadPid > 0);
  assert.throws(() => process.kill(deadPid, 0), { code: "ESRCH" });
  const lock = path.join(context.archive.directory, "storage.lock");
  const reclaim = path.join(context.archive.directory, "storage.reclaim");
  fs.writeFileSync(lock, JSON.stringify({ pid: deadPid, nonce: "dead-writer" }));
  // The process can terminate after claiming reclamation but before deleting
  // the old lock. Recovery must cover this intermediate persisted state too.
  fs.writeFileSync(reclaim, "");
  try {
    assert.equal(context.captureReturned(historyWithRead("after-reclaimer-crash"), () => true), 1);
  } finally {
    for (const file of [lock, reclaim]) if (fs.existsSync(file)) fs.unlinkSync(file);
  }
});

test("terminal fallback does not retain a finished execution's pending pins forever", t => {
  let now = Date.now();
  const f = fixture(t, { maxRecords: 1, ttlMs: 50, now: () => now });
  const first = f.context("first"), second = f.context("second");
  const pending = capture(first, "returned-before-first-interruption");
  // A result arrived, but cancellation prevented its first model consumer.
  // The original visible transcript still owns that raw result at shutdown.
  first.seal(pending.history, pending.history);
  now += 100;

  assert.equal(second.captureReturned(historyWithRead("after-terminal-retention-expired"), () => true), 1);
});

test("a first runtime interruption survives seal and restore with dispatch and effect unknown", t => {
  const { RoundTransaction } = require("../dist/execution-state");
  const { retainInterruptedCalls } = require("../src/interrupted-calls.js");
  const f = fixture(t);
  const first = f.context("first");
  const source = Chat.from([{ role: "user", content: "Apply the requested change." }]);
  const request = ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id: "request-only", name: "replace_in_file",
      arguments: { path: "Character.cpp", oldText: "old", newText: "new" } } }] });
  source.append(request);
  const transaction = new RoundTransaction({ messages: [request], predictionCompleted: false,
    failure: new Error("connection lost") }, true);
  const candidate = Chat.from([{ role: "user", content: "Apply the requested change." }]);
  transaction.commitEvidence(candidate);
  const interruption = transaction.interruptionMessage([{
    callKey: "execution:0:sdk-1", causalModelInputId: "execution:input-0", providerRequestId: "request-only",
    toolName: "replace_in_file", provider: "mcp/unreal-agent", projectIdentity: "test-project",
    generationState: "finalized", validationState: "allowed", approvalState: "allowed_host_policy",
    guardAllowed: true, executionState: "unknown",
  }]);
  assert.ok(interruption);
  candidate.append(interruption);

  assert.equal(first.seal(source, candidate), true, first.lastCommitReason);
  const child = f.context("child", "first");
  const restored = child.restore(source);
  assert.equal(restored.reason, "restored_remeasure_required");
  const facts = retainInterruptedCalls(restored.history.getMessagesArray());
  assert.equal(facts.length, 1);
  assert.equal(facts[0].providerDispatch, "unknown");
  assert.equal(facts[0].effect, "unknown");
  assert.equal(facts[0].resultObservation, "not_observed");
  assert.equal(restored.history.getMessagesArray().flatMap(message => message.getToolCallResults()).length, 0);
  assert.equal(child.manifest.measurement, null);
  assert.equal(child.manifest.remeasureRequired, true);
  assert.equal(child.seal(source, restored.history), false,
    "restoring an old interruption alone must not authorize another terminal window");
  assert.equal(child.lastCommitReason, "no_committed_window_in_execution");
});

test("restore cannot report a usable window when concurrent GC removes its refs before pin registration", t => {
  const f = fixture(t, { maxRecords: 1, maxWindows: 1 });
  const parent = f.context("parent");
  const record = capture(parent, "parent-for-concurrent-restore");
  commit(parent, record.history);
  assert.equal(parent.seal(record.history, record.history), true);
  const child = f.context("child", "parent"), other = f.context("other");
  const unrelated = Chat.from([{ role: "user", content: "An independent branch." }]);
  const setPins = child.archive.setPins.bind(child.archive);
  let interleaved = false;
  child.archive.setPins = ids => {
    if (!interleaved) {
      interleaved = true;
      // Deterministically model a competing writer in the gap between reading
      // the inherited window and registering the child execution's pins.
      const accepted = other.commit(unrelated, unrelated, { exact: true, remainingTokens: 4096 },
        hash(serialize(unrelated)), "test-model");
      if (accepted) other.captureReturned(historyWithRead("competing-writer"), () => true);
    }
    return setPins(ids);
  };

  const restored = child.restore(record.history);
  if (restored.reason === "restored_remeasure_required") {
    assert.equal(read(child, record).ok, true,
      "validation and pin registration must share a storage critical section or revalidate on publication");
  } else {
    assert.deepEqual(serialize(restored.history), serialize(record.history));
  }
});

test("independent processes never enter the archive storage critical section together", async t => {
  const f = fixture(t);
  const context = f.context("observer");
  context.archive.ensureDirectory(true);
  const counter = path.join(context.archive.directory, "test-counter.json");
  fs.writeFileSync(counter, "0");
  const worker = `
    const fs = require("node:fs"), path = require("node:path");
    const { EvidenceArchive } = require(process.argv[1]);
    const archive = new EvidenceArchive(JSON.parse(process.argv[3]), { root: process.argv[2], durable: true });
    const counter = path.join(archive.directory, "test-counter.json");
    const marker = path.join(archive.directory, "test-critical-owner");
    const pause = new Int32Array(new SharedArrayBuffer(4));
    let completed = 0, refused = 0;
    for (let i = 0; i < 100; i++) {
      let entered = false;
      try {
        archive.withStorageLock(() => {
          entered = true;
          let fd;
          try { fd = fs.openSync(marker, "wx"); }
          catch (error) { throw new Error("CRITICAL_SECTION_OVERLAP:" + error.code); }
          try {
            const prior = Number(fs.readFileSync(counter, "utf8"));
            Atomics.wait(pause, 0, 0, 1);
            fs.writeFileSync(counter, String(prior + 1));
            completed++;
          } finally { fs.closeSync(fd); fs.unlinkSync(marker); }
        });
      } catch (error) {
        if (entered) throw error;
        refused++;
      }
      Atomics.wait(pause, 0, 0, 1);
    }
    process.stdout.write(JSON.stringify({ completed, refused }));
  `;
  const moduleFile = require.resolve("../src/evidence-archive.js");
  const run = () => new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["-e", worker, moduleFile, f.root, JSON.stringify(scope)],
      { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "", stderr = "";
    child.stdout.on("data", data => { stdout += data; });
    child.stderr.on("data", data => { stderr += data; });
    child.on("error", reject);
    child.on("exit", code => {
      if (code !== 0) { reject(new Error(`storage worker failed (${code}): ${stderr}`)); return; }
      try { resolve(JSON.parse(stdout)); } catch (error) { reject(error); }
    });
  });

  const settled = await Promise.allSettled([run(), run(), run(), run()]);
  for (const outcome of settled) if (outcome.status === "rejected") throw outcome.reason;
  const outcomes = settled.map(outcome => outcome.value);
  const completed = outcomes.reduce((sum, outcome) => sum + outcome.completed, 0);
  assert.ok(completed > 0);
  assert.ok(outcomes.some(outcome => outcome.refused > 0), "workers must actually contend");
  assert.equal(Number(fs.readFileSync(counter, "utf8")), completed);
  assert.equal(fs.existsSync(path.join(context.archive.directory, "test-critical-owner")), false);
});

test("temporary archive contention cannot abort exposure of already pinned raw evidence", t => {
  const f = fixture(t);
  const active = f.context("active"), contender = f.context("contender");
  const pending = capture(active, "already-pinned-before-contention");

  contender.archive.withStorageLock(() => {
    assert.doesNotThrow(() => active.captureExposure(pending.history, "next-input", false),
      "an unavailable retention update must keep the existing pin and raw input fallback");
  });
  assert.equal(read(active, pending).ok, true);
});

test("a successful terminal window does not leak its lease when another writer contends during close", t => {
  let now = Date.now();
  const f = fixture(t, { maxRecords: 1, ttlMs: 50, now: () => now });
  const first = f.context("first"), second = f.context("second");
  const record = capture(first, "terminal-window-record");
  commit(first, record.history);
  const close = first.archive.close.bind(first.archive);
  first.archive.close = () => second.archive.withStorageLock(close);
  try {
    assert.equal(first.seal(record.history, record.history), true, first.lastCommitReason);
  } finally {
    first.archive.close = close;
  }
  now += 100;

  assert.equal(second.captureReturned(historyWithRead("after-closed-window-expires"), () => true), 1);
});

test("cross-scope cleanup cannot delete a record that a reactivated owner has pinned", t => {
  const now = Date.now();
  const f = fixture(t, { ttlMs: 1000, now: () => now });
  const previous = f.context("resumed");
  const record = capture(previous, "reactivated-before-scope-unlink");
  previous.archive.close();
  const resumed = f.context("resumed");
  const target = path.join(previous.archive.directory, `${record.evidenceId}.json`);
  const old = new Date(now - 2000);
  fs.utimesSync(target, old, old);
  fs.utimesSync(previous.archive.directory, old, old);
  const unlink = fs.unlinkSync;
  let attempted = false, reactivated = false;
  fs.unlinkSync = (file, ...args) => {
    if (String(file) === target && !attempted) {
      attempted = true;
      try {
        resumed.archive.setPins([record.evidenceId]);
        reactivated = true;
      } catch (error) {
        if (error.message !== "archive_lock_unavailable") throw error;
      }
    }
    return unlink(file, ...args);
  };
  try {
    new EvidenceArchive({ ...scope, conversation: "scope-cleaner", lineage: "scope-cleaner" },
      { root: f.root, durable: true, ttlMs: 1000, now: () => now });
  } finally {
    fs.unlinkSync = unlink;
  }

  if (reactivated) {
    assert.equal(fs.existsSync(target), true,
      "a pre-activation directory listing must not authorize deletion after an owner publishes its pin");
    assert.equal(read(resumed, record).ok, true);
  } else {
    assert.equal(resumed.archive.pins.size, 0, "a competing activation may be refused before it publishes pins");
  }
});

test("failed restore rolls back membership and leaves the previous in-memory window unpublished", t => {
  const f = fixture(t);
  const parent = f.context("parent");
  const record = capture(parent, "rollback-parent-record");
  commit(parent, record.history);
  const child = f.context("child", "parent");
  const originalSetPins = child.archive.setPins.bind(child.archive);
  child.archive.setPins = () => { throw new Error("retention-publication-failed"); };
  let restored;
  try { restored = child.restore(record.history); }
  finally { child.archive.setPins = originalSetPins; }

  assert.notEqual(restored.reason, "restored_remeasure_required");
  assert.deepEqual(serialize(restored.history), serialize(record.history));
  assert.equal(child.manifest, null);
  assert.equal(child.generation, 0);
  assert.equal(child.refs.size, 0);
  assert.equal(child.returnedRefs.size, 0);
  assert.deepEqual(catalogIds(child), []);
  assert.equal(read(child, record).ok, false);
});

test("restoring and recapturing historical results alone cannot authorize a new terminal window", t => {
  const f = fixture(t);
  const parent = f.context("parent");
  const record = capture(parent, "historical-only-result");
  commit(parent, record.history);
  const child = f.context("child", "parent");
  const restored = child.restore(record.history);
  assert.equal(restored.reason, "restored_remeasure_required");
  child.captureReturned(restored.history, () => true);

  assert.equal(child.seal(record.history, restored.history), false);
  assert.equal(child.lastCommitReason, "no_committed_window_in_execution");
  assert.equal(fs.existsSync(path.join(child.archive.directory, child.windowFile("child"))), false);
});
