"use strict";
// Revised plan 2026-11-07 §B. A compacted listing kept only its metadata, so
// the model listed nine directories again (conversation 1793984074550). The
// listing body becomes names only, behind a per-record body view version.
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { listingFacts, listingBody, isAuditSource } = require("../src/directory-listing.js");
const { EvidenceArchive, evidenceBodyView, recordBodyView, hash } = require("../src/evidence-archive.js");
const { WorkingContext } = require("../src/working-context.js");
const { ReadLedger } = require("../src/read-ledger.js");

const scope = { conversation: "listing", lineage: "listing", workspace: "w", repository: "r" };
function listing(names, extra = {}) {
  return { ok: true, path: "project://Source/Game/Private/Components", resolvedRootType: "active_project",
    absolutePath: "C:\\Users\\dev\\Documents\\Git\\Game\\Source\\Game\\Private\\Components",
    activeProject: "C:\\Users\\dev\\Documents\\Git\\Game\\Game.uproject",
    projectRelativePath: "Source/Game/Private/Components", workspaceRelativePath: "Source/Game/Private/Components",
    displayPath: "project://Source/Game/Private/Components", canonicalProject: "C:/Users/dev/Documents/Git/Game/Game.uproject",
    workspaceIdentity: "c".repeat(64), schemaVersion: 1, kind: "workspace_directory_listing",
    entries: names.map(name => name.endsWith("/") ? { name: name.slice(0, -1), type: "directory", modifiedAt: "2026-11-06T17:00:00.000Z" }
      : { name, type: "file", size: 1000 + name.length, modifiedAt: "2026-11-06T17:00:00.000Z" }),
    offset: 0, nextOffset: null, totalEntries: names.length, truncated: false, ...extra };
}
const NAMES = ["CombatInputComponent.cpp", "CombatScenarioComponent.cpp", "CombatTelemetryComponent.cpp", "CombatTimeSyncComponent.cpp",
  "HealthComponent.cpp", "RespawnComponent.cpp", "RewindHistoryComponent.cpp", "TeamSpawnComponent.cpp", "WeaponAmmoComponent.cpp",
  "WeaponFireComponent.cpp", "WeaponHitValidationComponent.cpp", "Game.ini", "Legacy/"];

test("listing facts name every entry and only a whole first page is complete", () => {
  const facts = listingFacts(listing(NAMES));
  assert.equal(facts.complete, true);
  assert.equal(facts.files.length, 12);
  assert.deepEqual(facts.directories, ["Legacy"]);
  assert.equal(listingFacts(listing(NAMES, { offset: 200, nextOffset: null })).complete, false, "a last page is not the whole directory");
  assert.equal(listingFacts(listing(NAMES, { nextOffset: 13, truncated: true })).complete, false);
  assert.equal(listingFacts(listing(NAMES, { totalEntries: 99 })).complete, false);
  assert.equal(listingFacts(listing(NAMES, { omittedByPathPolicy: 2 })).complete, false);
  const bad = listingFacts(listing([...NAMES, "a/b.cpp", "..", "x\ny.h"]));
  assert.equal(bad.rejected, 3);
  assert.equal(bad.complete, false);
  assert.equal(listingFacts({ ok: false, path: "p", entries: [] }), null);
  // The body keeps every kind of name; the source filter is for audit scope only.
  const body = JSON.parse(listingBody(facts));
  assert.ok(body.files.includes("Game.ini"));
  assert.equal(isAuditSource("project://Source/Game/Game.ini"), false);
  assert.equal(isAuditSource("project://Source/Game/Private/A.cpp"), true);
  assert.equal(isAuditSource("project://Intermediate/Build/A.cpp"), false);
});

