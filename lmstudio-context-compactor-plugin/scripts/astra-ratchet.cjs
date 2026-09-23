"use strict";
const fs=require('node:fs'),path=require('node:path');
const {Chat,LMStudioClient}=require('@lmstudio/sdk');
const {ContextManager,createInputAssembler}=require('../dist/context-manager');
const {BudgetBroker}=require('../dist/budget-broker');
const {readConfig}=require('../dist/execution-config');
async function run() {
 const live=process.argv.includes('--live');
 const cycles=live?3:30;
 const client=live?new LMStudioClient():null;
 const model=live?await client.llm.model('swift-qwen3.8-27b'):{
   async getContextLength(){return 38912},async applyPromptTemplate(chat,{toolDefinitions}){return chat.toString()+JSON.stringify(toolDefinitions)},
   async countTokens(text){return Math.ceil(text.length/4)}};
 const config=readConfig({getPluginConfig(){return {get:key=>({contextManagementMode:'deterministic',
   workingInputTargetTokens:6000,workingInputTriggerTokens:9000,maxOutputReserve:1024,safetyMarginTokens:2048})[key]}}});
 const manager=new ContextManager(config,new BudgetBroker(config));
 const assemble=createInputAssembler({tokenSource:model,config,roundTools:[],roundScopeInstructions:[],
   getRoundOutputReserve:()=>config.maxOutputReserve,modelInputId:'astra-ratchet',noteEnabled:false,getNote:()=>null,historicalAvailabilityLedger:[]});
 let history=Chat.from([{role:'system',content:'Retain the exact invariant SENTINEL-731. Answer concisely.'},
   {role:'user',content:'Inspect evidence and preserve SENTINEL-731.'}]);
 const results=[];
 for(let cycle=0;cycle<cycles;cycle++) {
   for(let n=0;n<8;n++) history.append('assistant',('Read-only observation: field A is 731; field B is 942. '+ 'stable fact '.repeat(20)).repeat(32));
   const before=(await assemble(history)).measurement;
   const result=await manager.enforceLowWater(history,assemble,{hasReadTools:false});
   if(!result.success || !result.history.toString().includes('SENTINEL-731')) throw new Error('Ratchet postcondition or mandatory invariant failed');
   history=result.history;
   results.push({cycle:cycle+1,rounds:8,before:before.inputTokens,...result.telemetry});
   console.log(JSON.stringify({cycle:cycle+1,before:before.inputTokens,after:result.assembled.measurement.inputTokens,low:result.telemetry.effectiveLowWaterTokens}));
 }
 const baselines=results.map(r=>r.finalExactInputTokens);
 const drift=Math.max(...baselines)-Math.min(...baselines);
 let prediction=null;
 if(live) {
   const input=Chat.from(history); input.append('user','Return the invariant SENTINEL-731 exactly.');
   const response=await model.respond(input,{maxTokens:128,temperature:0,signal:AbortSignal.timeout(45000)});
   prediction={text:response.content,stats:response.stats,taskCompleted:response.content.includes('SENTINEL-731')};
 }
 const report={mode:live?'live_sdk_exact_template':'deterministic_fixture',cycles,results,baselines,drift,
   duplicateSourceReads:0,compactionsPer10Rounds:10/8,taskCompleted:prediction?prediction.taskCompleted:true,prediction,
   interpretation:'Synthetic repeated equivalent evidence; live mode uses actual loaded tokenizer/template and one real prediction. Does not prove installed GUI plugin or editor workflows.',
   status:drift<=128&&(!prediction||prediction.taskCompleted)?'PASS':'FAIL'};
 fs.writeFileSync(path.resolve(__dirname,'../../artifacts/astra-full-refactor',live?'ratchet-live.json':'ratchet-soak.json'),JSON.stringify(report,null,2)+'\n');
 if(report.status==='FAIL') process.exitCode=1;
}
run().catch(e=>{console.error(e);process.exitCode=1});
