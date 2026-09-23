from pathlib import Path
p=Path('lmstudio-context-compactor-plugin/src/prediction-loop.ts');s=p.read_text(encoding='utf-8')
s='import { RecoveryCoordinator } from "./recovery-coordinator";\nimport { DeliveryController } from "./delivery-controller";\n'+s
s=s.replace('  let finalizationAttempts = 0;', '  const deliveryController = new DeliveryController();\n  const recoveryCoordinator = new RecoveryCoordinator();')
s=s.replace('        finalizationAttempts += 1;', '        deliveryController.attempts += 1;')
s=s.replace('finalizationAttempts,','finalizationAttempts: deliveryController.attempts,')
import re
s=re.sub(r'(?<![\w.:])finalizationAttempts(?!:)', 'deliveryController.attempts',s)
s=s.replace('attempt: finalizationAttempts: deliveryController.attempts,','attempt: deliveryController.attempts,')
a=s.index('        const reportText = captured.messages',s.index('const phaseTimedOut'))
b=s.index('        if (safePartialReport) {',a)
s=s[:a]+'''        const evaluated = deliveryController.evaluate(workingHistory, captured, phaseTimedOut,
          execution.finalizationTrigger, recoveryCoordinator.noProgressRounds);
        const finalDelivery = evaluated.delivery;
        const {deliveryState} = finalDelivery;
        const safePartialReport = evaluated.partial;
'''+s[b:]
a=s.index('      if (researchRecoveryRound) {',s.index('const phaseTimedOut'))
b=s.index('      if (boundedAudit && captured.finishReason',a)
s=s[:a]+'''      if (researchRecoveryRound) {
        const decision = recoveryCoordinator.advance(recoveryProgress, config.auditResearchRounds,
          captured.continueAfterTools, finalRawToolIntent, structuredToolRequestCount,
          actualResultCount, visibleTextFromMessages(captured.messages).trim());
        if (decision.trigger) {
          execution.finishResearch(decision.trigger);
          if (config.showDebugInfo) ctl.debug({event:decision.noProgress
            ? "read_only_research_recovery_exhausted":"read_only_research_recovery_completed",
            executionId,modelInputId,recoveryReason:researchRecoveryReason,
            toolRounds:recoveryCoordinator.toolRounds,paginationRounds:recoveryCoordinator.paginationRounds,
            maxPaginationRounds:decision.maxPaginationRounds,noProgressRounds:recoveryCoordinator.noProgressRounds,
            recoveryProgress,nextPhase:"final_report"});
          roundIndex++; continue;
        }
        if(decision.endRecovery) execution.endRecovery();
        if(decision.continue) {roundIndex++;continue;}
      }
'''+s[b:]
for old,new in [('researchRecoveryToolRounds','toolRounds'),('researchRecoveryPaginationRounds','paginationRounds'),('researchRecoveryNoProgressRounds','noProgressRounds')]:
 s=s.replace('  let '+old+' = 0;\n','')
 s=re.sub(r'\b'+old+r'\b','recoveryCoordinator.'+new,s)
p.write_text(s,encoding='utf-8')
