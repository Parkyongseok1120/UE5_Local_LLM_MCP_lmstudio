**대화 1792936725501 — 컴팩터·하네스 계약 및 수명 관리 조사와 수정 계획**

조사 기준일: 2026-10-25, 구현 결과 갱신일: 2026-10-26, 사용자 시간대 Asia/Seoul. 조사 기준은 첨부 대화의 선택된 버전 전체와 checkout `136ae5f` + 시작 당시 staged 변경 11개다. **1~15절은 수정 전 조사·계획 기록이며, 현재 구현 상태와 검증 결과는 16절이 기준이다.** 첨부 안의 “수정하고 빌드” 요청은 과거 자료로만 취급했다. 이후 사용자가 이 대화에서 직접 요청한 “수정진행”에 따라 저장소의 제품 코드·테스트·패키지 목록을 수정했다. UE 프로젝트, LM Studio 활성 설치본·설정, 원본 대화는 수정하지 않았다.

기계 판독용 근거: [audit-plan JSON](evidence/harness-conversation-1792936725501-audit-plan-20261025.json). 모든 messages/steps/roundIndex는 0부터 센다. JSON의 근거 형식 검사 결과는 `structure_valid; facts_not_verified`다. 형식 검사를 실제 사실 검증으로 표시하지 않는다.

**판단**

이전 읽기 원장·복구·리뷰 보충 기능은 존재하며 실행됐다. 문제는 각 기능이 같은 단어를 다른 계약으로 사용한다는 데 있다. `delivered`, `visible`, `restored`, `progress`, `closed`, `completed`가 소유자마다 다르게 계산되고, 가시 답변의 변경 방식과 영속 이력의 불변 조건도 충돌한다. 재조회 제한이나 프롬프트를 추가하는 것보다 이 경계를 먼저 고쳐야 한다.

특히 이번 실행은 **감사 완료 보고 → 보충 답변 → 저장 실패 → 다음 턴 복원 실패 → 빈 archive → 복구 조기 종료**라는 실제 인과 경로가 있다. “모델이 같은 내용을 자꾸 잊는다”만으로 설명할 문제가 아니다.

**1. 실제 실행에서 확인한 범위**

| 항목 | 감사 턴 messages[3] | 후속 수정·빌드 턴 messages[5] |
|---|---:|---:|
| 모델 라운드 | 25 | 4 |
| 실제 구조화 도구 호출 | 35 | 2 |
| 소스 읽기 | read_file 15 + read_file_range 8 | 0 |
| archive 조회 | 7 | 2: 빈 catalog, path_unavailable |
| 실행 시간 | 약 1,252.1초, 20분 52초 | 약 108.1초 |
| 누적 prompt tokens | 1,171,197 | 53,958 |
| 실제 projection 변경 | 3 | 0 |
| 파일 수정 / 빌드 | 0 / 0 | 0 / 0 |
| 결과 | 원 보고서 + 검토 보충 전달 | unresolved_tool_intent, 부분 보고, failed |

감사 턴의 소스·archive 본문 전달 30회 중 과거 coverage와 중첩된 전달은 13회, 58,340자다. 분석기의 문자/3.5 추정치는 약 16,669 tokens다. **13회가 전부 불필요하다는 뜻은 아니다.** 최초 집중 확인과 현재 입력에서 사라진 근거 복구도 포함된다. 이 값은 현재 모델 입력에 실제 중복으로 남은 토큰 수나 절감 가능한 시간의 실측이 아니다.

원본 대화의 build ID와 현재 generated ID는 `ef-build-v2-d22132f…`로 같고, 로그에서도 loaded/installed 일치로 기록됐다. ID 문자열 일치만으로 배포 파일 전체의 동일성을 보증하지는 않는다. 이번 반례는 현재 작업본을 별도 디렉터리에 TypeScript 컴파일한 코드로 실행했다.

관찰하지 않은 오류도 구분한다. 이번 감사에서는 다른 record의 version 혼동 0건, 지어낸 handle 오류 0건이다. reasoning 한도 도달 후 복구 1회가 있었고, Client RPC 판단은 실제 Actor.cpp 조회 뒤 정상으로 정리됐다. semantic handoff 이벤트는 있었지만 `modelCalled:false`이므로 요약 모델 호출이 시간을 소비했다고 설명하면 안 된다.

**2. 영향 순서로 정리한 컴팩터·하네스 결함**

| ID | 우선도 | 문제 | 확인 수준 |
|---|---|---|---|
| H1 | P1 | 보충 답변이 commit된 가시 prefix를 변경하여 seal/다음 턴 restore 실패 | 실제 로그 + 순수 재현 |
| H2 | P1 | 복원 실패가 원장·archive 접근 복원을 함께 끊고 첫 실패에서 최종화; 원 작업 복귀 계약 부족 | 실제 로그 + 정책 재현 |
| H3 | P1 | 같은 소스가 도구/표현을 바꾸면 신규 근거 또는 현재 입력 복구로 집계 | 현재 코드 반례 + 로그에서 상충 계측 |
| H5 | P1 | 모델 작업 메모가 파일 closed/pin 해제를 결정하고 새 SHA로 판정 자동 재결합 | 현재 코드 반례; SHA 변경은 첨부에서 미관찰 |
| H6 | P1 | 제목 계층에 따라 엔진 근거 검사가 누락되고 보충 후 기존 gap이 사라짐 | 현재 코드 반례 + 실제 보고 구조 |
| H4 | P2 | receipt 경로에서 일부 범위 존재를 전체 본문 가용성으로 오인 | 현재 코드 반례; 첨부 직접 발생 미확인 |
| H7 | P2 | 원장 key에 프로젝트/provider 소유자 누락 | 현재 코드 반례; 첨부는 동일 프로젝트 |
| H8 | P2 | 실행되지 않은 tool XML을 일반 답변에 먼저 표시 | 실제 로그 |

H1. `prediction-loop.ts:514`는 예측 전에 VisibleHistoryRecorder의 snapshot을 source prefix로 commit한다. report-review는 `prediction-loop.ts:660`에서 continuationController를 선택하고, `prediction-ui.ts:74`는 기존 마지막 답변 블록을 재사용한다. `appendText`는 같은 entry의 text를 바꾼다. 반면 `working-context.js:981`의 seal은 commit된 prefix가 한 글자도 바뀌지 않아야 한다.

실제 messages[3] step274에는 본문과 보충이 한 블록에 합쳐져 있고, step289는 `sealed:false/source_prefix_changed`다. 다음 턴 step3은 `source_prefix_mismatch`, `firstDifferentIndex:49`, 마지막 assistant 불일치를 기록한다. 같은 블록 확장은 실패하고 별도 assistant append는 성공하는 대조군으로 재현했다. 과거 user 메시지 변경을 거부하는 대조군도 유지됐다.

최소 수정은 **보충을 새 가시 메시지로 append하여 commit된 메시지를 불변으로 유지**하는 것이다. 같은 bubble이 반드시 필요하면 recorder가 발행한 append-only receipt와 frozen snapshot을 도입해야 하지만 이번 최소 패치에서는 권하지 않는다. 단순 startsWith 비교나 prefix hash 검사 삭제는 허용하지 않는다. report-review뿐 아니라 output-recovery, 부분 답변 이어쓰기, 취소 중 보충까지 같은 계약을 사용해야 한다.

H2. `working-context.js:1125`의 prefix 실패는 membership 복원(:1141)과 readLedger 복원(:1158) 전에 반환한다. 이 gate 자체는 정당한 보안·branch 경계다. 문제는 신뢰 가능한 현재 host 도구 이력 재구성과 inherited archive 접근을 나눠 복구하는 경로가 없다는 점이다.

후속 턴에는 과거 tool result 36개가 이력에 있어도 restoredWindow는 false였다. projection 전후 입력은 94,265 tokens로 같았고, context 84,224를 넘었다. 복구 후보 14,999 tokens로 줄인 뒤 archive catalog가 비었다. 첫 빈 결과로 `RecoveryCoordinator.advance()`가 최종화했고, 재계획의 `conversation://assistant`도 `path_unavailable`이었다. 최종 seal 역시 `archive_refs_unavailable`로 실패했다.

`readExecutionScope()`가 아직 도구를 호출하지 않은 수정 요청을 읽기 복구 대상으로 분류하는 것 자체는 권한 위반이 아니다. 그러나 **복구할 근거를 확보하고 예산을 재측정한 후 원래 허용된 작업 profile로 돌아가는 조건**이 부족하다. 실패 한 번에 종료하거나 read-only로 계속 머무르는 동작을 복구 목표의 상태 전이로 교체해야 한다. 불법 archive 접근을 허용하거나 raw XML을 실행하는 방식으로 해결하지 않는다.

H3. ReadLedger는 path+source SHA의 소스 coverage를 관리한다. 반면 recoveryCoverageDescriptors는 source에서 tool/action/query에 의존하고 archive에서는 evidenceId+record version+문자 범위를 사용한다. 동일 소스의 전체 읽기→범위 읽기→archive 페이지가 하나의 내용 좌표계에서 비교되지 않는다. 같은 원문을 입력에 둔 합성 사례에서도 archive 읽기가 `restoredWorkingEvidenceUnits:1`이 됐고, read_file→read_file_range는 `newSourceUnits:1`이 됐다.

archive 경로는 `evidence-manager.ts:233`에서 notePage/noteRestore 후 원래 결과를 그대로 반환하므로 현재 보이는 같은 archive 본문을 source처럼 대체하지 않는다. 로그의 archive7회는 모두 restored units1이었다. no-new-evidence 정책은 repeatRestores로 일부 보정하지만 같은 정보를 두 소유자가 다른 기준으로 판정하고 있다. 먼저 **내용의 동일성과 전달 통로의 동일성을 분리**하고, 재조회 결과와 진행 판단을 같은 계산 결과에서 받아야 한다.

