"use strict";
// Unreal contract summaries: generated from config/unreal_contracts.json into
// guidance Markdown, version gated, and observed in the final model input.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { Chat } = require("@lmstudio/sdk");
const { focusedGuidanceCandidates, autoGuidanceCandidates } = require("../dist/design-guidance");
const { bundledGuidanceSections } = require("../dist/generated-design-guidance");
const { emptyReferenceSnapshot } = require("../dist/reference-context");
const { readConfig } = require("../dist/execution-config");
const { ContextManager, createInputAssembler } = require("../dist/context-manager");
const { BudgetBroker } = require("../dist/budget-broker");
const { syncContractSummaries, splitSections } = require("../scripts/build-design-guidance.cjs");

const repo = path.resolve(__dirname, "../..");
const docRoot = path.join(repo, "docs/model-guidance");
const contracts = JSON.parse(fs.readFileSync(path.join(repo, "config/unreal_contracts.json"), "utf8"));
const label = `unreal_contracts@${contracts.revision}`;
const scope = { engine: "unreal", source: "config" };
const snapshotFor = (version, signals = []) => {
  const snapshot = emptyReferenceSnapshot();
  if (version) snapshot.applicability.engineVersion = version;
  snapshot.signals = signals;
  snapshot.autoSignals = signals.map(signal => ({ signal, order: "last_observation", observationIds: [] }));
  return snapshot;
};
const sectionOf = id => bundledGuidanceSections.find(s => s.id === id);

test("shipped guidance blocks match the contract file and every guidance reference is placed", () => {
  const updated = syncContractSummaries(docRoot, contracts);
  for (const [file, text] of updated) assert.equal(fs.readFileSync(path.join(docRoot, file), "utf8"), text, file);
  for (const contract of contracts.contracts) {
    for (const ref of contract.guidanceRefs.filter(r => /^[a-z-]+\.md#/.test(r))) {
      const [file, section] = ref.split("#");
      const bundled = sectionOf(`${file.replace(/\.md$/, "")}/${section}`);
      assert.ok(bundled, ref);
      assert.match(bundled.text, new RegExp(`\x60${contract.id.replace(/\./g, "\\.")}\x60`));
      assert.ok(bundled.text.includes(contract.statement), contract.id);
      assert.ok(bundled.text.includes(label));
    }
  }
  assert.ok(bundledGuidanceSections.every(s => !s.text.includes("contract-summary")));
});

test("engine fact sections are gated to validated versions; policy sections are not", () => {
  for (const id of ["unreal/lifetime", "unreal/subsystems", "unreal-networking/engine-defaults"]) {
    assert.deepEqual(sectionOf(id).applicability.engineVersions, contracts.validatedEngineVersions, id);
  }
  for (const id of ["unreal/threads", "unreal/verification", "unreal/responsibility"]) {
    assert.equal(sectionOf(id).applicability.engineVersions, undefined, id);
  }
});

function fixture(t, markdown) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "contract-guidance-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(path.join(root, "unreal.md"), markdown);
  const data = { schemaVersion: 1, revision: 7, validatedEngineVersions: ["5.8"], contracts: [
    { id: "ue.sample", kind: "engine_fact", statement: "Sample fact.", guidanceRefs: ["unreal.md#a", "34_Rag.md"],
      evidenceRefs: [{ path: "Engine/Source/A.h", symbol: "A", line: 3 }, { path: "Engine/Source/B.h", symbol: "B", line: 4 }] }] };
  return { root, data, sync: () => syncContractSummaries(root, data).get("unreal.md") };
}

