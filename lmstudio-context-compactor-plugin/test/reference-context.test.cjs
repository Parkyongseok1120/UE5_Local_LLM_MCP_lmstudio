"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { emptyReferenceState, ingestReferenceObservations, referenceSnapshot, REFERENCE_LIMITS } = require("../dist/reference-context");
const { renderReferenceData } = require("../dist/reference-rendering");
const { EvidenceManager } = require("../dist/evidence-manager");
const unreal = { engine: "unreal", source: "config", projectIdentity: "C:/Projects/Game/Game.uproject" };
const unity = { engine: "unity", source: "config", projectIdentity: "C:/Projects/Unity" };
const u = value => ({ canonicalProjectRoot: unity.projectIdentity, projectIdentity: "opaque-id", ...value });
const build = value => ({ ok: false, diagnostics: ["A.cpp(2): error C1: missing type"],
  project: { projectPath: unreal.projectIdentity, target: "GameEditor", platform: "Win64", configuration: "Development", engineAssociation: "5.7" }, ...value });
function pair(id, name, value, args = {}) {
  return [ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: { type: "function", id, name, arguments: args } }] }),
    ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id, content: typeof value === "string" ? value : JSON.stringify(value) }] })];
}
function history(messages = []) { const chat = Chat.empty(); messages.forEach(m => chat.append(m)); return chat; }
function fixture(scope = unreal) {
  const state = emptyReferenceState(); let serial = 0; const messages = [];
  return { state, messages,
    ingest(name, value, args = {}, id = "r" + (++serial), provider = scope.engine === "unity" ? "mcp/unity-tools" : "mcp/unreal-agent") {
      const batch = pair(id, name, value, args); messages.push(...batch);
      ingestReferenceObservations(state, batch, [{ name, pluginIdentifier: provider }], scope, "execution"); return batch;
    }, snapshot(current = messages) { return referenceSnapshot(state, history(current)); } };
}
const read = (path, text, value = {}) => u({ path, text, hash: "a".repeat(64), startLine: 1,
  endLine: text.split("\n").length, totalLines: text.split("\n").length, status: "observed", truncated: false, ...value });

test('version provenance and conflicts survive display omission and disconnect',()=>{
  const f=fixture(unity);
  f.ingest('unity_status',u({connection:'connected',editorSessionId:'s',domainGeneration:1,editorVersion:'6000.0.44f1'}));
  f.ingest('read_file',read('ProjectSettings/ProjectVersion.txt','m_EditorVersion: 6000.1.0f1'));
  for(let i=0;i<7;i++) f.ingest('read_file',read(`Assets/F${i}.cs`,'class F {}'));
  let s=f.snapshot(); assert.equal(s.applicability.versionConflict,true); assert.equal(s.applicability.engineVersion,undefined);
  assert.equal(s.applicability.versions.runtime,'6000.0.44f1');
  assert.equal(s.applicability.versions.saved_project,'6000.1.0f1');
  f.ingest('unity_status',u({connection:'disconnected'})); s=f.snapshot();
  assert.equal(s.applicability.versions.runtime,undefined);
  // Editor closed: the saved ProjectVersion.txt is still an observation, reported as the project file.
  assert.equal(s.applicability.versions.saved_project,'6000.1.0f1');
  assert.equal(s.applicability.engineVersion,'6000.1.0f1'); assert.equal(s.applicability.engineVersionSource,'project_file');
  assert.equal(s.applicability.engineVersionNote,'project_file_editor_not_observed'); assert.equal(s.applicability.versionConflict,false);
});

test('an offline Editor still lets the saved project version select Unity guidance; a live Editor that disagrees is a conflict',()=>{
  const f=fixture(unity);
  f.ingest('unity_status',u({connection:'disconnected'}));
  f.ingest('read_file',read('ProjectSettings/ProjectVersion.txt','m_EditorVersion: 6000.0.44f1'));
  let s=f.snapshot();
  assert.equal(s.applicability.engineVersion,'6000.0.44f1'); assert.equal(s.applicability.engineVersionSource,'project_file');
  f.ingest('unity_status',u({connection:'connected',editorSessionId:'s',domainGeneration:1,editorVersion:'6000.3.1f1'}));
  s=f.snapshot();
  assert.equal(s.applicability.versionConflict,true); assert.equal(s.applicability.engineVersion,undefined);
  assert.equal(s.applicability.versions.runtime,'6000.3.1f1'); assert.equal(s.applicability.versions.saved_project,'6000.0.44f1');
});