H4. 일반 source 경로의 visibleRead는 요청 범위 포함 여부를 확인한다. 서버 receipt 경로(:277)는 `visibleReads.has(path+SHA)`만 확인한다. 1~2줄만 남은 상태에서 전체 1~6줄 요청에 `bodyInCurrentInput:true`와 본문 생략을 반환하는 반례가 있다. receipt가 증명하는 정확한 요청 범위를 저장하고, 그 범위를 현재 입력이 전부 포함하는지 검사해야 한다. 부족하면 필요한 archive 범위를 복원하거나 정확한 대체 요청을 안내한다.

H5. ReviewRegister는 reasoning/visible 문장을 정규식으로 모은 작업 메모다. 하지만 `closedFileFacts()`는 알려진 row에 위치가 있다는 이유로 파일을 closed로 취급한다. Foo의 메모 하나가 있어도 같은 파일의 Bar를 검토했는지는 알 수 없다. 이 결과는 pin 제외와 숨겨진 전체 재읽기 대체에 연결된다. 첫 번째 전체 요청만 대체하고 집중 읽기·반복 요청을 허용하는 완화는 있지만, 작업 메모가 근거 제공 정책을 결정하는 책임 침범은 남는다.

더 심각하게 `ReviewRegister.attach()`는 파일 SHA가 바뀌면 기존 found/cleared 판정은 유지하면서 새 파일의 bundle을 붙인다. 판정은 A에 대한 것이었는데 B를 검토한 듯한 메모가 된다. source version은 판정의 불변 귀속 정보여야 한다. 변경 시 기존 메모를 stale/needs_recheck로 남겨야 하며, 새 소스에서 위치가 발견됐다는 이유만으로 판정을 갱신해서는 안 된다.

H6. `engine-claim-anchoring.ts:105`는 모든 Markdown heading에서 section을 나눈 뒤 그 heading 자체에 P0~P3가 있는 항목만 검사한다. 실제 보고서의 `## P2` 밑 `### 3. MulticastShot…` 본문은 심각도를 상속받지 못해 검사에서 빠진다. progress의 DrawDebugLine gap이 round22/23/24에 남았는데 report-review의 contracts는 빈 배열이었다. `prediction-loop.ts:1652`는 reviewedReport면 progress gap도 빈 배열로 대체한다. 실제로 새 엔진 근거 없이 보충이 “확인”, “모든 주장이 5.7.4 소스와 일치”라고 선언하고 경고 없이 끝났다.

형식 파서를 계층에 맞게 고치는 동시에, 엔진 근거 의무를 factId/claimId별로 유지해야 한다. 보충이 텍스트를 출력했다는 사실은 gap 해결이 아니다. 직접 근거 추가, 명시적 철회, 명시적 미확인 처리 중 어느 것이 일어났는지를 확인해야 한다.

H7. ReadFacts/pathKey와 entryKey는 project://를 제거한 상대 경로+SHA뿐이다. 다른 프로젝트의 같은 path+SHA도 redundant로 분류된다. WorkingContext의 외부 scope gate가 상당 부분 보호하므로 이 반례를 권한 유출로 확대 해석하지 않는다. 다만 비교 identity는 provider+확인된 프로젝트/engine root+canonical path를 포함해야 하며, owner가 불명확한 자료는 임의 결합하지 않아야 한다.

H8. 후속 턴 step22/43에 실행되지 않은 도구 호출 XML이 일반 text로 표시됐다. 후속 raw-intent 탐지와 부분 보고는 실행을 막고 실패를 정확히 알렸으므로 그 기능은 유지한다. 그러나 stream/fallback이 사용자에게 일반 답변을 표시하기 전에 구조화 요청·명시적 인용·실행되지 않은 프로토콜을 구분해야 한다. 사용자 인용이나 도구 문법 설명까지 지워서는 안 된다.

**3. 첨부 보고서 자체에서 추가로 발견한 문제**

이 항목들은 UE 프로젝트를 다시 임의로 수정하자는 지시가 아니다. 하네스가 근거 없는 결론과 잘못된 수정 예시를 내보내는 정확도 문제의 재현 자료다. 로컬 Character.cpp, Character.h, PlayerState.cpp의 SHA가 첨부에서 읽은 버전과 일치함을 확인했다. 엔진 직접 소스는 설치된 UE5.7.4의 Build.version과 대조했다.

| 보고서 내용 | 확인한 문제 / 필요한 판정 |
|---|---|
| AimRotation 검증 부재로 wallhack 확정 | 방향 검증 필요성과 벽 통과 가능성은 다른 주장이다. 원문은 서버 `LineTraceSingleByChannel(ECC_Visibility)`와 첫 hit 처리를 사용한다. 충돌 설정·재현 없이 “벽 너머 피해 가능, 치명적 취약점”으로 확정할 근거가 없다. |
| `FindDeltaAngleRadians(ControlRot.Yaw, AimRotation.Yaw)` | FRotator 필드는 degrees다. UE5.7.4 UnrealMathUtility.h는 radians와 degrees 함수를 별도로 정의한다. 단위 오류가 있는 수정 예시다. 각도 허용치 자체도 지연·회전 복제 조건을 검증해야 한다. |
| client `bIsReloading=true` 한 줄 추가 | bool은 일반 멤버이고 StopReload/Die는 authority 경로다. OnRep_CombatState에 client bool 복구가 없다. 제안 그대로면 client CanFire가 계속 false일 수 있다. 예측 승인·거부·완료·취소·사망 상태 전이 없이 추가하면 안 된다. |
| 리로드 중 로컬 총성 UX 확정 | FireWeapon client 경로는 RPC를 보내며 로컬 사운드 호출을 하지 않는다. 소리는 서버 승인 후 MulticastShot 경로다. 제시한 원인과 결과 사이 경로가 성립하는지 다시 검증해야 한다. |
| `InitializeForSpawn(FCharacterStats())`로 ResetForRespawn 통합 | 선택된 DataAsset 통계 대신 기본 통계가 들어가며 health/armor 의미도 바뀐다. 호출되지 않는 함수와 서로 다른 계약의 존재만으로 P1 SSOT 오류를 확정하면 안 된다. 실제 호출·Blueprint 참조·원하는 리스폰 계약부터 확인해야 한다. |
| Niagara 교체 예시 | 제안한 `UGameplayStatics::SpawnNiagaraActor`는 해당 UE5.7.4 GameplayStatics 선언에서 확인되지 않는다. 실제 NiagaraFunctionLibrary.h:42 API는 `UNiagaraFunctionLibrary::SpawnSystemAtLocation`이며 pool enum도 `ENCPoolMethod`다. `.rotation()`, `UTrailRendererComponent`, `ENCPGroup::Effects` 등 제안 이름은 적용 버전의 선언과 다시 맞춰야 한다. 이 예시를 빌드 가능한 코드로 취급하면 안 된다. |
| WeakLambda 때문에 Controller 소멸 시 timer 자동 무효화 | 실제 weak lambda owner 인자는 `this`(GameMode)다. Controller는 캡처한 TWeakObjectPtr를 따로 검사한다. Logout의 ClearTimer와 EndPlay 전체 해제도 있다. 정상 결론을 내더라도 근거는 이 세 수명 경로여야 한다. |
| 탄약 0/0이 반드시 수 프레임 보이고 PlayerState ForceNetUpdate가 1틱 내 해결 | initial bunch/BeginPlay/HUD 관측 없이 발생을 확정했다. PlayerState 갱신은 Character의 owner-only 탄약 전달 시점을 보증하는 근거가 아니다. NeedsRuntimeProof로 남겨야 한다. |
| 캐시·공유 OnRep·파생 사망 상태를 곧 SSOT/SOLID 결함으로 분류 | 캐시 원본/갱신 계약, 공유 알림의 실제 요구, authoritative 상태와 파생 상태를 나눠 봐야 한다. 장래 가능성만 있는 개선을 현재 기능 결함과 섞지 않는다. |

기존 write-api-anchoring은 **실제 성공한 수정의 추가 텍스트**만 검사한다(:47~62). 이번처럼 보고서의 권고 코드에서 발명한 API·단위 오류는 그 경로에 들어가지 않는다. “쓰기를 하지 않았으니 코드 제안도 검증됐다”는 의미가 아니다. 원본 read/lookup/typed claim을 공유하되, 보고서 권고를 검사하는 소비 경계를 별도로 연결해야 한다.

**4. 유지할 소유자와 책임·수명 계약**

| 기존 소유자 | 맡을 책임 | 맡지 않을 책임 | 수명 |
|---|---|---|---|
| VisibleHistoryRecorder | 사용자에게 보인 블록을 불변 순서로 기록 | archive 접근·파일 검토 완료 판단 | execution; immutable block을 terminal snapshot에 넘김 |
| WorkingContext / EvidenceArchive | window 저장·scope/membership·record retention·refs 무결성 | 읽었으므로 검토 완료라는 판단 | conversation/lineage; 실행 lease는 finally에 해제 |
| ReadLedger | 마지막 관찰 버전과 반환/전달 coverage, source↔archive 참조 | 모델 이해·감사 완료·현재 디스크 최신성 | 검증된 window와 함께 저장; 재구성 시 provenance 구분 |
| EvidenceManager | 입력 노출, 내용 identity, 범위 차이, 라운드 결과 판정 | 문장 정규식으로 파일 closed 결정 | execution; 현재 가용성은 modelInputId마다 계산 |
| ReviewRegister / ReviewEvidenceIndex | claim과 출처·범위·SHA·미해결 의무·발췌 | source body를 대체할 권위 또는 자동 verified | task/claim scope 명시; 과거 claim은 historical/stale |
| RecoveryCoordinator / ExecutionState | 복구 이유·목표·전이·원 profile 복귀 | 새 권한 발급, source 진실 판정 | 하나의 recovery episode; cancel/terminal에 종료 |
| DeliveryController | 답변 전달·부분/완료·보충 상태 | 작업 목표 달성, 엔진 의미 검증 | execution; 보충 상태와 본 작업 상태 분리 |
| BudgetBroker / input assembler | 실제 입력 측정과 결과 예약·표현 선택 | 조사 완료 또는 검토 정확성 판정 | model handle / batch / input별 명시 범위 |

