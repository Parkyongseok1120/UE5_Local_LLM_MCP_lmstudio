"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const { Chat } = require("@lmstudio/sdk");
const { designGuidanceMode, selectDesignGuidance, designGuidanceCandidates } = require("../dist/design-guidance");
const { bundledDesignGuidance } = require("../dist/generated-design-guidance");
const { loadContractSets, renderBundle } = require("../scripts/build-design-guidance.cjs");
const { readConfig } = require("../dist/execution-config");
const { ContextManager, createInputAssembler } = require("../dist/context-manager");
const { BudgetBroker } = require("../dist/budget-broker");

const scope = (engine, source = "config") => ({ engine, source });
const candidates = mode => designGuidanceCandidates(selectDesignGuidance(mode, scope("unreal")));

test("guidance is opt-in, numeric allowance is bounded, and selection does not infer an engine from tools", () => {
  assert.equal(designGuidanceMode(undefined), "off");
  assert.equal(designGuidanceMode("arbitrary-path"), "off");
  const config = readConfig({ getPluginConfig: () => ({ get: () => undefined }) });
  assert.equal(config.designGuidanceMode, "off");
  assert.equal(config.designGuidanceMaxTokens, 2048);
  assert.deepEqual(selectDesignGuidance("off", scope("unreal")), []);
  for (const source of ["available_tools", "ambiguous"]) {
    assert.deepEqual(selectDesignGuidance("multiplayer", scope("unreal", source)).map(d => d.id), ["core", "multiplayer"]);
  }
});

test("engine selection is per execution and never imports the audited project's facts", () => {
  const unreal = selectDesignGuidance("multiplayer", scope("unreal", "message"));
  const unity = selectDesignGuidance("multiplayer", scope("unity", "workspace"));
  assert.deepEqual(unreal.map(d => d.id), ["core", "multiplayer", "unreal-networking"]);
  assert.deepEqual(unity.map(d => d.id), ["core", "multiplayer", "unity-networking"]);
  assert.deepEqual(selectDesignGuidance("lifecycle", scope("unknown", "ambiguous")).map(d => d.id), ["core"]);
  assert.equal(bundledDesignGuidance.length, 9);
  assert.ok(bundledDesignGuidance.every(d => !/Latency_MultiCombat|C:[/\\]Users/i.test(d.text)));
  assert.deepEqual(selectDesignGuidance("core", scope("mixed")).map(d => d.id), ["core"]);
  assert.deepEqual(selectDesignGuidance("code-style", scope("unreal")).map(d => d.id), ["core", "code-style"]);
  assert.deepEqual(selectDesignGuidance("multiplayer", scope("mixed")).map(d => d.id),
    ["core", "multiplayer", "unity-networking", "unreal-networking"]);
});

test("generated deployment content is derived exactly from canonical Markdown", () => {
  const plugin = path.resolve(__dirname, "..");
  assert.equal(fs.readFileSync(path.join(plugin, "src/generated-design-guidance.ts"), "utf8").replace(/\r\n/g, "\n"),
    renderBundle(path.resolve(plugin, "../docs/model-guidance"), loadContractSets(path.resolve(plugin, "../config"))));
});

test("fallbacks retain whole documents and deduplicate references", () => {
  const core = bundledDesignGuidance.find(d => d.id === "core");
  const result = designGuidanceCandidates([core, core]);
  assert.equal(result.length, 1);
  assert.equal(result[0].instruction.split("[reference id=core ").length - 1, 1);
  assert.ok(result[0].instruction.includes(core.text));
  assert.deepEqual(selectDesignGuidance("core", scope("unreal"), []), []);
});

function fixture({ maxTokens = 8192, charge, config: extra = {} } = {}) {
  const config = readConfig({ getPluginConfig: () => ({ get: key => ({
    contextManagementMode: "hybrid", inputAvailabilityMode: "inject", ...extra,
  })[key] }) });
  const history = Chat.from([{ role: "system", content: "USER_SYSTEM" },
    { role: "user", content: "CURRENT_OBJECTIVE\nActual compiler error and source evidence." }]);
  const source = {
    async getContextLength() { return 38912; },
    async applyPromptTemplate(chat, options) { return chat.toString() + JSON.stringify(options.toolDefinitions); },
    async countTokens(text) { return charge ? charge(text) : Math.ceil(text.length / 2); },
  };
  const tools = [{ name: "read_file", pluginIdentifier: "mcp/unreal-agent", description: "REAL_TOOL_SCHEMA" }];
  const assemble = createInputAssembler({ tokenSource: source, config, roundTools: tools,
    roundScopeInstructions: ["REQUIRED_SCOPE"], getRoundOutputReserve: () => config.maxOutputReserve,
    modelInputId: "guidance-fixture", noteEnabled: false, getNote: () => null, historicalAvailabilityLedger: [] });
  const manager = new ContextManager(config, new BudgetBroker(config));
  const options = { maxAddedTokens: maxTokens, hasReadTools: true, minimumReadTokens: 2048, guardAbort() {} };
  return { history, config, manager, assemble, options,
    async add(list = candidates("multiplayer"), overrides = {}) {
      const base = await assemble(history);
      return manager.addOptionalReferences(base, list,
        text => assemble(history, { instructions: ["REQUIRED_SCOPE", text] }), { ...options, ...overrides });
    } };
}

