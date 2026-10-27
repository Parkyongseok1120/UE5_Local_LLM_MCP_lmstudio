const test=require('node:test'),assert=require('node:assert/strict');
const {ToolCapabilityRegistry,isObservationOnlyToolCall,localObservationTools}=require('../dist/tool-capability-registry');
const {BatchReservation,reserveReadResult}=require('../dist/budget-broker');
const {capabilityFor}=require('../dist/tool-capability-core');
const make=(name,pluginIdentifier)=>({name,pluginIdentifier,parametersJsonSchema:{properties:{}}});
test('v3 classification is provider qualified and operation-specific',()=>{
 const t=make('unity_symbols','mcp/unity-tools'),r=new ToolCapabilityRegistry([t]);
 assert.equal(r.resultKind(t,{name:t.name,arguments:{action:'source'}}),'source_body');
 assert.equal(r.needsReadReservation(t,{name:t.name,arguments:{action:'status'}}),false);
 assert.equal(r.resultKind(t,{name:t.name,arguments:{action:'invented'}}),null);
 assert.equal(capabilityFor({...t,pluginIdentifier:'untrusted'},{name:t.name,arguments:{action:'source'}}),null);
 assert.equal(new ToolCapabilityRegistry([t,{...t}]).resultKind(t,{name:t.name,arguments:{action:'source'}}),null);
});
test('v3 proposal is neither a source reservation nor a recovery read',()=>{
 const t=make('propose_file_deletions','mcp/unreal-agent'),request={name:t.name,arguments:{}};
 const r=new ToolCapabilityRegistry([t]); assert.equal(r.resultKind(t,request),'proposal');
 assert.equal(r.needsReadReservation(t,request),false); assert.equal(isObservationOnlyToolCall(t,request),false);
 assert.equal(r.readProfile().length,0); assert.equal(reserveReadResult(new BatchReservation(0),'p',t,{}).allowed,true);
});
test('v3 validator stays allowed at zero capacity and archive needs local identity',()=>{
 const t=make('evidence_first_validate','mcp/evidence-first');
 assert.equal(reserveReadResult(new BatchReservation(0),'v',t,{}).allowed,true);
 const archive=make('evidence_first_read_context','mcp/evidence-first');
 assert.equal(capabilityFor(archive),null);localObservationTools.add(archive);
 assert.equal(capabilityFor(archive).resultKind,'archive_page');
});
test('v3 mutation focus narrows mixed Unity actions',()=>{
 const t=make('unity_scene','mcp/unity-tools'),r=new ToolCapabilityRegistry([t]);
 const focus=r.mutationProfile()[0].parametersJsonSchema.properties.action.enum;
 assert.ok(focus.includes('create'));assert.equal(focus.includes('list'),false);
});
test('v3 source and built CommonJS queries use the generated manifest',()=>{
 const src=require('../src/tool-capability-core');
 const t=make('unreal_symbol_lookup','mcp/unreal-rag');
 assert.deepEqual(src.capabilityFor(t),capabilityFor(t));
});
test('v3 only the locally registered attachment reader remains in read recovery',()=>{
 const t=make('read_attached_document',undefined),r=new ToolCapabilityRegistry([t]);
 assert.equal(r.readProfile().length,0);localObservationTools.add(t);
 assert.equal(r.readProfile()[0],t);assert.equal(r.resultKind(t,{name:t.name,arguments:{}}),'source_body');
 const spoof=make(t.name,'remote');assert.equal(capabilityFor(spoof),null);
});