test('descriptor observation invalidates engine applicability and same-batch order cannot restore it',()=>{
  const local={ok:true,canonicalProject:unreal.projectIdentity,sourceMode:'engine_local',
    observedEngineIdentity:{status:'observed',engineRoot:'C:/UE',version:'5.7.4',hashScope:'file',sha256:'b'.repeat(64)}};
  const descriptor={ok:true,canonicalProject:unreal.projectIdentity,path:'project://Game.uproject',
    content:'{"EngineAssociation":"5.8"}',sha256:'c'.repeat(64),startLine:1,endLine:1,totalLines:1};
  const f=fixture(); f.ingest('unreal_symbol_lookup',local,{},'local','mcp/unreal-rag');
  f.ingest('read_file',descriptor); assert.equal(f.snapshot().applicability.engineVersion,undefined);
  assert.ok(f.snapshot().items.some(i=>i.data.observedEngineVersion==='5.7.4' && i.data.currentApplicability));
  f.ingest('unreal_symbol_lookup',local,{},'new','mcp/unreal-rag'); assert.equal(f.snapshot().applicability.engineVersion,'5.7.4');
  for(const reverse of [false,true]) {
    const state=emptyReferenceState(), a=pair('a','unreal_symbol_lookup',local),b=pair('b','read_file',descriptor);
    const messages=[a[0],b[0],...(reverse?[b[1],a[1]]:[a[1],b[1]])];
    ingestReferenceObservations(state,messages,[{name:'unreal_symbol_lookup',pluginIdentifier:'mcp/unreal-rag'},
      {name:'read_file',pluginIdentifier:'mcp/unreal-agent'}],unreal,'e');
    assert.equal(referenceSnapshot(state,history(messages)).applicability.engineVersion,undefined);
  }
});

test('reference project observations never change execution scope and preserve shared/default distinction',()=>{
  const {referenceGuidanceScope}=require('../dist/reference-context');
  const scope={engine:'unreal',source:'available_tools',projectIdentity:''}; const saved=JSON.stringify(scope),f=fixture(scope);
  f.ingest('get_active_project',{ok:true,activeProject:unreal.projectIdentity});
  assert.equal(referenceGuidanceScope(scope,f.snapshot()).engine,'unknown');
  f.ingest('unreal_get_active_project',{ok:true,activeProject:unreal.projectIdentity,projectBindingStatus:'bound'}, {},'bound','mcp/unreal-rag');
  assert.equal(referenceGuidanceScope(scope,f.snapshot()).engine,'unreal');
  f.ingest('unreal_get_active_project',{ok:true,activeProject:unreal.projectIdentity,projectBindingStatus:'stale'}, {},'stale','mcp/unreal-rag');
  assert.equal(referenceGuidanceScope(scope,f.snapshot()).engine,'unknown');
  f.ingest('build_unreal_project',build(),{project:unreal.projectIdentity});
  f.ingest('unreal_get_active_project',{ok:true,activeProject:'C:/Other/B.uproject',projectBindingStatus:'bound'}, {},'other','mcp/unreal-rag');
  assert.equal(f.snapshot().projectObservation.project,unreal.projectIdentity);
  assert.equal(JSON.stringify(scope),saved);
  const fixed=fixture(); fixed.ingest('build_unreal_project',build(),{project:'C:/Other/B.uproject'});
  assert.equal(fixed.snapshot().items.length,1,'post-binding actual target overrides pre-binding model args');
});

test('new implicit default selection expires old default-target applicability and diagnostics',()=>{
  const scope={engine:'unreal',source:'available_tools',projectIdentity:''},f=fixture(scope);
  f.ingest('build_unreal_project',build());
  f.ingest('unreal_get_active_project',{ok:true,activeProject:null,projectBindingStatus:'unbound'},{},'clear','mcp/unreal-rag');
  assert.equal(f.snapshot().projectObservation.usable,false);
  assert.equal(f.snapshot().autoSignals.length,0);
  f.ingest('build_unreal_project',build({project:{projectPath:'C:/Other/B.uproject'},diagnostics:[],ok:true}));
  assert.equal(f.snapshot().projectObservation.project,'C:/Other/B.uproject');
  assert.equal(f.snapshot().autoSignals.some(s=>s.signal==='diagnostic_present'),false);
});

test('macro observations are advisory signals only while the actual body is available',()=>{
  const f=fixture(unity);f.ingest('read_file',read('Assets/A.cs','[SerializeField] int x;'));
  assert.ok(f.snapshot().autoSignals.some(s=>s.signal==='api_contract_observed'));
  assert.equal(f.snapshot([]).autoSignals.some(s=>s.signal==='api_contract_observed'),false);
  assert.equal(f.snapshot().items[0].data.macroMissing,undefined);
});

test('package manifest and lock observations in the same batch never choose an order',()=>{
  const lock=JSON.stringify({dependencies:{'com.unity.netcode.gameobjects':{version:'2.7.0'}}});
  for(const reverse of [false,true]) {
    const state=emptyReferenceState(),a=pair('lock','read_file',read('Packages/packages-lock.json',lock)),
      b=pair('manifest','read_file',read('Packages/manifest.json','{}'));
    const messages=[a[0],b[0],...(reverse?[b[1],a[1]]:[a[1],b[1]])];
    ingestReferenceObservations(state,messages,[{name:'read_file',pluginIdentifier:'mcp/unity-tools'}],unity,'e');
    assert.deepEqual(referenceSnapshot(state,history(messages)).applicability.packages,{});
  }
});