test("final input includes exact measured guidance and tools without modifying evidence history", async () => {
  const f = fixture();
  const before = f.history.toString();
  const result = await f.add();
  assert.equal(result.telemetry.reason, "included");
  assert.deepEqual(result.telemetry.selectedIds, ["core", "multiplayer", "unreal-networking"]);
  assert.match(result.assembled.history.toString(), /CURRENT_OBJECTIVE/);
  assert.match(result.assembled.history.toString(), /REQUIRED_SCOPE/);
  assert.equal(result.assembled.measurement.exact, true);
  assert.equal(result.assembled.stages.finalExactInputTokens, result.assembled.measurement.inputTokens);
  assert.equal(f.history.toString(), before);
  assert.equal((await f.assemble(f.history)).history.toString().includes("[Optional design reference]"), false);
});

test("allowance and shared budget omit documents without shrinking evidence or the output reserve", async () => {
  const f = fixture({ maxTokens: 1000, charge: text => 500 + (text.includes("[reference id=core ") ? 600 : 0)
    + (text.includes("[reference id=multiplayer ") ? 1000 : 0) });
  const result = await f.add();
  assert.deepEqual(result.telemetry.selectedIds, ["core"]);
  assert.equal(result.telemetry.addedTokens, 600);
  assert.equal(result.assembled.measurement.outputReserve, f.config.maxOutputReserve);
  const small = fixture({ maxTokens: 10 });
  const omitted = await small.add();
  assert.equal(omitted.telemetry.reason, "budget_omitted");
  assert.deepEqual(omitted.telemetry.selectedIds, []);
  assert.match(omitted.assembled.history.toString(), /CURRENT_OBJECTIVE/);
  const pressured = fixture({ charge: text => text.includes("[Optional design reference]") ? 28000 : 18000 });
  const priority = await pressured.add();
  assert.equal(priority.telemetry.reason, "existing_input_priority");
  assert.equal(priority.telemetry.attempts, 0);
});

test("topic-backed admission may use space up to high water; generic material keeps low water", async () => {
  const probe = fixture();
  const water = probe.manager.broker.watermarks((await probe.assemble(probe.history)).measurement, 0, true, 2048);
  assert.ok(water.effectiveHighWaterTokens - water.effectiveLowWaterTokens > 2000);
  const between = water.effectiveLowWaterTokens + 500;
  const at = (base, added, maxTokens) => fixture({ maxTokens,
    charge: text => text.includes("[Optional design reference]") ? base + added : base });
  const generic = await at(between, 600).add();
  assert.deepEqual([generic.telemetry.reason, generic.telemetry.attempts, generic.telemetry.admissionWater],
    ["existing_input_priority", 0, "low"]);
  const f = at(between, 600);
  const before = f.history.toString();
  const backed = await f.add(undefined, { admissionWater: "high" });
  assert.deepEqual([backed.telemetry.reason, backed.telemetry.addedTokens, backed.telemetry.admissionWater], ["included", 600, "high"]);
  assert.match(backed.assembled.history.toString(), /\[Optional design reference\][\s\S]*CURRENT_OBJECTIVE/);
  assert.equal(backed.assembled.measurement.outputReserve, f.config.maxOutputReserve);
  assert.equal(f.history.toString(), before);
  // The admitted reference sits in the leading system message, ahead of every
  // history message, so later rounds that admit it again keep the same prefix.
  const next = Chat.from([{ role: "system", content: "USER_SYSTEM" },
    { role: "user", content: "CURRENT_OBJECTIVE\nActual compiler error and source evidence." },
    { role: "assistant", content: "ROUND_ONE_ANSWER" }, { role: "user", content: "FOLLOW_UP" }]);
  const later = await f.manager.addOptionalReferences(await f.assemble(next), candidates("multiplayer"),
    text => f.assemble(next, { instructions: ["REQUIRED_SCOPE", text] }), { ...f.options, admissionWater: "high" });
  assert.equal(later.telemetry.reason, "included");
  const system = result => result.assembled.history.getMessagesArray()[0];
  assert.ok(system(backed).isSystemPrompt());
  assert.equal(system(later).getText(), system(backed).getText());
  // Over high water, over the allowance, or a base already at high water: the measured base stays.
  for (const [base, added, maxTokens, reason, attempts] of [
    [water.effectiveHighWaterTokens - 100, 600, 8192, "budget_omitted", 1],
    [between, 1500, 1000, "budget_omitted", 1],
    [water.effectiveHighWaterTokens, 10, 8192, "existing_input_priority", 0],
  ]) {
    const omitted = await at(base, added, maxTokens).add(candidates("core"), { admissionWater: "high" });
    assert.deepEqual([omitted.telemetry.reason, omitted.telemetry.attempts, omitted.telemetry.selectedIds], [reason, attempts, []]);
    assert.equal(omitted.assembled.measurement.inputTokens, base);
  }
});

