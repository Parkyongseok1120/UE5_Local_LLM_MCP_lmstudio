const test=require('node:test'),assert=require('node:assert/strict');
const {ChatMessage}=require('@lmstudio/sdk');
const {EvidenceManager}=require('../dist/evidence-manager');
const {ReadLedger}=require('../dist/read-ledger');
const {returnedEngineEvidence,packetEngineClaimGaps,engineClaimNotice}=require('../dist/engine-claim-anchoring');
const {fileCoverageNotice}=require('../dist/quote-anchoring');
const make=(name,provider='mcp/unreal-agent')=>({name,pluginIdentifier:provider,parametersJsonSchema:{properties:{}}});
function pair(name,value,id='r',args={}){return [
 ChatMessage.from({role:'assistant',content:[{type:'toolCallRequest',toolCallRequest:{id,type:'function',name,arguments:args}}]}),
 ChatMessage.from({role:'tool',content:[{type:'toolCallResult',toolCallId:id,content:JSON.stringify(value)}]})];}
test('v3 search lines are delivered evidence without source coverage or synthetic file hash',()=>{
 const m=new EvidenceManager(null,[make('search_files')]);
 m.recordReturnedSources(pair('search_files',{ok:true,canonicalProject:'A',sha256:'a'.repeat(64),results:[{uri:'engine://Engine/A.cpp',path:'A.cpp',line:58,text:'PrimaryActorTick.bCanEverTick = true;'}]}),'e1');
 const view=m.returnedSources(),file=view.files[0];assert.equal(file.path,'engine://Engine/A.cpp');
 assert.deepEqual(m.returnedSource(file.sourceKey,[58,58]),{returned:true,via:'search_hit',scope:'execution'});
 assert.equal(m.returnedSource(file.sourceKey,[1,100]).returned,false);assert.equal(m.ledger.entries().length,0);
 assert.equal(file.bodyReturned,false);assert.match(fileCoverageNotice(view,'I reviewed all 1 files.'),/0개/);
});
test('v3 returned source persists as conversation evidence and rereturn has execution scope only for its lines',()=>{
 const l=new ReadLedger();l.recordReturnedSource({provider:'p',canonicalProject:'A',path:'A.cpp',startLine:1,text:'One();\nTwo();'},'source_body','e1');
 const restored=new ReadLedger();assert.equal(restored.load(JSON.parse(JSON.stringify(l.toJSON()))),true);
 const key=restored.returnedSources('e2').files[0].sourceKey;
 assert.equal(restored.returnedSource(key,[1,2],'e2').scope,'conversation');
 restored.recordReturnedSource({provider:'p',canonicalProject:'A',path:'A.cpp',startLine:2,text:'Two();'},'source_body','e2');
 assert.equal(restored.returnedSource(key,[2,2],'e2').scope,'execution');
 assert.equal(restored.returnedSource(key,[1,2],'e2').scope,'conversation');
});
test('v3 Unity text and each supported body field are source delivery',()=>{
 for(const field of ['text','content','body','code']){
  const m=new EvidenceManager(null,[make('read_file','mcp/unity-tools')]);
  m.recordReturnedSources(pair('read_file',{status:'observed',path:'Assets/A.cs',startLine:4,[field]:'Update();'}),'e');
  assert.equal(m.returnedSources().files[0].lines.get(4),'Update();');
 }
});
test('v3 orphan failed duplicate and spoofed results do not back source claims',()=>{
 const t=make('read_file'),v={ok:true,path:'A.cpp',startLine:1,content:'X();'};
 for(const [tools,messages] of [[[t],pair(t.name,{...v,ok:false})],[[t],pair(t.name,v).slice(1)],[[{...t,pluginIdentifier:'spoof'}],pair(t.name,v)],[[t],[...pair(t.name,v),...pair(t.name,v)]]]){
  const m=new EvidenceManager(null,tools);m.recordReturnedSources(messages,'e');assert.equal(m.returnedSources().files.length,0);
 }
});
test('v3 matching paths in different projects keep separate source keys',()=>{
 const l=new ReadLedger();for(const canonicalProject of ['A','B'])l.recordReturnedSource({provider:'p',canonicalProject,path:'Source/A.cpp',startLine:1,content:'X();'},'source_body','e');
 assert.equal(new Set(l.returnedSources('e').files.map(f=>f.sourceKey)).size,2);
});
test('v3 historical source scope reaches notices while claim to fact precision remains unchanged',()=>{
 const l=new ReadLedger();l.recordReturnedSource({provider:'p',path:'Project.cpp',startLine:1,content:'Old();'},'source_body','old');
 const evidence=returnedEngineEvidence(l.returnedSources('new'));
 const gaps=packetEngineClaimGaps({claims:[{severity:'P1',claim:'Duplicate bCanEverTick = true in the PlayerController constructor',evidence:[]}]},evidence,'unreal');
 assert.ok(gaps.some(g=>g.factId==='ue.controller.player-tick-input'),'existing broad marker mapping intentionally remains');
 assert.match(engineClaimNotice(gaps),/이번 대화에서/);
});
test('v3 an unhashed search never erases returned known-version body lines',()=>{
 const l=new ReadLedger(),base={provider:'p',canonicalProject:'A',path:'A.cpp'};
 l.recordReturnedSource({...base,sha256:'a'.repeat(64),startLine:1,text:'One();\nTwo();'},'source_body','e1');
 const key=l.returnedSources('e1').files[0].sourceKey;
 l.recordReturnedSource({...base,results:[{path:'A.cpp',line:2,text:'Two();'}]},'search_hits','e2');
 assert.equal(l.returnedSource(key,[1,1],'e2').returned,true);
 assert.equal(l.returnedSources('e2').files[0].bodyReturned,true);
 assert.equal(l.entries().length,0);
});
test('v3 missing persisted ledger rebuilds historical delivery without inherited source authority',()=>{
 const {Chat}=require('@lmstudio/sdk');const m=new EvidenceManager(null,[make('read_file')]);
 const history=Chat.empty();for(const msg of pair('read_file',{ok:true,path:'A.cpp',startLine:1,content:'Old();'}))history.append(msg);
 m.rebuildReturnedSources(history);const f=m.returnedSources().files[0];
 assert.equal(f.scope,'conversation');assert.equal(m.ledger.entries().length,0);
 assert.equal(m.returnedSource(f.sourceKey,[1,1]).scope,'conversation');
});
test('v3 a pending operation cannot publish source delivery',()=>{
 const l=new ReadLedger();for(const status of ['accepted','pending','running','outcome_unknown'])
  l.recordReturnedSource({status,path:'A.cpp',startLine:1,text:'Pending();'},'source_body','e');
 assert.equal(l.returnedSources('e').files.length,0);
});
test('v3 returned range survives text eviction without a false missing-source notice',()=>{
 const l=new ReadLedger(),path='engine://Engine/Source/Runtime/Engine/Private/Controller.cpp';
 l.recordReturnedSource({provider:'p',path,startLine:1,content:'Line();\n'.repeat(599)+'PrimaryActorTick.bCanEverTick = true;'},'source_body','e');
 const key=l.returnedSources('e').files[0].sourceKey;
 assert.equal(l.returnedSource(key,[600,600],'e').returned,true);
 for(let i=0;i<8;i++)l.recordReturnedSource({provider:'p',path:`Other${i}.cpp`,startLine:1,text:'x'.repeat(16000)},'source_body','e');
 assert.equal(l.returnedSource(key,[600,600],'e').returned,true);
 assert.equal(l.returnedSources('e').files.find(f=>f.sourceKey===key).completeText,false);
 const gaps=packetEngineClaimGaps({claims:[{severity:'P1',claim:'Disable PlayerController actor tick bCanEverTick = false',evidence:[]}]},returnedEngineEvidence(l.returnedSources('e')),'unreal');
 assert.equal(gaps.length,0,'missing cached text cannot prove that source was never returned');
 const restored=new ReadLedger();restored.load(JSON.parse(JSON.stringify(l.toJSON())));
 assert.equal(restored.returnedSource(key,[600,600],'next').scope,'conversation');
});
test('v3 contract IDs only come from the provider-qualified contract lookup capability',()=>{
 const m=new EvidenceManager(null,[make('search_files')]);
 m.recordReturnedSources(pair('search_files',{ok:true,sourceMode:'engine_local',results:[],relatedContracts:{items:[{id:'ue.controller.player-tick-input'}]}}),'e');
 assert.equal(m.returnedSources().contractIds.size,0);
});