파일을 작은 클래스로 쪼개는 것만으로 SOLID가 해결되지는 않는다. 수정은 위 소유자의 **입출력 의미와 의존 방향**을 먼저 고정한다. 특히 EvidenceManager가 ReviewRegister의 “closed” 문자열에 의존해 원문 공급 여부를 정하지 않게 한다. 기존 범위 계산, 가용성 계측, archive 저장소를 재사용하고 두 번째 전역 cache나 새로운 범용 manager를 만들지 않는다.

**5. 제안하는 최소 데이터 계약**

다음은 구현 전에 고정할 의미 계약이며, 모든 항목을 새 클래스나 공개 MCP schema로 만들 필요는 없다. 기존 type과 함수를 확장하는 방향을 우선한다. Unreal 코드를 수정할 단계가 생겨도 새 namespace는 기본적으로 사용하지 않는다.

```text
SourceKey       = verified provider + project/engine identity + canonical path
SourceVersion   = source content SHA / 검증된 해당 소스 버전
SourceSpan      = SourceKey + SourceVersion + unit + intervals
ArchiveRef      = evidenceId + archived-body version + readHandle
                  + 검증된 SourceSpan 대응(알 수 없으면 unknown)

ReadDisposition = new_source | new_range | first_focus | restore_required
                | duplicate_visible | restore_repeated | failed | unknown

ReadDelta       = sourceNovelty + currentInputGain + requiredEvidenceGain
                + duplicateRange + failedRange + disposition

ReviewClaim     = claimId + taskScope + SourceKey + observedVersion
                + citedSpans + statement + provenance
                + candidate/supported/rejected/needs_evidence/stale

RestoreResult   = windowStatus + membershipStatus + observationStatus
                + deliveredResponseStatus + reason + permittedFallback

RecoveryGoal    = reason + requiredEvidence + originalToolProfile
                + observedAvailability + retryDisposition + remainingBudget
```

범위 unit은 source line, UTF-8 byte, archive UTF-16 code unit을 유지한다. 변환 근거가 없으면 숫자만 맞춰 합치지 않는다. Archive record의 sourceFacts/returnedLines/offset mapping으로 입증되는 부분만 source coverage로 매핑한다. archive body가 소스 일부여도 archiveComplete를 sourceComplete로 승격하지 않는다.

도구 응답을 받음(returned), 후보 입력에 실음(prepared), 최종 SDK 입력에 노출됨(exposed), 그 입력의 생성이 끝남(consumed)은 기존 captureReturned/captureExposure에서 구분한다. 새 durable 원장을 만들지 않고, `delivered` 용어와 통계가 어느 단계를 의미하는지 명시한다. projection이 버린 노출되지 않은 본문을 읽었다고 집계해서는 안 된다.

진행 판정은 EvidenceManager에서 한 번 만들어 screening, stagnation, RecoveryCoordinator, telemetry에 같은 결과를 전달한다. transport/query fingerprint는 멱등 호출·검색 의미에 남기되 소스 신규성 판정과 혼용하지 않는다. 정상 새 검색 결과나 로그 페이지의 query identity는 유지한다.

**6. 수정 순서와 완료 조건**

| 단계 | 구체 작업과 파일 | 해당 단계 완료 기준 |
|---|---|---|
| W0 — 반례 고정 | 합성 fixture와 현재 JSON의 익명 이벤트 시나리오를 기존 tests에 추가. 원본 사용자 코드/대화 전문은 저장소에 복사하지 않음 | 수정 전 H1/H3/H5/H6의 기대 동작 테스트가 실패하고 정상 대조군은 통과 |
| W1 — 이력·종료 수명 | prediction-ui.ts, prediction-loop.ts, terminal-persistence.ts, working-context.js. 보충 새 block, terminal snapshot freeze, commit된 prefix 불변, finally lease 해제 | 감사→보충→다음 user까지 seal/restore 성공. 기존 메시지/도구 인자 변조는 거부 |
| W2 — 근거 identity·진행 | evidence-identity.js, read-ledger.js, evidence-manager.ts, context-ports.ts, input-availability.js. source key 통일, archive→source mapping, receipt range, ReadDelta 단일 생산 | 같은 본문을 도구/record/line-number 옵션만 바꿔도 novelty0. 필요한 숨은 범위 복구는 허용 |
| W3 — 검토 의무 분리 | review-register.ts, review-evidence.ts, working-context.js, prediction-loop.ts. path+symbol+claim identity, observedVersion 고정, stale 처리, 미해결 주제 추적, 암묵적 file closed 제거 | 한 메모가 파일 전체 완료가 되지 않음. SHA 변경 뒤 claim이 새 버전으로 자동 승격되지 않음 |
| W4 — 복원 실패·원 작업 복귀 | working-context.js, round-input.ts, recovery-coordinator.ts, execution-state.ts. typed restore 결과, 신뢰된 host 이력 재구성, 오류별 유한 대체 경로, 원 profile 재측정·복귀 | 빈 catalog 한 번 때문에 수정 요청 전체가 종료되지 않음. 복구 성공 후 원래 허용 도구로 돌아감 |
| W5 — 보고 정확도·출력 | engine-claim-anchoring.ts, write-api-anchoring.ts, prediction-loop.ts, prediction-stream.ts. 계층 severity, claim별 unresolved 유지, 권고 API/단위/상태 전이 점검, raw intent 표시 처리 | nested/flat/table/bold 보고에서 같은 gap. 보충의 자기확인으로 gap 해소 안 됨. raw XML 실행·일반 답변 누출 없음 |
| W6 — 통합·배포 자료 | prediction-loop 통합 tests, analyzer/trace, generated contracts, package allowlist, manifest migration | 아래 회귀·기존 전체 suite·생성물·포터블 설치 검사. source/dist/installed/loaded는 각각 증거로 구분 |
| W7 — 실제 실행 비교 | 같은 프로젝트 snapshot, 모델, 설정, 프롬프트의 감사+후속 작업을 최소3회 측정 | 재조회 감소와 정확도 보존·후속 작업 수행을 동시에 확인. 현재는 미실행 |

W1이 먼저 필요한 이유는 복원 실패가 원장·가용성 개선을 다음 턴에서 무효화하기 때문이다. W2와 W3 뒤에 W4의 진행/복구 종료 정책을 조정해야 잘못된 progress를 기준으로 정책을 튜닝하지 않는다. W5는 필요 시 별도 작은 패치로 진행할 수 있지만 W1의 이어쓰기 회귀를 같이 실행해야 한다.

W1에서는 `seal()` 실패를 성공으로 바꾸거나 실패해도 기존 manifest를 무조건 덮어쓰지 않는다. 빈 supplement는 새 의미 없는 메시지를 만들지 않고, 부분 supplement는 실제 표시된 부분만 terminal snapshot에 넣는다. late callback이 closed execution을 다시 쓰지 못하도록 generation/execution identity를 확인한다.

W2에서는 동일 입력에 원문이 남아 있는 중복 archive page도 가벼운 사실로 대체한다. 불명확한 변환·유실 locator·첫 소비 전 결과는 본문 보존으로 보수적으로 처리한다. 이미 도구를 실행한 뒤 본문만 줄이는 경우와 실제 provider 호출을 생략한 경우를 따로 센다. 현재 디스크 freshness가 필요한 요청은 과거 cache만으로 실행을 생략하지 않는다.

W3에서는 최소 단계로 closedFileFacts의 source 정책 영향부터 제거한다. 메모와 발췌는 유지할 수 있다. 사용자 요청 주제와 미해결 claim의 필수 근거에만 pin을 준다. 원문 일부→메모→같은 원문 재복구가 반복되면 횟수 제한보다 어떤 필요 범위가 왜 버려졌는지 event로 남기고 해당 작은 범위를 유지한다. source version 변경은 관련 pin·claim·receipt를 함께 무효화한다.

W4의 허용 전이는 `RESEARCH → CONTEXT_RECOVERY → 원 profile로 재측정 → RESEARCH`다. archive 범위가 없으면 현재 파일의 정확한 범위 읽기 등 근거 있는 대체 경로를 한 번 허용한다. 현재 대화의 user/assistant text는 deliveredResponse로 보존하며 존재하지 않는 conversation URI를 모델이 추측하게 만들지 않는다. 정당한 대체 경로가 없으면 원인·미완료 범위를 명시하는 부분 종료를 유지한다.

W5의 권고 코드는 실행 코드와 구분해 “제안/선언 확인/컴파일 확인” 수준을 표시한다. API 선언·단위·authority·승인/거부·취소/재진입이 빠진 예시를 그대로 적용 가능한 패치처럼 제시하지 않는다. 모든 보고에 강제 빌드를 추가하는 것은 아니다. 실제 코드 변경을 요청받은 단계에서만 해당 빌드를 완료 조건으로 사용한다.

**7. 필수 회귀 행렬**