test('only the project package manifest invalidates resolved package applicability', () => {
  const f = fixture(unity);
  const lock = JSON.stringify({ dependencies: { 'com.unity.netcode.gameobjects': { version: '2.7.0' } } });
  f.ingest('read_file', read('Packages/packages-lock.json', lock));
  f.ingest('read_file', read('Assets/Templates/Packages/manifest.json', '{}'));
  assert.equal(f.snapshot().applicability.packages['com.unity.netcode.gameobjects'], '2.7.0');

  f.ingest('read_file', read('Packages/manifest.json', '{}'));
  assert.deepEqual(f.snapshot().applicability.packages, {});
});

test('target mismatch pauses only reference applicability until a later matching result',()=>{
  const {referenceGuidanceScope}=require('../dist/reference-context');const f=fixture();
  f.ingest('build_unreal_project',build({project:{projectPath:'C:/Other/B.uproject'}}));
  assert.equal(referenceGuidanceScope(unreal,f.snapshot()).reason,'returned_target_mismatch');
  assert.equal(unreal.projectIdentity,'C:/Projects/Game/Game.uproject');
  f.ingest('build_unreal_project',build());
  assert.equal(referenceGuidanceScope(unreal,f.snapshot()).engine,'unreal');
});

test('reviewed RAG read authority stays in parity with the provider catalogue',()=>{
  const fs=require('node:fs'),path=require('node:path');
  const contract=fs.readFileSync(path.join(__dirname,'../../scripts/direct_rag_contract.py'),'utf8');
  const names=[...contract.match(/DIRECT_RAG_TOOL_NAMES:.*?= \(([\s\S]*?)\)/)[1].matchAll(/"(unreal_[^"]+)"/g)].map(m=>m[1]);
  const mutations=[...contract.match(/DIRECT_RAG_MUTATING_TOOL_NAMES = frozenset\(\{([^}]+)\}/)[1].matchAll(/"([^"]+)"/g)].map(m=>m[1]);
  const {UNREAL_RAG_OBSERVATION_TOOLS}=require('../dist/tool-capability-registry');
  assert.deepEqual([...UNREAL_RAG_OBSERVATION_TOOLS].sort(),names.filter(n=>!mutations.includes(n)).sort());
});

test("Unreal applicability uses observed local identity; unrelated Unity files and later failed binding cannot assert it", () => {
  const f = fixture();
  const local = { ok: true, canonicalProject: unreal.projectIdentity, sourceMode: "engine_local",
    observedEngineIdentity: { status: "observed", engineRoot: "C:/UE_5.8", version: "5.8.0", hashScope: "file", sha256: "b".repeat(64) } };
  f.ingest("unreal_symbol_lookup", local, {}, "local", "mcp/unreal-rag");
  assert.equal(f.snapshot().applicability.engineVersion, "5.8.0");
  f.ingest("read_file", { ok: true, canonicalProject: unreal.projectIdentity, path: "project://ProjectSettings/ProjectVersion.txt",
    text: "m_EditorVersion: 5.7.1f1", sha256: "a".repeat(64), startLine: 1, endLine: 1, totalLines: 1 });
  assert.equal(f.snapshot().applicability.engineVersion, "5.8.0");
  f.ingest("unreal_symbol_lookup", { ...local, ok: false, errorCode: "ENGINE_SOURCE_VERSION_MISMATCH" }, {}, "failed", "mcp/unreal-rag");
  assert.equal(f.snapshot().applicability.engineVersion, undefined);
});

test("Auto diagnostic signals expire when a later Unity compilation observation supersedes them", () => {
  const f = fixture(unity);
  const common = { editorSessionId: "session", domainGeneration: 1 };
  f.ingest("unity_logs", u({ ...common, compilationId: "c1", compilationOutcome: "failed", items: [
    { ...common, source: "compiler", compilationId: "c1", message: "OLD_ERROR", severity: "error" }] }), { action: "read" });
  assert.ok(f.snapshot().autoSignals.some(s => s.signal === "diagnostic_present"));
  f.ingest("unity_status", u({ ...common, compilationId: "c2", compiling: false }));
  assert.equal(f.snapshot().autoSignals.some(s => s.signal === "diagnostic_present"), false);
});

test("RAG observation classification grants only the exact published read tools", () => {
  const { isObservationOnlyToolCall } = require("../dist/tool-capability-registry");
  for (const name of ["unreal_symbol_lookup", "unreal_rag_search"])
    assert.equal(isObservationOnlyToolCall({ name, pluginIdentifier: "mcp/unreal-rag" }, { name, arguments: {} }), true);
  for (const [name, provider] of [["unreal_rag_refresh", "mcp/unreal-rag"], ["unreal_set_active_project", "mcp/unreal-rag"],
    ["unreal_symbol_lookup", "mcp/unknown"]])
    assert.equal(isObservationOnlyToolCall({ name, pluginIdentifier: provider }, { name, arguments: {} }), false);
});

