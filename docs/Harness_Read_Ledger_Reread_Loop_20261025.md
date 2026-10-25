# 압축 뒤 재읽기 루프 차단 구현 기록 (읽기 원장)

2026-10-25. LM Studio 감사 실행 `1792842246501`(51라운드, 사용자 중단, 보고 없음)을 읽기 전용으로 분석한 뒤 승인된 계획(P0+P1+P2)대로 구현했다. 자동 검사는 마쳤다. **실모델 재실행은 하지 않았다.**

## 문제

- `LatencyMultiCombatCharacter.cpp`는 실행 내내 sha `3cddf4aa…` 그대로였지만 본문이 여러 번 다시 전달됐다.
- `read_file` 49회 중 33회가 이미 읽은 같은 버전을 다시 읽었다. 재읽기 묶음은 모두 `working_context_projection` 압축 직후였다.
- 모델이 Character.cpp의 `evidenceId`에 CombatTypes.h의 `version`을 붙여 `version_mismatch`를 받고, 파일이 바뀌었다고 잘못 믿었다.

## 변경

1. **읽기 원장 (P0-1)**: [read-ledger.js](../lmstudio-context-compactor-plugin/src/read-ledger.js)
   - `경로 + sourceVersion`마다 전달한 줄·바이트 범위, 라운드, 전달 횟수, archive 위치를 기록한다.
   - working window와 함께 저장·복원한다([working-context.js](../lmstudio-context-compactor-plugin/src/working-context.js)).
   - projection된 읽기도 checkpoint의 파일 관찰로 남긴다([compaction-tool-memory.js](../lmstudio-context-compactor-plugin/src/compaction-tool-memory.js) `projectedReadObservation`).
   - 본문이 입력에서 빠진 파일이 있으면 runtime fact로 "전달됨 / 이번 입력에 보임 / 검증됨"을 따로 보여 준다.
2. **같은 sha 재읽기 (P0-2)**: [evidence-manager.ts](../lmstudio-context-compactor-plugin/src/evidence-manager.ts) `screenReturnedReads`, [prediction-loop.ts](../lmstudio-context-compactor-plugin/src/prediction-loop.ts)
   - 이미 전달한 범위를 같은 sha로 다시 읽으면 본문 대신 `UNCHANGED` 사실과 archive 위치(`evidenceId`/`version`/`readHandle`, 선언 개요)를 넣고 `redundant_reread`를 기록한다.
   - 예외: 전달한 범위보다 좁은 읽기는 본문을 주고 "unchanged since round N"을 붙인다. sha가 다르면 항상 전체 본문을 준다. 읽을 수 있는 archive record가 없으면 본문을 준다.
   - 사용자 대화 기록에는 원래 본문이 남는다.
   - projection·archive된 결과도 repeat receipt를 보내고([tool-boundary.ts](../lmstudio-context-compactor-plugin/src/tool-boundary.ts)), `byteBudget` 재작성은 요청 키에서 뺀다.
3. **version 불일치 설명 (P0-3)**: [evidence-archive.js](../lmstudio-context-compactor-plugin/src/evidence-archive.js) `versionMismatch`
   - 그 version이 어느 record·파일의 것인지, 원래 record의 version과 원본 sha, "파일 수정이 아님"을 알려 준다. 최신 읽기 sha와 같으면 `sourceUnchanged: true`.
4. **감사 확인 (P1-1)**: [reference-context.ts](../lmstudio-context-compactor-plugin/src/reference-context.ts) `auditChecksFromPacket`
   - 제출된 evidence packet의 검증된 claim(`Proposed` 제외)마다 `claimType`을 인용 파일의 현재 sha에 묶어 기록한다. sha가 바뀌면 "re-check"로 표시한다.
5. **함수 단위 복원 (P1-2)**
   - `evidence_first_read_context`가 `symbol`(source outline 이름)로 한 선언만 읽는다.
   - partial projection 사실에 선언 span을 함께 보여 준다([resume-facts.ts](../lmstudio-context-compactor-plugin/src/resume-facts.ts)).
   - 부모 lineage나 record 자신의 lineage가 발급한 handle도 membership이 있으면 읽힌다. 없는 handle에는 그 경로의 유효한 handle을 돌려준다.
6. **새 근거 없음 → 보고 (P1-3)**
   - `redundant_reread`, 전달한 버전의 archive 재조회, 같은 버전의 좁은 재읽기, 실패한 위치 지정만 있는 라운드는 진전이 아니다.
   - `No-new-evidence rounds`(기본 3, [execution-config.ts](../lmstudio-context-compactor-plugin/src/execution-config.ts)) 연속이면 `report_now` 사실을 넣고, 두 번째에는 도구 없는 최종 보고(`research_no_new_evidence`)로 넘어간다. `Repeated tool rounds`가 Off면 하지 않는다.
7. **예산 (P1-4)**: [budget-broker.ts](../lmstudio-context-compactor-plugin/src/budget-broker.ts)
   - 같은 요청이 전에 반환한 크기를 알면 배치의 절반 대신 그 크기(×1.25 + 512 B)를 예약한다.
8. **분석기 (P2)**: [analyze_lmstudio_run.py](../scripts/analyze_lmstudio_run.py)
   - 파일별 본문 전달(경로 + sha + 범위, 소스 읽기와 archive 페이지), 중복 전달과 중복 토큰 추정, 압축 후 2라운드 안 재읽기, 다른 record version으로 인한 `version_mismatch`, 지어낸 handle을 센다.
   - 합성 회귀 fixture: [reread_loop.conversation.json](../tests/fixtures/lmstudio_run/reread_loop.conversation.json) (사용자 코드 없음).

## 수치 (실행 `1792842246501`)

| | 이전 (기록된 실행) | 이후 (같은 요청 순서를 새 코드로 재생) |
|---|---|---|
| 같은 버전 소스 재읽기 | 33회, 모두 본문 전달 | 33회 모두 `UNCHANGED` 사실로 대체 |
| 중복 전달 | 47회 (소스 33 + archive 재조회 14), 약 60.7k 토큰 | 소스 0회; archive 재조회 14회는 진전 아님으로 집계 |
| 줄어든 입력 | — | 약 135k자(약 38.6k 토큰) |
| 압축 후 2라운드 안 재읽기 | 15회 | 15회 모두 본문 없음 |
| 다른 record version 불일치 | 1회, 원인 설명 없음 | 소유 record·파일과 "파일 수정 아님"을 알려 줌 |
| 지어낸 handle | 1회, `read_handle_unavailable`만 | 그 경로의 유효한 handle 목록을 돌려줌 |
| 종료 | 51라운드 뒤 사용자 중단, 보고 없음 | round 8에 `report_now`, round 11 뒤 도구 없는 최종 보고 |

"이후" 열은 기록된 도구 결과를 같은 순서로 새 screening에 통과시킨 값이다. 모델이 `UNCHANGED`·`report_now`를 보고 다르게 행동하는 것은 반영하지 않았다.

## 남은 확인 (사용자)

1. 이 브랜치로 플러그인을 빌드·설치한다.
2. LM Studio에서 같은 감사 요청을 다시 실행한다.
3. `python scripts/analyze_lmstudio_run.py <새 conversation id>`로 확인한다.
   - 통과 조건: 최종 보고가 있다.
   - Character.cpp의 sha당 본문 전달이 1회 이하다(`suppressed by ledger`로 대체된 것은 제외).
   - "파일이 수정됐다"는 잘못된 주장이 없다.
