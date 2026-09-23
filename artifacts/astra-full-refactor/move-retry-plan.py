from pathlib import Path
p=Path('lmstudio-context-compactor-plugin/src/prediction-loop.ts');s=p.read_text(encoding='utf-8')
a=s.index('      const exactRawTools =');b=s.index('      const runtimeDispatchCount',a);block=s[a:b]
q=Path('lmstudio-context-compactor-plugin/src/recovery-coordinator.ts');t=q.read_text(encoding='utf-8')
t='import { toolMemory } from "./context-ports";\nimport { isUnregisteredGitReadIntent } from "./tool-capability-registry";\n'+t
t+='''
export function planReadRecovery(historyBeforeRound:Chat,workingHistory:Chat,
  rawIntent:RawToolIntentClassification,modelTools:Array<RemoteToolLike>) {
'''+block+'''
  return {catalogueCorrectionAllowed,archiveOnlyRecoveryAllowed,retryTools,retryInstruction};
}
'''
s=s[:a]+'''      const {catalogueCorrectionAllowed,archiveOnlyRecoveryAllowed,retryTools,retryInstruction}
        = planReadRecovery(historyBeforeRound,workingHistory,rawIntent,modelTools);
'''+s[b:]
s='import { planReadRecovery } from "./recovery-coordinator";\n'+s
p.write_text(s,encoding='utf-8');q.write_text(t,encoding='utf-8')
