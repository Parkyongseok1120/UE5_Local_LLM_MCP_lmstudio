'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {ReadLedger}=require('../src/read-ledger');
const {packetQuoteMismatches}=require('../dist/quote-anchoring');
const {ReviewEvidenceIndex}=require('../dist/review-evidence');
const {ReviewRegister}=require('../dist/review-register');
const {ChatMessage}=require('@lmstudio/sdk');
const base={provider:'mcp/unreal-agent',canonicalProject:'C:/Fixture/P.uproject',path:'Source/A.cpp',ok:true};
const a='a'.repeat(64),b='b'.repeat(64);
const tool=name=>({name,pluginIdentifier:'mcp/unreal-agent'});
function pair(id,name,value){return [ChatMessage.from({role:'assistant',content:[{type:'toolCallRequest',toolCallRequest:{id,type:'function',name,arguments:{path:base.path}}}]}),ChatMessage.from({role:'tool',content:[{type:'toolCallResult',toolCallId:id,content:JSON.stringify({...base,...value})}]})];}
test('unknown snippets cannot replace known-version lines or produce a false quote mismatch',()=>{
 const l=new ReadLedger();l.recordReturnedSource({...base,sha256:a,startLine:1,content:'void OriginalCall() {}'},'source_body','e');
 l.recordReturnedSource({...base,results:[{path:base.path,line:1,text:'void DifferentCall() {}'}]},'search_hits','e');
 const f=l.returnedSources('e').files[0];assert.equal(f.lines.get(1),'void OriginalCall() {}');assert.equal(f.versionUncertain,true);
 assert.equal(f.unversionedSnippets[0].lines[0][1],'void DifferentCall() {}');
 assert.deepEqual(packetQuoteMismatches({claims:[{evidence:[{kind:'project_source',location:'Source/A.cpp:1',observation:'Defines OriginalCall().'}]}]},[f]),[]);
 assert.ok(packetQuoteMismatches({claims:[{evidence:[{kind:'project_source',sourceVersion:a,location:'Source/A.cpp:1',observation:'Defines InventedCall().'}]}]},[f]).length);
 l.recordReturnedSource({...base,sha256:b,startLine:1,content:'void CurrentCall() {}'},'source_body','e');
 assert.equal(l.returnedSources('e').files[0].lines.get(1),'void CurrentCall() {}');assert.equal(l.returnedSources('e').files[0].versionUncertain,undefined);
 l.recordReturnedSource({...base,results:[{path:base.path,line:1,text:'void CurrentCall() {}'}]},'search_hits','e');
 const identical=l.returnedSources('e').files[0];assert.equal(identical.versionUncertain,false);
 assert.ok(packetQuoteMismatches({claims:[{evidence:[{kind:'project_source',location:'Source/A.cpp:1',observation:'Defines InventedCall().'}]}]},[identical]).length);
});
test('known commit invalidates old review applicability without clearing or rereading its body',()=>{
 const index=new ReviewEvidenceIndex([tool('read_file'),tool('replace_in_file')]),register=new ReviewRegister();
 index.observe(pair('r','read_file',{sha256:a,startLine:1,content:'void OriginalCall() {}'}));
 register.observe([ChatMessage.create('assistant','OriginalCall() is a confirmed bug at Source/A.cpp:1.')],1);register.attach(index);
 assert.equal(register.rows[0].status,'found');
 index.observe(pair('failed','replace_in_file',{ok:false,errorCode:'FILE_VERSION_CONFLICT',sha256:b}));register.attach(index);assert.equal(register.rows[0].status,'found');
 index.observe(pair('change','replace_in_file',{operation:'replaced',previousSha256:a,sha256:b}));register.attach(index);
 assert.equal(register.rows[0].status,'needs_recheck');assert.equal(register.rows[0].bundle.sha,a);assert.match(register.rows[0].bundle.excerpt,/OriginalCall/);
 const late=new ReviewRegister();late.observe([ChatMessage.create('assistant','OriginalCall() is a confirmed bug at Source/A.cpp:1.')],2);late.attach(index);
 assert.equal(late.rows[0].status,'needs_recheck');
});
test('same-version contained ranges including line one retain wider evidence; a new hash replaces it',()=>{
 for(const startLine of [1,2]){
  const index=new ReviewEvidenceIndex([tool('read_file'),tool('read_file_range')]);
  index.observe(pair('wide','read_file',{sha256:a,startLine:1,content:'void FirstCall() {}\nvoid LastCall() {}'}));
  index.observe(pair('narrow','read_file_range',{sha256:a,startLine,content:startLine===1?'void FirstCall() {}':'void LastCall() {}'}));
  assert.match(index.bundleFor('LastCall','Source/A.cpp:2').excerpt,/LastCall/);
  index.observe(pair('new','read_file_range',{sha256:b,startLine:1,content:'void ChangedFirst() {}'}));
  assert.equal(index.bundleFor('LastCall','Source/A.cpp:2').excerpt,null);
 }
});