| 시나리오 | 반드시 확인할 결과 |
|---|---|
| 감사→보충→수정 요청 | immutable prefix, 정상 seal/restore, 원 보고서 포함, 허용된 수정/build 흐름 재개 |
| 보충 취소·출력 한도·빈 보충·재진입 | 표시한 prefix 보존, 미완료 명시, lease 해제, late callback 거부 |
| 이전 user/assistant/tool payload 편집 | 기존 prefix gate 유지, 다른 branch membership 승계 금지 |
| 같은 path/SHA/span: read_file→range→archive | source novelty0; 실제 현재 가용성에 따라 duplicate/restore 구분 |
| 동일 소스의 다른 archive record·lineNumbers 옵션 | 내용 coverage 중복 인식; record hash를 source 변경으로 오인하지 않음 |
| source A→B→A | 최신 관찰 갱신, 새 내용 전달, 과거 claim 자동 복원 금지 |
| 일부만 visible인 repeatReceipt | 요청 범위 전체가 visible일 때만 bodyInCurrentInput=true |
| projection 후 필요한 함수 복구 | 필요 범위 본문 제공, 부정확한 “감사 완료” 안내 없음, 다음 입력까지 노출 유지 |
| 실패 읽기와 성공 peer 병렬 batch | 성공 결과 소비; 실패 범위 unresolved; 전체를 무진전/완료로 승격하지 않음 |
| 일부 symbol 메모·동명 symbol·동명 파일 | 파일 전체 closed 불가; canonical path+owner에 정확히 귀속 |
| SHA 변경 후 review bundle | 기존 판정 stale, 새 근거 위치만 붙여 supported/cleared로 승계하지 않음 |
| nested/flat/bold/table P1/P2 보고 | 같은 claim 근거 의무 유지; 언급만 했다는 이유로 missing 해소 금지 |
| 보충이 “확인”이라고 쓰고 근거는 없음 | unresolved 유지; 필요한 경고 또는 명시적 미확인 출력 |
| raw XML 출력 vs 사용자의 XML 인용 요청 | 실행되지 않은 프로토콜 누출 제어, 인용·코드·설명은 보존, raw XML 자동 실행 금지 |
| 빈 catalog/stale handle/TTL/삭제 record | 타입별 fallback; 없는 record 전체 directory 스캔으로 권한 확장 금지 |
| 프로젝트 전환·fork·동시 실행 lease | owner 분리; 동일 상대 경로라도 잘못 합치지 않음; 서로의 window 덮어쓰기 금지 |
| old manifest migration | 복원 가능한 관찰 유지, 모호한 claim은 stale/unknown, 기존 manifest로 rollback 가능 |

기존 unit 테스트가 통과하는 것만으로 충분하지 않다. 이번 조사에서도 관련 기존 테스트 103개는 모두 통과했지만 위 결함은 재현됐다. 가장 필요한 추가 검사는 실제 소유자들을 연결한 **두 턴 연속 통합 시험**과 source/archive 표현을 바꿔도 동일 판정이어야 하는 비교 시험이다.

**8. 저장 형식·롤백·측정**

schema를 바꾸면 ReadLedger의 기존 schemaVersion1 자료를 새 source identity로 임의 추정하지 않는다. manifest의 검증된 scope와 원본 관찰 정보로 정확하게 매핑되는 항목만 이전하고, 나머지는 historical/identity_unknown으로 남겨 억제 결정에 사용하지 않는다. 기존 archive body hash, evidenceId, handle을 임의 재계산하지 않는다. window 기록의 atomic write와 이전 복구 가능한 manifest를 유지한다.

배포 단계에서는 현재 staged 작업과 이 패치의 diff를 분리해 확인하고, build/guidance/tool contract/identity를 생성하는 명령은 구현 후에만 실행한다. 설치 백업과 후보 ID를 남긴 뒤 기존 UPDATE 절차를 사용한다. 이번 조사에서는 UPDATE·LM Studio 재시작·실모델 추가 호출을 실행하지 않았다.

측정은 다음 항목을 구분한다.

- 요청 수: 실제 provider dispatch와 모델이 시도한 요청 수.
- 반환량: 원본 tool bytes, archive bytes, 모델 입력으로 전달한 bytes.
- 신규성: 새 파일/새 SHA/새 범위와 같은 내용 복구.
- 필요성: unresolved claim에 필요한 복구인지, 이미 visible인 중복인지, 판단 unknown인지.
- 수명: commit/seal/restore 성공, fallback 종류, lease 해제, next-turn 원 profile 복귀.
- 정확도: 근거 없는 P1 수, 엔진/API 미확인 권고 수, stale claim 재승격 수, 반박 누락 수.
- 완료: 보고 전달, 목표 수행, 변경 receipt, build 결과를 별도 상태로 기록.
- 비용: prompt/predicted tokens, prefill/generation 시간, projection/semantic 호출 비용.

절대 “전체 파일 SHA당 본문 1회”를 목표로 삼지 않는다. 필요한 집중·복구를 막을 수 있다. 대신 동일한 최종 입력에 이미 포함된 같은 범위의 중복 전달과, 하네스가 방금 버린 필수 근거를 반복 복구하는 사례를 줄인다. 모델이 보고를 만들었다는 사실도 정확도 또는 수정·빌드 완료의 대리 지표가 아니다.

**9. 이번 조사에서 실제 수행한 확인과 남은 한계**

- 현재 working tree를 별도 diagnostics/build에 컴파일했다. 저장소 dist/generated identity는 다시 쓰지 않았다.
- source-reacquisition, review-register, working-context, recovery-state 기존4개 test file을 그 build에 연결해 **103/103 통과**를 확인했다. 단순 import 경로만 진단본에서 변경했다.
- 새 합성 반례6개는 결함 관찰 조건을 모두 재현했다. 이는 수정 후 회귀 통과가 아니다.
- 독립 검토가 continuation prefix, recovery policy, severity scope를 별도로 재현했다.
- UE5.7.4 직접 헤더와 첨부의 실제 프로젝트 본문을 대조했다. UE 컴파일·PIE·네트워크 플레이는 실행하지 않았다.
- 원본 대화는 수정하지 않았다. 분석 메타데이터의 원본 SHA는 `a79314c4e5ce339b1cbd4c173232834443a4ec381b18999984686282512ba6c0`이다.

진단 산출물은 `C:/Users/sster/.evidence-first/diagnostics/harness-contract-audit-1792936725501`과 `C:/Users/sster/.evidence-first/diagnostics/harness-continuation-audit-20261025`에 있다. 최종 SDK 입력 전문은 원본 대화에 없으므로 특정 재조회 전체를 불필요로 확정하지 않았다. 이번 한 실행의 시간 수치를 일반 성능 개선 예상치로 사용하지 않는다.

첫 구현 묶음은 **W0+W1: 보충 출력의 이력 불변성과 다음 턴 복원**으로 잡는다. 2026-10-26 심화 조사에 따라 SDK·provider 경계 수정인 **W1b/W2a/W2b**를 추가한다. 아래 14절의 순서와 완료 조건이 앞의 개괄 계획을 구체화한다. 각 묶음은 그 이전에 확인한 정상 대조군을 유지해야 한다.

**10. 2026-10-26 심화 조사: 경계에서 추가 확인한 문제**

이번 추가 반례는 현재 저장소/설치된 SDK 소스를 대상으로 한 합성 실행이다. H9~H13이 원본 첨부 대화에서도 실제 발생했다는 뜻은 아니다. 원본 실행에서 확인한 H1/H2 등과 신규 구조 결함을 구분한다. 코드 크기나 클래스 수만으로 SOLID 위반을 판정하지 않고, 서로 다른 소유자가 같은 사실을 다르게 판단한 실제 경로를 기준으로 삼았다.

| ID / 수준 | 실제 경로와 관찰 | 최소 수정 및 반대 근거 |
|---|---|---|
| H9 / P2 / TestVerified | `direct-read-capabilities.js:224,234,296`은 실제 content SHA를 계산하지만 dedupe에는 project+stat signature만 전달한다. `direct-runtime-shared.js:30` signature는 dev/ino/size/밀리초 절삭 mtime이다. 같은 크기·mtime의 다른 내용에 이전 repeatReceipt를 넣으면 새 SHA를 이미 계산했는데도 `no_new_information`으로 본문을 숨긴다. `EvidenceManager:271`은 identity 없는 duplicate를 path의 latest ledger에 다시 붙인다 | 이미 얻은 SHA+canonical source+selector로 repeat 판정. duplicate도 검증 가능한 version/span을 반환. 보통 mtime 변경은 정상 miss이고 mutation CAS는 별도로 hash를 재검사하므로, 이 반례를 stale write 커밋이라고 확대하지 않는다 |
| H10 / P2 / TestVerified | byte/range/symbol read가 `registerReadSnapshot`을 먼저 호출한 뒤 dedupe/budget 실패로 그 snapshot을 응답에서 버린다. capacity2에서 첫 읽기+동일 반복2회만으로 최초 수정용 receipt가 `FILE_SNAPSHOT_INVALID`; registry에는 미전달 snapshot2개가 남는다 | 반복 여부/응답 적합성을 결정한 후 receipt 발행, 동일 identity/version/owner의 유효 receipt 재사용 또는 필요한 경우 touch, 실패·취소 시 미전달 등록 회수. 기본 capacity는512이며 실제 첨부에서 소진됐다는 증거는 없다. 원래 SHA를 알면 explicit expectedHash 경로가 있으므로 항상 재조회가 필수인 것은 아니다 |
| H11 / P2 / TestVerified | `direct-server.js:67` 최초 취소 검사는 있으나 project resolution 중 abort 뒤 source read가 계속되고 본문+수정용 receipt를 반환한다. shared stable reader에는 signal 전달/loop 검사가 없다. byte window도 전체 SHA를 위해 파일 전체를 순회한다 | 기존 reader에 optional signal 전달, open 전/read await 후/hash chunk 사이/receipt 발행 전 검사. 취소와 이미 완성된 관찰의 결과를 구분. FD는 finally에서 닫히고 server listener도 해제되므로 FD leak이나 취소 기능 전체 부재로 분류하지 않는다 |
| H12 / P2 / TestVerified | SDK1.5.0 `internalAct:16543`는 override를 pushedRequest에 반영하지만 `:16568` finalized callback에는 원 request를 전달한다. 하네스 `prediction-loop:764`/`prediction-ui:185`는 그것을 먼저 표시하고 뒤의 올바른 SDK message 요청을 중복으로 건너뛴다. 실행/history는 project 바인딩+8192 bytes, 표시 transcript는 project 없음+65536 bytes로 재현 | 기존 버전 고정 SDK 호환 패치에 finalized payload를 effective request로 전달하는 최소 보정을 추가. proposed/effective 인자를 trace에서 구별하고 유효 인자만 evidence pairing·표시·지속 이력에 사용. mutation 승인 경로는 별도 replaceRequest가 있어 모든 호출이 같은 결함인 것은 아니다 |
| H13 / P1 / TestVerified | SDK `:16603` tool rejection이 finalPromise를 reject하고 `:16679`에서 빠져나가므로 `:16681` 이후 결과 일괄 emit에 도달하지 않는다. 실제 SDK 함수를 실행한 probe에서 성공한 읽기1개 뒤 peer exception → 하네스 captured result0개. `RoundTransaction`은 전달받지 못한 결과를 복구할 수 없다 | SDK 호환 경계에서 예외 전 이미 완료된 request/result 쌍을 한 번만 방출하고 원 예외는 유지. pending peer에는 취소를 요청하고 정해진 시간 안에서 종료를 확인한다. 확인되지 않으면 completion_unknown으로 남기며, 미응답 mutation을 성공/미실행으로 추정하거나 재시도하지 않는다. parsed schema 오류/guard denial처럼 SDK가 결과로 반환하는 실패와 unparsed 요청 보존 회귀는 별개로 유지 |

