from pathlib import Path
p=Path('lmstudio-context-compactor-plugin/src/context-budget.ts')
s=p.read_text(encoding='utf-8')
a=s.index('export type GenerationBudget')
b=s.index('// Candidate sizes',a)
budget=s[a:b]
p.write_text(s[:a]+'export { resolveGenerationBudget, type GenerationBudget } from "./budget-broker";\n\n'+s[b:],encoding='utf-8')
p=Path('lmstudio-context-compactor-plugin/src/budget-broker.ts')
p.write_text(p.read_text(encoding='utf-8')+'\n'+budget,encoding='utf-8')
p=Path('lmstudio-context-compactor-plugin/src/context-manager.ts')
s=p.read_text(encoding='utf-8')
needle='''        const baseMeasurement = await measureProfileInput(baseComposition.history);'''
s=s.replace(needle,needle+'''
        const historyMeasurement = await measureContext(tokenSource, sourceHistory, config, [], {outputReserve:profileOutputReserve});
        const stages = async (input: Chat, measured: ContextMeasurement) => {
          const metadataMeasurement = await measureContext(tokenSource, input, config, [], {outputReserve:profileOutputReserve});
          return {postHistoryTokens:historyMeasurement.exact ? historyMeasurement.inputTokens : null,
            postProjectionTokens:historyMeasurement.exact ? historyMeasurement.inputTokens : null,
            postMetadataTokens:metadataMeasurement.exact ? metadataMeasurement.inputTokens : null,
            postToolSchemaTokens:measured.exact ? measured.inputTokens : null,
            finalExactInputTokens:measured.exact ? measured.inputTokens : null};
        };''')
s=s.replace('            composition: baseComposition,','            stages: await stages(baseComposition.history, baseMeasurement),\n            composition: baseComposition,')
s=s.replace('          composition,\n          history: composition.history,','          stages: await stages(composition.history, measurement),\n          composition,\n          history: composition.history,')
s=s.replace('finalExactInputTokens:final.inputTokens, targetDelta:', '...selected.assembled.stages, finalExactInputTokens:final.inputTokens, targetDelta:')
p.write_text(s,encoding='utf-8')
