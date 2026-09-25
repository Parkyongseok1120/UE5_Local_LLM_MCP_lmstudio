"use strict";
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const {randomInt} = require('node:crypto');
const {execFileSync} = require('node:child_process');
const {Chat, LMStudioClient, rawFunctionTool} = require('@lmstudio/sdk');
const {createPredictionLoopHandler} = require('../dist/prediction-loop');
const {visibleBlocks, evaluateCompletion} = require('./measurement-contract.cjs');
const {createRuntime} = require('../../lmstudio-unity-mcp/src/server');

async function main() {
  const gitMode = process.argv.includes('--git'), toolName = gitMode ? 'git_read_file' : 'read_file';
  const root = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), 'astra-live-native-')));
  const output = path.resolve(__dirname, '../../artifacts/fc5373-audit-fixes');
  fs.mkdirSync(output, {recursive:true});
  try {
    for (const dir of ['Assets', 'Packages', 'ProjectSettings']) fs.mkdirSync(path.join(root,dir));
    fs.writeFileSync(path.join(root,'Packages/manifest.json'),'{}');
    fs.writeFileSync(path.join(root,'ProjectSettings/ProjectVersion.txt'),'m_EditorVersion: 6000.0.1f1');
    // The oracle exists only in files read through the native adapter.
    const oracle = Object.fromEntries(['A','B','C'].map(name => [`Assets/${name}.txt`,randomInt(100000,1000000)]));
    for (const [file,value] of Object.entries(oracle)) fs.writeFileSync(path.join(root,file),`FACT=${value}\n`);
    let revision = null;
    if (gitMode) {
      const git = (...args) => execFileSync('git',args,{cwd:root,encoding:'utf8',windowsHide:true}).trim();
      git('init','-q'); git('add','.');
      git('-c','user.name=Audit Fixture','-c','user.email=fixture@example.invalid','commit','-qm','native fixture');
      revision = git('rev-parse','HEAD');
    }
    const runtime = createRuntime({UNITY_PROJECT_ROOT:root});
    const definition = runtime.tools.find(t => t.name === toolName);
    const calls = [], events = [], blocks = [], nativeResults = [];
    const read = rawFunctionTool({name:definition.name,description:definition.description,
      parametersJsonSchema:definition.inputSchema, implementation:async args => {
        calls.push(args.path);
        const value = await runtime.call(toolName,args); nativeResults.push(value); return value;
      }});
    read.pluginIdentifier = 'mcp/unity-tools';
    const client = new LMStudioClient(), model = await client.llm.model(process.env.ASTRA_MODEL || 'swift-qwen3.8-27b');
    const loadedContextLength = await model.getContextLength();
    const config = {contextManagementMode:'hybrid',projectEngine:'unity',projectIdentity:root,showDebugInfo:true,
      maxOutputReserve:8192,safetyMarginTokens:2048,auditCompletionMode:'off',inputAvailabilityMode:'inject'};
    const signal = AbortSignal.timeout(300000);
    const prompt = `Read each of ${Object.keys(oracle).join(', ')} once using ${toolName}. ${revision ? `Use revision ${revision}.` : ''} `
      + 'Return only one JSON object mapping each exact path to its numeric FACT value. Do not repeat source reads.';
    const ctl = {abortSignal:signal,guardAbort(){if(signal.aborted)throw signal.reason},
      getPluginConfig(){return {get:k=>config[k]}},getWorkingDirectory(){return root},
      async pullHistory(){return Chat.from([{role:'user',content:prompt}])},async tokenSource(){return model},
      async startToolUseSession(){return {tools:[read],[Symbol.dispose](){}}},
      async requestConfirmToolCall(){throw new Error('Native read unexpectedly requested approval')},
      debug(e){events.push(e)},createStatus(){return {setText(){},setState(){},remove(){}}},
      createContentBlock(options={}){const block={...options,text:''};blocks.push(block);return {
        appendText(t){block.text+=t},appendToolRequest(){},appendToolResult(){},setStyle(style){block.style=style}}}};
    let failure = null;
    try {await createPredictionLoopHandler()(ctl)} catch(error) {failure=String(error.stack || error)}
    const round = events.filter(e=>e.event==='direct_round_observation').at(-1);
    const result = evaluateCompletion({text:visibleBlocks(blocks),finishReason:round?.finishReason,calls,oracle,failure});
    const nativeSucceeded = nativeResults.length===3 && nativeResults.every(value=>!value.errorCode && value.ok!==false
      && value.kind===(gitMode?'git_observation':'workspace_file_observation'));
    const report = {status:result.success && nativeSucceeded ? 'PASS':'FAIL',loadedContextLength,
      configuration:config,oracle,calls,failure,validation:result,nativeSucceeded,nativeResults,events,
      scope:'Production handler + actual LM Studio SDK + native Unity filesystem/Git adapter and published schema in a temporary project. No MCP transport, installed GUI or editor workflow claim. No long-retention claim.'};
    const destination = path.join(output,gitMode?'live-native-git.json':'live-native-file.json');
    fs.writeFileSync(destination,JSON.stringify(report,null,2));
    console.log(JSON.stringify({status:report.status,loadedContextLength,calls,failure,destination}));
    if(report.status!=='PASS')process.exitCode=1;
  } finally {
    if(path.dirname(root)!==fs.realpathSync.native(os.tmpdir()) || !path.basename(root).startsWith('astra-live-native-'))
      throw new Error('Unexpected fixture cleanup path');
    fs.rmSync(root,{recursive:true,force:true});
  }
}
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1});
