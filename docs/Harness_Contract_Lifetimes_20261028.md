# 하네스·컴팩터 계약과 수명 후속 수정

기준은 `fe98e8d`이며, 대화1793156817525 감사의 A1–A7과 추가 조사 B1–B7을 반영했다. 이 문서는 구현과 회귀의 범위를 기록한다. 원본 대화·window·실제 게임 프로젝트는 수정하지 않았으며 원문이나 개인별 경로를 패키지에 포함하지 않는다.

구성 요소는 compactor **0.4.86/revision133**, Unreal agent **0.3.32**다. 제품1.4.3 및 설치 구성2.1.34와는 별도다. 제품 태그나 GitHub 릴리스를 발행하는 변경은 아니다.

## 계약과 소유자

| 감사 항목 | 변경한 계약 | 기존 소유자 |
|---|---|---|
| A1 protocol 분류 | 완성·잘림·durable이 같은 property-path classifier를 쓴다. JSON key 순서를 의미로 쓰지 않고 coverage.scope를 확인한다. literal/fence와 record·tool 위치를 보존한다. | internal-data-contract / InternalDataOutputFilter |
| A2 review profile | 엔진별 mutation 예외를 없애고 manifest operation effect로 제한한다. mixed 도구는 허용 action만 공개하고 guard가 확인한 override도 같은 제한을 적용한다. | ToolCapabilityRegistry / 기존 restrictedOperationProfiles |
| A3 retry minimum | 검증한 같은 provider/path/project의 source-read suggestion에도 기존 floor를 연결한다. 성공하면 연결된 floor를 해제한다. 실행을 승인하는 권한은 아니다. | BudgetBroker / 기존 retryFloors |
| A4/A5 보고 범위 | 숫자 없는 전체 파일 검토 주장, 구조 검사의 사실 확인 오인, 선언만 읽고 구현이 없다는 주장을 기존 최대1회 도구 없는 report review에 넣는다. | returnedSources / terminal scope notice / report supplement |
| A6 진단·출력 계측 | 바로 뒤 notice event가 확인된 host 문구를 모델 주장과 분리한다. metadata budget과 retry를 남긴다. raw 생성량과 renderer 전달량·protocol 제외를 별도 기록한다. | trace adapter / PredictionStreamRenderer |
| A7 지적 수명 | 후속 수정에서 과거 P1 보고서 항목을 언급하지 않으면 처리 상태를 밝히는 bounded 보완 의무를 준다. 과거 판단을 사실·수정 권한으로 승격하지 않는다. | 기존 report review / prior assistant report |
| B1 continuation 목적 | guidance와 같은 objective continuity로 활성 목적을 잇는다. 명시적인 새 요청은 그 요청의 effects를 사용한다. host 자동승인은 이전 제약을 철회하지 않는다. | objective continuity / TaskObjective |
| B2 source version | hash unknown snippet은 known-version body를 덮거나 확장하지 않는다. 별도 provenance와 uncertainty를 유지하고 불확실한 소스를 부재 증명으로 사용하지 않는다. | ReadLedger / quote anchoring |
| B3 commit 후 검토 | provider-qualified 성공 mutation의 version을 검토 index에 인계한다. old bundle을 보존하고 needs_recheck로 표시하며 자동 cleared 처리하지 않는다. 같은 batch의 arrival order로 read-after-write를 추론하지 않는다. | ReviewEvidenceIndex / ReviewRegister |
| B4 범위 수명 | 같은 hash의 포함 범위는 시작 행과 무관하게 더 넓은 기존 본문을 유지한다. 다른 hash는 섞지 않는다. | 기존 bounded ReviewEvidenceIndex |
| B5 삭제 승인 | proposal이 approved hash를 보유하고 현재 CAS proof와 함께 locked commit에서 대조한다. version 변경은 새 proposal이 필요하다. | delete proposals / FileSnapshotRegistry / path lock |
| B6 삭제 변경 통지 | 성공한 삭제도 기존 Source 변경 ledger에 경로를 기록한다. 없는 본문 검사는 skip하되 모듈 검증 범위는 전달한다. | BuildChangeLedger |
| B7 transaction 복구 | journal을 쓰기 전부터 기존 PID/start-identity path lock으로 소유한다. startup recovery도 같은 lock을 얻고, live/busy이면 journal을 바꾸지 않고 deferred로 반환한다. | 기존 write-locks / transaction journal / recovery |

## 보존한 조건

