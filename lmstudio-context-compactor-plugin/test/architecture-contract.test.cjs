"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), os = require("node:os"), path = require("node:path");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { exchangeIndex, WorkingContext, hash } = require("../dist/working-context");
const { completedRequestFingerprints } = require("../dist/evidence-manager");
const { emptyReferenceState, ingestReferenceObservations, referenceSnapshot } = require("../dist/reference-context");
const { autoGuidanceCandidates } = require("../dist/design-guidance");
const memory = require("../src/compaction-tool-memory");
const notes = require("../src/continuity-model-notes");
const { fileObservation, coalesceFileObservations } = require("../src/continuity-file-observations");
const { EvidenceArchive, atomicWrite } = require("../src/evidence-archive");
const project = "C:/Projects/Game/Game.uproject";
const scope = { engine: "unreal", source: "config", projectIdentity: project };
const request = (id, name = "read_file") => ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
  toolCallRequest: { type: "function", id, name, arguments: { project, path: "Source/A.cpp" } } }] });
const result = (id, payload) => ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id, content: JSON.stringify(payload) }] });
const chat = messages => { const h = Chat.empty(); messages.forEach(m => h.append(m)); return h; };
const normalize = messages => messages.map((m, index) => ({ index, role: m.getRole(), text: m.getText(),
  toolRequests: m.getToolCallRequests(), toolResults: m.getToolCallResults() }));
const file = extra => ({ ok: true, canonicalProject: project, path: "project://Source/A.cpp", operation: "read",
  sha256: "a".repeat(64), startLine: 1, endLine: 1, totalLines: 1, text: "source", ...extra });

test("A02 split and reverse results share one pairing for retry, reference and factual memory", () => {
  for (const reverse of [false, true]) {
    const builds = [result("a", { ok: true, project: { projectPath: project, target: "Editor", platform: "Win64", configuration: "Dev" }, diagnostics: ["A"] }),
      result("b", { ok: true, project: { projectPath: project, target: "Game", platform: "Win64", configuration: "Dev" }, diagnostics: ["B"] })];
    const messages = [request("a", "build_unreal_project"), request("b", "build_unreal_project"), ...(reverse ? builds.reverse() : builds)];
    assert.equal(exchangeIndex(chat(messages)).matches.size, 2);
    assert.equal(exchangeIndex(chat(messages)).ambiguous, false);
    // Distinct arguments make both successful reads observable to retry memory.
    const reads = [request("a"), request("b"), result("a", file()), result("b", file({ path: "project://Source/B.cpp" }))];
    reads[1] = ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
      type: "function", id: "b", name: "read_file", arguments: { project, path: "Source/B.cpp" } } }] });
    assert.equal(completedRequestFingerprints(chat(reads)).size, 2);
    const normalized = normalize(reads), outcomes = memory.toolOutcomeRecords(normalized, normalized.length, { aggregateAll: true });
    assert.equal(outcomes.length, 2);
    const state = emptyReferenceState(); ingestReferenceObservations(state, messages,
      [{ name: "build_unreal_project", pluginIdentifier: "mcp/unreal-agent" }], scope, "exec");
    assert.equal(referenceSnapshot(state, chat(messages)).items.length, 2);
  }
  for (const messages of [[request("a"), request("a"), result("a", file())],
    [request("a"), result("a", file()), result("a", file()), result("a", file())], [result("orphan", file())],
    [request("a"), ChatMessage.create("assistant", "interruption"), result("a", file())],
    [request("a"), ChatMessage.create("user", "cancel previous request"), result("a", file())]]) {
    assert.equal(exchangeIndex(chat(messages)).ambiguous, true);
    assert.equal(completedRequestFingerprints(chat(messages)).size, 0);
  }
  const incomplete = chat([request("a"), request("b"), result("a", file()), request("c"), result("c", file())]);
  assert.equal(exchangeIndex(incomplete).ambiguous, true);
  assert.equal(exchangeIndex(incomplete).matches.size, 2);
  const reused = chat([request("a"), result("a", file()), request("a"), result("a", file())]);
  assert.equal(exchangeIndex(reused).ambiguous, false);
  assert.equal(exchangeIndex(reused).matches.size, 2);
});

test("A03 failed scoped read clears current hash/coverage and successful later read recovers", () => {
  for (const errorCode of ["NOT_FOUND", "FILE_NOT_FOUND", "ACCESS_DENIED", "EIO"]) {
    const messages = [request("r"), result("r", file({ ok: false, errorCode, text: undefined }))];
    const normalized = normalize(messages), observations = memory.stateMemory(memory.toolOutcomeRecords(normalized, normalized.length));
    assert.equal(observations.files[0].observationState, "unavailable");
    const prior = fileObservation(file());
    const merged = coalesceFileObservations([prior, observations.files[0]]);
    assert.equal(merged[0].sha256AtObservation, undefined); assert.equal(merged[0].readCoverageState, undefined);
    const recovered = coalesceFileObservations([...merged, fileObservation(file({ sha256: "b".repeat(64) }))]);
    assert.equal(recovered[0].observationState, "observed"); assert.equal(recovered[0].sha256AtObservation, "b".repeat(64));
    assert.equal(recovered[0].changeEvidence, undefined);
  }
  const unknown = normalize([request("r"), result("r", { ok: false, errorCode: "NOT_FOUND", path: "Source/A.cpp" })]);
  assert.equal(memory.stateMemory(memory.toolOutcomeRecords(unknown, unknown.length)).files.length, 0);
});

