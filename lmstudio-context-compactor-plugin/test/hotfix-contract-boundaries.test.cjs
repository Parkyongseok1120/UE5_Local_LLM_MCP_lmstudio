"use strict";
// R0.5 boundary regressions from the 2026-10-31 plan review: host invalidation
// idempotency, storage-budget rejection versus clear, note store round-trip,
// prune isolation, and registered host frames in output/history filters.
const test=require("node:test"),assert=require("node:assert/strict");
const fs=require("node:fs"),os=require("node:os"),path=require("node:path");
const notes=require("../src/continuity-model-notes");
const contract=require("../src/internal-data-contract");
const {stripInternalDataEcho,InternalDataOutputFilter}=require("../dist/internal-data-output");
const {mergeActiveNote}=require("../dist/architecture-round");
const {composeModelHistory}=require("../dist/context-manager");
const {Chat,configFor}=require("./helpers/v4.cjs");
const {binding,evidence,populated,renderArchitectureContext}=require("./helpers/architecture.cjs");

const fingerprint="a".repeat(64),scope={objectiveFingerprint:fingerprint};
const general=()=>notes.resolveNoteUpdate({decisions:[{statement:"가".repeat(200),rationale:"나".repeat(200)},
  {statement:"라".repeat(200),rationale:"마".repeat(200)}],openQuestions:[{question:"다".repeat(200)}]},fingerprint,[]).note;
const repeat=(c,n)=>c.repeat(n);
const decision=(id,c,extra=1)=>({id,subject:repeat(c,110),state:"selected",currentOwner:repeat(c,110),requiredLifetime:repeat(c,90),
  sharingScope:repeat(c,90),definitionVsRuntime:repeat(c,170),options:[{candidate:repeat(c,70),reason:repeat(c,150),cost:repeat(c,110)}],
  chosen:repeat(c,70),connectionPlan:repeat(c,470),counterEvidence:Array(extra).fill(repeat(c,150)),unknowns:Array(extra).fill(repeat(c,150)),
  revision:1,applicability:"unknown",evidenceRefs:[],patternRefs:[]});
const part=(extra=1)=>({architecture:[decision("decision_a","하",extra),decision("decision_b","호",extra)],
  architectureTombstones:[],architectureOmittedCount:0});
const tempDir=t=>{const dir=fs.mkdtempSync(path.join(os.tmpdir(),"hotfix-notes-"));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));return dir;};

test("H01 repeated host invalidation does not create new revisions",()=>{
  const state=populated();
  state.invalidateUnknown();const first=state.snapshot().architecture[0].revision;
  for(let i=0;i<5;i++)state.invalidateUnknown();
  assert.equal(state.snapshot().architecture[0].revision,first);
  assert.equal(state.snapshot().architecture[0].applicability,"unknown");
});

test("H02 a storage-budget merge rejection keeps the general note instead of clearing it",()=>{
  const oversized=part(3);
  assert.ok(require("../src/architecture-note-contract").validateArchitectureStored(oversized),"the structural part alone is valid");
  assert.equal(notes.mergeArchitecture(general(),oversized,scope),null,"general + structure exceed the stored budget");
  const kept=mergeActiveNote(general(),oversized,scope,null);
  assert.deepEqual(kept.decisions,general().decisions);assert.equal(kept.openQuestions.length,1);
  assert.equal(kept.architecture,undefined,"structural judgments stay execution-local");
  // An explicit clear (null) is still a clear of the general note.
  assert.equal(mergeActiveNote(null,part(1),scope,null).decisions.length,0);
});

test("H03 every note the store writes reads back, including a Korean v2 note above the former 4096-byte cap",t=>{
  const store=new notes.ContinuityNoteStore(tempDir(t));
  const v2=notes.mergeArchitecture(general(),part(1),scope);
  assert.ok(Buffer.byteLength(JSON.stringify(v2))>4096,"fixture exceeds the former 4096-byte read cap");
  for(const [index,note] of [general(),v2].entries()) {
    const key=String(index).repeat(64);
    assert.equal(store.write(key,note),true);
    assert.deepEqual(store.read(key),notes.validateStoredNote(note));
  }
});