test("a compacted listing keeps all its names and costs less than the original", () => {
  const names = [...NAMES, "CombatFeedbackComponent.cpp", "CombatPresentationComponent.cpp"];
  const payload = listing(names);
  const history = Chat.from([{ role: "user", content: "List the components" }]);
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id: "ls", name: "list_directory", arguments: { path: payload.path } } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "ls", content: JSON.stringify(payload) }] }));
  const original = JSON.stringify(payload).length;
  assert.ok(original > 2048, `fixture ${original} chars is above the projection threshold`);
  const projected = new WorkingContext(scope).project(history, () => true, {}, 2048);
  assert.equal(projected.changed, true);
  const content = projected.history.getMessagesArray().at(-1).getToolCallResults()[0].content;
  assert.ok(content.length < original);
  for (const name of names) assert.ok(content.includes(name.replace(/\/$/u, "")), `${name} survives compaction`);
});

test("each archive record renders and pages its own body view version", () => {
  const archive = new EvidenceArchive(scope);
  const payload = listing(NAMES);
  const saved = archive.put(JSON.stringify(payload), { callKey: "ls" });
  assert.equal(saved.record.bodyViewVersion, 2);
  const page = archive.read(saved.record.evidenceId, saved.record.archivedBodyHash, 0, 8192, "body");
  assert.equal(page.viewVersion, 2);
  assert.equal(page.body ?? page.content ?? page.text, recordBodyView(saved.record).body.slice(0, (page.body ?? page.content ?? page.text).length));
  // A record from before versioning (no field) keeps the JSON entries view and its offsets.
  const legacyPayload = listing(NAMES.map(name => name.replace("Component", "Part")));
  const legacySaved = archive.put(JSON.stringify(legacyPayload), { callKey: "legacy" });
  const { recordHash: _hash, bodyViewVersion: _version, ...legacy } = legacySaved.record;
  legacy.recordHash = hash(legacy);
  archive.records.set(legacy.evidenceId, legacy);
  const v1 = evidenceBodyView(legacyPayload, 1).body;
  assert.equal(recordBodyView(legacy).body, v1);
  const offset = 500;
  assert.ok(v1.length > offset + 10);
  const legacyPage = archive.read(legacy.evidenceId, legacy.archivedBodyHash, offset, 8192, "body");
  assert.equal(legacyPage.ok, true, "an offset valid before the upgrade stays valid");
  assert.equal(legacyPage.viewVersion, 1);
  // put() of the same body returns the stored legacy record unchanged.
  assert.equal(archive.put(JSON.stringify(legacyPayload), { callKey: "legacy" }).record.bodyViewVersion, undefined);
  // Source bodies are the same in both versions.
  const source = { ok: true, path: "A.cpp", content: "int A;\n" };
  assert.deepEqual(evidenceBodyView(source, 1), evidenceBodyView(source, 2));
});

function readFacts(path, start, end, total, sha = "a".repeat(64)) {
  return { path, key: path.toLowerCase(), sourceKey: `key:${path}`, sourceVersion: sha, lines: [start, end], bytes: null,
    complete: start === 1 && end >= total, totalLines: total, size: null, chars: 10 };
}

test("the inventory reports per-file coverage from the read entries and resets per execution", () => {
  const ledger = new ReadLedger();
  const facts = listingFacts(listing(["A.cpp", "B.cpp", "C.cpp", "D.cpp", "Sub/"]));
  const fileKey = name => `key:${facts.path}/${name}`;
  ledger.recordListing("exec-1", "dir:components", facts, fileKey);
  ledger.recordListing("exec-1", "dir:components", facts, fileKey);
  ledger.record(readFacts(`${facts.path}/A.cpp`, 1, 25, 57));
  ledger.record(readFacts(`${facts.path}/B.cpp`, 1, 25, 57));
  ledger.record(readFacts(`${facts.path}/B.cpp`, 26, 57, 57));
  ledger.record(readFacts(`${facts.path}/D.cpp`, 1, 10, 10));
  ledger.invalidateReturnedSource(fileKey("D.cpp"));
  const inventory = ledger.inventory("exec-1");
  const state = Object.fromEntries(inventory.files.map(file => [file.path.split("/").at(-1), file.coverage]));
  assert.deepEqual(state, { "A.cpp": "partial", "B.cpp": "complete", "C.cpp": "unread", "D.cpp": "unknown" });
  assert.equal(inventory.directories.length, 1, "re-observing the same listing is idempotent");
  assert.equal(inventory.directories[0].complete, true);
  assert.deepEqual(inventory.directories[0].subdirectories, ["Sub"], "a listed subdirectory is not an inspected one");
  // A second page makes the union incomplete.
  ledger.recordListing("exec-1", "dir:components", { ...facts, offset: 4, complete: false, files: [{ name: "E.cpp", size: 1, modifiedAt: null }] }, fileKey);
  assert.equal(ledger.inventory("exec-1").directories[0].complete, false);
  // Persisted, restored, then replaced by a new execution.
  const restored = new ReadLedger();
  restored.load(JSON.parse(JSON.stringify(ledger.toJSON())));
  assert.deepEqual(restored.inventory("exec-1").files.map(file => file.path), ledger.inventory("exec-1").files.map(file => file.path));
  assert.equal(restored.inventory("exec-2"), null, "another execution's listing is not this one's inventory");
  restored.recordListing("exec-2", "dir:other", listingFacts(listing(["Z.cpp"])), fileKey);
  assert.deepEqual(restored.inventory("exec-2").files.map(file => file.path.split("/").at(-1)), ["Z.cpp"]);
});

