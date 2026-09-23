"use strict";
const fs=require('node:fs'),path=require('node:path');
const {Chat,LMStudioClient,rawFunctionTool}=require('@lmstudio/sdk');
const {createPredictionLoopHandler}=require('../dist/prediction-loop');
async function main(){
 const git=process.argv.includes('--git'),toolName=git?'git_read_file':'read_file';
 const client=new LMStudioClient(),model=await client.llm.model('swift-qwen3.8-27b');
 const events=[],answers=[],calls=[];
 const read=rawFunctionTool({name:toolName,description:'Read a synthetic audit fixture, read only. Call once for each path A, B and C.',
  parametersJsonSchema:{type:'object',properties:{path:{type:'string',enum:['A','B','C']},maxChars:{type:'integer',minimum:128,maximum:8192}},required:['path'],additionalProperties:false},
  implementation:async(args)=>{calls.push(args.path);return {ok:true,kind:git?'git_observation':'file_observation',path:args.path,sha256:'fixture-v1',
    startLine:1,endLine:1,content:`FACT-${args.path}: ${ {A:731,B:942,C:513}[args.path]}.`+' supporting read-only evidence.'.repeat(180)};}});
 read.pluginIdentifier='mcp/unreal-agent';
 const config={contextManagementMode:'deterministic',projectEngine:'unreal',showDebugInfo:true,
  workingInputTargetTokens:6000,workingInputTriggerTokens:9000,maxOutputReserve:1024,safetyMarginTokens:2048,
  auditCompletionMode:'bounded',auditResearchRounds:5,auditResearchSeconds:100,auditFinalSeconds:45,auditFinalMaxTokens:512,
  inputAvailabilityMode:'inject'};
 const timeout=AbortSignal.timeout(155000);
 const ctl={abortSignal:timeout,guardAbort(){if(timeout.aborted)throw timeout.reason},
  getPluginConfig(){return {get:k=>config[k]}},getWorkingDirectory(){return ''},
  async pullHistory(){return Chat.from([{role:'user',content:`Read A, B and C using ${toolName}. Report the three FACT values with the file names. Do not read any file twice. These are synthetic read-only fixtures.`}])},
  async tokenSource(){return model},async startToolUseSession(){return {tools:[read],[Symbol.dispose](){}}},
  async requestConfirmToolCall(){throw new Error('Read-only fixture unexpectedly requested approval')},
  debug(e){events.push(e)},createStatus(){return {setText(){},setState(){},remove(){}}},
  createContentBlock(){const block={text:''};answers.push(block);return {appendText(t){block.text+=t},appendToolRequest(){},appendToolResult(){},setStyle(){}}}};
 let failure=null;try{await createPredictionLoopHandler()(ctl)}catch(e){failure=String(e.stack||e)}
 const text=answers.map(x=>x.text).join('\n');
 const success=['731','942','513'].every(x=>text.includes(x))&&['A','B','C'].every(x=>calls.includes(x))&&!failure;
 const report={status:success?'PASS':'FAIL',calls,duplicateReads:calls.length-new Set(calls).size,failure,
  answer:text,events,scope:`Repository-built handler with actual LM Studio model and synthetic ${git?'Git':'non-Git'} read-only provider; installed GUI plugin and real editor state not modified.`};
 fs.writeFileSync(path.resolve(__dirname,'../../artifacts/astra-full-refactor',git?'live-git-flow.json':'live-non-git-flow.json'),JSON.stringify(report,null,2));
 console.log(JSON.stringify({status:report.status,calls,failure})); if(!success)process.exitCode=1;
}
main().catch(e=>{console.error(e);process.exitCode=1});