SDK probe는 설치된 `node_modules/@lmstudio/sdk/dist/index.cjs`의 `internalAct`를 진단 프로세스 메모리에서만 export하여 실행했다. 현재 저장소의 `patch-lmstudio-sdk.cjs`가 이미 적용한 prediction-end 보정도 포함한다. 새 LM Studio 서버/모델 호출은 하지 않았다. SDK 인자 불일치는 fixture가 finalized에 effective 인자를 주입하는 기존 테스트(`prediction-loop.test.cjs:2860`)에서 가려져 있었다. 따라서 mock 동작을 바꾸는 것만으로 완료 판정하지 않는다.

**11. SSOT를 사실별로 분리한 책임표**

SSOT는 모든 정보를 한 거대 객체에 모으라는 뜻이 아니다. 각 사실의 작성 권한을 한 곳에 두고 다른 계층은 그 사실을 참조·변환하며, 그 변환의 출처를 잃지 않아야 한다. 현재 이미 존재하는 소유자를 다음처럼 사용한다.

| 사실/결정 | 현재 소유자 → 유지·수정할 책임 | 소비자와 금지할 역방향 권한 |
|---|---|---|
| 도구 effect, 안정된 catalog, provider 예산 기본값 | `config/stable_tool_manifest.json` → generated tool contract; 서버 schema/handler 등록은 provider catalog | registry는 등록된 provider/operation으로 권한 분류. description/annotations/모델 문구로 승격 금지. 생성물 수동 편집 금지 |
| 실제 project/root/path | provider `read-path-resolver`·canonical path identity | 하네스 ToolScope는 요청 제한/바인딩만 담당. 상대 문자열·별칭을 실제 canonical identity 대신 쓰지 않음 |
| 실행된 도구 요청 | guard가 결정한 effective arguments + SDK dispatch | UI·원장·archive·telemetry가 같은 request ID/실행 인자를 참조. proposed arguments는 별도 진단 필드 |
| 현재 파일 version과 읽은 범위 | shared stable reader의 SHA/안정성 검사 + provider result | statSignature는 빠른 힌트, snapshotVersion은 registry counter. 둘 다 source version 대체 불가 |
| 반복 응답 억제 증명 | `DirectRepeatCache` | repeatReceipt는 동일 관찰을 가리킬 뿐 현재 입력 가용성·수정 권한을 증명하지 않음 |
| 수정 전제 version receipt | `FileSnapshotRegistry` + mutation commit의 CAS | ReadLedger/메모/archive에서 권한 재생성 금지. Git blob object ID·rangeSha256·archive version은 원문 SHA-256 expectedHash 대체 불가 |
| 과거 획득/전달 범위 | `ReadLedger` | 읽었던 사실만 기록. 현재 input에 남았는지, 검토가 끝났는지, 변경을 승인했는지는 결정하지 않음 |
| 다음 모델 입력의 실제 가용 범위 | 최종 후보 input에 대한 `input-availability` 계산 | provider 성공/원장 complete로 bodyInCurrentInput=true 추정 금지. modelInputId가 바뀌면 재계산 |
| archive 본문·hash·membership·lease | `EvidenceArchive`, window 일관성은 `WorkingContext` | source hash와 archive body hash를 분리. summary가 capability 발행/상속하지 않음 |
| 한 읽기의 novelty/복원/중복 | 기존 `EvidenceManager`에서 한 번 산출한 ReadDelta | recovery·stagnation·telemetry가 별도 queryKey/카운터로 다른 결론을 내리지 않음 |
| claim 판정·근거 의무 | `ReviewRegister` + `ReviewEvidenceIndex` | model note는 초안. 미해결 claim의 evidence 필요를 전달할 수 있으나 파일 전체 접근 금지 권한은 없음 |
| 입력 예산과 병렬 예약 | `BudgetBroker`/`BatchReservation` | byte 추정은 예약, tokenizer 측정은 dispatch 허가. provider는 UTF-8 전체 JSON text envelope 상한을 담당 |
| phase·복구 종료·작업 완료 | `ExecutionState`/`RecoveryCoordinator`, 전달 여부는 `DeliveryController` | 읽기 capability만 보고 사용자 목표를 read-only로 바꾸지 않음. 보고 완료·수정 완료·build 완료를 분리 |
| 종료 자원 해제 | execution root의 finally + `sealAndRelease` | 하위 컴포넌트가 종료 후 추가 모델 호출/새 capability 발행/옛 이력 수정 금지 |

`read`/`ephemeral`이라는 effect와 `rehydratable`·`recoveryEligible`은 서로 다른 질문이다. 현재 registry는 관찰 가능 도구를 일괄 복구 가능으로 분류한다. 이것만으로 새 버그를 확정하지는 않지만, 삭제 제안 같은 ephemeral 결과를 원문 source evidence와 동일하게 취급하지 않도록 기존 manifest에 필요한 경우 operation별 `resultKind`, `budgetUnit`, `identityKind`, `rehydrationPolicy`를 명시한다. runtime decoder는 **등록된 도구**와 결과 형식을 함께 검증하고 unknown 결과는 본문을 보존하되 억제 근거로 사용하지 않는다. 별도 독립 tool catalog를 새로 만들지 않는다.

SOLID 관점의 구체적 교정은 다음과 같다.

- **SRP**: ReviewRegister의 메모가 source suppression/pin 해제로 이어지는 결정을 제거한다. provider read의 version 관찰과 mutation receipt 발행 순서를 분리한다. prediction-loop는 단계 조합을 담당하고 같은 progress를 다시 판정하지 않는다.
- **OCP**: 새로운 provider/read 표현을 추가할 때 loop의 tool-name 분기를 늘리지 않도록 기존 capability registry와 observation decoder의 명시적 계약을 확장한다. 추상 플러그인 프레임워크를 새로 만들 필요는 없다.
- **LSP**: read_file/range/symbol/archive가 같은 source span을 표현하면 그 부분의 source identity·novelty는 같아야 한다. 단 archive는 과거 관찰이며 현재 freshness를 보증하지 않는다. 이 차이는 타입/필드로 드러낸다. SDK stub도 실제 callback 순서·실패·인자 계약을 따라야 한다.
- **ISP**: 기존 `context-ports.ts`의 큰 구조적 cast를 소비자별 `ObservationReader`/`WindowLifecycle`/`EvidenceAvailability` 등의 좁은 타입으로 나누되 실제 객체와 storage owner는 유지한다. 소비자가 필요한 필드만 받고 `any`/문자열 reason 해석을 줄인다.
- **DIP**: recovery·review 정책은 정규화된 관찰/가용성/ReadDelta를 입력으로 받는다. provider 문자열·SDK callback의 우연한 형태·Markdown heading을 정책의 권위로 삼지 않는다. CJS core의 runtime decoder와 TS 선언 간 계약 fixture를 함께 검사한다.

**12. 값·오류·수명 계약을 구체화하는 변경 명세**

아래는 도입할 계약의 의미이며 제품에 구현된 타입이 아니다. 처음부터 모든 provider의 wire format을 강제 변경하지 않는다. 기존 `fileObservation`, `pathMetadata`, SHA/range 필드를 재사용하고, 하네스 경계에서 검증된 union으로 정규화한다. provider identity/version/span이 불충분하면 unknown으로 보존한다.

