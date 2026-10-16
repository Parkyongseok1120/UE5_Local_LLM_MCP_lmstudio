from pathlib import Path
p=Path('lmstudio-context-compactor-plugin/src/prediction-loop.ts');s=p.read_text(encoding='utf-8')
q=Path('lmstudio-context-compactor-plugin/src/context-manager.ts');t=q.read_text(encoding='utf-8')
a=s.index('      let before = await measureRoundInput'); b=s.index('      const semanticCheckpoint',a)
block=s[a:b]
t+='''
export async function prepareWorkingInput(options: {
  tokenSource:unknown; config:DirectConfig; workingHistory:Chat; activeNote:ContinuityNote|null;
  noteEnabled:boolean; roundScopeInstructions:Array<string>; roundTools:Array<RemoteToolLike>;
  roundOutputReserve:number; beforeInput:ReturnType<typeof composeModelHistory>;
  measureRoundInput:(history:Chat)=>Promise<ContextMeasurement>;
  ctl:PredictionLoopHandlerController; executionId:string; roundIndex:number;
}) {
  let {workingHistory}=options;
  const {tokenSource,config,activeNote,noteEnabled,roundScopeInstructions,roundTools,
    roundOutputReserve,beforeInput,measureRoundInput,ctl,executionId,roundIndex}=options;
'''+block+'''
  return {before,modelHistory,workingHistory,compacted,compactionAppliedCount,compactionAppliedModes,
    compactionCheckpoint,compactionRetention};
}
'''
s=s[:a]+'''      let {before,modelHistory,compacted,compactionAppliedCount,compactionAppliedModes,
        compactionCheckpoint,compactionRetention} = await prepareWorkingInput({tokenSource,config,
          workingHistory,activeNote,noteEnabled,roundScopeInstructions,roundTools,roundOutputReserve,
          beforeInput,measureRoundInput,ctl,executionId,roundIndex});
      workingHistory=modelHistory;
'''+s[b:]
a=s.index('      const semanticCheckpoint');b=s.index('      const assembleModelInput',a)
block=s[a:b]
t+='''
export async function updateSemanticContext(options: {
  tokenSource:Parameters<typeof generateSemanticHandoff>[0]["tokenSource"]; config:DirectConfig;
  workingContext:InstanceType<typeof workingContextModule.WorkingContext>|null;
  compactionCheckpoint:CheckpointResult|null; compacted:boolean; projectionApplied:boolean;
  visibleHistory:Chat; activeNote:ContinuityNote|null; semanticSummaryCooldownUntilRound:number;
  boundedAudit:boolean; finalizing:boolean; roundIndex:number; executionId:string;
  objectiveFingerprint:string; ctl:PredictionLoopHandlerController;
}) {
  let {activeNote,semanticSummaryCooldownUntilRound}=options;
  const {tokenSource,config,workingContext,compactionCheckpoint,compacted,projectionApplied,visibleHistory,
    boundedAudit,finalizing,roundIndex,executionId,objectiveFingerprint,ctl}=options;
'''+block+'''  return {activeNote,semanticSummaryCooldownUntilRound};
}
'''
s=s[:a]+'''      ({activeNote,semanticSummaryCooldownUntilRound}=await updateSemanticContext({tokenSource,config,
        workingContext,compactionCheckpoint,compacted,projectionApplied,visibleHistory,activeNote,
        semanticSummaryCooldownUntilRound,boundedAudit,finalizing,roundIndex,executionId,objectiveFingerprint,ctl}));
'''+s[b:]
s='import { prepareWorkingInput, updateSemanticContext } from "./context-manager";\n'+s
# Projection uses the evidence owner, including its collision-safe authority lookup.
a=s.index('        const projected = workingContext.project(');b=s.index('        if (projected.changed)',a)
old=s[a:b];m=old.index('}, { executionId'); meta=old[m+3:]
s=s[:a]+'        const projected = evidenceManager.project(workingHistory, '+meta+s[b:]
q.write_text(t,encoding='utf-8');p.write_text(s,encoding='utf-8')
