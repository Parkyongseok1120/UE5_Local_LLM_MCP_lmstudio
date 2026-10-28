# v4 후속 수정 — 출력·복구·저장의 계약

기준: Develop `b43db72`. 대화 `1793102946974`의 읽기 전용 재감사에서 찾은 F1–F7을 수정했다. 원본 대화와 프로젝트 소스는 저장소에 복사하지 않았다. 당시 실행은 revision131의 실제 loaded/installed build가 일치했으며, 감사 턴의 마지막에 내부 reference JSON 1,376자와 continuation 23,986자가 표시되고 다음 턴용 window에도 저장됐다.

이 문서의 결과는 로컬 소스·빌드·합성 재현·provider 회귀다. 수정 후 실제 모델 성능, 설치 상태, 해당 원본 대화의 다음 턴 실행은 별도의 관찰이 필요하다. 진단 자료 별칭은 `${DIAGNOSTICS}/harness-v4-followup-1793102946974/`다.

## 변경한 계약과 소유자

| 결함 | 변경 | 기존 소유자 |
|---|---|---|
| F1 잘린 내부 JSON 공개 | 전용 header와 observation envelope를 확인한 후보는 EOS·취소·길이 상한에도 내부 분류를 유지한다. 큰 후보는 bounded discard로 처리하고 end marker 뒤 정상 본문은 전달한다. | InternalDataOutputFilter |
| F2 잘못된 출력 복구 | renderer가 만든 filtered prose를 GUI·완료 판정·model history가 공유한다. raw 생성량은 비용 계측이고 보고서 존재를 증명하지 않는다. 미실행 tool intent는 별도 관찰로 terminal finish까지 전달한다. | PredictionStreamRenderer, DeliveryController, ExecutionState |
| F3 window 오염 | 전용 protocol 계약을 reference renderer·출력 filter·durable 정리가 공유한다. 저장/구 window 복원에서 assistant text run을 정리하며 실제 tool parts와 record 위치, 사용자 원문을 보존한다. | WorkingContext, retainVisibleResponse |
| F4 metadata 부족 오안내 | 전체 응답 metadata 부족은 OUTPUT_LIMIT_EXCEEDED와 bounded retry.minimum으로 안내한다. 실제 긴 줄의 byte-window 복구는 유지한다. 계산은 변경된 budget 자릿수까지 포함한다. | Direct runtime payloadFits/serializer, 기존 BudgetBroker retry floor |
| F5 임시 후보의 통합 판정 | reference 선택 전 baseline에서는 아직 선택하지 않은 reference를 superseded로 판정하지 않는다. 완성된 채택 입력에서 통합을 확정하고, 그 재작성이 보류 topic을 허용하면 1회만 다시 선택한다. telemetry는 채택 결과 1건이다. | prepareRoundInput, ContextManager |
| F6 별도 크기 캐시 수명 | mutable source의 request→exact bytes 캐시를 제거했다. source는 기존 provider/project-scoped sizeHint 추정을 사용하고, exact 크기 재사용은 immutable archive 응답에 한정한다. | ReadLedger, EvidenceManager |
| F7 추론을 최종 주장으로 집계 | thinking text를 final delivered claims에서 제외한다. context 포함 스위치는 화면 표시 여부와 별개이며 일반 본문은 계속 집계한다. 도구 호출/결과도 계속 추적한다. | trace_lmstudio_run |

## 출력 결과의 단일화

`PredictionStreamRenderer.normalizeCaptured()`는 finalized message와 streaming 결과를 실행 범위 WeakMap으로 한 번 대응시킨다. 반환 text는 user-visible filter를 통과한 prose다. `historyMessage()`는 같은 prose에 기존 SDK reasoning prefix만 보존하므로 별도의 data-filter 재해석으로 화면과 다음 입력이 달라지지 않는다.

미실행 raw tool 문법은 화면에서 제외해도 제어 관찰은 사라지지 않는다. `unexecutedToolIntent`를 normalized result→DeliveryController.observe→classifyExecutionOutcome→classifyFinalDelivery까지 전달한다. 정상 prose 뒤 tool XML이 나오는 경우에도 완료 보고서로 승격하지 않는다. 이 flag는 실행 권한이나 실행된 tool 결과가 아니다.

내부 data뿐인 응답은 output recovery의 report continuation이 아니다. 이유가 reasoning 한도라면 기존 reasoning recovery 정책과 횟수 제한이 결정한다. 새 retry loop나 token budget 상향은 추가하지 않았다. 정상 보고서의 이어쓰기와 report_review의 중복 제거는 유지한다.

## 저장·복원의 수명 경계

`internal-data-contract.js`가 Derived header/end 및 envelope 식별을 소유한다. legacy 정리는 serialized assistant text run 전체를 읽어 text part/assistant block 경계를 넘어 fence와 protocol을 판정하고, 제거 범위를 원래 text part에 돌려준다. 메시지 수·tool part·raw result 위치가 유지돼 기존 bindings와 exchange 검증을 깨지 않는다.

`retainVisibleResponse()`는 host-visible prefix와 replacement 양쪽을 같은 정책으로 정리한다. `WorkingContext.persistable()`도 같은 경계를 적용한다. 이미 저장된 window는 기존 digest·scope·lineage·source-prefix 검증 이후 이 정리를 거쳐 모델 입력으로 복원된다. 원본 채팅이나 window 파일을 직접 고쳐 서명을 우회하지 않는다.