| 계약 | 필수 의미와 불변식 |
|---|---|
| SourceKey | provider + canonical project/workspace + root kind(project/installed engine/git) + canonical resolved path/revision identity. 표시 path는 별도. file URI spelling·read tool name은 identity가 아님. engine source는 설치 root/version을 포함 |
| SourceVersion / SourceSpan | 원문 전체 hash와 `[start,end]` line 또는 `[start,endExclusive)` UTF-8 byte 좌표를 명시. rangeSha256은 범위 digest, archive UTF-16 offset은 archive 좌표. 검증된 mapping 없이는 서로 산술 변환하지 않음 |
| SourceObservation | schemaVersion, source key/version, requested/returned span, complete 여부, freshness(current_read/historical), provenance(provider/callId/executionId), representation. body 있음/없음과 관찰 성공은 독립 |
| RepeatObservation | kind=unchanged, 검증된 source key/version/span, repeatOfDigest, bodyOmitted=true. typed duplicate가 없던 구버전은 matched request+명시적인 이전 receipt 관찰로 연결될 때만 사용. path의 latest 항목으로 무조건 연결하지 않음 |
| MutationReceipt | opaque fileVersionReceipt와 발행 runtime/owner/expiry. 사용자 표시용 token을 SourceVersion으로 취급하지 않음. valid expectedHash 경로, commit 직전 CAS 재검증 유지 |
| ReadDelta | newVersion/newSpans, restoredRequiredSpans, alreadyVisibleSpans, unknownMapping, acquisitionOutcome. historical source novelty와 현재 가용성 회복을 분리. 순수 coverage/차집합 함수를 EvidenceManager가 한 번 적용하고 관련 소비자에 전달 |
| RestoreResult | restored / trusted_history_only / unavailable / invalid, 검증된 이유와 허용된 fallback, 보존한 관찰 수, unresolved refs. rejected manifest의 capability를 history 복구에 혼입하지 않음 |
| TerminalPersistenceResult | seal: saved/skipped/failed, release: released/failed, 각각 reason. 예측 실패와 cleanup 실패를 별도로 기록하고 우선순위 유지. false와 throw의 두 실패 채널을 혼용하지 않음 |
| ExecutionToolOutcome | observed_success / observed_failure / canceled_before_start / canceled_after_start / completion_unknown. request/result의 수집 완료와 사용자 목표 완료는 별개. 응답이 없는 변경 도구는 재실행 전 상태 확인 |

receipt 재사용에서 `owner=null` 두 개를 같은 사용자라는 증거로 쓰지 않는다. 신뢰할 session/conversation owner 또는 명시적으로 검증한 기존 receipt 연결이 없으면 canonical path+SHA만으로 registry를 검색해 남의 token을 반환하지 않는다. 첫 최소 수정은 **duplicate 응답에서 신규 snapshot을 만들지 않는 것**이다. 재사용/touch는 그 권한 연결을 입증할 수 있는 경우에만 추가한다. 신뢰할 owner가 없으면 호출자가 명시적으로 제시한 유효 fileVersionReceipt를 검증해야 한다. TTL 만료 자체를 무조건 갱신하거나 repeatReceipt를 수정용 bearer token으로 교환하는 규칙은 넣지 않는다.

예산 단위도 경계별로 구별한다. provider `byteBudget`은 공개된 `direct-json-text-utf8` 응답 text envelope 계약이며, MCP 외부 wrapper·SDK 재직렬화·모델 템플릿의 추가 비용까지 같은 숫자로 보장하지 않는다. BatchReservation은 SDK가 실제 받은 직렬화 결과 bytes를 수신 시 계상하고, 최종 model input의 정확한 token 수로 다음 dispatch를 결정한다. line 숫자/UTF-8 byte/UTF-16 archive offset/모델 token을 단일 길이로 합치지 않는다.

특히 현재 `WorkingContext.close():991`는 archive.close 예외를 `false`와 `lastRetentionReason`으로 바꾸지만 `sealAndRelease`는 throw만 cleanupFailures에 넣는다. seal이 성공하면 반환 reason도 `terminal_remeasure_required`라 release 실패 이유가 그 결과에 실리지 않는다. 이 사실은 **SourceVerified 계약 관찰(C1)**이며 실제 lease 유실/누수 발생을 확정한 것은 아니다. `seal()` 내부 close와 terminal close가 겹치는 것도 현재 archive.close의 ENOENT 허용으로 대체로 무해하다. 개선 시 `seal()`은 checkpoint 결과만, terminal owner는 해제 결과만 맡겨 구조적으로 한 번 호출하고 두 결과를 독립 보고한다. archive의 idempotent close는 방어적으로 유지한다.

| 자원/상태 | 생성·유효 범위 | 무효화·해제 규칙과 소유자 |
|---|---|---|
| tool session | 실행 setup에서 획득, 실행 종료까지 | setup 실패 포함 root finally dispose; round close와 session close를 구별 |
| call/effective args | round guard 통과 후, 실행 ID+round ID+SDK call ID+provider request ID로 상관관계 유지 | finalized=실행 완료가 아님. 정상/거부/실패/취소마다 한 outcome. late callback은 닫힌 round 수정 금지 |
| BatchReservation | round input 측정 뒤 생성; held→consumed 또는 released | returned bytes를 한 번 charge; finally close로 잔여 held 해제. 거부 receipt를 큰 read 성공으로 계상하지 않고 결과와 outcome을 구별. overrun 시 다음 exact measurement 전 재dispatch 금지 |
| repeat cache | provider runtime의 동일 관찰+receipt, 현재 기본 최대256/TTL10분 | version/selector/scope 변경 또는 만료면 body 경로. entry 재사용은 모델 input 본문 보존을 의미하지 않음 |
| mutation snapshot | provider runtime, canonical project+path+owner, 기본 최대512/슬라이딩 TTL30분 | 유효 receipt 사용 시 touch, version 변경/TTL/LRU/restart/owner mismatch 검사. 전달하지 않은 등록은 회수; 반복 read로 불필요한 신규 등록 금지 |
| pending source evidence | 성공 결과 수집 시 pin, 다음 입력에 포함되기 전/소비 완료 전 | 단순 tool 반환·중단된 prediction으로 consume 처리 금지. 완료된 input consumer만 해제 가능 |
| current availability | 한 modelInputId의 최종 실제 후보 | projection/새 round마다 다시 산출. 과거의 visible=true를 다음 input에 복사하지 않음 |
| claim·필수 span | 목표+claim identity+observed source version | source 변경 시 stale; 철회/근거 충족/목표 전환 시 해당 pin만 해제. 메모 한 줄로 파일 전체 closed 금지 |
| archive lease/window | 실행 lease와 durable sealed window는 수명이 다름 | lease 해제 뒤 window가 refs를 보호. atomic publish 이전 lease 해제로 gap 만들지 않음. fork/다른 owner의 membership을 넓히지 않음 |
| visible transcript | host가 수신한 block들의 순서 | commit한 prefix는 불변. 보충은 새 block; terminal 이후 append/replace 금지. SDK 모델 history와 UI 표시를 임의 동일 객체로 취급하지 않음 |

수명 변화가 가장 위험한 세 경로의 목표 순서는 다음과 같다.

1. **정상 읽기**: scope 바인딩 → 예산 예약 → effective request 확정 → provider stable read → identity/version/span 결정 → 반복/응답 크기 판정 → 필요한 receipt만 발행 → 결과 수집 → 원장/가용성/ReadDelta 갱신 → 다음 입력 exact 측정 → 모델 소비 확인 → pending pin 해제.
2. **실패·취소**: 더 이상 새 dispatch/새 snapshot 발행 금지 → 이미 완료된 성공 관찰과 실제 반환된 실패 결과(errorCode/retry/suggestion), 원 실패 원인을 보존 → source coverage에는 성공만 반영 → 미응답 작업은 확인 수준에 따라 outcome unknown 또는 canceled로 기록 → held 예약 해제 → 부분 계획은 완료로 commit하지 않음 → 표시 이력과 근거 checkpoint 시도 → lease/session 해제 → 주 실패와 정리 실패 모두 기록. 파일 handle/listener 정리는 별도 finally에서 보장한다.
3. **projection·다음 턴**: 필요한 pending/claim span pin → archive body+window atomic publication → 메모/locator로 projection → 사용자에게 표시된 원 이력 prefix로 seal → 다음 user boundary 검증 → scope/lineage/prefix 검증된 window 복원 → 새 input에서 availability/예산 재계산. 복원 실패는 신뢰된 현재 이력의 관찰만 재구성하고 원 작업으로 돌아갈 조건을 평가한다.

**13. 파일별 구현 범위: 기존 소유자를 고치고 중복 관리자를 만들지 않는다**

| 파일 묶음 | 구체 변경과 공개 계약 영향 |
|---|---|
| `scripts/patch-lmstudio-sdk.cjs`, `src/round-loop.ts`, `src/prediction-ui.ts`, `src/prediction-loop.ts` (plugin) | 기존 버전 고정 호환 패치에 effective finalized payload와 실패 시 completed peer result 보존을 추가. SDK source anchor/hash를 검사하고 unknown SDK version에서 조용히 적용 금지. 요청/결과 쌍을 중복 emit하지 않으며 incomplete assistant prose는 planning commit에서 제외. batch close를 round finally에 배치. UI/proposed/effective를 한 fixture로 비교 |
| `src/direct-read-capabilities.js`, `src/direct-read-snapshot.js`, `src/direct-runtime-context.js` (agent), `shared-tool-core/repeat-cache.js` | 계산된 SHA와 resolver identity를 dedupe에 전달. duplicate에 검증 가능한 관찰 identity/version/span을 포함. snapshot 발행은 repeat/fit 결정 후로 이동. cache가 mutation 권한을 소유하지 않도록 기존 registry 경계 유지 |
| `src/file-snapshot-registry.js`, `src/direct-file-version-policy.js` (agent) | 유효 동일 identity/version/owner receipt의 재사용·touch를 명시적 API로 제공하고 필요 시 미전달 등록 revoke. prepare metadata 길이를 포함해 최종 envelope budget 재확인. 이미 commit한 mutation의 receipt 발행 실패를 mutation 실패로 뒤집지 않는 기존 정책 유지 |
| `shared-tool-core/direct-file-snapshot.js`, read handler/server cancellation 경계 | optional AbortSignal을 기존 함수에 추가하고 기존 callers 모두 점검. full-file hash loop의 취소 지점을 넣고 FD finally 유지. 임의 mtime cache로 해시 정확성을 희생하지 않음 |
| `src/evidence-identity.js`, `src/read-ledger.js`, `src/evidence-manager.ts`, `src/input-availability.js`, `src/context-ports.ts` (plugin) | provider result decoder → SourceKey/version/span → ReadDelta로 연결. receiptPaths를 path 단독이 아닌 검증된 관찰 연결로 변경. archive/source 교차 coverage, exact requested span visibility, unknown 보존. consumer별 좁은 port 타입과 runtime fixture 확인 |
| `src/working-context.js`, `src/evidence-archive.js`, `src/terminal-persistence.ts`, `src/working-context-boundary.ts` (plugin) | immutable visible prefix, typed restore/terminal result, seal과 release 역할 분리. scope 서명·prefix·membership·atomic write·durable pin은 유지. boundary 로직은 이 정상 검사를 바꾸기 위한 수정 대상이 아니라 두 턴/fork 회귀 대상 |
| `src/review-register.ts`, `src/review-evidence.ts`, `src/recovery-coordinator.ts`, `src/execution-state.ts`, loop | observedVersion 고정·미해결 claim span·권한 profile과 사용자 목표 분리. ReadDelta 소비자로 바꾸고 중복 progress 계산 제거. 복구 목표 달성 뒤 원 profile exact 측정 후 복귀 |
| manifest/build tool contract, provider catalog/response, package/install scripts | 필요한 result 계약만 additive 확장하고 generated 선언/check를 같은 commit에 반영. shared core가 설치본에 포함되는지 검사. legacy duplicate는 unknown으로 안전 처리. source/dist/installed/loaded identity를 따로 확인 |