test("A01 reconcile rejects a changed objective/project and clears only requested attachment review claims", () => {
  const messages = [ChatMessage.create("user", "Inspect this goal")], fingerprint = notes.objectiveFingerprint(messages);
  const note = notes.attachScope({ decisions: [{ statement: "decision", rationale: "why" }] }, fingerprint, messages);
  assert.ok(notes.reconcileStoredNote(note, messages));
  assert.equal(notes.reconcileStoredNote(note, [ChatMessage.create("user", "Different goal")]), null);
  assert.equal(notes.reconcileStoredNote({ ...note, scope: { ...note.scope, projectIdentity: "other-project" } }, messages), null);
  const sourceMessages = [...messages, request("read"), result("read", file())];
  const scoped = notes.attachScope({ decisions: [{ statement: "decision", rationale: "why" }], reviewClaims: [{
    path: "project://Source/A.cpp", sha256: "a".repeat(64), reviewScope: "review",
    statement: "assistant review only", refs: ["tool-call:read"] }] }, fingerprint, sourceMessages);
  assert.equal(scoped.reviewClaims.length, 1);
  assert.equal(notes.reconcileStoredNote(scoped, sourceMessages, new Set(), { discardReviewClaims: true }).reviewClaims, undefined);
  assert.equal(notes.reconcileStoredNote(scoped, sourceMessages, new Set(), { projectDescriptor: "C:/Projects/Clone/Game.uproject" }), null);
  assert.ok(notes.reconcileStoredNote(scoped, sourceMessages, new Set(), { projectDescriptor: project }));
  const failedSource = [...sourceMessages, request("failed"), result("failed", file({ ok: false,
    errorCode: "EIO", observationState: "unavailable" }))];
  assert.equal(notes.reconcileStoredNote(scoped, failedSource).reviewClaims, undefined);
  const interrupted = [...messages, request("read"), ChatMessage.create("assistant", "request interrupted"), result("read", file())];
  assert.equal(notes.provenanceFromMessages(interrupted).verifiedToolIds.size, 0);
  assert.equal(notes.provenanceFromMessages(interrupted).projectIdentity, "");
});

test("A04 summary uses bounded semantic bodies despite long leading metadata", () => {
  for (const field of ["content", "text", "body", "code", "items"]) {
    const context = new WorkingContext({ conversation: field, lineage: field, workspace: "w", repository: "r" });
    const payload = { ok: true, metadata: "m".repeat(2000), [field]: field === "items" ? [{ value: "BODY_MARKER" }] : "BODY_MARKER" + "x".repeat(1500) };
    context.captureReturned(chat([request("r"), result("r", payload)]), () => true);
    const excerpt = context.summaryEvidence()[0].verifiedExcerpt;
    assert.match(excerpt, /BODY_MARKER/); assert.ok(excerpt.length <= 640);
    assert.equal(JSON.parse(excerpt).fullRawProvided, false);
  }
});

test("A07 only exact Unity root settings supply applicability; unavailable read removes and later read restores it", () => {
  const unity = { engine: "unity", source: "config", projectIdentity: "C:/Projects/Unity" }, state = emptyReferenceState(), all = [];
  let serial = 0;
  const observe = (p, body, extra = {}) => {
    const id = "r" + ++serial, messages = [request(id), result(id, { canonicalProjectRoot: unity.projectIdentity, projectIdentity: "id",
      path: p, text: body, hash: "a".repeat(64), startLine: 1, endLine: 1, totalLines: 1, ...extra })]; all.push(...messages);
    ingestReferenceObservations(state, messages, [{ name: "read_file", pluginIdentifier: "mcp/unity-tools" }], unity, "exec");
    return referenceSnapshot(state, chat(all));
  };
  const lock = '{"dependencies":{"com.unity.netcode.gameobjects":{"version":"2.7.0"}}}';
  let s = observe("Assets/Templates/Packages/packages-lock.json", lock);
  assert.deepEqual(s.applicability.packages, {});
  s = observe("Assets/Templates/ProjectSettings/ProjectVersion.txt", "m_EditorVersion: 6000.0.44f1");
  assert.equal(s.applicability.engineVersion, undefined);
  s = observe("Packages/packages-lock.json", lock);
  assert.ok(autoGuidanceCandidates("multiplayer", undefined, unity, s)[0].ids.includes("unity-networking/ngo"));
  s = observe("Packages/packages-lock.json", undefined, { ok: false, errorCode: "ENOENT", observationState: "unavailable" });
  assert.deepEqual(s.applicability.packages, {});
  s = observe("Packages/packages-lock.json", lock, { ok: true });
  assert.equal(s.applicability.packages["com.unity.netcode.gameobjects"], "2.7.0");
});

