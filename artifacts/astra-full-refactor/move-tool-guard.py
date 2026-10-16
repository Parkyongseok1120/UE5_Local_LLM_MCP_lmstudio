from pathlib import Path
p=Path('lmstudio-context-compactor-plugin/src/prediction-loop.ts');s=p.read_text(encoding='utf-8')
a=s.index('          guardToolCall: async (_roundIndex, callId, controller) => {');b=s.index('\n        },\n        { maxTokens:',a)
block=s[a:b].strip().removeprefix('guardToolCall: ').removesuffix(',')
q=Path('lmstudio-context-compactor-plugin/src/tool-boundary.ts');t=q.read_text(encoding='utf-8')
t='''import type { LLMActionOpts, PredictionLoopHandlerController } from "@lmstudio/sdk";
import { bindProjectArguments, type ScopedTool } from "./tool-scope";
import { isObservationOnlyToolCall, type RemoteToolLike } from "./tool-capability-registry";
import { telemetryFingerprint } from "./evidence-telemetry";
import { reserveReadResult, type BatchReservation } from "./budget-broker";
import { toolPluginIdentifier, createMessageEmitter, createToolGenerationTracker } from "./prediction-ui";
import type { DirectConfig } from "./execution-contracts";
'''+t+'''
export function createToolGuard(options: {
  ctl: PredictionLoopHandlerController; emitter: ReturnType<typeof createMessageEmitter>;
  roundTools: Array<RemoteToolLike>; config: DirectConfig; scope: {projectIdentity:string};
  toolPlanningRetryRound: boolean; toolPlanningRetryBlockedFingerprints: Set<string>;
  batchReservation: BatchReservation|null; reservationIds: Map<string,string>;
  toolGeneration: ReturnType<typeof createToolGenerationTracker>;
  traceCall: (id:number,values:Record<string,unknown>)=>void;
}): NonNullable<LLMActionOpts["guardToolCall"]> {
  const {ctl,emitter,roundTools,config,scope,toolPlanningRetryRound,toolPlanningRetryBlockedFingerprints,
    batchReservation,reservationIds,toolGeneration,traceCall}=options;
  return '''+block+';\n}\n';q.write_text(t,encoding='utf-8')
s=s[:a]+'''          guardToolCall: createToolGuard({ctl,emitter,roundTools,config,scope,toolPlanningRetryRound,
            toolPlanningRetryBlockedFingerprints,batchReservation,reservationIds,toolGeneration,traceCall}),'''+s[b:]
s='import { createToolGuard } from "./tool-boundary";\n'+s;p.write_text(s,encoding='utf-8')