test("returned failed build remains failed, bounded and scoped, without treating association as actual version", () => {
  const f = fixture(); f.ingest("build_unreal_project", build({ diagnostics: Array.from({ length: 30 }, (_, i) => "error " + i) }));
  const snapshot = f.snapshot(), data = snapshot.items[0].data;
  assert.equal(data.delivery, "returned"); assert.equal(data.operationStatus, "failed");
  assert.equal(data.diagnostics.length, 12); assert.equal(data.coverage.omittedHere, 18);
  assert.equal(snapshot.applicability.engineVersion, undefined);
  assert.equal(data.proof.sourceCompilationVerified, "unknown");
});

test("up-to-date is execution proof only, a timeout is not a completed failed compile", () => {
  const f = fixture(); f.ingest("build_unreal_project", build({ ok: true, diagnostics: [], proof: { upToDate: true, actionsExecuted: 0 } }));
  assert.equal(f.snapshot().items[0].data.proof.actionsExecuted, 0);
  assert.equal(f.snapshot().items[0].data.operationStatus, "completed");
  f.ingest("build_unreal_project", build({ timedOut: true }));
  assert.equal(f.snapshot().items[0].data.operationStatus, "outcome_unknown");
});

test("Unity accepted compilation and RPC uncertainty never become build success", () => {
  const f = fixture(unity);
  f.ingest("unity_editor", u({ status: "accepted", compilationIdAtRequest: "old", editorSessionId: "s", domainGeneration: 1 }), { action: "compile" });
  let data = f.snapshot().items[0].data;
  assert.equal(data.operationStatus, "accepted"); assert.equal(data.compilationIdAtRequest, "old");
  assert.equal(data.compilationId, undefined); assert.equal(data.playerBuildVerification, "unknown");
  f.ingest("unity_editor", u({ status: "outcome_unknown", errorCode: "rpc_timeout" }), { action: "compile" });
  data = f.snapshot().items[0].data;
  assert.equal(data.delivery, "transport_error"); assert.equal(data.operationStatus, "outcome_unknown");
});

test("Unity log rows cannot borrow the current compilation outcome or fabricate a domain generation", () => {
  const f = fixture(unity);
  f.ingest("unity_logs", u({ editorSessionId: "s", domainGeneration: 3, compilationId: "current",
    compilationOutcome: "completed_without_observed_errors", collectionScope: "subscribed_events_only", droppedCount: 5, truncated: true,
    items: [{ source: "compiler", editorSessionId: "s", compilationId: "old", message: "OLD" },
      { source: "compiler", editorSessionId: "s", compilationId: "current", message: "CURRENT", severity: "Warning" }] }));
  const data = f.snapshot().items[0].data;
  assert.deepEqual(data.diagnostics.map(d => d.message), ["CURRENT"]);
  assert.equal(data.diagnostics[0].domainGeneration, "unknown");
  assert.equal(data.coverage.excludedRows, 1); assert.equal(data.coverage.droppedCount, 5);
  assert.equal(data.operationStatus, "completed"); assert.equal(data.errorsObserved, false); assert.equal(data.sourceAssemblyVerification, "unknown");
});

test("a new domain/session invalidates runtime observations while retaining source observations", () => {
  const f = fixture(unity); f.ingest("read_file", read("Assets/A.cs", "code"));
  f.ingest("unity_editor", u({ status: "accepted", editorSessionId: "s", domainGeneration: 1 }), { action: "compile" });
  f.ingest("unity_status", u({ editorSessionId: "s", domainGeneration: 2, editorVersion: "6000.0.44f1" }));
  assert.equal(f.snapshot().items.some(i => i.kind === "operation"), false);
  assert.equal(f.snapshot().items.some(i => i.kind === "file"), true);
  f.ingest("unity_status", u({ connection: "disconnected" }));
  assert.equal(f.snapshot().applicability.engineVersion, undefined);
});

test("actual project, provider and unique tool binding are required; proposed project is not proof", () => {
  const f = fixture();
  f.ingest("build_unreal_project", build({ project: { projectPath: "C:/Other/Other.uproject" } }), { project: unreal.projectIdentity });
  f.ingest("build_unreal_project", build(), {}, "foreign", "mcp/other");
  f.ingest("build_unreal_project", build({ project: {} }));
  assert.equal(f.snapshot().items.length, 0);
  const messages = pair("collision", "build_unreal_project", build());
  ingestReferenceObservations(f.state, messages, [{ name: "build_unreal_project", pluginIdentifier: "mcp/unreal-agent" },
    { name: "build_unreal_project", pluginIdentifier: "mcp/other" }], unreal, "x");
  assert.equal(f.snapshot().items.length, 0);
});