test("sync rewrites only the marked block, keeps CRLF and rejects stale structure", t => {
  const f = fixture(t, "# T\r\nhand\r\n<!-- contract-summary: a -->\r\nold line\r\n<!-- /contract-summary: a -->\r\ntail\r\n");
  const text = f.sync();
  assert.match(text, /^# T\r\nhand\r\n<!-- contract-summary: a -->\r\n엔진 계약 unreal_contracts@7 /);
  assert.match(text, /- `ue\.sample` \(엔진 사실\) Sample fact\. 근거: Engine\/Source\/A\.h:3 외 1\r\n<!-- \/contract-summary: a -->\r\ntail/);
  assert.doesNotMatch(text, /old line/);
  assert.doesNotMatch(splitSections(text).text, /contract-summary/);
  fs.writeFileSync(path.join(f.root, "unreal.md"), "# T\n");
  assert.throws(f.sync, /block missing: unreal\.md#a/);
  fs.writeFileSync(path.join(f.root, "unreal.md"), "<!-- contract-summary: a -->\n<!-- contract-summary: b -->\n");
  assert.throws(f.sync, /Unexpected contract summary block/);
  fs.writeFileSync(path.join(f.root, "unreal.md"), "<!-- contract-summary: a -->\n");
  assert.throws(f.sync, /Unterminated/);
  fs.writeFileSync(path.join(f.root, "unreal.md"), "```\n<!-- contract-summary: a -->\n```\n");
  assert.throws(f.sync, /block missing/);
  assert.throws(() => splitSections("<!--contract-summary: a-->\n"), /Malformed contract summary marker/);
  f.data.schemaVersion = 2; assert.throws(f.sync, /Unsupported/);
});

function budgetFixture(charge) {
  const config = readConfig({ getPluginConfig: () => ({ get: key => ({ contextManagementMode: "hybrid", inputAvailabilityMode: "inject" })[key] }) });
  const history = Chat.from([{ role: "system", content: "ORIGINAL_SYSTEM" }, { role: "user", content: "OBJECTIVE" }]);
  const assemble = createInputAssembler({ tokenSource: {
    async getContextLength() { return 38912; }, async applyPromptTemplate(chat) { return chat.toString(); },
    async countTokens(body) { return charge(body); } }, config, roundTools: [], roundScopeInstructions: [],
    getRoundOutputReserve: () => 4096, modelInputId: "id", noteEnabled: false, getNote: () => null, historicalAvailabilityLedger: [] });
  const manager = new ContextManager(config, new BudgetBroker(config));
  const admit = async (candidates, maxAddedTokens = 8192) => manager.addOptionalReferences(await assemble(history), candidates,
    (instruction, referenceData) => assemble(history, { instructions: [instruction], referenceData }),
    { maxAddedTokens, hasReadTools: false, minimumReadTokens: 0, guardAbort() {} });
  return { admit };
}
const FACT = "ue.net.cmc-prediction";

test("a validated engine puts the contract facts into the final model input", async () => {
  const f = budgetFixture(body => Math.ceil(body.length / 20));
  const result = await f.admit(focusedGuidanceCandidates("multiplayer", scope, snapshotFor("5.8.2")));
  assert.equal(result.telemetry.reason, "included");
  assert.ok(result.telemetry.selectedIds.includes("unreal-networking/engine-defaults"));
  const input = result.assembled.history.toString();
  assert.ok(input.includes(FACT) && input.includes(label) && input.includes("ClientAdjustPosition"));
  assert.doesNotMatch(input, /contract-summary/);
  const lifecycle = await f.admit(autoGuidanceCandidates("lifecycle", undefined, scope, snapshotFor("5.8.0")));
  assert.match(lifecycle.assembled.history.toString(), /ue\.actor\.lifecycle/);
});

test("other or unknown engine versions, Off and an exhausted budget leave the facts out", async () => {
  const f = budgetFixture(body => Math.ceil(body.length / 20));
  for (const version of ["5.7.4", "5.4", undefined]) {
    const candidates = focusedGuidanceCandidates("multiplayer", scope, snapshotFor(version));
    assert.ok(candidates.every(c => !c.ids.includes("unreal-networking/engine-defaults")), String(version));
    const result = await f.admit(candidates);
    assert.equal(result.telemetry.reason, "included");
    assert.ok(!result.assembled.history.toString().includes(FACT), String(version));
  }
  assert.deepEqual(focusedGuidanceCandidates("off", scope, snapshotFor("5.8.2")), []);
  const tight = budgetFixture(body => 500 + (body.includes("Optional design reference") ? 100 : 0) + (body.includes(FACT) ? 5000 : 0));
  const reduced = await tight.admit(focusedGuidanceCandidates("multiplayer", scope, snapshotFor("5.8.2")), 600);
  assert.equal(reduced.telemetry.reason, "included");
  assert.ok(reduced.telemetry.omittedIds.includes("unreal-networking/engine-defaults"));
  assert.ok(!reduced.assembled.history.toString().includes(FACT));
  const none = await tight.admit(focusedGuidanceCandidates("multiplayer", scope, snapshotFor("5.8.2")), 50);
  assert.equal(none.telemetry.reason, "budget_omitted");
  assert.deepEqual(none.telemetry.selectedIds, []);
});

test("a build diagnostic brings the claim policies into the model input", async () => {
  const f = budgetFixture(body => Math.ceil(body.length / 20));
  const candidates = autoGuidanceCandidates("debugging", undefined, scope, snapshotFor("5.7.4", ["diagnostic_present"]));
  assert.ok(candidates[0].ids.includes("unreal/verification"));
  const result = await f.admit(candidates);
  assert.match(result.assembled.history.toString(), /policy\.claims\.build-evidence/);
  const quiet = autoGuidanceCandidates("debugging", undefined, scope, snapshotFor("5.7.4"));
  assert.ok(!quiet[0].ids.includes("unreal/verification"));
});

test("bundled sections list the contract ids their generated block delivers", () => {
  for (const section of bundledGuidanceSections.filter(s => s.contractIds)) {
    for (const id of section.contractIds) assert.ok(section.text.includes(`\x60${id}\x60`), section.id + " " + id);
  }
  assert.deepEqual(sectionOf("unreal-networking/engine-defaults").contractIds,
    contracts.contracts.filter(c => c.guidanceRefs.includes("unreal-networking.md#engine-defaults")).map(c => c.id));
  assert.equal(sectionOf("unreal/ownership").contractIds, undefined);
});

test("auto mode admits the verified contract pack beside the engine pack, without displacing it", () => {
  const ids = (mode, version, signals, supplement) => autoGuidanceCandidates(mode, supplement, scope, snapshotFor(version, signals))[0].ids;
  const multiplayer = ids("multiplayer", "5.8.2");
  assert.ok(multiplayer.includes("unreal-networking/authority") && multiplayer.includes("unreal-networking/engine-defaults"));
  assert.ok(ids("lifecycle", "5.8.0").includes("unreal/lifetime"));
  const debugging = ids("debugging", "5.8.2");
  assert.ok(debugging.includes("unreal/api"));
  const designCompile = ids("design", "5.8.2", ["compilation_pending"], "debugging");
  assert.ok(designCompile.includes("debugging/verification"));
  for (const mode of ["multiplayer", "lifecycle", "design", "debugging"]) {
    assert.ok(ids(mode, "5.8.2").length <= 6);
    assert.ok(!ids(mode, "5.7.4").some(id => sectionOf(id).applicability.engineVersions?.includes("5.8")), mode);
  }
});

test("core requests receive the claim policies as their engine pack", () => {
  for (const version of ["5.8.2", "5.7.4", undefined]) {
    const [first] = autoGuidanceCandidates("core", undefined, scope, snapshotFor(version));
    assert.deepEqual(first.ids, ["core/proof", "unreal/verification"], String(version));
    assert.ok(!first.omissionReasons?.includes("engine_pack_unavailable"));
  }
  assert.equal(autoGuidanceCandidates("core", undefined, { engine: "unity", source: "config" }, snapshotFor())[0]
    .ids.includes("unreal/verification"), false);
});

test("a closed version gate is named in the omission reasons", () => {
  const reasons = (mode, version) => autoGuidanceCandidates(mode, undefined, scope, snapshotFor(version))[0].omissionReasons || [];
  assert.ok(reasons("multiplayer", undefined).includes("contract_sections_version_unverified"));
  assert.ok(reasons("lifecycle", "5.7.4").includes("contract_sections_engine_version_unvalidated"));
  assert.ok(!reasons("multiplayer", "5.8.2").some(r => r.startsWith("contract_sections_")));
  assert.ok(!reasons("debugging", undefined).some(r => r.startsWith("contract_sections_")));
});
