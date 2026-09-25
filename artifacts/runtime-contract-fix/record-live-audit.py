"""Record additional live findings without changing the tested runtime."""
import json
from pathlib import Path

p = Path(__file__).with_name("audit.json")
packet = json.loads(p.read_text(encoding="utf8"))
packet["claims"] = packet["claims"][:3]
packet["claims"][2]["unknowns"] = ["Unity 파일 조회 이외의 실제 엔진 작업과 다른 모델의 장기 실행은 이번 live 테스트 범위가 아니다."]

def ev(kind, location, observation):
    return dict(kind=kind, location=location, observation=observation)

def stage(stage, location, symbol):
    return dict(stage=stage, stageStatus="present", location=location, symbol=symbol)

packet["claims"].extend([
    {"claim": "같은 READ_RECOVERY 구간에서 압축 후 exact 입력과 mandatory floor가 함께 상승하여 안정된 Low 복귀를 검증하지 못한다.",
     "claimType": "data_flow", "verdict": "Bug", "severity": "P1", "proofLevel": "RuntimeVerified",
     "evidence": [ev("runtime", "artifacts/runtime-contract-fix/live-gui.json", "첫 아홉 압축 입력: 19351,20274,21244,22063,21162,21956,22571,23303,23507. 각 값이 effectiveLow/mandatoryFloor와 같음."),
                  ev("project_source", "lmstudio-context-compactor-plugin/src/context-manager.ts:508", "maxCurrentTurnMessages=0 후보도 생성된 checkpoint를 포함하여 floor로 측정"),
                  ev("project_source", "lmstudio-context-compactor-plugin/src/budget-broker.ts:33", "effectiveLow=max(configuredLow,mandatoryFloor)")],
     "behaviorPath": [stage("entry", "src/context-manager.ts:enforceLowWater", "buildCompactedHistory floor candidate"),
                      stage("decision", "src/budget-broker.ts:watermarks", "effectiveLowWaterTokens"),
                      stage("observer", "artifacts/runtime-contract-fix/live-gui.json", "completed prediction exact input and low-water")],
     "counterEvidence": [ev("project_source", "src/direct-compaction-core.js:599", "checkpoint 문자 수 제한이 있으므로 무한 증가 자체가 증명된 것은 아님"),
                         ev("runtime", "artifacts/runtime-contract-fix/live-gui.json", "모든 해당 입력은 동적으로 상승한 effectiveLow를 충족; 새 사용자 턴 첫 입력은 17242로 하락")],
     "unknowns": ["장시간 후 checkpoint 포화 상태와 동일 단계 정상상태 baseline은 미확인; 새로운 사용자 턴 하락을 자동 compaction 안정성으로 취급하지 않음."]},
    {"claim": "정상 모드의 연속 읽기가 다음 페이지를 반환하고 있는데도 auditResearchRounds 한도로 최종 보고 단계에 들어가 미완료 종료한다.",
     "claimType": "state_transition", "verdict": "Bug", "severity": "P1", "proofLevel": "RuntimeVerified",
     "evidence": [ev("project_source", "lmstudio-context-compactor-plugin/src/prediction-loop.ts:1342", "boundedAudit 여부와 무관하게 recoveryCoordinator.advance에 config.auditResearchRounds 전달"),
                  ev("project_source", "lmstudio-context-compactor-plugin/src/recovery-coordinator.ts:21", "진행한 pagination 횟수가 maximum 이상이면 research_recovery_complete"),
                  ev("runtime", "artifacts/runtime-contract-fix/live-gui-events.json", "첫 실행12회 recovery 후 lastLine280/total680/hasMore=true, final report unresolved_tool_intent")],
     "behaviorPath": [stage("entry", "src/prediction-loop.ts:628", "blocked_next_action to FINAL_DELIVERY"),
                      stage("dispatch", "src/prediction-loop.ts", "fresh tool planning / READ_RECOVERY"),
                      stage("decision", "src/recovery-coordinator.ts:21", "paginationBound"),
                      stage("mutation", "src/prediction-loop.ts:1346", "finishResearch"),
                      stage("observer", "artifacts/runtime-contract-fix/live-gui-events.json", "partial_report_delivery unresolved_tool_intent")],
     "counterEvidence": [ev("project_source", "src/delivery-controller.ts:evaluate", "부분 보고를 taskCompleted로 승격하지 않는 방어는 정상 동작"),
                         ev("runtime", "artifacts/runtime-contract-fix/live-verification.json", "반환된 페이지의 내용/해시/범위는 원문과 일치")],
     "unknowns": ["bounded recovery의 제한 자체는 안전 정책일 수 있으나 일반 장기 조회를 완료시키는 실행 정책과 연결되지 않았다."]}
])
p.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf8")
