# 하네스·컴팩터·도구 계약 v4 — 실측 결함 수정

## 1. 범위와 확인 수준

v3 구현 기준 `5663998`에서 시작해 C1–C8을 유지하며 D1–D9와 C9–C16을 구현했다. 소유자는 기존 `ReadLedger`, `EvidenceManager`, `BudgetBroker`, `ContextManager`, `prepareRoundInput`, 출력 renderer다. 새 전역 관리자나 실행 권한 계층은 추가하지 않았다.

검증 범위는 로컬 소스·빌드·합성 fixture 및 provider 회귀다. **v4 설치와 실모델 성능 측정은 하지 않았다.** 아래 v3 수치는 읽기 전용 대화 `1793075034058`에 대한 작업 명세의 실측 근거이며, v4 테스트 결과와 구분한다. 대화 원문과 사용자 소스는 저장소에 복사하지 않았다. 진단 로그 공개 별칭은 `${DIAGNOSTICS}/harness-contract-v4-20261027/`이다.

## 2. v3 비용 기준선

| 턴 | 라운드 | prefill 합 | 생성 합 | prompt 토큰 합 | prefill > 20초 | 예산 거부 |
|---|---:|---:|---:|---:|---:|---:|
| msg3 감사 | 17 | 503초 | 611초 | 749,481 | 8회 | 1 |
| msg5 수정 | 12 | 381초 | 534초 | 589,152 | 6회 | 2 |

v3 W7 첫 실행에서 수정→빌드→자동화 테스트 3/3은 완료됐다. 아래는 명세에 기록된 20초 초과 prefill 14건이다. 당시 로그에는 `prefix_reuse`가 없으므로 특히 data 통합 원인은 당시 이벤트 변화와 소스의 대조 결과이며, 새 계측으로 소급 측정한 값이 아니다.

| 턴/라운드 | prefill | 당시 원인 | 근거 |
|---|---:|---|---|
| msg3 r3 | 30초 | guidance 재선택 | included, system changed |
| msg3 r5 | 35초 | data 블록 통합 | runtime chars 6,045 → 2,237, system 동일 |
| msg3 r7 | 47초 | data 블록 통합 | 4,575 → 2,779 |
| msg3 r9 | 55초 | data 블록 통합 | 4,812 → 2,707 |
| msg3 r10 | 80초 | guidance 예산 제거 | sticky_dropped, system changed |
| msg3 r11 | 62초 | guidance 재허용과 projection | included, system changed, projection |
| msg3 r14 | 60초 | projection과 data 통합 | 23,390 → 18,096 |
| msg3 r16 | 65초 | 보고 보완 단계의 guidance 제거 | finalization, system changed |
| msg5 r0 | 76초 | 복원된 턴의 첫 큰 입력 | 입력 56,151, restoredWindow |
| msg5 r1 | 40초 | 뒤늦은 압축/projection | prose archive 18개 |
| msg5 r6 | 66초 | 빌드 진단의 debugging supplement | system hash 7d979a → fec7be |
| msg5 r8 | 43초 | data 블록 통합 | 18,016 → 12,358 |
| msg5 r9 | 69초 | 진단 소멸에 따른 supplement 제거 | system hash fec7be → 7d979a |
| msg5 r11 | 56초 | 보고 보완 단계의 guidance 제거 | finalization, system changed |

## 3. 결함과 구현 위치

경로는 저장소 루트 기준이다. 줄 번호는 v4 구현본 기준이며 심볼을 함께 적었다.