test("the inventory is bounded and says so when it overflows", () => {
  const ledger = new ReadLedger();
  const many = listingFacts(listing(Array.from({ length: 600 }, (_, i) => `F${i}.cpp`), { totalEntries: 600 }));
  ledger.recordListing("exec", "dir", many, name => `key:${name}`);
  const inventory = ledger.inventory("exec");
  assert.equal(inventory.files.length, 512);
  assert.equal(inventory.overflow, true);
});

test("a listing page keeps its paging in the projection's source facts", () => {
  const payload = listing(NAMES, { offset: 200, nextOffset: 400, totalEntries: 650, truncated: true });
  const history = Chat.from([{ role: "user", content: "List" }]);
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id: "ls", name: "list_directory", arguments: { path: payload.path, offset: 200 } } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "ls",
    content: JSON.stringify({ ...payload, padding: "x".repeat(1200) }) }] }));
  const projected = new WorkingContext(scope).project(history, () => true, {}, 512);
  const view = JSON.parse(projected.history.getMessagesArray().at(-1).getToolCallResults()[0].content);
  assert.equal(view.sourceFacts.offset, 200);
  assert.equal(view.sourceFacts.nextOffset, 400);
  assert.equal(view.sourceFacts.totalEntries, 650);
});

test("through the evidence manager, a listed file read in full is complete and the rest unread", () => {
  const { EvidenceManager } = require("../dist/evidence-manager");
  const { tools, pair } = require("./helpers/v4.cjs");
  const dir = { ok: true, path: "project://Source/Fixture/Public", project: "Fixture.uproject", canonicalProject: "Fixture.uproject",
    entries: [{ name: "Character.h", type: "file", size: 40, modifiedAt: "2026-10-27T00:00:00Z" },
      { name: "Weapon.h", type: "file", size: 40, modifiedAt: "2026-10-27T00:00:00Z" }],
    offset: 0, nextOffset: null, totalEntries: 2, truncated: false };
  const em = new EvidenceManager(null, tools);
  em.recordReturnedSources(pair("list_directory", { path: dir.path, project: dir.project }, dir), "exec-A");
  const path = `${dir.path}/Character.h`;
  // The production order of one round: record returned sources, then screen reads into the ledger.
  const read = pair("read_file", { path, project: dir.project }, { ok: true, path, canonicalProject: dir.project,
    sha256: "a".repeat(64), startLine: 1, endLine: 2, totalLines: 2, content: "1\tclass A;\n2\tclass B;" });
  em.recordReturnedSources(read, "exec-A");
  em.screenReturnedReads(read, 1);
  const listed = em.ledger.returnedSources("exec-A").listed;
  const state = Object.fromEntries(listed.files.map(file => [file.path.split("/").pop(), file.coverage]));
  assert.equal(state["Weapon.h"], "unread");
  assert.equal(state["Character.h"], "complete", "the listing key and the read key are the same source key");
});