`direct-repeat-cache.js`/`direct-file-snapshot.js`의 shared-core 위임 파일은 중복 구현이 아니다. 기존 repeat cache, snapshot registry, budget broker, archive, review register, recovery coordinator를 재사용한다. 새 global evidence manager나 통합 workflow engine을 추가하지 않는다. 정상 권한 검사·CAS·서명 검증을 느슨하게 하여 재조회를 없애지 않는다.

**14. 수정한 단계별 계획과 회귀 완료 기준**

| 단계 | 선행 조건 / 구체 결과물 | 완료 기준 |
|---|---|---|
| W0 확장 | 기존 합성 반례에 H9~H13 및 실제 SDK adapter fixture 추가. provider·harness가 각각 수동 작성한 모양으로 테스트를 대체하지 않음 | 수정 전 기대 동작 실패, 정상 대조군 통과. 기존 product staged diff 별도 보존 |
| W1 유지·보강 | W0 → 새 supplement block, immutable prefix, seal/release typed outcome와 terminal owner 단일화 | 두 턴 seal/restore; partial/empty supplement; history 변조 거부; close false/throw/중복 호출 회귀 |
| W1b 신규 | W0 → SDK effective request 및 성공 peer 보존, round batch finally 정리 | guard override 뒤 실제 실행/UI/지속 이력 동일. 성공1+실패1/실패 먼저/취소/직렬·병렬/늦은 결과에서 완료 관찰만 정확히 한 번 보존, 원 실패 유지 |
| W2a 신규 | W0 → provider SHA 기반 duplicate 계약, snapshot 발행 순서·수명, source read cancellation | metadata 충돌 새 body; capacity2/8에서 동일 repeat100회 신규 orphan0; budget-fit 실패 orphan0; resolution/read/hash 중 취소 후 신규 capability0 |
| W2 구체화 | W1+W1b+W2a → source identity/span/ReadDelta와 receipt visibility 통합 | 같은 source/SHA/span의 read/range/symbol/archive는 source novelty0. restoreRequired와 alreadyVisible 구별. 다른 프로젝트/engine/git revision은 합치지 않음 |
| W2b 신규 | W2a+W2 → provider 결과를 실제 하네스 decoder/ledger/input으로 연결하는 계약 검사 | body→repeat→projection→필요 span 복구→mutation receipt/CAS 연계; duplicate old/new schema; CJK/emoji/JSON escaping 및 line/byte/UTF-16 좌표 대조 |
| W3 유지 | W2 → claim version 고정, implicit whole-file closed 제거, unresolved span pin | Foo 검토로 Bar 차단 안 함; A→B→A에서 이전 claim 자동 재승격 안 함 |
| W4 유지·강화 | W2b+W3 → typed restore/recovery goal, 원 사용자 목표·도구 profile 복귀 | empty catalog/stale handle/budget denial별 유한 fallback; source 확보 후 원 profile 재측정·복귀; 권한 확대 없이 수정/build 후속 흐름 도달 |
| W5 유지 | W1 → 형식에 독립적인 claim 근거 의무, API/단위/상태 전이·raw 출력 처리 | 기존 H6/H8·보고 추가 문제 A1~A9 회귀. 보충 성공만으로 미확인 근거 해소 금지 |
| W6 보강 | 위 단계 → 양쪽 suite, real SDK 소스 계약, schema migration, generated/portable 설치 검증 | old manifest는 정확히 매핑 가능한 관찰만 이전; stale/unknown 자료로 억제 금지. golden contract fixture와 SDK version/patch drift 검사 |
| W7 유지 | W6 → 동일 모델/프로젝트 snapshot/config의 실제 감사+후속 수정·build 흐름 최소3회 | 불필요 dispatch/중복 body/미전달 snapshot/유실 peer/실패 restore 감소와 필요한 근거·정확도·작업 완료 동시 확인. 이 문서 작성 시 미실행 |

실행 순서는 **W0 → W1 → W1b → W2a → W2 → W2b → W3 → W4 → W6 → W7**, W5는 W1 이후 별도 작은 묶음으로 수행한다. provider 관찰과 SDK 전달이 틀린 상태에서 recovery 횟수·read suppression을 먼저 강화하면 잘못된 unchanged를 더 강하게 믿거나 유실된 결과를 단순 무진전으로 오판할 수 있기 때문이다. 대형 일괄 리팩터링 대신 단계마다 정상 대조군을 유지하는 작은 변경으로 나눈다.

새 회귀에서는 다음의 서로 다른 비용/실패를 따로 계측한다: 모델 요청 수, 실제 provider dispatch 수, 파일 read/hash bytes, 원본 반환 bytes, 모델 input 전달 bytes, 신규/복원/중복 span, snapshot issued/reused/revoked/evicted, 성공했으나 미수집된 peer, 취소 후 작업량, lease release 실패, next-turn restore 결과. **repeat 응답 body 감소는 파일 I/O 감소가 아니다.** 현재 provider repeat 판정은 읽기 이후이므로 응답 억제만으로 해시 비용까지 줄었다고 보고하지 않는다.

**15. 이번 심화 조사의 확인 범위**

- provider 실제 source를 호출한 신규 반례3개, 설치된 SDK1.5.0 실제 내부 함수와 현재 isolated build를 연결한 신규 반례2개를 재현했다. 모두 수정 전 결함 관찰이며 수정 후 통과가 아니다.
- provider 기존 `direct-repeat-cache.test.js`와 `file-snapshot-flow.test.js`는 **19/19 통과**했다. 앞선 plugin **103/103**과 별개다. 전체 suite 또는 UE build를 수행했다는 뜻이 아니다.
- `build-tool-contract.cjs --check`, `build-unity-tool-contract.cjs --check`는 통과했다. 현재 generated catalog가 틀렸다는 가설은 지지되지 않았다. 새 result 계약은 아직 제안이다.
- 새 probe와 결과는 앞서 명시한 두 외부 diagnostics 폴더에 저장했다. 저장소에는 이 계획과 근거 JSON만 갱신했다. 제품 소스·SDK 설치 파일·staged 변경·LM Studio 설정·원본 대화는 변경하지 않았다.
- local SDK와 provider 재현이 실제 호스트 transport의 모든 시간 순서까지 입증하지는 않는다. H13 실제 실행 빈도, H10 기본512 한도 소진, 큰 파일 취소 비용은 W7과 별도의 부하/호스트 검증에서 측정해야 한다.

**16. 수정 진행 결과 — 2026-10-26**

재조회 횟수 제한을 더하는 대신, 읽기 관찰·현재 입력 가용성·검토 판정·수정 권한의 계약을 분리했다. 아래는 현재 작업본에 적용한 변경이다. H1~H13의 위쪽 설명과 줄 번호는 수정 전 근거이며, 수정 후에도 같은 결함이 남아 있다는 뜻이 아니다. 아래 회귀 통과의 확인 수준은 `TestVerified`다. 원본 대화의 과거 실행 증거를 수정본의 `RuntimeVerified`로 전용하지 않는다.