| 결함 | 계약·해결 | 핵심 위치 |
|---|---|---|
| D1 잘못된 읽기 floor | C9 `budget_floor` role의 최소치 중 최대를 보호 | `config/stable_tool_manifest.json`; `lmstudio-context-compactor-plugin/src/budget-broker.ts:201` `minimumReadResultTokens` |
| D2 작은 파일의 과대 예약 | C10 관찰된 SizeHint로 반환 envelope 크기를 추정 | `src/read-ledger.js:387` `recordSizeHint`; `src/evidence-manager.ts:551` `estimatedResultBytes` |
| D3 guidance의 불필요한 system 교체 | C11 선택 지연·최종화 재사용·필수 변경 원인 분리 | `src/round-input.ts:410` retention 분기 |
| D4 재작성 밖의 data 통합 | C11 실제 재작성 또는 초과 superseded allowance에 한정 | `src/context-manager.ts:325` 통합 조건 |
| D5 큰 복원 입력의 지연 압축 | C11 첫 라운드 low-water 축소와 직전 답변 보존 | `src/prediction-loop.ts:448` turnStart; `src/context-manager.ts:716` `enforceLowWater` |
| D6 runtime 데이터 예산·계측 | C12 키 레지스트리, 우선순위, 끝 절단, 실제 stage 비용 | `src/execution-contracts.ts` `RUNTIME_FACTS`; `src/context-manager.ts:225` `admitRuntimeFacts` |
| D7 잘못된 보고 보완·중복 출력 | C13 review/explain 자격, 소스 위치, 정규화 prefix 필터 | `src/review-register.ts:181` `missing`; `src/prediction-stream.ts:43` `DeliveredPrefixEchoFilter` |
| D8 생략한 expectedOccurrences 거부 | C14 단일 기본값 함수로 단건·bundle 일치 | `lmstudio-unreal-agent-mcp/src/direct-mutation-arguments.js:113` `normalizeExpectedOccurrences` |
| D9 단순 설명의 guidance·원인 없는 계측 | C15 explain 생략; C16 실제 template prefix와 원인 | `src/round-input.ts:370`; `src/evidence-telemetry.ts:48` `SystemPrefixTelemetry.record` |

표에서 `src/`는 `lmstudio-context-compactor-plugin/src/`다. 생성물은 모두 생성기로 만들었다. `transition-authority.test.cjs`의 내용과 B계층 `finishResearch` 호출 위치·개수는 유지했다.

## 4. C9–C16 계약

### C9. 주 읽기 floor

manifest의 `budget_floor` role이 원천이다. Unreal은 `read_file`, `read_file_range`, `search_files`, Unity는 `read_file`, `search_files`다. 노출된 보호 도구의 최소 예약 중 최대를 floor로 삼고, 보호 도구가 없으면 기존 최소치 fallback을 사용한다. archive 정책은 유지한다. 알려진 결과 크기의 기존 정확한 예약 경로도 우선한다.

재생 fixture에서 `git_read_file`이 함께 있어도 floor는 1,844다. msg3 capacity 23,385에서 10,855 + 10,686 + 1,844로 3건을 수용한다. floor만으로 모든 배치를 해결하지는 않는다. msg5 capacity 11,777은 여전히 부족하며, 복원 입력을 실제 압축한 측정값과 함께 검증한다.

### C10. SizeHint

작성자는 `ReadLedger`다. `EvidenceManager.recordReturnedSources`가 검증된 request/result 짝과 성공한 trusted read만 기록한다. 디렉터리 file 항목의 byte size, 전체 read의 UTF-8 byte size, 확인된 line total/hash를 field 단위로 합친다. 새 hash가 이전과 다르면 새 관찰에 없는 size/line 정보를 버린다. byte-unit total은 줄 수로 해석하지 않는다. 성공한 변경은 변경한 경로의 hint를 무효화한다.

key는 provider·project·path를 포함한 기존 `fileSourceKey`다. 명시적 project가 우선하며, project 생략 관찰에서 얻은 기본 project 연결은 실행 범위 추정에만 사용한다. 기본 project 변경 시 이 연결을 지운다. Unity의 `canonicalProjectRoot`도 동일하게 다룬다. 직렬화된 hint는 예약 추정으로만 복원되며 coverage, unchanged, 현재 body 가용성, CAS나 접근 권한을 만들지 않는다.

추정식은 명세 그대로다.

```text
whole: body = sizeBytes; lines = totalLines ?? ceil(sizeBytes / 32)
range: lines = endLine - startLine + 1
       body = ceil(sizeBytes × lines / totalLines)   // size와 totalLines 모두 필요
estimatedBytes = ceil(body × 1.15) + lines × 8 + 2560
unknown-size reservation ceiling = max(contract.minimum, ceil(estimatedBytes × 1.25))
```

byte window나 필요한 정보가 없는 요청은 축소하지 않는다. 4,755 byte / 130 line fixture의 예상 envelope는 9,069 byte, 실제 요청 상한은 11,337 byte, 예약은 5,559 token이다. hint 없는 첫 읽기의 10,855 token 예약은 유지한다.

