"use strict";
const fs=require("node:fs"),path=require("node:path");
const {Chat,LMStudioClient}=require("../../lmstudio-context-compactor-plugin/node_modules/@lmstudio/sdk/dist/index.cjs");
const {ContextManager,createInputAssembler}=require("../../lmstudio-context-compactor-plugin/dist/context-manager");
const {BudgetBroker}=require("../../lmstudio-context-compactor-plugin/dist/budget-broker");
const {readConfig}=require("../../lmstudio-context-compactor-plugin/dist/execution-config");
const out=path.resolve(__dirname,"stress-to-380k.json");
const TARGET=380000;
const LOCK="ASTRA-380K-LOCK";
const FACTS="ASTRA-FIXED-EVIDENCE: A=731; B=942; C=513; LOCK="+LOCK+".";
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function main(){
 const client=new LMStudioClient();
 const model=await client.llm.model("swift-qwen3.8-27b");
 const contextLength=await model.getContextLength();
 const config=readConfig({getPluginConfig(){return {get(key){return ({
   contextManagementMode:"deterministic",
   workingInputTargetTokens:18000,
   workingInputTriggerTokens:22000,
   maxOutputReserve:8192,
   safetyMarginTokens:2048,
   assumedContextLength:38912,
   inputAvailabilityMode:"off",
   maxCheckpointChars:22000
 })[key]}}}});
 const broker=new BudgetBroker(config);
 const manager=new ContextManager(config,broker);
 const assemble=createInputAssembler({tokenSource:model,config,roundTools:[],roundScopeInstructions:[],
   getRoundOutputReserve:()=>config.maxOutputReserve,modelInputId:"astra-stress-380k",
   noteEnabled:false,getNote:()=>null,historicalAvailabilityLedger:[]});
 const initial=Chat.from([{role:"system",content:
   "Maintain these verified read-only source facts exactly across every context compaction: "+FACTS+
   " On every request, repeat the three values and lock verbatim."},
   {role:"user",content:"Confirm the fixed evidence "+FACTS}]);
 const perRecord=n=>"EvidenceRecord-"+String(n).padStart(6,"0")+
   " source_path component_header verified_range offset_"+(n*17)+
   " source_version astra_fixture_v1 finding numeric_fact_"+((n%3)+1)+
   " preserved_after_compaction confirmed_read_only boundary_checked.";
 const sample=Array.from({length:40},(_,i)=>perRecord(i)).join("\n");
 const sampleTokens=await model.countTokens(sample);
 const recordTokens=Math.max(1,sampleTokens/40);
 const recordCount=Math.floor(11500/recordTokens);
 const evidenceChunk=Array.from({length:recordCount},(_,i)=>perRecord(i+1)).join("\n");
 const evidenceChunkTokens=await model.countTokens(evidenceChunk);
 const result={status:"RUNNING",targetCumulativeExactInputTokens:TARGET,
   tokenMetric:"sum_of_exact_templated_model_input_tokens_sent_to_each_actual_model_response",
   contextLength,configuredDefaults:{workingInputTargetTokens:config.workingInputTargetTokens,
     workingInputTriggerTokens:config.workingInputTriggerTokens,maxOutputReserve:config.maxOutputReserve,
     safetyMarginTokens:config.safetyMarginTokens},
   watermarks:null,evidenceChunk:{records:recordCount,exactTokens:evidenceChunkTokens},
   preservationFacts:FACTS,rounds:[],cumulativeExactInputTokens:0,cumulativeBackendPromptEvalTokens:0,
   startedAt:new Date().toISOString(),codeChangesMade:false};
 if(contextLength!==38912) {result.status="BLOCKED_CONTEXT_LENGTH_MISMATCH";result.observedContextLength=contextLength;
   fs.writeFileSync(out,JSON.stringify(result,null,2));process.exitCode=2;return;}
 if(evidenceChunkTokens<9000||evidenceChunkTokens>13000){
   result.status="BLOCKED_STRESS_FIXTURE_FLOOR";result.reason="Generated evidence chunk outside preselected 9K-13K token fixture band; no model call was made.";
   fs.writeFileSync(out,JSON.stringify(result,null,2));process.exitCode=2;return;
 }
 let history=initial,cycle=0;
 while(result.cumulativeExactInputTokens<TARGET&&cycle<60){
   cycle++;
   const user="New accumulated read-only evidence batch "+cycle+". Keep and report the fixed original source facts exactly. "+
     FACTS+"\n"+evidenceChunk;
   history.append("user",user);
   const before=await assemble(history);
   const enforced=await manager.enforceLowWater(history,assemble,{force:true,hasReadTools:false});
   const water=enforced.telemetry;
   if(cycle===1) result.watermarks={
     effectiveLowWaterTokens:water.effectiveLowWaterTokens,effectiveHighWaterTokens:water.effectiveHighWaterTokens,
     hardInputCeiling:water.hardInputCeiling,mandatoryFloorTokens:water.mandatoryFloorTokens,
     reservedToolResultTokens:water.reservedToolResultTokens,requiredPostToolOverhead:water.requiredPostToolOverhead
   };
   const record={cycle,beforeExactInputTokens:before.measurement.inputTokens,
     afterCompactionExactInputTokens:enforced.assembled.measurement.inputTokens,
     effectiveLowWaterTokens:water.effectiveLowWaterTokens,effectiveHighWaterTokens:water.effectiveHighWaterTokens,
     hardInputCeiling:water.hardInputCeiling,compactionRequested:water.compactionRequested,
     compactionChangedHistory:enforced.changed,compactionSucceeded:enforced.success,
     selectedCandidate:water.selectedCandidate,candidateMeasurements:water.candidateMeasurements,
     fixedFactsPresentInAssembledHistory:enforced.assembled.history.toString().includes(FACTS),
     cumulativeExactInputTokensBefore:result.cumulativeExactInputTokens};
   if(!enforced.success||!enforced.assembled.measurement.exact||
      enforced.assembled.measurement.inputTokens>water.effectiveLowWaterTokens||
      enforced.assembled.measurement.inputTokens>water.hardInputCeiling||
      !record.fixedFactsPresentInAssembledHistory){
     record.stoppedAt="compaction_postcondition_or_evidence_preservation_failed";
     result.rounds.push(record);result.status="FAIL";result.failure=record.stoppedAt;
     break;
   }
   const started=Date.now();
   let prediction;
   try { prediction=await model.respond(enforced.assembled.history,{maxTokens:96,temperature:0}); }
   catch(error){record.stoppedAt="actual_model_response_failed";record.failure=String(error.stack||error);
     result.rounds.push(record);result.status="FAIL";result.failure=record.stoppedAt;break;}
   const response=String(prediction.content||"");
   record.modelResponseMs=Date.now()-started;
   record.backendPromptEvalTokens=Number(prediction.stats?.promptTokensCount)||null;
   record.predictedTokens=Number(prediction.stats?.predictedTokensCount)||null;
   record.stopReason=prediction.stats?.stopReason||null;
   record.responsePreservedFacts=["731","942","513",LOCK].every(x=>response.includes(x));
   record.responseExcerpt=response.slice(-600);
   record.exactInputTokens=enforced.assembled.measurement.inputTokens;
   result.cumulativeExactInputTokens+=record.exactInputTokens;
   if(record.backendPromptEvalTokens!==null) result.cumulativeBackendPromptEvalTokens+=record.backendPromptEvalTokens;
   record.cumulativeExactInputTokensAfter=result.cumulativeExactInputTokens;
   result.rounds.push(record);
   history=enforced.history;
   history.append("assistant",response);
   result.currentExactInputTokens=enforced.assembled.measurement.inputTokens;
   result.roundCount=cycle;
   result.lastResponsePreservedFacts=record.responsePreservedFacts;
   result.updatedAt=new Date().toISOString();
   fs.writeFileSync(out,JSON.stringify(result,null,2));
   console.log(JSON.stringify({cycle,cumulativeExactInputTokens:result.cumulativeExactInputTokens,
     promptExact:record.exactInputTokens,backendPromptEval:record.backendPromptEvalTokens,
     afterCompaction:record.afterCompactionExactInputTokens,preserved:record.responsePreservedFacts}));
   if(!record.responsePreservedFacts){result.status="FAIL";result.failure="model_response_lost_fixed_evidence";break;}
   await sleep(100);
 }
 if(result.status==="RUNNING") result.status=result.cumulativeExactInputTokens>=TARGET?"PASS":"FAIL";
 result.finishedAt=new Date().toISOString();
 result.roundCount=result.rounds.length;
 result.exactInputCumulativeOvershoot=result.cumulativeExactInputTokens-TARGET;
 result.summary={rounds:result.rounds.length,
   minPostCompactionExactInput:Math.min(...result.rounds.map(x=>x.afterCompactionExactInputTokens)),
   maxPostCompactionExactInput:Math.max(...result.rounds.map(x=>x.afterCompactionExactInputTokens)),
   maxExactInput:Math.max(...result.rounds.map(x=>x.exactInputTokens)),
   failedRounds:result.rounds.filter(x=>x.stoppedAt||x.compactionSucceeded!==true||!x.responsePreservedFacts).length,
   compactions:result.rounds.filter(x=>x.compactionChangedHistory).length};
 fs.writeFileSync(out,JSON.stringify(result,null,2));
 if(result.status!=="PASS")process.exitCode=1;
}
main().catch(error=>{const result={status:"ERROR",failure:String(error.stack||error),codeChangesMade:false};
 fs.writeFileSync(out,JSON.stringify(result,null,2));console.error(error);process.exitCode=1;});