test("Windows path equivalence works but neighboring descriptors and outside files do not match", () => {
  const f = fixture(); f.ingest("build_unreal_project", build({ project: { projectPath: "c:\\PROJECTS\\GAME\\Game.uproject" } }));
  assert.equal(f.snapshot().items.length, 1);
  f.ingest("build_unreal_project", build({ project: { projectPath: "C:/Projects/Game/Other.uproject" } }));
  f.ingest("read_file", { canonicalProject: unreal.projectIdentity, path: "../Other.h", content: "no", sha256: "b" });
  assert.equal(f.snapshot().items.length, 1);
});

test("unmatched, duplicate, replayed and malformed results do not invent relationships", () => {
  const f = fixture(); const batch = f.ingest("build_unreal_project", build(), {}, "id");
  f.ingest("build_unreal_project", build(), {}, "id");
  assert.equal(f.snapshot().items.length, 0);
  ingestReferenceObservations(f.state, [batch[1]], [{ name: "build_unreal_project", pluginIdentifier: "mcp/unreal-agent" }], unreal, "x");
  f.ingest("build_unreal_project", "{broken");
  assert.equal(f.snapshot().items.length, 0);
  const duplicate = pair("dup", "build_unreal_project", build()); duplicate.push(duplicate[1]);
  ingestReferenceObservations(f.state, duplicate, [{ name: "build_unreal_project", pluginIdentifier: "mcp/unreal-agent" }], unreal, "x");
  assert.equal(f.snapshot().items.length, 0);
});

test("archive projections and explicit repeated results are not fresh observations", () => {
  const f = fixture();
  for (const extra of [{ kind: "archived_tool_result_projection" }, { kind: "historical_evidence_range" },
    { kind: "no_new_information" }, { status: "no_new_information", repeatReceipt: "receipt" }]) f.ingest("build_unreal_project", build(extra));
  assert.equal(f.snapshot().items.length, 0);
});

test("recurrence requires different runs of the same target; later first error is not a resolution claim", () => {
  const f = fixture(); f.ingest("build_unreal_project", build()); f.ingest("build_unreal_project", build());
  assert.ok(f.snapshot().signals.includes("diagnostic_recurred"));
  assert.equal(f.snapshot().items[0].data.recurrence.mutationCausality, "unknown");
  f.ingest("build_unreal_project", build({ diagnostics: ["NEW ERROR"] }));
  assert.equal(f.snapshot().signals.includes("diagnostic_recurred"), false);
  assert.equal(f.snapshot().items.length, 1);
  assert.equal(f.snapshot().items[0].data.previousErrorResolved, undefined);
  f.ingest("build_unreal_project", build({ ok: true, diagnostics: [] }));
  assert.equal(f.snapshot().signals.includes("diagnostic_present"), false);
});

test("two builds in one parallel request block are not an ordered retry", () => {
  const f = fixture(), a = pair("a", "build_unreal_project", build()), b = pair("b", "build_unreal_project", build());
  const request = ChatMessage.from({ role: "assistant", content: [
    ...a[0].getToolCallRequests().map(toolCallRequest => ({ type: "toolCallRequest", toolCallRequest })),
    ...b[0].getToolCallRequests().map(toolCallRequest => ({ type: "toolCallRequest", toolCallRequest }))] });
  const messages = [request, b[1], a[1]];
  ingestReferenceObservations(f.state, messages, [{ name: "build_unreal_project", pluginIdentifier: "mcp/unreal-agent" }], unreal, "x");
  assert.equal(f.snapshot(messages).signals.includes("diagnostic_recurred"), false);
});

test("source availability follows current exact raw input, not past exposure or archive projection", () => {
  const f = fixture(unity); f.ingest("read_file", read("Assets/A.cs", "class A {}"));
  assert.equal(f.snapshot().items[0].data.sourceBodyInCurrentInput, true);
  const compacted = pair("r1", "read_file", { kind: "archived_tool_result_projection", archiveRef: { evidenceId: "archive" } });
  assert.equal(f.snapshot(compacted).items[0].data.sourceBodyInCurrentInput, false);
  assert.equal(f.snapshot([]).signals.includes("source_body_unavailable"), true);
  assert.equal(f.snapshot([]).items[0].data.archiveRef, undefined);
});

test("complete observed Unity settings enable narrow metadata; a partial read cannot", () => {
  const f = fixture(unity);
  const lock = JSON.stringify({ dependencies: { "com.unity.netcode.gameobjects": { version: "2.7.0" } } });
  f.ingest("read_file", read("Packages/packages-lock.json", lock));
  f.ingest("read_file", read("ProjectSettings/ProjectVersion.txt", "m_EditorVersion: 6000.0.44f1"));
  assert.equal(f.snapshot().applicability.packages["com.unity.netcode.gameobjects"], "2.7.0");
  assert.equal(f.snapshot().applicability.engineVersion, "6000.0.44f1");
  f.ingest("read_file", read("Packages/packages-lock.json", lock, { truncated: true, totalLines: 9 }));
  assert.deepEqual(f.snapshot().applicability.packages, {});
});