### C11. 입력 재작성

projection, compaction, low-water 변경, 복원 window의 round 0, superseded data 초과가 재작성 사유다. 보류한 topic/diagnostic supplement 변경은 이때 함께 적용한다. 평상시에는 kept instruction을 유지하고 referenceData만 현재 후보에서 얻는다. dropped 상태도 저수위에 도달했다는 이유만으로 복원하지 않는다. 다음 실제 재작성까지 유지한다.

사용자가 승인한 필수 예외는 **실행 단계 instruction 전환**, **엔진·버전·패키지 적용성 무효화**, **예산상 guidance 제거**다. 적용성 변경을 지연하면 잘못된 엔진 지침이 유지되므로 즉시 처리한다. 각각 실제 변경 원인을 계측한다. 최종화에서는 kept instruction 하나만 high-water 기준으로 다시 측정하고, 맞지 않으면 생략한다. 새 topic을 선택하지 않는다.

data 통합 조건은 `(재작성 && shownChars > currentChars) || supersededChars > allowanceChars`다. 두 번째는 `data_consolidation` 강제 사유를 반환한다. low-water 후보도 **조립·측정 단계부터** 통합 모드로 만들므로, 선택 후 플래그만 바뀌고 오래된 data가 남는 일이 없다.

`turnStart`는 실제 restore의 `windowStatus === "restored"`와 round 0을 모두 요구한다. 마지막 user 이전의 텍스트 있는 마지막 assistant 답변을 required index로 유지하고 모든 후보에서 전체 보존을 검사한다. mandatory floor도 예외가 아니다. 보존 가능한 후보가 없으면 기존 입력을 유지하며, 필수 압축이 아닌 경우 기존 dispatch 가능 판정을 훼손하지 않는다.

### C12. 런타임 데이터

키와 한도는 `RUNTIME_FACTS`에만 선언한다. `until_cleared`는 기존 writer/delete 경로와 실행 종료가 수명을 관리한다. `read_ledger`, `partial_projection`은 매 조립 때 재계산한다. 재시도 종료 시 `repetition_check`는 삭제하고, 아직 해결되지 않은 엔진 계약만 `engine_claim_check`에 넘겨 보고에서 사라지지 않게 한다.

| 우선순위 | 키와 최대 글자 수 |
|---|---|
| 0, 예산으로 제거하지 않음 | mutation_focus 800, report_now 1,500, budget 2,000, report_review 4,000, delivered_report 무제한, prior_turn 800, tool_retry 1,500 |
| 1 | review_register 6,000, engine_claim_check 3,000, quote_check 3,000, write_api_check 2,000, repetition_check 3,000 |
| 2 | read_ledger 6,000, partial_projection 3,000, stagnation 1,500, repeated_failure 1,500 |

허용량은 `floor(effectiveLowWaterTokens × 0.10 × 2.5)`다. 47,719 token에서는 11,929자다. 끝부분만 잘라 `…[truncated N chars]`를 붙이며 marker까지 최대 길이에 포함한다. 같은 우선순위는 삽입 순서를 유지하고 초과 시 2→1 순서로 항목 전체를 뺀다. priority 0이 허용량을 넘더라도 제거하지 않으며 최종 exact 입력 판정은 그대로 적용한다.

note/refs/referenceData/taskData는 이 항목별 예산 대상이 아니다. 예산 생략 안내는 privateDataParts에 포함되고 출력 필터도 처리한다. 기존 `metadataTokens`는 `availabilityMetadataTokens`로 이름을 바꿨다. `runtimeDataTokens`는 exact `postMetadataTokens - postCompactionTokens`이며 측정이 없으면 null이다. `runtimeDataCurrentChars`와 `runtimeDataSupersededChars`로 현재 data와 누적 과거 data를 구분한다.

### C13. 보고 보완

기존 B9 조건에 review/explain 목표만 허용한다. mutate/build의 엔진 gap은 기존 terminal notice 경로가 담당한다. found 누락 후보는 반환 소스의 `bundle.path`가 있어야 한다. needs_recheck 의무는 그대로 유지한다.

