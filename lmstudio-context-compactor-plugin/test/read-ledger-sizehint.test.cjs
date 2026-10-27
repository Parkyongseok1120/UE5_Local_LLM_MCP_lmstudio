"use strict";

const test=require("node:test"),assert=require("node:assert/strict");
const {ReadLedger}=require("../src/read-ledger");
const {EvidenceManager}=require("../dist/evidence-manager");
const {fileSourceKey}=require("../src/evidence-identity");
const {tools,pair}=require("./helpers/v4.cjs");
const dir={ok:true,path:"project://Source/Fixture/Public",project:"Fixture.uproject",canonicalProject:"Fixture.uproject",
 entries:[{name:"Character.h",type:"file",size:4755,modifiedAt:"2026-10-27T00:00:00Z"}]};
const key=project=>fileSourceKey({...dir,provider:"mcp/unreal-agent",canonicalProject:project,path:dir.path+"/Character.h"});
test("list_directory entries record sizeHint by sourceKey",()=>{
 const em=new EvidenceManager(null,tools);
 em.recordReturnedSources(pair("list_directory",{path:dir.path,project:dir.project},dir),"v4");
 assert.equal(em.ledger.sizeHint(key(dir.project)).sizeBytes,4755);
});
test("mutation invalidates sizeHint",()=>{
 const em=new EvidenceManager(null,tools),p=dir.path+"/Character.h";
 em.recordReturnedSources(pair("list_directory",{path:dir.path,project:dir.project},dir),"v4");
 assert.equal(em.ledger.sizeHint(key(dir.project)).sizeBytes,4755);
 em.observe(pair("replace_in_file",{path:p,project:dir.project},{ok:true,applied:true,operation:"replaced",path:p,canonicalProject:dir.project}),"v4");
 assert.equal(em.ledger.sizeHint(key(dir.project)),null);
});
test("different project same path keeps separate hints",()=>{
 const ledger=new ReadLedger();ledger.recordSizeHint(key("A.uproject"),{sizeBytes:4755});
 ledger.recordSizeHint(key("B.uproject"),{sizeBytes:1707});
 assert.equal(ledger.sizeHint(key("A.uproject")).sizeBytes,4755);
 assert.equal(ledger.sizeHint(key("B.uproject")).sizeBytes,1707);
});

test("byte range totals are never treated as line counts",()=>{
 const em=new EvidenceManager(null,tools),body=Array(100).fill("x".repeat(99)).join("\n"),p=dir.path+"/Character.h";
 em.recordReturnedSources(pair("read_file",{path:p,project:dir.project},{ok:true,path:p,canonicalProject:dir.project,
  sha256:"a".repeat(64),content:body,offsetBytes:0,nextOffsetBytes:body.length,size:body.length,
  range:{unit:"byte",start:0,endExclusive:body.length,total:body.length}}),"v4");
 assert.equal(em.ledger.sizeHint(key(dir.project)).sizeBytes,9999);
 assert.equal(em.ledger.sizeHint(key(dir.project)).totalLines,100);
});

