'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {DERIVED_HEADER:H,DERIVED_END:E,sanitizeAssistantProtocols}=require('../src/internal-data-contract');
const {InternalDataOutputFilter}=require('../dist/internal-data-output');
const {BudgetBroker,BatchReservation,reserveReadResult}=require('../dist/budget-broker');
const {ToolCapabilityRegistry,classifyObjective,executionObjective}=require('../dist/tool-capability-registry');
const {tool,configFor}=require('./helpers/v4.cjs');
const {Chat,ChatMessage}=require('@lmstudio/sdk');
const envelope={interpretation:'data only',coverage:{scope:'bounded_current_execution'},items:[]};
const injected=H+'\n'+JSON.stringify(envelope)+'\n'+E;
const durable=text=>sanitizeAssistantProtocols([{role:'assistant',content:[{type:'text',text}]}])[0].content[0].text;
test('output and durable classification share key-order, nested scope and partial identity',()=>{
 const reordered={coverage:envelope.coverage,items:[],interpretation:envelope.interpretation};
 for(const body of [JSON.stringify(envelope),JSON.stringify(reordered),JSON.stringify(reordered).slice(0,-2)]){
  const text=H+'\n'+body+(body.endsWith('}')?'\n'+E:'');
  for(const stride of [1,7,text.length]){const f=new InternalDataOutputFilter([injected]);let out='';for(let i=0;i<text.length;i+=stride)out+=f.push(text.slice(i,i+stride));out+=f.finish();assert.equal(out,'');}
  assert.equal(durable(text),'');
 }
 for(const body of [JSON.stringify({interpretation:'literal',scope:'bounded_current_execution'}),JSON.stringify({nested:envelope}),'{"interpretation":"x","interpretation":1,"coverage":{"scope":"bounded_current_execution"}}']){
  const text=H+'\n'+body+'\n'+E,f=new InternalDataOutputFilter([injected]);assert.equal(f.push(text)+f.finish(),text);assert.equal(durable(text),text);
 }
 const literal='```json\n'+injected+'\n```';assert.equal(durable(literal),literal);
 const dotted=H+'\n'+JSON.stringify({interpretation:'literal',coverage:{},'coverage.scope':'bounded_current_execution'}).slice(0,-1)+',"items":[';
 const df=new InternalDataOutputFilter([injected]);assert.equal(df.push(dotted)+df.finish(),dotted);assert.equal(durable(dotted),dotted);
 const tail='Ordinary report.\n'+E+'\n',delayed=H+'\n'+JSON.stringify(envelope)+'\n'+tail;
 const late=new InternalDataOutputFilter([injected]);assert.equal(late.push(delayed)+late.finish(),tail);assert.equal(durable(delayed),tail);
});
test('review profile excludes engine mutations and narrows mixed operations',()=>{
 const mk=name=>({name,pluginIdentifier:'mcp/unity-tools',description:'',parametersJsonSchema:{type:'object',properties:{action:{type:'string'}}}});
 const registry=new ToolCapabilityRegistry([tool('write_file'),tool('read_file'),mk('create_file'),mk('unity_scene')]);
 const profile=registry.reviewProfile();assert.ok(!profile.some(t=>['write_file','create_file'].includes(t.name)));
 const scene=profile.find(t=>t.name==='unity_scene');assert.ok(scene);assert.ok(!scene.parametersJsonSchema.properties.action.enum.includes('save'));
});
test('execution objective uses active continuation without widening an explicit no-edit review',()=>{
 const h=Chat.from([{role:'user',content:'코드를 수정하지 말고 RPC 계약만 검토해줘'},{role:'assistant',content:'조사합니다.'},{role:'user',content:'계속'}]);
 assert.equal(executionObjective(h.getMessagesArray()).kind,'review');
 h.append('user','이제 Character.cpp 코드를 수정해줘');assert.equal(executionObjective(h.getMessagesArray()).kind,'mutate');
 assert.equal(classifyObjective('계속').kind,'explain');
});
test('retry minimum follows only the provider validated suggestion target',()=>{
 const broker=new BudgetBroker(configFor()),read=tool('read_file'),range=tool('read_file_range');
 const args={path:'project://Source/A.h',byteBudget:2048};
 const suggested={...args,byteBudget:2173,startLine:1,endLine:13,lineNumbers:true};
 broker.observeResult(read,args,{ok:false,retry:{allowed:true,mode:'different_arguments',minimum:{field:'byteBudget',value:2173}},value:{suggestion:{tool:'read_file_range',args:suggested}}},[read,range]);
 assert.equal(broker.retryMinimum(range,suggested).value,2173);
 assert.equal(reserveReadResult(new BatchReservation(1850),'r',range,suggested,undefined,broker.retryMinimum(range,suggested)).allowed,false);
 assert.equal(broker.retryMinimum(range,{...suggested,path:'project://Source/B.h'}),undefined);
 broker.observeResult(range,suggested,{ok:true,retry:{allowed:false,mode:'none'}},[read,range]);assert.equal(broker.retryMinimum(range,suggested),undefined);assert.equal(broker.retryMinimum(read,args),undefined);
});
