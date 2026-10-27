"use strict";
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { toolDefinitions } = require("../../../lmstudio-unreal-agent-mcp/src/direct-tool-catalog");
const { readConfig } = require("../../dist/execution-config");
const { BudgetBroker } = require("../../dist/budget-broker");
const { ContextManager } = require("../../dist/context-manager");
const { EvidenceManager } = require("../../dist/evidence-manager");
const { prepareRoundInput } = require("../../dist/round-input");
const tools = toolDefinitions().map(t => ({ name:t.name, description:t.description,
  pluginIdentifier:"mcp/unreal-agent", parametersJsonSchema:t.inputSchema }));
const readers = tools.filter(t => ["read_file","read_file_range","search_files","git_read_file"].includes(t.name));
const tool = name => tools.find(t => t.name === name);
function pair(name, args, value, id = name) {
  return [ChatMessage.from({role:"assistant",content:[{type:"toolCallRequest",
    toolCallRequest:{type:"function",id,name,arguments:args}}]}),
    ChatMessage.from({role:"tool",content:[{type:"toolCallResult",toolCallId:id,content:JSON.stringify(value)}]})];
}
function configFor(overrides={}) {
  return readConfig({getPluginConfig:()=>({get:key=>({
    projectEngine:"unreal",contextManagementMode:"hybrid",showDebugInfo:true,semanticHandoff:false,
    maxOutputReserve:8192,safetyMarginTokens:2048,designGuidanceMode:"off",
    workingInputTargetTokens:46000,workingInputTriggerTokens:56000,...overrides})[key]})});
}
async function prepare(history, extra={}) {
  const events=[], config=extra.config || configFor();
  const tokenSource={async getContextLength(){return 84224;},
    async applyPromptTemplate(chat){return chat.toString();},async countTokens(text){return Math.ceil(text.length/4);}};
  const broker=new BudgetBroker(config), contextManager=new ContextManager(config,broker);
  const evidenceManager=new EvidenceManager(null,readers);
  const result=await prepareRoundInput({
    ctl:{debug:e=>events.push(e),guardAbort(){}},config,tokenSource,contextManager,evidenceManager,workingContext:null,
    workingHistory:history,visibleHistory:history,preProjectionHistory:history,postProjectionHistory:history,
    roundTools:readers,modelTools:readers,roundScopeInstructions:["v4 fixture scope"],scopeInstructions:["v4 fixture scope"],
    roundOutputReserve:8192,activeNote:null,noteEnabled:false,historicalAvailabilityLedger:[],
    semanticSummaryCooldownUntilRound:99,objectiveFingerprint:"v4",finalizing:false,boundedAudit:true,
    researchRecoveryEpisodeStarted:false,allowReadRecovery:false,finalizationTrigger:null,projectionApplied:false,
    executionId:"v4",modelInputId:"v4:0",roundIndex:0,...extra});
  return {result,events,broker,contextManager,evidenceManager};
}
module.exports={Chat,ChatMessage,tools,readers,tool,pair,configFor,prepare};