- 원본 transcript와 window를 직접 고쳐 서명이나 prefix 검증을 우회하지 않는다.
- source CAS, 실제 경로 재검증, 승인 token의 scope·만료·1회 사용, 외부 변경/ABA의 manual recovery를 유지한다.
- mutation 성공은 새 본문이나 기존 지적의 해소를 증명하지 않는다. 반환된 과거 source는 delivery 근거이며 현재 파일 변경 권한이 아니다.
- legacy pending journal은 모든 대상 lock을 확보한 뒤에만 rollback 또는 journal 변경을 시작한다. 잠금 하나가 busy이면 파일과 journal 모두 그대로 둔다.
- report 보완은 기존 최대1회·tools0·예산 검사·취소 정책을 재사용한다. 새 재시도 루프나 사실 검증기를 추가하지 않는다.
- mandatory budget과 exact template gate, tool request/result pairing, window scope·lineage·lease를 약화하지 않는다.

## 검증

신규 회귀는 key 재배열/중첩 scope/partial protocol과 literal 대조, provider 공통 review profile, no-edit→계속→명시적 변경 전환, suggestion minimum의 다른 도구 인계, unknown search와 known source 인용, commit 후 재검토, same-hash range 포함 관계를 다룬다.

보고서 검사는 실제 prediction handler에서 구조 검사 후 과장 문구가 나왔을 때 보완을 한 번만 호출하고 tools0임을 확인한다. pure scope 검사는 unread implementation·명시적 미확인 대조군·과거 항목의 보류 표현을 다룬다. trace는 host notice와 모델 본문을 구분한다.

provider 회귀는 임시 fixture만 사용한다. 삭제 rename은 fake callback으로 가로채고, stale CAS 거부·새 hash에 옛 승인 거부·새 승인 성공·1회 사용·변경 ledger 기록을 확인한다. 실제 bundle의 hook에서 startup recovery를 불러 live journal 불변을 확인하고, legacy busy lock을 해제한 뒤 정상 rollback도 확인한다.

최종 검사 결과는 [근거 JSON](evidence/harness-contract-lifetimes-20261028.json)에 기록한다. packet 형식 검사는 기록 구조만 확인하며 소스 사실이나 작업 완료를 인증하지 않는다.

| 검사 | 결과 |
|---|---|
| plugin clean build / 전체 | 1208 통과, 실패0, 제외0 |
| Unreal agent 전체 | 375 통과, 실패0, 제외2 |
| Unity 전체 | 132 통과, 실패0, 제외5 |
| Python 전체 | 1492 통과, 실패0, 제외41 / 246.30초 |
| 생성물 | runtime/tool/design/unity 4종 소스 일치 |
| 최종 패키지 구성·재현성 | 2 통과, 실패0 |

독립 검토에서 발견한 새 bundle의 즉시 적용성, 동일 unknown 검색의 불필요한 인용 검사 면제, dotted JSON property와 중첩 경로 구분, 완성 JSON 뒤 늦은 marker의 정상 본문 보존도 회귀에 추가했다. 검토자가 최신 src/dist로 네 반례를 재확인했다. 기존 기대값은 약화하지 않았다.

provider의 승인 hash, transaction lock 획득·실패·정상·rollback 해제, legacy busy의 journal 불변, 외부 변경/ABA manual 보호도 별도 읽기 전용 검토에서 대조했다.

## 확인 범위와 남은 실측

A4는 임의의 모든 구현 부재를 검증하는 기능이 아니다. 현재 턴에 완전히 반환한 목록, 반환된 대응 header의 이름과 정확한 함수명, 구현 부재 문구가 함께 있을 때만 보수적으로 보완 의무를 만든다. 전체 source 검토를 의미적으로 증명하지 않는다.

A7의 과거 지적 누락 원인이 compactor였는지는 여전히 증명하지 못했다. 이번 변경은 반환된 이전 P1 heading/identifier에 대한 처리 상태 보완이다. 자동 task planner나 모든 자연어 작업의 의미적 해결 추론은 추가하지 않았다. 보완에서 이름만 언급해도 실제 해결이 증명되는 것은 아니며 taskCompleted 권한은 부여하지 않는다.

같은 모델·snapshot·설정에서의 수정 후 실제 대화, cache-hit 수, prefill·재조회 감소량은 별도 실측이 필요하다. 자동 회귀 성공을 모델의 감사 정확도나 모든 수명 경로의 런타임 보장으로 보고하지 않는다.