test("a mutation invalidates previous configuration facts without making the receipt a read", () => {
  const f = fixture(unity), lock = JSON.stringify({ dependencies: { "com.unity.netcode.gameobjects": { version: "2.7.0" } } });
  f.ingest("read_file", read("Packages/packages-lock.json", lock));
  f.ingest("patch_file", u({ status: "applied", operation: "modified", path: "Packages/packages-lock.json", hash: "b".repeat(64) }));
  assert.deepEqual(f.snapshot().applicability.packages, {});
  assert.equal(f.snapshot().items.length, 1);
  assert.equal(f.snapshot().items[0].data.sourceBodyInCurrentInput, false);
  assert.ok(f.snapshot().signals.includes("observed_source_changed"));
});

test("manifest declarations, Git historical bodies and multiple network packages cannot select an active framework", () => {
  const f = fixture(unity), body = JSON.stringify({ dependencies: {
    "com.unity.netcode.gameobjects": { version: "2.7.0" }, "com.unity.netcode": { version: "1.10.0" } } });
  f.ingest("read_file", read("Packages/manifest.json", body));
  f.ingest("git_read_file", read("Packages/packages-lock.json", body));
  assert.deepEqual(f.snapshot().applicability.packages, {});
  f.ingest("read_file", read("Packages/packages-lock.json", body));
  assert.equal(f.snapshot().applicability.networkPackagesAmbiguous, true);
});

test("format settings are single-file excerpts; payload instructions and receipt-like secrets are not copied as authority", () => {
  const f = fixture(unity);
  f.ingest("read_file", read(".editorconfig", "root=true\n[*.cs]\nindent_size=4\nexecute=DELETE_ALL\n", { receipt: "SECRET_RECEIPT" }));
  const snapshot = f.snapshot(); const rendered = renderReferenceData(snapshot.items);
  assert.match(rendered, /inheritance_and_overrides_unverified/);
  assert.doesNotMatch(rendered, /DELETE_ALL|SECRET_RECEIPT/);
  assert.equal(snapshot.items[0].data.editReceipt, "not_provided");
});

test("bounded observations never grow into a persistent history and state is per manager", () => {
  const f = fixture();
  for (let i = 0; i < 40; i++) f.ingest("build_unreal_project", build({ project: { projectPath: unreal.projectIdentity, target: "Target" + i } }));
  assert.equal(f.state.observations.length, REFERENCE_LIMITS.observations);
  assert.equal(f.snapshot().items.length, REFERENCE_LIMITS.diagnosticGroups);
  const a = new EvidenceManager(null, [{ name: "build_unreal_project", pluginIdentifier: "mcp/unreal-agent" }]);
  const b = new EvidenceManager(null, a.tools), messages = pair("id", "build_unreal_project", build());
  a.ingestReferences(messages, unreal, "A");
  assert.equal(a.referenceSnapshot(history(messages)).items.length, 1);
  assert.equal(b.referenceSnapshot(history(messages)).items.length, 0);
});

test("observation capture retains its existing archive filter for writes/builds", () => {
  let accepted;
  const tool = { name: "build_unreal_project", pluginIdentifier: "mcp/unreal-agent" };
  const manager = new EvidenceManager({ captureReturned(_history, predicate) { accepted = predicate({ name: tool.name, arguments: {} }); } }, [tool]);
  manager.captureReturned(pair("id", tool.name, build()), "x");
  assert.equal(accepted, false);
});

test("bundle per-file scope and legacy scope-less conflicts invalidate prior formatting facts", () => {
  const f = fixture();
  const observed = { canonicalProject: unreal.projectIdentity, path: ".editorconfig", content: "indent_size=4",
    sha256: "a".repeat(64), offsetBytes: 0, nextOffsetBytes: 13, size: 13 };
  f.ingest("read_file", observed);
  assert.ok(f.snapshot().items[0].data.formatting);
  f.ingest("apply_edit_bundle", { ok: true, operation: "bundle_applied", files: [{ canonicalProject: unreal.projectIdentity,
    path: ".editorconfig", sha256: "b".repeat(64) }] });
  assert.equal(f.snapshot().items[0].data.hash, "b".repeat(64));
  assert.equal(f.snapshot().items[0].data.formatting, undefined);
  f.ingest("read_file", observed);
  f.ingest("replace_in_file", { ok: false, errorCode: "FILE_VERSION_CONFLICT" });
  assert.equal(f.snapshot().items.length, 0);
});

test("a proven Unity conflict invalidates package data even without a new hash", () => {
  const f = fixture(unity);
  f.ingest("read_file", read("Packages/packages-lock.json", JSON.stringify({ dependencies: { "com.unity.netcode.gameobjects": { version: "2.7.0" } } })));
  f.ingest("patch_file", u({ status: "not_applied", path: "Packages/packages-lock.json", errorCode: "receipt_conflict", observationState: "conflict_observed" }));
  assert.deepEqual(f.snapshot().applicability.packages, {});
  assert.equal(f.snapshot().items[0].data.observationState, "conflict_observed");
});

