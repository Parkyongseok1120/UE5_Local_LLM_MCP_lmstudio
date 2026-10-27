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