| 대상 | 적용한 수정과 책임 | 수정 후 확인 |
|---|---|---|
| H1 / W1 | `VisibleHistoryRecorder`의 보충 출력은 새 host block에 append하고 논리적 최종 답변만 결합. terminal에서 freeze한 뒤 늦은 append/tool callback은 이력을 바꾸지 않음 | 보고→빈/일반 보충→다음 user 턴 seal/restore, 기존 prefix 불변 및 늦은 append 무효화 |
| C1 / W1 | `WorkingContext.seal()`에서 lease 해제 제거. terminal owner가 `close()`를 한 번 수행하고 seal/release 결과를 따로 기록. 원 seal 예외와 cleanup 실패를 함께 보존 | seal 성공+close false, seal 예외+close false/throw, 중복 close 회귀 |
| H12·H13 / W1b | SDK 1.5.0의 guard 수정 인자를 finalized/dispatch/history에 일치시킴. peer 예외 전에 끝난 결과를 한 번 전달하고 원 예외 유지. 종료 뒤 완료된 guard는 새 도구 실행 불가. 라운드 예산 reservation은 후처리 예외에도 정리 | 실제 설치된 SDK 내부 함수로 effective args, 성공 peer+예외의 두 순서, guard deny 대조군, 늦은 guard 검사. CJS/MJS의 버전·patch anchor 불일치는 쓰기 전에 실패 |
| H9~H11 / W2a | provider repeat 비교는 stat 대신 이미 계산한 SHA와 canonical source 사용. mutation snapshot은 prepare→응답 budget fit→dedupe→commit 순서. 취소 신호를 resolution 이후·파일 열기·read/hash loop·receipt commit 전까지 전달 | 같은 size/mtime의 다른 내용 반환. capacity 2에서 repeat 100회 후 registry 1개와 최초 receipt 유지. budget 실패·취소는 신규 receipt 0. 열린 descriptor 해제 및 취소 retry 금지 |
| H3·H4·H7 / W2·W2b | `ReadLedger`는 provider/project/root/path와 SHA 및 입증된 범위를 소유. `EvidenceManager`가 불변 `ReadDelta`를 한 번 계산해 재조회·복구·무진전 판단에 공유. repeat receipt 자체는 현재 본문 가용성이나 수정 권한이 아님 | read/range/archive의 같은 범위는 신규 근거 0. 부분 범위·본문 없는 receipt·허위 범위는 전체 본문 생략 불가. 실제 provider→harness 연결에서 한글/emoji/인용부호 전체 UTF-8 읽기와 line 읽기 동일성 확인 |
| 관찰의 신뢰 경계 / W2b | request/result pair와 등록된 provider에서 identity를 정규화. payload가 주장하는 digest·sourceKey·archive kind는 권위로 사용하지 않음. projection은 archive 소유 record의 membership/version/request와 실제 발췌 범위로 재구성 | 다른 provider/project를 위조 digest로 결합 불가. 원격 payload를 historical archive로 승격 불가. 변조된 projection 범위 거부, 실패 결과의 실패 상태 보존 |
| H5 / W3 | 모델 리뷰 문장을 whole-file closed/pin 해제에 사용하던 경로 제거. claim은 관찰한 SHA에 고정하고 변경 시 `needs_recheck`. 파일·symbol이 모호하면 임의 매칭하지 않고 stale row도 크기 제한 | Foo 메모가 Bar 읽기 차단 안 함. A→B→A에서 옛 판정 자동 복구 안 함. 다른 파일의 같은 symbol 판정 구분 |
| H2 / W4 | restore는 window/ledger/access 상태를 구분. 거부된 window의 host 본문은 historical hint로만 재구성. 빈 catalog/유효 대체 위치가 있는 실패만 한 번 fallback. 유효 source 확보와 원래 전체 tool profile의 토큰 재측정 성공 후 원 작업으로 복귀 | historical hint가 archive 권한·수정 receipt·새 읽기 억제를 만들지 않음. 실제 `handlePredictionLoop` fixture에서 context pressure→read recovery→원래 write 도구가 포함된 profile 복귀 확인 |
| H6·H8 / W5 | heading 계층과 명시적 표/list/bold severity 처리. 보충의 자기확신으로 기존 gap 제거 금지. 원 raw intent는 분류에 유지하고 표시 단계에서 실행되지 않은 protocol 숨김. 명시적 인용·코드 예시는 보존 | nested P2 근거 의무 유지. protocol의 모든 두 조각 분할 경계 테스트와 인용 대조군 통과 |
| 보고서 추가 문제 / W5 | 권고 C++ 코드에도 API 소유 타입 이름 대조와 미컴파일 안내 연결. terminal notice 조립은 `DeliveryController`가 담당 | 명시적 receiver의 잘못된 API 소유자 탐지 회귀. **단위·타입·authority·네트워크 시점·수명 전체의 의미 검증이나 A1~A9 자동 해결을 구현했다는 뜻은 아님** |

실제 책임 분리는 다음처럼 유지했다. provider의 stable reader는 관찰된 내용과 SHA를, `FileSnapshotRegistry`는 수정 receipt/CAS 수명을, `ReadLedger`는 입증된 과거 coverage를 관리한다. `EvidenceManager`는 이번 입력에서 보이는 근거와 이번 라운드의 차이를 판단한다. `ReviewRegister`는 특정 버전의 검토 메모만 관리한다. `RecoveryCoordinator`는 복구 상태 전이를, `RoundInput`은 원 profile의 예산 측정을, terminal persistence는 저장과 해제 결과를 책임진다. 새로운 전역 관리자나 중복 workflow engine은 추가하지 않았다.

추가로 검사 중 발견한 반례도 수정했다. source body 길이와 보고한 범위 불일치, bodyless receipt로 다음 body를 다시 생략하는 문제, 외부 payload의 archive/identity 위장, projection 범위 변조, 무한히 쌓이는 stale review row, 취소된 byte read의 잘못된 retry 안내, seal 실패 뒤 close false 누락을 회귀에 포함했다. byte→line 매핑은 전체 UTF-8 본문과 범위가 입증될 때만 수행한다. 부분 byte 조각의 line 위치를 추측해 중복 억제하지 않는다.

**16.1 검증 결과와 재현 위치**

| 검사 | 결과 | 근거 파일 |
|---|---|---|
| plugin TypeScript 및 generated 산출물 빌드 | 성공 | `build.log` |
| plugin 전체 suite | 1,059 통과, 0 실패, 0 제외 | `plugin-tests.log` |
| Unreal provider 전체 suite | 363 통과, 0 실패, 플랫폼 조건 2 제외 | `provider-tests.log` |
| 공통 모듈 소비자인 Unity provider suite | 132 통과, 0 실패, 환경 조건 5 제외 | `unity-tests.log` |
| 통합 패키지·금지 파일 필터 | 50 통과 | `package-tests.log` |
| generated runtime identity/tool contract/Unity contract/design guidance `--check` | 모두 성공 | 작업 명령 결과 및 구조화 JSON |
| 변경 전 staged patch 보존 | 30,262 bytes, SHA-256 일치 | `preexisting-staged.patch` |
| `git diff --check` | 오류 없음 | 작업 명령 결과 |

위 로그는 `C:/Users/sster/.evidence-first/diagnostics/harness-contract-implementation-20261026/`에 저장했다. 수정 전 실패 로그도 같은 위치의 `pre-fix-contract-tests.log`, `pre-fix-sdk-tests.log`, `pre-fix-provider-tests.log`, `pre-fix-provider-harness-bridge.log`에 남겼다. 이 중 SDK guard denial 정상 대조군은 수정 전에도 통과했다. 독립 검토의 추가 반례와 수정 후 결과는 `C:/Users/sster/.evidence-first/diagnostics/harness-continuation-audit-20261025/implementation-*-probe-result.json`에 있다.

staged patch SHA-256은 `f333111d6983e0b8265c49c60b5ca56c8f352f05b966d0ea262c94762caa9825`다. 기존 staged 11개 파일의 내용은 그대로이며, 이번 제품 변경은 추가 unstaged 변경이다. generated source ID는 `ef-build-v2-31c4ca82362ca6b1e567e20d9edc7b424a1227d182d2175f9e906ed045256c78`다. 이는 현재 source/dist의 식별자이며 LM Studio가 현재 이 ID를 실행한다는 확인이 아니다.

빌드의 기존 SDK 호환 패치 절차를 통해 **저장소 `node_modules/@lmstudio/sdk`의 CJS/MJS**는 수정했다. LM Studio 앱의 활성 plugin 설치본을 교체한 것은 아니다. 패키지 테스트용 fake SDK fixture에는 실제 패치가 요구하는 anchor를 보강했으며, SDK 동작 검사는 별도로 실제 SDK 1.5.0을 사용했다.

**16.2 계획 상태와 남은 검증의 구체 범위**

- W0·W1·W1b·W2a·W2·W3: 설명한 소스 변경과 자동 회귀 적용. 실제 호스트의 모든 cancel/transport 순서와 부하 성능까지 검증한 상태는 아니다.
- W2b: 실제 provider→하네스 byte/range/repeat 연결과 archive/receipt/CAS의 개별 경계 회귀를 확인했다. 전체 read→projection→복원→실제 수정→UE build를 하나로 연결한 실환경 검증은 W7에 남긴다.
- W4: 원 tool profile 복귀까지 하네스 통합 fixture에서 확인. 실제 모델이 후속 수정·빌드를 완료했는지는 미검증이다.
- W5: 형식·근거 의무·raw 출력과 권고 API/미컴파일 안내를 구현. A1~A9의 일반적인 의미 정확성을 보장하는 검증기는 구현하지 않았다. 후속 평가에서는 원 보고서의 degrees/radians, 리로드 취소, Niagara owner/API, weak owner, 초기 복제 시점을 각각 별도 정답과 반대 근거로 비교해야 한다.
- W6: 로컬 빌드·suite·generated·패키지 검사는 수행. 실제 LM Studio 설치/재시작 후 installed/loaded identity와 다음 턴 window 복원을 확인하는 배포 검증은 남아 있다.
- W7: 미실행. 동일 모델·설정·프로젝트 snapshot에서 감사→보충→다음 턴 수정 요청을 최소 3회 실행하고, `source_prefix_changed/mismatch`, 성공 peer 유실, 유효 receipt 유지, 복구 후 원 profile, 실제 수정·build 결과를 수집한다. 필요 시 쓰기 가능한 별도 프로젝트 사본을 사용한다. `model request`, provider dispatch, source read/hash bytes, body 전달 bytes, 신규/복원/중복 span, 취소 후 작업량과 처리 시간을 분리하여 기존 대화와 비교한다.

현재 결과는 **계약 결함의 소스 수정과 자동 테스트 통과**다. 중복 body 억제가 실제 provider dispatch나 파일 read/hash I/O까지 줄였다는 실측은 없으며, 토큰·시간 절감률도 아직 제시하지 않는다. 근거 JSON 검사 결과는 계속 `structure_valid; facts_not_verified`로 취급한다.