test("optional reference failure or inexact measurement keeps the already measured input", async () => {
  const f = fixture({ charge: text => {
    if (text.includes("[Optional design reference]")) throw new Error("tokenizer unavailable");
    return 500;
  } });
  const result = await f.add();
  assert.equal(result.telemetry.reason, "measurement_unavailable");
  assert.equal(result.assembled.measurement.exact, true);
  assert.equal(result.assembled.history.toString().includes("[Optional design reference]"), false);
  const base = await f.assemble(f.history);
  const failed = await f.manager.addOptionalReferences(base, candidates("core"), async () => { throw new Error("failure"); }, f.options);
  assert.equal(failed.assembled, base);
  assert.equal(failed.telemetry.reason, "assembly_failed");
});

test("cancellation during optional measurement is propagated", async () => {
  const f = fixture();
  const base = await f.assemble(f.history);
  let stopped = false;
  const reason = new Error("user canceled");
  await assert.rejects(f.manager.addOptionalReferences(base, candidates("core"), async () => {
    stopped = true;
    return base;
  }, { ...f.options, guardAbort() { if (stopped) throw reason; } }), error => error === reason);
});

test("disabled allowance performs no additional measurement", async () => {
  const result = await fixture().add(candidates("core"), { maxAddedTokens: 0 });
  assert.equal(result.telemetry.reason, "disabled");
  assert.equal(result.telemetry.attempts, 0);
});

test("documents delivery omits unknown/other-major sections and annotates same-major facts", () => {
  const { bundledGuidanceSections } = require("../dist/generated-design-guidance");
  const facts = version => ({ engineVersion: version, packages: {}, networkPackagesAmbiguous: false });
  const unreal = version => selectDesignGuidance("lifecycle", scope("unreal"), undefined, facts(version)).find(d => d.id === "unreal");
  const lifetime = bundledGuidanceSections.find(s => s.id === "unreal/lifetime");
  const timer = bundledGuidanceSections.find(s => s.id === "unreal/timer");
  const same = unreal("5.3.1");
  assert.ok(same.text.includes(lifetime.text));
  assert.ok(same.text.includes(timer.text));
  assert.ok(same.text.includes("[unvalidated for unreal 5.3.1; validated on 5.8]"));
  assert.ok(same.text.includes("[unvalidated for unreal 5.3.1; validated on 5.7]"));
  for (const version of [undefined, "4.27.2"]) {
    const doc = unreal(version);
    assert.ok(!doc.text.includes(lifetime.text));
    assert.ok(!doc.text.includes(timer.text));
    assert.ok(doc.text.includes("[omitted unreal/lifetime:"));
    assert.ok(designGuidanceCandidates([doc])[0].omissionReasons.length > 0);
  }
  const unityFacts = { engineVersion: "6000.3.1f1", packages: { "com.unity.netcode.gameobjects": "2.7.1" }, networkPackagesAmbiguous: false };
  const doc = selectDesignGuidance("multiplayer", scope("unity"), undefined, unityFacts).find(d => d.id === "unity-networking");
  const ngo = bundledGuidanceSections.find(s => s.id === "unity-networking/ngo");
  const entities = bundledGuidanceSections.find(s => s.id === "unity-networking/entities");
  assert.ok(doc.text.includes(ngo.text));
  assert.ok(!doc.text.includes(entities.text));
  assert.ok(doc.text.includes("[unvalidated for com.unity.netcode.gameobjects 2.7.1; validated on 2.7.0]"));
  assert.equal(same.revision, bundledDesignGuidance.find(d => d.id === "unreal").revision);
});

test("versionTier derives the major or package family from the validated list", () => {
  const { versionTier } = require("../dist/design-guidance");
  assert.equal(versionTier("5.8.2", ["5.8"]), "validated");
  assert.equal(versionTier("5.3", ["5.8"]), "same_major_unvalidated");
  assert.equal(versionTier("4.27.2", ["5.8"]), "other_major");
  assert.equal(versionTier("6000.3.1f1", ["6000.0"]), "same_major_unvalidated");
  assert.equal(versionTier("6000.0.44f1", ["6000.0"]), "validated");
  assert.equal(versionTier("2022.3.1f1", ["6000.0"]), "other_major");
  assert.equal(versionTier(undefined, ["5.8"]), "unknown");
  assert.equal(versionTier("2.7.0-preview", ["2.7.0"], "package"), "unknown");
  assert.equal(versionTier("2.7.1", ["2.7.0"], "package"), "same_major_unvalidated");
  assert.equal(versionTier("2.8.0", ["2.7.0"], "package"), "other_major");
  assert.equal(versionTier("1.14.2", ["1.14"], "package"), "validated");
});