test("parallel file read/mutation has no authoritative order in either result ordering", () => {
  for (const reverse of [false, true]) {
    const f = fixture(unity);
    const a = pair("read", "read_file", read("Packages/packages-lock.json", '{"dependencies":{"com.unity.netcode.gameobjects":{"version":"2.7.0"}}}'));
    const b = pair("write", "patch_file", u({ path: "Packages/packages-lock.json", status: "applied", operation: "modified", hash: "b".repeat(64) }));
    const request = ChatMessage.from({ role: "assistant", content: [a[0], b[0]].flatMap(m => m.getToolCallRequests())
      .map(toolCallRequest => ({ type: "toolCallRequest", toolCallRequest })) });
    const batch = [request, ...(reverse ? [b[1], a[1]] : [a[1], b[1]])];
    ingestReferenceObservations(f.state, batch, ["read_file", "patch_file"].map(name => ({ name, pluginIdentifier: "mcp/unity-tools" })), unity, "x");
    assert.deepEqual(f.snapshot(batch).applicability.packages, {});
    assert.equal(f.snapshot(batch).items.length, 0);
  }
});

test("later compilation status supersedes the pending signal without claiming the request succeeded", () => {
  const f = fixture(unity), session = { editorSessionId: "s", domainGeneration: 1 };
  f.ingest("unity_editor", u({ ...session, status: "accepted", compilationIdAtRequest: "old" }), { action: "compile" });
  assert.ok(f.snapshot().signals.includes("compilation_pending"));
  f.ingest("unity_status", u({ ...session, compiling: true, compilationId: "new" }));
  f.ingest("unity_logs", u({ ...session, compilationId: "new", compilationOutcome: "completed_without_observed_errors", items: [] }));
  const snapshot = f.snapshot();
  assert.equal(snapshot.signals.includes("compilation_pending"), false);
  const operation = snapshot.items.find(i => i.kind === "operation");
  assert.equal(operation.data.operationStatus, "accepted");
  assert.equal(operation.data.currentCompilationStatus, "not_inferred_from_prior_acceptance_or_status");
});

function parallel(a, b, reverse = false) {
  return [ChatMessage.from({ role: "assistant", content: [a[0], b[0]].flatMap(m => m.getToolCallRequests())
    .map(toolCallRequest => ({ type: "toolCallRequest", toolCallRequest })) }), ...(reverse ? [b[1], a[1]] : [a[1], b[1]])];
}
test("target-unknown partial failure/legacy conflict poisons only that batch's file applicability in both orders", () => {
  for (const failure of [u({ status: "partially_applied", errorCode: "io_error" }), { errorCode: "receipt_conflict" },
    u({ errorCode: "receipt_conflict" })]) {
    for (const reverse of [false, true]) {
      const f = fixture(unity), body = '{"dependencies":{"com.unity.netcode.gameobjects":{"version":"2.7.0"}}}';
      const observed = read("Packages/packages-lock.json", body);
      f.ingest("read_file", observed);
      const batch = parallel(pair("failure", "patch_file", failure), pair("parallel-read", "read_file", observed), reverse);
      ingestReferenceObservations(f.state, batch, ["patch_file", "read_file"].map(name => ({ name, pluginIdentifier: "mcp/unity-tools" })), unity, "x");
      assert.deepEqual(f.snapshot(batch).applicability.packages, {});
      f.ingest("read_file", observed);
      assert.equal(f.snapshot().applicability.packages["com.unity.netcode.gameobjects"], "2.7.0");
    }
  }
});
test("ambiguous same-batch Unity sessions do not choose a current version by arrival order", () => {
  for (const reverse of [false, true]) {
    const f = fixture(unity);
    const batch = parallel(pair("status1", "unity_status", u({ editorSessionId: "s", domainGeneration: 1, editorVersion: "6000.0.1f1" })),
      pair("status2", "unity_status", u({ editorSessionId: "s", domainGeneration: 2, editorVersion: "6000.0.2f1" })), reverse);
    ingestReferenceObservations(f.state, batch, [{ name: "unity_status", pluginIdentifier: "mcp/unity-tools" }], unity, "x");
    assert.equal(f.snapshot(batch).applicability.engineVersion, undefined);
    f.ingest("unity_status", u({ editorSessionId: "s", domainGeneration: 2, editorVersion: "6000.0.2f1" }));
    assert.equal(f.snapshot().applicability.engineVersion, "6000.0.2f1");
  }
});
test("Unreal rollback incomplete removes old file facts until a later real read", () => {
  const f = fixture(); const observed = { canonicalProject: unreal.projectIdentity, path: ".editorconfig", content: "indent_size=4",
    sha256: "a".repeat(64), startLine: 1, endLine: 1, totalLines: 1 };
  f.ingest("read_file", observed);
  f.ingest("apply_edit_bundle", { ok: false, errorCode: "ROLLBACK_INCOMPLETE", details: { rollback: [] } });
  assert.equal(f.snapshot().items.length, 0);
  f.ingest("read_file", observed);
  assert.ok(f.snapshot().items[0].data.formatting);
});

