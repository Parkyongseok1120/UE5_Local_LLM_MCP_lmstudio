'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {Chat,ChatMessage}=require('@lmstudio/sdk');
const {InternalDataOutputFilter}=require('../dist/internal-data-output');
const {PredictionStreamRenderer}=require('../dist/prediction-stream');
const {classifyOutputLimitStage,canRecoverOutputLimit}=require('../dist/delivery-controller');
const {DERIVED_HEADER:H,DERIVED_END:E,sanitizeAssistantProtocols}=require('../src/internal-data-contract');
const {WorkingContext,hash,serialize}=require('../src/working-context');
const {composeModelHistory}=require('../dist/context-manager');
const {EvidenceManager}=require('../dist/evidence-manager');
const {configFor,tool}=require('./helpers/v4.cjs');
const notes=require('../src/continuity-model-notes');
const {REASONING_SEPARATOR}=require('../src/continuity-text');
const object={interpretation:'Last observed values only.',coverage:{scope:'bounded_current_execution'},items:[{id:'real',text:'source'}]};
const injected=H+'\n'+JSON.stringify(object)+'\n'+E;
const partial=(H+'\n'+JSON.stringify({...object,items:[{id:'different',text:'source'}]})).slice(0,-4);
for(const fragment of [1,7,1000000])test(`mutated incomplete protocol stays internal at EOS, fragments=${fragment}`,()=>{
 const f=new InternalDataOutputFilter([injected]);let output='';
 for(let i=0;i<partial.length;i+=fragment)output+=f.push(partial.slice(i,i+fragment));
 assert.equal(output+f.finish(),'');assert.equal(f.finish(),'');assert.equal(f.observation.incompleteProtocol,true);
});
test('internal protocol overflow is discarded through the end marker and ordinary prose resumes',()=>{
 const text=H+'\n'+JSON.stringify({...object,items:[{text:'x'.repeat(270000)}]})+'\n'+E+'\nActual conclusion.';
 const f=new InternalDataOutputFilter([injected]);let output='';
 for(let i=0;i<text.length;i+=127)output+=f.push(text.slice(i,i+127));
 assert.equal(output+f.finish(),'Actual conclusion.');assert.equal(f.observation.suppressedProtocol,true);
});
test('a confirmed internal frame cannot become prose when its JSON body is malformed',()=>{
 const text=H+'\n{"interpretation":"Observed","coverage":{"scope":"bounded_current_execution"},"items":[\nunescaped internal source\n';
 const f=new InternalDataOutputFilter([injected]);
 assert.equal(f.push(text)+f.finish(),'');assert.equal(f.observation.incompleteProtocol,true);
 const closed=new InternalDataOutputFilter([injected]);
 assert.equal(closed.push(text+E+'\nReal report.')+closed.finish(),'Real report.');
});
test('stream and fallback share normalized report/history while preserving reasoning and tool requests',()=>{
 for(const streaming of [false,true]){
  const blocks=[],r=new PredictionStreamRenderer({createContentBlock(){const b={text:'',appendText(t){this.text+=t;},setStyle(){}};blocks.push(b);return b;}},notes.splitVisibleAnswer,false,false,[injected]);
  if(streaming)for(const c of partial)r.onFragment({roundIndex:0,reasoningType:'none',content:c,tokensCount:1});
  const message=ChatMessage.create('assistant','reasoning'+REASONING_SEPARATOR+partial);
  assert.equal(r.visibleMessage(message).getText(),'');
  assert.equal(r.historyMessage(message).getText(),'reasoning'+REASONING_SEPARATOR);
  assert.equal(r.consumeMessage(message).incompleteProtocol,true);
  assert.equal(blocks.map(b=>b.text).join(''),'');
 }
});
test('raw non-reasoning usage is cost, not proof of a delivered report or output recovery',()=>{
 const c={finishReason:'maxPredictedTokensReached',continueAfterTools:false,messages:[ChatMessage.create('assistant','')],predictionUsage:{visibleTokensCount:620,visibleChars:1747,reasoningTokensCount:7564}};
 assert.equal(classifyOutputLimitStage(c,false,{hasUnfinished:()=>false}),'reasoning');
 assert.equal(canRecoverOutputLimit(c,{hasUnfinished:()=>false}),false);
});
test('normalized prose retains unresolved tool intent through final evaluation and terminal finish',()=>{
 const {DeliveryController,classifyFinalDelivery}=require('../dist/delivery-controller');
 const r=new PredictionStreamRenderer({createContentBlock(){assert.fail();}},notes.splitVisibleAnswer,false);
 const captured=r.normalizeCaptured({messages:[ChatMessage.create('assistant','Checking the evidence.\n<tool_call><function=read_file>unfinished</function></tool_call>')],predictionUsage:{},finishReason:'eosFound'});
 assert.equal(captured.unexecutedToolIntent,true);
 assert.doesNotMatch(captured.messages[0].getText(),/tool_call/);
 assert.equal(classifyFinalDelivery(captured.messages[0].getText(),captured,false,captured.messages).reportState,'unresolved_tool_intent');
 const controller=new DeliveryController();controller.observe(captured);
 assert.equal(controller.finish().reportCompleted,false);
});
test('persistence keeps fences across text parts and strips protocol text beside a real tool request',()=>{
 const request={type:'toolCallRequest',toolCallRequest:{type:'function',id:'real',name:'read_file',arguments:{path:'A.h'}}};
 const mixed={role:'assistant',content:[{type:'text',text:injected},request]};
 const cleaned=sanitizeAssistantProtocols([mixed]);
 assert.equal(cleaned[0].content[0].text,'');assert.deepEqual(cleaned[0].content[1],request);
 const quoted={role:'assistant',content:[{type:'text',text:'```json\n'},{type:'text',text:injected+'\n```\n'}]};
 assert.deepEqual(sanitizeAssistantProtocols([quoted]),[quoted]);
 const split={role:'assistant',content:[{type:'text',text:H+'\n'},{type:'text',text:partial.slice(H.length+1)}]};
 assert.ok(sanitizeAssistantProtocols([split])[0].content.every(p=>p.text===''));
});
test('legacy internal prefix and adjacent partial suffix are excluded without modifying the raw transcript',()=>{
 const messages=[{role:'user',content:[{type:'text',text:injected}]},
  {role:'assistant',content:[{type:'text',text:'Visible report.\n'+partial}]},
  {role:'assistant',content:[{type:'text',text:'[Delivered partial assistant response; unverified]\nJSON suffix'}]}];
 const before=JSON.stringify(messages),clean=sanitizeAssistantProtocols(messages);
 assert.equal(clean[0].content[0].text,injected);assert.equal(clean[1].content[0].text,'Visible report.\n');assert.equal(clean[2].content[0].text,'');
 assert.equal(JSON.stringify(messages),before);
 const literal='```json\n'+injected+'\n```\nOrdinary';
 assert.equal(sanitizeAssistantProtocols([{role:'assistant',content:[{type:'text',text:literal}]}])[0].content[0].text,literal);
});
test('old signed window restoration sanitizes internal responses while keeping prefix integrity and real prose',()=>{
 const context=new WorkingContext({conversation:'fixture',lineage:'one',workspace:'w',repository:'r'});
 const visible=Chat.from([{role:'user',content:'Review.'},{role:'assistant',content:'Visible report.\n'+partial},{role:'assistant',content:'JSON suffix'}]);
 assert.equal(context.commit(visible,visible,{exact:true,remainingTokens:5000},hash(serialize(visible)),'model'),true);
 // Simulate a valid old writer after the current writer's sanitization.
 const {digest,...body}=context.manifest;body.replacement=serialize(visible);context.manifest={...body,digest:hash(body)};
 const next=Chat.from(visible);next.append('user','Continue.');
 const restored=context.restore(next);assert.equal(restored.reason,'restored_remeasure_required');
 assert.match(restored.history.toString(),/Visible report/);assert.doesNotMatch(restored.history.toString(),/Derived reference data|JSON suffix/);
 const changed=Chat.from(next);changed.getMessagesArray()[0].replaceText('Different');
 assert.equal(context.restore(changed).reason,'source_prefix_mismatch');
});
test('provisional reference omission cannot force consolidation of an unchanged current reference',()=>{
 const h=Chat.from([{role:'user',content:'Review'}]),ref=H+'\n'+JSON.stringify({...object,items:[{text:'x'.repeat(1100)}]})+'\n'+E;
 const shown=new Map([['user',[ref]]]);
 const baseline=composeModelHistory(h,null,configFor(),['scope'],false,undefined,[],shown,false,1000,true);
 const adopted=composeModelHistory(h,null,configFor(),['scope'],false,ref,[],shown,false,1000);
 const omitted=composeModelHistory(h,null,configFor(),['scope'],false,undefined,[],shown,false,1000);
 assert.equal(baseline.forcedRewrite,undefined);assert.equal(adopted.forcedRewrite,undefined);assert.equal(omitted.forcedRewrite,'data_consolidation');
});
test('mutable source reads never inherit exact size from a legacy request-size cache',()=>{
 const em=new EvidenceManager(null,[tool('read_file')]);
 em.ledger.load({schemaVersion:2,entries:[],resultSizes:[['legacy',1800]],sizeHints:[]});
 assert.equal(em.knownResultBytes('read_file',{path:'project://Source/A.h'}),undefined);
 assert.equal('resultSizes' in em.ledger.toJSON(),false);
});
