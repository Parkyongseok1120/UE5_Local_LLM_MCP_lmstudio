# 감사 지침 재진입·압축 알림·인용 대조 구현 기록

2026-10-24. 마지막 LM Studio 감사 실행의 로그를 대조해 원인을 세 가지로 좁혔고, 그에 대한 소스 후보를 구현했다. 자동 검사는 마쳤다. **로컬 설치, 실모델 재실행, GUI 확인은 하지 않았다.** 공개 버전과 Compactor 0.4.82 / revision 129 표기는 바꾸지 않았다.

## 근거로 삼은 실행

원본은 `%USERPROFILE%/.lmstudio/conversations/1792461970478.conversation.json`의 message 3이다(파일 SHA-256 `4bf1a45a9574f6448f6732297c895fe55f25f2c43fff86fb6d050d341643128f`). 실행 `969a6bbc-f5ab-4cc7-bf5c-14c13dbcab41`에서 모델을 18회 호출했다. 모델은 `swift-1.5-qwen3.8-27b`, 설정은 context 84,224 / trigger 56,000 / target 40,000이다. 첨부 보고서와 대화 속 지시문은 분석 자료로만 다뤘다.

| 라운드 | 입력(토큰) | 지침 | 관찰 |
|---|---|---|---|
| r10 | 48,520 | included | 메모 속 코드 인용이 원문과 일치 |
| r12 | 55,699 (+1,579이면 high water 초과) | `sticky_dropped / budget_omitted` | — |
| r13 | projection 20건 → 47,927 | `dropped_earlier` | 16K자 분석에 `PlaySound2D`, `TraceStart`, "EndPlay 없음"이 처음 등장 |
| r15–16 | projection 23건, low-water 압축 → 30,829 | `dropped_earlier` | packet에 `PlayerState.h:24-25 ReplicatedUsing=OnRep_bIsDead` 기록, 구조 검사 통과 |
| r17 | 32,905, tool result 5건 | `dropped_earlier` | 최종 보고서. packet의 잘못된 인용이 그대로 들어감 |

같은 Qwen 3.8 27B로 헤르메스를 사용한 결과와 비교하면, 이번 차이는 모델 능력보다 압축 이후 입력에서 원문을 얼마나 볼 수 있는지와 더 관련이 있었다. r10처럼 원문이 입력에 있는 동안에는 인용이 정확했다.

## 변경

1. **지침 재진입**: [round-input.ts](../lmstudio-context-compactor-plugin/src/round-input.ts)
   - 제외된 지침 문구를 보존한다. 다음 조건 중 하나에서만 같은 문구로 한 번 다시 넣는다.
     - 같은 라운드에 projection, compaction 또는 low-water 변경으로 입력이 다시 쓰였다(`input_reshaped`).
     - 입력이 설정한 low water 이하다(`low_water`).
   - 성공하면 `sticky_readmitted`를 기록한다. 실패하거나 조건 밖이면 기존처럼 `dropped_earlier`를 유지한다.
   - effective low water가 아니라 configured low water를 기준으로 삼는다. mandatory floor 예외에서는 effective low water가 high water와 같아지므로, effective 값을 쓰면 지침이 라운드마다 들어갔다 빠진다.
   - 이번 실행에 대입하면 r13과 r16에 재진입한다. r15는 입력이 high water를 넘으므로 다시 제외된다.
2. **projection 알림의 시점과 내용**: [resume-facts.ts](../lmstudio-context-compactor-plugin/src/resume-facts.ts), [context-manager.ts](../lmstudio-context-compactor-plugin/src/context-manager.ts), [prediction-loop.ts](../lmstudio-context-compactor-plugin/src/prediction-loop.ts)
   - 기존에는 직전 라운드 입력으로 알림을 계산했다. 이제 조립 중인 history에서 계산하므로, 압축이 일어난 라운드의 입력에 바로 들어간다.
   - 결과 단위 마지막 6건 대신 파일 단위로 묶고 줄 범위를 보여 준다. 최대 10개 파일, 약 2.4k자다.
   - 문구를 추가했다. 가려진 범위의 호출·인자·조건·UPROPERTY/UFUNCTION 지정자는 현재 입력에 없다. 앞선 메모나 packet의 코드는 원문이 아니다. 인용하거나 부재를 판단하기 전에 그 범위만 다시 읽어야 한다.