test("A05 expired windows are reclaimed while active/parent windows and their records stay protected", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "window-retention-")); t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  let now = 1000;
  const archive = new EvidenceArchive({ conversation: "c", lineage: "c", workspace: "w", repository: "r" },
    { durable: true, root, now: () => now, ttlMs: 50, maxWindows: 2 });
  archive.ensureDirectory(true); archive.protectedWindows = new Set(["window-live.json", "window-parent.json"]);
  const record = archive.put("evidence", { callKey: "r" }).record;
  for (const name of ["live", "parent", "expired"]) {
    const body = { schemaVersion: 1, scope: archive.scope, createdAt: now, refs: [[record.evidenceId, record.archivedBodyHash]] };
    atomicWrite(archive.directory, "window-" + name + ".json", { ...body, digest: hash(body) });
  }
  now += 100;
  assert.deepEqual(archive.cleanupWindows().map(e => e.name).sort(), ["window-live.json", "window-parent.json"]);
  assert.ok(archive.windowRefs().has(record.evidenceId));
  assert.equal(fs.existsSync(path.join(archive.directory, "window-expired.json")), false);
});

test("A05 inactive scopes expire conservatively and a cleanup failure does not break a valid read", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "scope-retention-")); t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  let now = Date.now();
  const scopeFor = id => ({ conversation: id, lineage: id, workspace: "w", repository: "r" });
  const opts = { durable: true, root, now: () => now, ttlMs: 1000 };
  const abandoned = new EvidenceArchive(scopeFor("abandoned"), opts), expired = abandoned.put("old", { callKey: "old" }).record;
  const recent = new EvidenceArchive(scopeFor("recent"), opts); recent.put("active", { callKey: "active" });
  const oldTime = new Date(now - 2000);
  fs.utimesSync(path.join(abandoned.directory, `${expired.evidenceId}.json`), oldTime, oldTime);
  fs.utimesSync(abandoned.directory, oldTime, oldTime);
  const current = new EvidenceArchive(scopeFor("current"), opts), record = current.put("readable", { callKey: "readable" }).record;
  assert.equal(fs.existsSync(abandoned.directory), false);
  assert.equal(fs.existsSync(recent.directory), true);
  const body = { schemaVersion: 1, scope: current.scope, createdAt: now - 2000, refs: [] };
  const window = path.join(current.directory, "window-expired.json");
  atomicWrite(current.directory, "window-expired.json", { ...body, digest: hash(body) });
  const unlink = fs.unlinkSync;
  fs.unlinkSync = file => { if (file === window) throw new Error("cleanup refused"); return unlink(file); };
  try { assert.equal(current.cleanupWindows().length, 1); assert.equal(current.load(record.evidenceId).ok, true); }
  finally { fs.unlinkSync = unlink; }
  current.protectedWindows.add("window-expired.json");
  const read = fs.readFileSync;
  fs.readFileSync = (file, ...args) => { if (file === window) throw new Error("manifest unreadable"); return read(file, ...args); };
  try {
    assert.equal(current.put("new record", { callKey: "new" }).errorCode, "archive_write_failed");
    assert.equal(current.load(record.evidenceId).ok, true);
  } finally { fs.readFileSync = read; }
});

test("A05 a legacy manifest backlog is pruned by file metadata before bodies are read", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "window-backlog-")); t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const archive = new EvidenceArchive({ conversation: "backlog", lineage: "backlog", workspace: "w", repository: "r" },
    { durable: true, root, maxWindows: 4, maxWindowBytes: 1024 });
  archive.protectedWindows = new Set(["window-live.json", "window-parent.json"]);
  const body = { schemaVersion: 1, scope: archive.scope, createdAt: Date.now(), refs: [], replacement: "x".repeat(180) };
  for (const name of ["live", "parent", ...Array.from({ length: 30 }, (_, i) => "old-" + i)])
    atomicWrite(archive.directory, "window-" + name + ".json", { ...body, digest: hash(body) });
  const read = fs.readFileSync; let bodyReads = 0;
  fs.readFileSync = (file, ...args) => { if (String(file).includes("window-")) bodyReads++; return read(file, ...args); };
  let retained;
  try { retained = archive.cleanupWindows(); } finally { fs.readFileSync = read; }
  assert.ok(bodyReads <= 4, `only retained manifest bodies may be read; read ${bodyReads}`);
  assert.ok(retained.some(e => e.name === "window-live.json")); assert.ok(retained.some(e => e.name === "window-parent.json"));
  assert.ok(retained.length <= 4); assert.ok(retained.reduce((n, e) => n + e.bytes, 0) <= 1024);
});
