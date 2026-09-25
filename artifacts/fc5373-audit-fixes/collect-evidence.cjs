"use strict";
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'../..'), plugin=path.join(root,'lmstudio-context-compactor-plugin');
const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
const git=(...args)=>execFileSync('git',args,{cwd:root,encoding:'utf8',windowsHide:true}).trim();
const base=git('rev-parse','HEAD');
const files=[...new Set(git('ls-files','-m','-o','--exclude-standard').split('\n'))]
  .filter(p=>p.startsWith('lmstudio-context-compactor-plugin/')).sort()
  .map(p=>({path:p,sha256:sha(fs.readFileSync(path.join(root,p)))}));
const productPatch={baseSha:base,files};
fs.writeFileSync(path.join(__dirname,'patch-manifest.json'),JSON.stringify({...productPatch,
  productPatchSha256:sha(JSON.stringify(productPatch)),definition:'SHA256 of JSON {baseSha, files:[{path,sha256}]} in the recorded sorted order. Includes untracked product files; excludes reports and generated dist.'},null,2));

const {BudgetBroker,minimumReadResultTokens}=require(path.join(plugin,'dist/budget-broker'));
const config={workingInputTargetTokens:18000,workingInputTriggerTokens:22000,safetyMarginTokens:2048};
const broker=new BudgetBroker(config),measurement={contextLength:38912,outputReserve:8192,inputTokens:19000};
const unreal={name:'read_file',pluginIdentifier:'mcp/unreal-agent',parametersJsonSchema:{properties:{maxBytes:{type:'integer'}}}};
const unity={name:'read_file',pluginIdentifier:'mcp/unity-tools',parametersJsonSchema:{properties:{byteBudget:{minimum:1024,maximum:65536}}}};
const watermarks={metric:'Policy probes using supplied C=38912, not current LM Studio measurements',config,
  noRead:broker.watermarks(measurement,0,false),unityRead:broker.watermarks(measurement,0,true,minimumReadResultTokens([unity])),
  unrealRead:broker.watermarks(measurement,0,true,minimumReadResultTokens([unreal])),
  starvation:broker.watermarks(measurement,19000,true,minimumReadResultTokens([unreal]))};
fs.writeFileSync(path.join(__dirname,'watermark-probes.json'),JSON.stringify(watermarks,null,2));

const source='lmstudio-context-compactor-plugin/src/',tests='lmstudio-context-compactor-plugin/test/';
const claims=[
  ['예산 부족과 exact 측정 불가를 dispatch/commit 전에 구분하고, 가능한 경우 근거를 보존한 최종 보고로 전환한다.',
    'context-manager.ts','enforceLowWater','prediction-loop.ts','context_execution_blocked',
    'Legacy/observeOnly는 명시적인 호환 경로이며 이 제한을 적용하지 않는다.'],
  ['native provider의 paired 결과를 공통 identity/query로 처리하고 SDK error 및 원본 실패를 성공 progress로 승격하지 않는다.',
    'evidence-manager.ts','observationRecords','evidence-manager.ts','observeRecoveryProgress',
    'Registry 없는 pure range 단위 검사 입력은 result-only sample을 허용하지만 production 경로는 pairing을 요구한다.'],
  ['capture/project archive ID를 일치시키고 pending omission을 durable 저장 전 거부하며 저장용 capability/reasoning을 제거한다.',
    'working-context.js','captureReturned','working-context.js','commit',
    'typed file, 범위/무결성, stale-prefix 거부는 유지되며 unconsumed 결과는 GC로 조용히 버리지 않는다.'],
  ['읽기 operation을 read profile에 유지하고 raw retry 후 source/archive 도구 목록을 보존한다.',
    'tool-capability-registry.ts','readProfile','tool-boundary.ts','createToolGuard',
    'Mutation은 여전히 host 승인 대상이고 좁혀진 read profile의 write action은 guard가 거부한다.'],
  ['잘린 planning을 commit하지 않고 보고 EOS와 목표 달성을 별도 값으로 처리한다.',
    'round-loop.ts','runOneToolRound','delivery-controller.ts','evaluate',
    '이미 반환된 tool evidence는 실패/취소 후에도 보존하며 정상 EOS/stopString planning은 허용한다.'],
  ['입력 조립 단계별 fingerprint/token 수와 RPC 비용을 구분하고 동일 라운드의 동일 후보만 캐시한다.',
    'context-manager.ts','createInputAssembler','context-manager.ts','measureContext',
    'Typed file 및 실패한/inexact 측정은 캐시하지 않으며 summary/preparation RPC 비용은 이 scope에 포함하지 않는다.']
].map(([claim,file,symbol,out,observer,counter])=>({claim,claimType:'codegen',verdict:'ByDesign',severity:'P1',proofLevel:'TestVerified',
  evidence:[{kind:'project_source',location:source+file,observation:symbol+'에서 정책을 적용한다.'},
    {kind:'test',location:tests+'audit-fc5373.test.cjs',observation:'검사 이름과 결과는 test-verification.log에 저장한다.'}],
  behaviorPath:[{stage:'entry',stageStatus:'present',location:source+file,symbol},
    {stage:'decision',stageStatus:'present',location:source+file,symbol:'입력 및 상태 계약 검사'},
    {stage:'observer',stageStatus:'present',location:source+out,symbol:observer}],
  counterEvidence:[{kind:'project_source',location:source+file,observation:counter}],
  unknowns:['실제 LM Studio 38,912 context 및 380K 누적 production 실행은 이번 패치에서 미검증.']}));
const packet={mode:'codegen',claims,
  invariants:['최종 exact input <= effective LOW가 compaction 성공 조건','Returned evidence를 cancel/retry에서 유실하지 않음',
    '출력 종료와 목표 달성을 혼동하지 않음','Mutation 권한을 evidence/자연어에서 만들지 않음'],
  impactedSurfaces:['BudgetBroker/ContextManager','EvidenceManager/WorkingContext','Capability/ToolBoundary','Recovery/Delivery','측정 harness'],
  validationPlan:['npm test: plugin/Unity/Unreal','30-cycle deterministic soak','140-observation archive lifecycle',
    'SDK 원문 및 별도 read-only reviewer 반례 검토','Loaded-model 조회 후 runtime 검증 가능성 확인'],
  doNotDuplicate:['기존 archive/continuity core/range matcher','기존 phase machine/host approval','기존 objective text continuity']};
fs.writeFileSync(path.join(__dirname,'audit.json'),JSON.stringify(packet,null,2));
console.log(JSON.stringify({baseSha:base,productPatchSha256:sha(JSON.stringify(productPatch)),changedProductFiles:files.length}));