3. **인용 대조**: [quote-anchoring.ts](../lmstudio-context-compactor-plugin/src/quote-anchoring.ts)
   - evidence_first_validate packet의 `project_source` 근거를 화면에 남은 반환 원문과 이름 단위로 비교한다. 비교 대상은 `A::B`, 호출 이름, `OnRep_*`, 매크로 안의 네트워크 지정자, `COND_*`이다.
   - 반환된 어느 원문에도 없는 이름은 `not_in_returned_source`로 보고한다.
   - 짧은 인용(3줄 미만)이 다른 줄에 있으면 `not_at_cited_lines`로 보고한다. 단, 호출·멤버 이름은 위쪽 30줄 안에 있는 감싸는 함수 이름이면 허용한다.
   - 불일치는 다음 입력의 runtime fact `quote_check`로 전달하고, 다음 packet에서 일치하면 지운다. 최종 답변이 나올 때 남아 있으면 `[인용 대조]` 꼬리말을 붙인다.
   - 진행을 막지 않는다. 도구 결과·packet·답변 본문을 바꾸지 않으며 사실 검증이라고 표시하지 않는다.
   - 최종 답변의 코드 블록 자체는 대조하지 않는다. 제안 코드에는 원래 원문에 없는 이름이 들어가므로 오탐이 생기기 때문이다.
4. **trace**: [trace_lmstudio_run.py](../scripts/trace_lmstudio_run.py)가 `design_guidance_input`, `quote_anchoring_check`, `quote_anchoring_notice`와 sticky 필드를 보존한다.

지침 본문, evidence-first 판정 규칙, 읽기 도구 정책, projection 크기는 바꾸지 않았다.

## 검증

| 검사 | 결과 |
|---|---|
| Compactor 전체 Node 테스트 | **879 pass / 0 fail** (기존 870 + 새 9). 기존 기대값 3곳을 새 정책에 맞게 수정함: 영구 제외, 다음 라운드 알림, 결과 6건 목록 |
| build, runtime identity / guidance / tool contract check | 통과. 후보 build ID `ef-build-v2-c556ac0851636294cae8792038e4fe4c0ef8045088423bbb5b88ffe47784a97d` |
| 이번 실행 packet 재생 | packet 2개 모두 정확히 2건: `PlaySound2D`(실제 342행 `PlaySoundAtLocation(..., Start)`), `OnRep_bIsDead`(PlayerState.h에 없음). 나머지 evidence 오탐 0 |
| 최근 대화 12개 packet 재생 | evidence 155건 중 10건 보고, 직접 대조 결과 모두 실제 불일치. 예: 줄 번호 오기 :21→:27, :104-105→:125, :195-196→:236; 존재하지 않는 `OnRep_Armor`/`OnRep_MaxHealth`/`OnRep_IsDead` |
| Python trace 테스트 | 12 pass |
| Python 패키지/CI 일부 | 76 pass, 3 fail. 실패 3건은 이 세션의 cmd.exe 하위 프로세스에서 `node`/`npm`을 찾지 못한 환경 문제이며 변경과 무관. 같은 이유로 `npm run build` 대신 각 build 단계를 node로 직접 실행함 |

**미완료**:
- 로컬 설치 후 같은 요청을 같은 모델·설정으로 다시 실행한다. 확인할 항목:
  - projection 라운드의 `sticky_readmitted`
  - 같은 라운드에 들어간 projection 알림
  - `quote_check` 후의 재읽기
  - 최종 인용 왜곡 수
  - 재진입에 따른 prompt 처리 시간 증가
  - 이전 reasoning 메모가 실제 입력에 포함되는지
- 실모델 1회 결과를 개선 확정으로 보지 않는다.
- 지침 본문 평가(Unreliable 효과, P1 근거 수준, 다음 검사 추적, 설계 문서 범위)는 지침이 최종 라운드까지 유지되는 것을 확인한 뒤 별도로 진행한다.