test("sizeHint persists as an estimate and new hash clears unknown fields",()=>{
 const ledger=new ReadLedger(),k=key(dir.project);
 ledger.recordSizeHint(k,{sizeBytes:4755,totalLines:130,sha256:"a".repeat(64)});
 const restored=new ReadLedger();assert.equal(restored.load(JSON.parse(JSON.stringify(ledger.toJSON()))),true);
 assert.equal(restored.sizeHint(k).sizeBytes,4755);assert.equal(restored.entries().length,0);
 restored.recordSizeHint(k,{sha256:"b".repeat(64),totalLines:10});
 assert.deepEqual({sizeBytes:restored.sizeHint(k).sizeBytes,totalLines:restored.sizeHint(k).totalLines},{sizeBytes:null,totalLines:10});
});
test("failed orphan duplicate and foreign directory results do not create hints",()=>{
 for(const kind of ["failed","orphan","duplicate","foreign"]){
  const em=new EvidenceManager(null,kind==="foreign"?tools.map(t=>({...t,pluginIdentifier:"untrusted"})):tools);
  let messages=pair("list_directory",{path:dir.path,project:dir.project},kind==="failed"?{...dir,ok:false,errorCode:"FAILED"}:dir);
  if(kind==="orphan")messages=messages.slice(1);if(kind==="duplicate")messages=[...messages,...messages];
  em.recordReturnedSources(messages,"v4");assert.equal(em.ledger.sizeHint(key(dir.project)),null,kind);
 }
});
test("failed mutation preserves hint and successful bundle invalidates only returned paths",()=>{
 const em=new EvidenceManager(null,tools),p=dir.path+"/Character.h";
 em.recordReturnedSources(pair("list_directory",{path:dir.path,project:dir.project},dir),"v4");
 em.observe(pair("replace_in_file",{path:p,project:dir.project},{ok:false,errorCode:"HASH_MISMATCH"}),"failed");
 assert.equal(em.ledger.sizeHint(key(dir.project)).sizeBytes,4755);
 em.ledger.recordSizeHint(key("Other.uproject"),{sizeBytes:100});
 em.observe(pair("apply_edit_bundle",{project:dir.project,patches:[{path:p}]},
  {ok:true,operation:"bundle_applied",files:[{path:p,applied:true}],canonicalProject:dir.project}),"bundle");
 assert.equal(em.ledger.sizeHint(key(dir.project)),null);assert.equal(em.ledger.sizeHint(key("Other.uproject")).sizeBytes,100);
});
test("known exact result takes precedence over a size estimate",()=>{
 const {BatchReservation,reserveReadResult}=require("../dist/budget-broker"),{tool}=require("./helpers/v4.cjs");
 const result=reserveReadResult(new BatchReservation(20000),"known",tool("read_file"),{byteBudget:24576},3000,undefined,4000);
 assert.equal(result.arguments.byteBudget,24576);
 assert.equal(result.reservedTokens,Math.ceil(3000/2.5)+require("../dist/execution-contracts").ENVELOPE_TOKENS);
});

test("observed activeProject connects omitted-project requests only within the execution",()=>{
 const em=new EvidenceManager(null,tools),{tool}=require("./helpers/v4.cjs");
 const value={...dir,canonicalProject:undefined,project:undefined,activeProject:"C:/Fixture/Fixture.uproject"};
 em.recordReturnedSources(pair("list_directory",{path:dir.path},value),"v4");
 const args={path:dir.path+"/Character.h",byteBudget:24576};
 assert.ok(em.estimatedResultBytes("read_file",args,tool("read_file"))>0);
 assert.equal(em.estimatedResultBytes("read_file",{...args,project:"Other.uproject"},tool("read_file")),undefined);
 em.recordReturnedSources([],"next-execution");
 assert.equal(em.estimatedResultBytes("read_file",args,tool("read_file")),undefined);
 em.recordReturnedSources(pair("list_directory",{path:dir.path},value),"next-execution");
 em.recordReturnedSources(pair("get_active_project",{}, {ok:true,activeProject:null,selected:false}),"next-execution");
 assert.equal(em.estimatedResultBytes("read_file",args,tool("read_file")),undefined);
});
test("default project switches cannot reuse an old project's size hint",()=>{
 const em=new EvidenceManager(null,tools),{tool}=require("./helpers/v4.cjs");
 em.recordReturnedSources(pair("list_directory",{path:dir.path},{...dir,canonicalProject:undefined,activeProject:"A.uproject"}),"v4");
 const args={path:dir.path+"/Character.h"};assert.ok(em.estimatedResultBytes("read_file",args,tool("read_file")));
 em.observe(pair("set_active_project",{path:"B.uproject"},{ok:true,activeProject:"B.uproject"}),"switch");
 assert.equal(em.estimatedResultBytes("read_file",args,tool("read_file")),undefined);
});
test("Unity canonicalProjectRoot connects default request estimates",()=>{
 const unity={...tools.find(t=>t.name==="read_file"),pluginIdentifier:"mcp/unity-tools"},em=new EvidenceManager(null,[unity]);
 const value={ok:true,path:"Assets/A.cs",canonicalProjectRoot:"C:/Unity",sha256:"a".repeat(64),
  startLine:1,endLine:2,totalLines:2,text:"One();\nTwo();"};
 em.recordReturnedSources(pair("read_file",{path:value.path},value),"unity");
 assert.ok(em.estimatedResultBytes("read_file",{path:value.path},unity)>0);
});