test("static validation engine identity supplies Unreal applicability only when Build.version was observed", () => {
  const f = fixture();
  const target = { ok: true, skipped: false, projectRoot: "C:/Projects/Game" };
  const identity = (extra = {}) => ({ status: "observed", engine: "unreal", source: "build_version_file", engineRoot: "C:/UE_5.3",
    version: "5.3.2", hashScope: "file", sha256: "c".repeat(64), ...extra });
  const validate = (engineIdentity, id) => f.ingest("static_validate_project", { ...target, analysis: { engineIdentity } }, {}, id);
  validate({ status: "unknown", reason: "no engine" }, "s1");
  assert.equal(f.snapshot().applicability.engineVersion, undefined);
  validate(identity({ source: "folder_name_inferred" }), "s2");
  assert.equal(f.snapshot().applicability.engineVersion, undefined);
  validate(identity(), "s3");
  const applicability = f.snapshot().applicability;
  assert.equal(applicability.engineVersion, "5.3.2");
  assert.equal(applicability.engineVersionSource, "installed_engine_source");
});

test("the newest engine-source observation, lookup or static validation, defines Unreal applicability", () => {
  const f = fixture();
  f.ingest("unreal_symbol_lookup", { ok: true, canonicalProject: unreal.projectIdentity, sourceMode: "engine_local",
    observedEngineIdentity: { status: "observed", engineRoot: "C:/UE_5.8", version: "5.8.0", hashScope: "file", sha256: "b".repeat(64) } }, {}, "l1", "mcp/unreal-rag");
  f.ingest("static_validate_project", { ok: true, skipped: false, projectRoot: "C:/Projects/Game", analysis: { engineIdentity: {
    status: "observed", source: "build_version_file", engineRoot: "C:/UE_5.3", version: "5.3.2", hashScope: "file", sha256: "c".repeat(64) } } }, {}, "s1");
  assert.equal(f.snapshot().applicability.engineVersion, "5.3.2");
});

test("numbered reads still yield version and package metadata from the plain file text", () => {
  const f = fixture(unity);
  f.ingest("read_file", read("ProjectSettings/ProjectVersion.txt", "1\tm_EditorVersion: 6000.0.44f1\n2\tm_Other: 1",
    { lineNumbers: true, text: "1\tm_EditorVersion: 6000.0.44f1\n2\tm_Other: 1" }));
  assert.equal(f.snapshot().applicability.engineVersion, "6000.0.44f1");
  const packages = JSON.stringify({ dependencies: { "com.unity.netcode.gameobjects": { version: "2.7.1" } } }, null, 1);
  const numberedLock = packages.split("\n").map((line, index) => `${index + 1}\t${line}`).join("\n");
  f.ingest("read_file", read("Packages/packages-lock.json", numberedLock, { lineNumbers: true, text: numberedLock }));
  assert.equal(f.snapshot().applicability.packages["com.unity.netcode.gameobjects"], "2.7.1");
});

test("Unity tests retain failed verdict despite completed operation, and symbols retain scoped index facts",()=>{
 const f=fixture(unity);
 f.ingest("unity_tests",u({status:"completed",operationId:"tests-1",testStatus:"Failed",passed:2,failed:1,mode:"EditMode"}),{action:"status",operationId:"tests-1"});
 let item=f.snapshot().items.find(i=>i.data.operationKind==="unity_tests"); assert.equal(item.data.operationStatus,"completed"); assert.equal(item.data.verification,"not_proven_passed"); assert.equal(item.data.failed,1);
 f.ingest("unity_symbols",u({status:"observed",indexVersion:"i1",indexSource:"persisted",total:10,items:[{name:"A"}],nextOffset:1,searchedAssemblies:["Game"]}),{action:"find"});
 item=f.snapshot().items.find(i=>i.data.operationKind==="unity_symbols"); assert.equal(item.data.coverage.completeness,"partial"); assert.equal(item.data.absenceVerified,false); assert.equal(item.data.indexSource,"persisted");
 f.ingest("unity_find",u({status:"observed",editorSessionId:"s",domainGeneration:1,total:2,items:[{name:"A"}],nextCursor:"next"}),{kind:"objects"});
 item=f.snapshot().items.find(i=>i.data.operationKind==="unity_find"); assert.equal(item.data.coverage.completeness,"partial");
});

test("Unity symbol source retains real returned file coverage",()=>{
 const f=fixture(unity); f.ingest("unity_symbols",read("Assets/A.cs","class A {}"),{action:"source",indexVersion:"i1",symbolId:"A"});
 assert.ok(f.snapshot().items.some(i=>i.kind==="file" && i.data.hash==="a".repeat(64)));
});