test("H04 a pruning error after the rename does not report a stored note as failed",t=>{
  const store=new notes.ContinuityNoteStore(tempDir(t)),original=fs.readdirSync;
  fs.readdirSync=()=>{throw Object.assign(Error("locked"),{code:"EBUSY"});};
  try { assert.equal(store.write("b".repeat(64),general()),true); } finally { fs.readdirSync=original; }
  assert.deepEqual(store.read("b".repeat(64)),general());
});

const architectureView=()=>renderArchitectureContext(binding(),"input-1",populated().snapshot(),[evidence()],[],"focused").text;

test("H05 an echoed architecture frame is not report text, repeated or reformatted",()=>{
  const view=architectureView(),[header,instruction,json]=view.split("\n");
  assert.equal(header,contract.ARCHITECTURE_HEADER);
  const pretty=JSON.stringify(JSON.parse(json),null,2);
  const output=stripInternalDataEcho(`Report start.\n${view}\n${view}\n${header}\n${pretty}\nReport end.\n`,[view]);
  assert.equal(output,"Report start.\nReport end.\n");
  assert.ok(!stripInternalDataEcho(`${header}\n${instruction}\n`,[view]).includes(instruction));
});

test("H06 a cut-off frame echo is suppressed and observed as incomplete",()=>{
  const view=architectureView(),[header,,json]=view.split("\n");
  const filter=new InternalDataOutputFilter([view]);
  let output="";for(const chunk of ["Done.\n",header.slice(0,20),header.slice(20)+"\n",json.slice(0,200)])output+=filter.push(chunk);
  output+=filter.finish();
  assert.equal(output,"Done.\n");
  assert.deepEqual(filter.observation,{suppressedProtocol:true,incompleteProtocol:true});
});

test("H07 frames stay literal when fenced, prose-continued or not injected",()=>{
  const view=architectureView(),[header]=view.split("\n");
  const fenced="```text\n"+view+"\n```\n";
  assert.equal(stripInternalDataEcho(fenced,[view]),fenced);
  assert.equal(stripInternalDataEcho(`${header}\nOrdinary prose after a heading.\n`,[view]),"Ordinary prose after a heading.\n");
  assert.equal(stripInternalDataEcho(view+"\n",[]),view+"\n");
});

test("H08 task continuity and prior note frames are registered too",()=>{
  for(const header of [contract.TASK_CONTINUITY_HEADER,contract.NOTE_HEADER]) {
    const frame=header===contract.NOTE_HEADER
      ? `${header}\nPrior assistant judgments, not verified facts.\n${JSON.stringify({modelNotes:{decisions:[]},sourceApplicability:[]})}`
      : `${header}\n${JSON.stringify({authority:"source_and_runtime_facts_only",completion:"not_inferred"})}`;
    assert.equal(stripInternalDataEcho(`A.\n${frame}\nB.\n`,[frame]),"A.\nB.\n",header);
  }
  assert.deepEqual([...contract.HOST_FRAME_HEADERS].sort(),
    [contract.ARCHITECTURE_HEADER,contract.NOTE_HEADER,contract.TASK_CONTINUITY_HEADER].sort());
});

test("H09 an architecture-only data part is private model input",()=>{
  const view=architectureView();
  const composed=composeModelHistory(Chat.from([{role:"user",content:"Fix health"}]),null,configFor(),[],false,view);
  assert.ok(composed.privateDataParts.includes(view));
  const quoted=composeModelHistory(Chat.from([{role:"user",content:`Explain ${contract.ARCHITECTURE_HEADER}`}]),null,configFor(),[],false,view);
  assert.ok(!quoted.privateDataParts.includes(view),"a user-quoted header keeps the user's text literal");
});

test("H10 the history sanitizer removes complete frames and keeps unrecognized ones",()=>{
  const view=architectureView(),[header]=view.split("\n");
  const message=text=>[{role:"assistant",content:[{type:"text",text}]}];
  assert.equal(contract.sanitizeAssistantProtocols(message(`Before.\n${view}\nAfter.`))[0].content[0].text,"Before.\nAfter.");
  const unknown=`${header}\n{"unrelated":true}\nAfter.`;
  assert.equal(contract.sanitizeAssistantProtocols(message(unknown))[0].content[0].text,unknown);
});