확인한 대조군은 정상 prose, fenced/quoted protocol 예제, text part 사이에 열린 fence, 사용자 입력의 같은 JSON, 실제 tool request와 같은 메시지에 있는 protocol, 완료된 tool 결과, 편집된 source prefix의 복원 거부다. 이 경계는 verified source·CAS·접근 권한을 새로 만들지 않는다.

## 읽기 응답과 예산

provider가 `payloadFits`로 metadata-only envelope가 현재 byteBudget에 들어가는지 확인한다. metadata부터 부족하면 serializer가 수용할 수 있는 최소 전체 응답 예산을 계산해 `retry.minimum={field:byteBudget,value:N}`과 같은 range의 suggestion을 보낸다. retry 직렬화는 해당 최소치가 정수이고 공개 범위2048..65536 안일 때만 보존한다.

계산 후보의 `effectiveByteBudget`와 continuation suggestion budget도 후보 N으로 측정한다. 4자리에서5자리로 늘어나는 경우도 같은 예산으로 재시도할 수 있다. 최소치는 최소 한 줄을 위한 값이며 전체 요청 range의 완료를 보장하지는 않는다. 후속 range cursor는 실제 반환 마지막 줄 바로 다음이다.

완료된 range는 continuation metadata가 없어 partial range보다 작은 응답일 수 있다. 따라서 전체 요청이 맞는지를 먼저 검사하고, 안 맞을 때만 기존 부분 페이지 탐색을 한다. 실제로 한 줄이 현재 budget을 넘고 metadata 자체는 들어가는 경우의 LINE_TOO_LARGE/CRLF byte cursor 계약은 유지한다.

하네스는 provider의 기존 retry minimum 관찰 경로를 재사용한다. source의 이전 반환 byte 수는 source version이나 현재 provider 상태의 증명이 아니므로 정확 예약에서 제거했다. legacy resultSizes 필드는 더 이상 복원·저장하지 않는다. unknown 첫 읽기, sizeHint의 성공 mutation 무효화, batch overrun 감시와 다음 exact template gate는 그대로다.

## 후보·채택 경계

reference 선택을 기다리는 baseline은 shown reference parts를 stale 길이 계산에서 제외한다. non-reference runtime fact의 명백한 초과는 기존 강제 재작성으로 취급할 수 있다. reference를 포함하거나 생략한 채택 composition에서 최종 강제 통합을 판정한다.

최종 통합 때문에 그 라운드가 실제 재작성으로 바뀌고 pending/dropped guidance가 있다면 한 번만 선택·측정을 반복한다. 선택되지 않은 후보의 dataBlock은 shownDataBlocks에 commit하지 않는다. guidance event는 최종 선택 1건만 기록한다. 기존 phase transition·엔진 적용성 무효화·예산 제거 예외와 exact dispatch 검사는 유지한다.

## 회귀와 결과

| 검사 | 기준 | 현재 결과 |
|---|---:|---:|
| plugin clean build/전체 | 1181 통과 | 1197 통과 / 실패0 / 제외0 |
| Unreal agent | 370 통과 / 제외2 | 372 통과 / 실패0 / 제외2 |
| Unity | 132 통과 / 제외5 | 132 통과 / 실패0 / 제외5 |
| Python 전체 | 1489 통과 / 제외41 | 1490 통과 / 실패0 / 제외41, 249.94초 |
| 생성물 | runtime/tool/design/unity | 4종 모두 소스와 일치 |

주요 추가 검증:

- 변형 미완성 protocol의 1자·7자·일괄 fragment, EOS/finish 중복, 270K overflow 뒤 정상 본문.
- streaming/fallback/model history 동일 결과, reasoning 보존, raw usage가 있는 빈 출력의 report 복구 거부.
- 정상 prose+raw tool intent의 terminal 완료 거부, mixed tool part와 multipart fence 보존.
- 구 writer의 유효한 window에 오염이 있는 경우 복원 정리, source prefix 편집은 계속 거부.
- 동일 current ref의 baseline 오판 방지, final reference consolidation에서 pending topic 적용과 event 1건.
- metadata가 큰 짧은 세 줄의 bounded retry, minimum budget 자릿수 경계, 기존 실제 긴 줄 byte cursor.
- legacy exact source size cache 배제, thinking-only claim 배제.
- 실제 prediction handler에서 변형된 내부 JSON이 max tokens로 끊겨도 스트리밍/비스트리밍 모두 output_recovery_scheduled=0, reportDelivered=false.

독립 검토에서 찾은 prose+tool intent·multipart sanitizer·최종 reference 통합·budget 자릿수 경계도 회귀에 포함했다. 기존 회귀 기대를 약화하지 않았다. 새 minimum 예산 테스트는 최소 한 줄의 성공·정확한 cursor를 검사하며, 전체 range를 한 번에 받는다는 잘못된 기대는 두지 않았다.

## 범위와 남은 확인

구성 요소의 공개 동작 변경에 따라 plugin0.4.85/revision132, Unreal agent0.3.31로 올렸다. 제품 릴리스 태그나 GitHub 게시물은 만들지 않았다.

실모델의 긴 reasoning 자체, 출력 언어·감사 정확도, backend KV-cache hit 수는 이 수정의 자동 테스트로 증명되지 않는다. 원본 대화의 full template이 없어 과거 최초 mismatch와 후보별 prefix는 완전히 복원하지 못했다. 새 실측에서는 정상 보고서 생성·내부 JSON 표시0·window 오염0·거짓 완료0을 먼저 확인하고, 같은 모델/snapshot으로 prefill과 후속 읽기를 비교해야 한다.

증거 JSON의 형식 검사는 packet 구조와 기록 일관성만 확인하며 사실성을 인증하지 않는다.
