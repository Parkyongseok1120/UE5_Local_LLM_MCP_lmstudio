from pathlib import Path
p=Path('lmstudio-context-compactor-plugin/src/prediction-loop.ts')
s=p.read_text(encoding='utf-8')
a=s.index('      const assembleModelInput = async (sourceHistory: Chat, profile: {')
b=s.index('      const assembleRecoveryCandidate = async (',a)
block=s[a:b].strip().replace('const assembleModelInput = async','return async',1)
block=block.replace('activeNote','getNote()').replace('profile.outputReserve ?? roundOutputReserve','profile.outputReserve ?? getRoundOutputReserve()')
c=Path('lmstudio-context-compactor-plugin/src/context-manager.ts')
t=c.read_text(encoding='utf-8')
t+='''
export type InputAssemblyOptions = {
  tokenSource: unknown; config: DirectConfig; roundTools: Array<RemoteToolLike>;
  roundScopeInstructions: Array<string>; getRoundOutputReserve: () => number;
  modelInputId: string; noteEnabled: boolean; getNote: () => ContinuityNote | null;
  historicalAvailabilityLedger: Array<Record<string, unknown>>;
};
export function createInputAssembler(options: InputAssemblyOptions) {
  const {tokenSource, config, roundTools, roundScopeInstructions, getRoundOutputReserve,
    modelInputId, noteEnabled, getNote, historicalAvailabilityLedger} = options;
'''+block+'\n}\n'
t=t.replace('core, modelNotes, workingContextModule','core, modelNotes, workingContextModule, inputAvailability')
c.write_text(t,encoding='utf-8')
s=s[:a]+'''      const assembleModelInput = createInputAssembler({ tokenSource, config, roundTools,
        roundScopeInstructions, getRoundOutputReserve: () => roundOutputReserve, modelInputId,
        noteEnabled, getNote: () => activeNote, historicalAvailabilityLedger });
'''+s[b:]
s='import { createInputAssembler, ContextManager } from "./context-manager";\nimport { BudgetBroker } from "./budget-broker";\n'+s
s=s.replace('  const execution = new ExecutionState();','  const execution = new ExecutionState();\n  const budgetBroker = new BudgetBroker(config);\n  const contextManager = new ContextManager(config, budgetBroker);')
# After all semantic/availability reassembly and emergency rescue, verify actual final input.
a=s.index('      const workingInputTargetActive =')
b=s.index('      let workingWindowCommitted =',a)
s=s[:a]+'''      const lowWater = await contextManager.enforceLowWater(workingHistory, assembleModelInput, {
        force: compacted || projectionApplied,
        hasReadTools: roundTools.some(tool => isObservationOnlyToolCall(tool, {name:tool.name,arguments:{}})),
      });
      if (lowWater.changed) {
        workingHistory = lowWater.history;
        modelHistory = workingHistory;
        assembledInput = lowWater.assembled;
        modelComposition = assembledInput.composition;
        modelInput = assembledInput.history;
        finalMeasurement = assembledInput.measurement;
        compactionCheckpoint = lowWater.checkpoint;
        compacted = lowWater.success;
        compactionAppliedCount += 1;
        compactionAppliedModes.push("final_exact_low_water");
      }
      const mandatoryFloorMeasurement = lowWater.floorMeasurement;
      const mandatoryFloorExceedsTarget = Boolean(mandatoryFloorMeasurement?.exact
        && mandatoryFloorMeasurement.inputTokens > config.workingInputTargetTokens);
      if (config.showDebugInfo && lowWater.telemetry) ctl.debug({ event: "context_low_water", executionId,
        modelInputId, roundIndex, ...lowWater.telemetry });
'''+s[b:]
s=s.replace('&& finalMeasurement.remainingTokens >= 0) {\n        try {','&& finalMeasurement.remainingTokens >= 0 && (!compacted || lowWater.success)) {\n        try {')
p.write_text(s,encoding='utf-8')
