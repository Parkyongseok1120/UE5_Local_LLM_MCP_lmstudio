"use strict";
const test=require("node:test"),assert=require("node:assert/strict");
const {BatchReservation,minimumReadResultTokens,reserveReadResult,estimateReadResponseBytes}=require("../dist/budget-broker");
const {EvidenceManager}=require("../dist/evidence-manager");
const {fileSourceKey}=require("../src/evidence-identity");
const {readers,tool}=require("./helpers/v4.cjs");
const requests=[["read_file_range",7150],["read_file_range",5340],["read_file_range",5340],["read_file_range",5340],["search_files",7337]];
function replay(capacity,items=requests){
 const batch=new BatchReservation(capacity,minimumReadResultTokens(readers));
 return items.map(([name,byteBudget],i)=>reserveReadResult(batch,String(i),tool(name),{byteBudget}));
}
test("floor protects read_file when git_read_file is exposed",()=>assert.equal(minimumReadResultTokens(readers),1844));
test("msg3 r6 replay: three explicit 24576 reads all admitted",()=>{
 const results=replay(23385,Array(3).fill(["read_file",24576]));
 assert.deepEqual(results.map(r=>r.allowed),[true,true,true]);
 assert.deepEqual(results.map(r=>r.reservedTokens),[10855,10686,1844]);
});
test("msg5 r0 replay without hints admits four requests",()=>assert.equal(replay(11777).filter(r=>!r.allowed).length,1));
// User approved 2026-10-27: this assumed capacity is a control, not proof of turn-start compaction.
test("msg5 r0 assumed turn-start capacity is sufficient (control)",()=>{
 const results=replay(27936);assert.ok(results.every(r=>r.allowed));
 assert.equal(results.reduce((n,r)=>n+r.reservedTokens,0),17323);
});
test("sizeHint lowers an explicit byteBudget",()=>{
 const em=new EvidenceManager(null,readers),args={path:"project://Source/Character.h",project:"Fixture.uproject",byteBudget:24576};
 em.ledger.recordSizeHint(fileSourceKey({...args,canonicalProject:args.project,provider:"mcp/unreal-agent"}),{sizeBytes:4755,totalLines:130});
 const bytes=em.estimatedResultBytes("read_file",args,tool("read_file"));
 assert.equal(bytes,Math.ceil(4755*1.15)+130*8+2560);
 const result=reserveReadResult(new BatchReservation(23385,1844),"hint",tool("read_file"),args,undefined,undefined,bytes);
 assert.equal(result.arguments.byteBudget,Math.ceil(bytes*1.25));assert.equal(result.reservedTokens,5559);
});
test("range estimate needs both totalLines and sizeBytes",()=>{
 const em=new EvidenceManager(null,readers),args={path:"project://Source/A.h",project:"Fixture.uproject",startLine:2,endLine:4,byteBudget:5340};
 if(em.ledger.recordSizeHint)em.ledger.recordSizeHint(fileSourceKey({...args,canonicalProject:args.project,provider:"mcp/unreal-agent"}),{sizeBytes:4755});
 const estimate=em.estimatedResultBytes?.("read_file_range",args,tool("read_file_range"));
 assert.equal(estimate,undefined);
 assert.equal(reserveReadResult(new BatchReservation(20000),"range",tool("read_file_range"),args,undefined,undefined,estimate).reservedTokens,3160);
});
test("unknown first read reservation unchanged",()=>{
 const result=reserveReadResult(new BatchReservation(23385,1844),"new",tool("read_file"),{byteBudget:24576});
 assert.equal(result.arguments.byteBudget,24576);assert.equal(result.reservedTokens,10855);
});
module.exports={requests};