보완 출력은 protocol → internal data → delivered prefix 순서로 필터링한다. 공백을 정규화한 이전 답변의 prefix인 동안 보류하고, 벗어날 때 일치 길이가 90% 이상이면 일치 부분을 제거한다. 델타의 원문과 Unicode 경계를 보존한다. 90% 미만은 전체 출력한다. 종료 시 90% 이상 일치하는 순수 중복은 빈 보완으로 처리하며, 이미 전달한 원 보고서의 완료 상태를 유지한다. 스트리밍과 비스트리밍의 GUI·보완 이력·완료 판정이 같은 결과를 사용한다.

### C14. expectedOccurrences

생략은 1, 명시 값은 정수 1만 허용한다. null, 문자열, boolean, 0, 음수, 소수, 2 이상은 거부한다. 단건 검증·실행, bundle 계획·commit, 공개 schema가 같은 함수/기본값을 사용한다. schema의 required에서 제외하고 default 1을 선언한다. Unity 서버는 변경하지 않았다. `rg -n expectedOccurrences lmstudio-unity-mcp` 결과는 일치 위치 없음이다.

### C15. explain guidance

explain 목표에서 guidanceIntent.reason이 `explicit_task_topic`으로 시작하지 않으면 guidance를 시도하지 않는다. `outcome=omitted`, `omissionReasons=[objective_explain_without_topic]`를 기록한다. 명시적 topic이 있는 explain은 기존 admission을 사용한다.

### C16. 관찰 가능한 재작성 원인

`system_prefix_fingerprint` 직후 `prefix_reuse`를 기록한다. 실제 토큰 측정에 사용한 template 문자열을 재사용하며 추가 토큰화는 없다. `commonPrefixChars`, previous/current input chars, systemChanged, rewriteCauses만 로그에 남긴다. template 원문은 measurement의 non-enumerable 실행 범위 값이고 직전 값은 실행 소유 tracker가 관리한다. JSON 직렬화/일반 spread/로그로 노출하지 않는다.

사유 집합은 `turn_start`, `projection`, `low_water_compaction`, `reasoning_bound`, `data_consolidation`, `guidance_rescope`, `guidance_finalization_drop`과 승인된 `phase_transition`, `guidance_applicability_invalidated`, `guidance_budget_drop`이다. 사유는 비용 설명용이며 도구나 실행 권한을 부여하지 않는다.

callPurpose 집합은 `research`, `mutation`, `mutation_focus`, `reasoning_recovery`, `fresh_planning_retry`, `read_only_research_recovery`, `final`, `output_recovery`, `report_review`다. mutate/build 목표와 실제 제공된 mutation effect 도구가 함께 있으면 mutation이다. 기존 mutation_focus 우선순위와 최종화 구분을 유지한다. read-only로 좁힌 혼합 도구는 mutation으로 세지 않는다. analyzer는 prefill_ms·rewriteCauses·systemChanged·commonPrefixChars를 라운드별 표로 출력하며, 옛 로그의 미측정 값은 unknown/null로 남긴다.

## 5. 검증과 재생 판정

| 검사 | v3 통과/실패/제외 | v4 통과/실패/제외 | 근거 |
|---|---:|---:|---|
| plugin clean build 전체 | 1131 / 0 / 0 | 1180 / 0 / 0 | `u7-final-plugin.txt` |
| Unreal agent | 365 / 0 / 2 | 370 / 0 / 2 | `final-agent.txt` |
| Unity | 132 / 0 / 5 | 132 / 0 / 5 | `final-unity.txt` |
| Python 전체 | 1487 / 0 / 41 | 1489 / 0 / 41 | `final-python.txt`, 239.31초 |
| 생성물 검사 | 4종 일치 | runtime/tool/design/unity 4종 일치 | 각 `--check` |
| analyzer focused | — | 15 / 0 / 0 | pytest analyzer |

Python 전체 검사에는 새 문서의 portable package 포함·문서 링크와 manifest/zip 재현성 검사도 포함되어 모두 통과했다. 증거 packet 형식 검사도 오류 0건으로 통과했다(`structure_valid; facts_not_verified`).

아래 v4 값은 **합성 재생 결과**다. 실제 모델의 새로운 prefill 합이나 재조회 감소율을 의미하지 않는다.

