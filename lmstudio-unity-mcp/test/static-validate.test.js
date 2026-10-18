"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), os = require("node:os"), path = require("node:path");
const { analyzeSource } = require("../src/static-validate");
const { createRuntime } = require("../src/server");
const groups = [
  ["UNITY_EVENT_CLEANUP", [
    "void OnEnable(){ bus.Tick += Handle; } void OnDisable(){ bus.Tick -= Handle; }",
    "void Start(){ Changed += Handle; } void OnDestroy(){ Changed -= Handle; }",
    'void OnEnable(){ var s = "bus.Tick += Handle;"; }',
    "void OnEnable(){ /* bus.Tick += Handle; */ }",
  ], [
    "void OnEnable(){ bus.Tick += Handle; }",
    "void Awake(){ Changed += Handler; }",
    "void Start(){ bus.Tick += this.Handle; } void OnDestroy(){ bus.Tick -= Other; }",
    "void OnEnable(){ bus.Tick += Handle; } void OnDisable(){ other.Tick -= Handle; }",
  ], ["void OnEnable(){ Register(); }", "void OnEnable(){ bus.Tick += () => Use(); }"]],
  ["UNITY_ASYNC_LIFETIME", [
    "async Task Load(){ await Fetch(destroyCancellationToken); this.Use(); }",
    "async Task Load(){ await Fetch(); if (this == null) return; transform.position = p; }",
    "async Task Load(){ await Fetch(); token.ThrowIfCancellationRequested(); this.Use(); }",
    "async Task Load(){ this.Use(); await Fetch(); }",
  ], [
    "async Task Load(){ await Fetch(); this.Use(); }",
    "async Task Load(){ await Fetch(); transform.position = p; }",
    "async void Load(){ await Fetch(); gameObject.SetActive(true); }",
    "async Task Load(){ await Fetch(); GetComponent<Renderer>().enabled = true; }",
  ], ["async Task Load(){ await Fetch(); Use(); }", "Task Load(){ return Fetch(); }"]],
  ["UNITY_HOT_LOOP_LOOKUP", [
    "void Awake(){ GetComponent<Renderer>(); }", "void Update(){ cached.Run(); }",
    'void Update(){ var s = @"Find(\"\")"; }', "void FixedUpdate(){ /* GetComponent<T>(); */ }",
  ], [
    "void Update(){ GetComponent<Renderer>(); }", "void FixedUpdate(){ GameObject.Find(name); }",
    "void Update(){ GetComponentInChildren<Renderer>(); }", "void FixedUpdate(){ FindFirstObjectByType<Camera>(); }",
  ], ["void Update(){ ResolveComponent(); }", "void Update() => GetComponent<Renderer>();"]],
  ["UNITY_COROUTINE_ACTIVATION", [
    "void Start(){ StartCoroutine(Work()); }", "void OnEnable(){ StartCoroutine(Work()); }",
    "void Launch(){ if (!gameObject.activeInHierarchy) return; StartCoroutine(Work()); }",
    'void Launch(){ var s = "StartCoroutine(Work());"; }',
  ], [
    "void Launch(){ StartCoroutine(Work()); }", "void OnDisable(){ StartCoroutine(Work()); }",
    "void Awake(){ StartCoroutine(Work()); }", "public void Begin(){ this.StartCoroutine(Work()); }",
  ], ["void Launch(){ StartWork(); }", "void Launch() => StartCoroutine(Work());"]],
];
for (const [rule, good, bad, unknown] of groups) for (const [kind, cases] of [["normal", good], ["warning", bad], ["unresolved", unknown]])
  cases.forEach((source, index) => test(`${rule} ${kind} ${index + 1}`, () => {
    const matches = analyzeSource(`class Behaviour : MonoBehaviour { ${source} }`).filter(f => f.code === rule);
    assert.equal(matches.length > 0, kind === "warning");
    for (const f of matches) { assert.equal(f.blocking, false); assert.equal(f.metadata.analysisKind, "lexical"); assert.ok(f.metadata.limitations.length); }
  }));

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unity-static-"));
  for (const name of ["Assets", "Packages", "ProjectSettings"]) fs.mkdirSync(path.join(root, name));
  fs.writeFileSync(path.join(root, "Packages/manifest.json"), "{}");
  fs.writeFileSync(path.join(root, "ProjectSettings/ProjectVersion.txt"), "m_EditorVersion: 6000.3.14f1");
  const runtime = createRuntime({ UNITY_PROJECT_ROOT: root });
  t.after(() => { runtime.close(); fs.rmSync(root, { recursive: true, force: true }); });
  return { root, ...runtime };
}
test("event cleanup in another class cannot hide a warning", () => {
  const r = analyzeSource("class A { void OnEnable(){ Changed += H; } } class B { void OnDisable(){ Changed -= H; } }");
  assert.equal(r.length, 1);
});
test("lexical source lines ignore comment and string bait", () => {
  const r = analyzeSource('// GetComponent<T>();\nclass A {\nvoid Update(){ GetComponent<T>(); }\n}');
  assert.equal(r[0].line, 3);
});
test("raw and verbatim strings cannot create warnings", () => {
  assert.deepEqual(analyzeSource('class A { void Update(){ var x = """GetComponent<T>();"""; var y = @"Find(""x"")"; } }'), []);
});
test("static tool is available offline and warnings carry contracts", async t => {
  const f = fixture(t); fs.writeFileSync(path.join(f.root, "Assets/A.cs"), "class A { void Update(){ GetComponent<T>(); } }");
  const r = await f.call("unity_static_validate"); assert.equal(r.findings.length, 1); assert.equal(r.absenceVerified, false);
  assert.ok(r.findings[0].metadata.contractIds.includes("unity.policy.hot-loop"));
});
test("static tool does not widen Assets scope", async t => {
  const f = fixture(t); assert.equal((await f.call("unity_static_validate", { path: "Packages" })).errorCode, "path_denied");
});
test("static pages fit budget and cursor covers all findings", async t => {
  const f = fixture(t); fs.writeFileSync(path.join(f.root, "Assets/A.cs"), 'class A { void Update(){'+ 'GetComponent<T>();\n'.repeat(20)+'} }');
  let args = { byteBudget: 4096 }, count = 0;
  for (let i = 0; i < 30; i++) { const r = await f.call("unity_static_validate", args); assert.ok(Buffer.byteLength(JSON.stringify(r)) <= args.byteBudget); count += r.items.length; if (!r.nextCursor) break; args = r.suggestion.args; }
  assert.equal(count, 20);
});
test("static cursor detects changed source", async t => {
  const f=fixture(t), file=path.join(f.root,"Assets/A.cs"); fs.writeFileSync(file,'class A { void Update(){ GetComponent<T>(); Find(name); } }');
  const r=await f.call("unity_static_validate",{limit:1}); fs.appendFileSync(file,'// change');
  assert.equal((await f.call("unity_static_validate",{limit:1,cursor:r.nextCursor})).errorCode,"snapshot_changed");
});
test("filename search ignores directory and content matches", async t => {
  const f=fixture(t); fs.mkdirSync(path.join(f.root,"Assets/Needle")); fs.writeFileSync(path.join(f.root,"Assets/Needle/A.cs"),'Needle');
  fs.writeFileSync(path.join(f.root,"Assets/Needle.cs"),''); const r=await f.call("search_files",{query:"Needle",matchFileNames:true,content:true});
  assert.deepEqual(r.items.map(x=>x.path),["Assets/Needle.cs"]);
});
test("repeat failure is marked but a newly created path is read", async t => {
  const f=fixture(t), args={path:"Assets/A.cs"}; assert.equal((await f.call("read_file",args)).errorCode,"not_found");
  assert.equal((await f.call("read_file",args)).duplicate,true); fs.writeFileSync(path.join(f.root,args.path),'class A {}');
  assert.ok((await f.call("read_file",args)).text);
});
test("returned Editor permission errors include nextStep", async t => {
  const f=fixture(t), runtime=createRuntime({UNITY_PROJECT_ROOT:f.root,ALLOW_WRITE:"1"},{call:async()=>({errorCode:"permission_denied",status:"not_applied"})});
  t.after(()=>runtime.close()); assert.match((await runtime.call("unity_scene",{action:"create",operationId:"test-1234",name:"A"})).nextStep,/Editor/);
});

test("large lexical input is bounded and reports omitted findings",()=>{
 const r=analyzeSource('class A { void Update(){'+ 'GetComponent<T>();\n'.repeat(30000)+'}}');
 assert.equal(r.length,1000); assert.equal(r.incomplete,true); assert.equal(r.at(-1).line,1000);
});