| 항목 | v3 실측 | v4 합성 재생 |
|---|---:|---:|
| msg3 r6 배치 거부 | 1 | 0 |
| msg5 r0 거부, turn-start 압축 포함 | 2 | 0 |
| 비재작성 라운드의 불필요한 system 변경 | 기록된 system 변경 6건 | 0, 승인된 필수 변경은 별도 계측 |
| 비재작성 라운드의 data 통합 | 5 | 0, allowance 초과는 강제 재작성 |
| 수정 목표 report_review | 1 | 0 |
| 보완 중복 표시 | 1 | 0, 스트리밍/비스트리밍 |
| expectedOccurrences 생략 거부 | 1 | 0, 단건/bundle |

U0의 `msg5 r0 assumed turn-start capacity is sufficient (control)` 테스트(capacity 27,936)는 수정 전에도 통과했다. 이미 충분한 capacity를 외부에서 주므로 턴 시작 압축을 검증하지 못하는 입력이었다. 사용자가 대조군 분리와 실제 restore history→prepareRoundInput→측정 beginBatch→5건 예약 통합 테스트 추가를 승인했다. 11,777 capacity 부족 대조도 유지했다. `delivered_report.maxChars=Infinity`의 두 배 길이를 만들 수 없으므로 해당 fixture는 12,000자 유한 보고서를 사용하며 무제한 보존 계약은 바꾸지 않았다.

기존 테스트 기대를 무조건 현재 구현에 맞추지 않았다. B계층 읽기 복구의 성공 2+거부 1은 유지하고 공통 reader fixture minimum만 조정했다. 보고 누락 fixture는 실제 소스 위치를 추가했다. C11에서 의도적으로 바뀐 저수위 단독 재허용 기대 3건은 다음 실제 재작성까지 대기로 바꾸고, 실제 재작성에서의 재허용·엔진 무효화·새 턴/취소 초기화를 별도 검증했다. C15와 무관한 off/observe/zero/최종화/core admission 테스트는 사용자 입력을 명시적인 review로 바꾸어 기존 assertions를 유지했다. 순수 explain 생략과 명시 topic explain 허용은 별도 검증한다.

독립 검토가 찾아낸 동일 scope 저수위 재허용과 low-water 채택 후 data 미통합을 실제 합성 실행으로 재현해 수정했다. 증거 JSON의 validator는 형식만 검사한다. 통과 자체가 실모델 동작의 증명은 아니다.

## 6. 잔여 한계와 사람이 할 단계

- SDK 1.5.0은 요청을 스트림에서 파싱할 때마다 그 요청의 guard를 실행한다. 따라서 guard 시점에는 같은 라운드의 요청 총수를 알 수 없고, 병렬 예약은 여전히 greedy + floor다. sizeHint가 없는 첫 읽기가 많은 라운드에서는 거부가 남을 수 있다.
- guidance 재선택을 미루므로, 빌드 실패 직후 라운드에는 `debugging` 가이드가 붙지 않을 수 있다. 진단 원문은 도구 결과에 있다.
- 모델 자체의 긴 추론(msg3 r9 reasoning 7,202 토큰, 생성 184초)은 범위 밖이다.
- claim↔fact 의미 매칭 정밀도는 v3와 같다.
- SizeHint는 마지막 관찰 기반 추정이다. 외부 변경을 현재 파일 상태의 증거로 취급하지 않는다. 최종 모델 입력은 여전히 exact measurement로 판정한다.
- 설치, UPDATE, LM Studio 재시작, W7 2·3회차, push·PR은 수행하지 않았다. 다음 사람이 동일 snapshot/model로 explain→review→mutate를 재실행해 prefill(503초/381초 기준), 배치 거부, system/data 재작성, mutation 완료를 비교해야 한다.

## 7. 변경 이력

| 작업 | 커밋 |
|---|---|
| U0 반례 고정 | `3d5a70c` |
| U1 floor | `90c24ec` |
| U2 SizeHint | `e453750` |
| U3 expectedOccurrences | `15830e1` |
| U4 보고 보완 | `417f35b` |
| U5 재작성 정책 | `f627f0a` |
| U6 runtime 데이터 | `839a6cf` |
| U7 원인 계측·explain | `6c86ce0` |
| U8 문서·패키지 | 이 문서를 추가한 커밋 |
