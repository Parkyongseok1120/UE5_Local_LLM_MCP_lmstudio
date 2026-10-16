# MCP 검증·읽기·보고 계약 보정 — 2026-10-04

기준 커밋은 `6893493`이며, 먼저 진행한 구조 리팩토링 변경을 유지한 작업 트리에서 수정했다. 최신 서버 로그와 저장된 LM Studio 대화의 도구 생성·guard·결과·입력 노출 경계를 대조했다. 이번 수정의 확인 수준은 **BuildVerified / TestVerified**다. 실제 LM Studio 설치본 갱신과 모델 생성은 아직 확인하지 않았다.

## 확인한 사건과 수정 범위

`evidence_first_validate`의 실패 두 건은 검증기 실행 전에 인수 생성이 출력 한도에 도달한 사건이었다. SDK 통계의 종료 사유는 `maxPredictedTokensReached`, 분류 단계는 `tool_arguments`였으며 guard와 정상 결과가 없었다. 승인이나 컨텍스트 부족을 원인으로 확정하지 않는다. 형식 검사 자체는 작은 정상 패킷으로 실행할 수 있었다.

이와 함께 읽기 예산의 큰 기본 예약, 최초 반환값의 사전 축소, guard 거부 뒤 상태 종료, Auto의 복수 주제 선택, 스키마와 서버 타입 검사 및 설치 cleanup 경계를 보정했다.

| 변경 | 기존 담당자 | 변경 후 계약 | 확인한 회귀 |
|---|---|---|---|
| 작은 검증 패킷 | `evidence_packet_contract.py` → MCP 설명·contract 응답·프리셋 | 주장 하나 또는 작은 묶음씩 검사한다. 형식·기록 일관성만 검사하며 사실성·전체 완료를 인증하지 않는다. | 정책·프리셋 일치, 독립 패킷 처리, 성공·실패의 scope 메타데이터, 실제 stdio 호출 |
| 생성 실패 뒤 전달 | `PredictionLoop` → `DeliveryController` | 잘린 요청은 미실행 상태로 끝내고, 구조화된 반환 기록과 미완료 범위를 부분 보고한다. | 출력 한도·일반 parse 실패, 기존 결과와 완료 peer 보존, 미완료 계획 제외, 추가 생성·승인 없음 |
| 실제 Unreal 읽기 보고 | `EvidenceManager`의 pairing/관찰 정규화 → `DeliveryController` | native byte/line 응답을 등록된 도구의 개별 확정 pair로 해석한다. 무관한 미완료 호출이 정상 근거를 제거하지 않는다. | native MCP envelope, line/byte 범위, 실패·고립·중복 결과, 위조 provider, 본문 없는 acknowledgment |
| 읽기 예약 | `BudgetBroker` | 공개 스키마에 `maxBytes`가 있는 Unreal 읽기는 생략된 창을 4 KiB 이하로 예약한다. 명시한 큰 창은 공개 한계와 남은 예산으로 제한한다. | 50,419 예약 용량에서 여러 기본 읽기 허용, 명시한 창 보존, 과도한 반환 뒤 다음 dispatch 차단 |
| 최초 반환값 | `WorkingContext` / 기존 정확한 입력 측정 | 아직 완료된 입력에 제공하지 않은 원문은 크기만으로 미리 축소하지 않는다. 전체 입력 부족 시 기존 projection·압축·재측정을 사용한다. | 8,192자보다 큰 저압력 Git 원문, 미완료 소비 보존, 완료 뒤 축소, 여섯 호출의 부족 예산, 이미지 보존 |
| 상태 종료 | 기존 도구 상태 추적기 / guard | 거부와 종료에서 loading을 닫는다. 종료 전 미완성 생성 관측은 출력 단계 분류·복구 판단에 별도로 고정한다. | scope·profile·중복·예산 거부, 중단, 결과 없음, 짧은 prose 뒤 미완성 생성의 보고 복구 금지 |
| Auto 선택 | 순수 `guidance-topic-selection` → 기존 guidance composer | 한국어 ‘찾을 것’ 계열을 인식한다. 명시한 두 주제는 주제·보조로 선택하고 현재 진단은 보조에서 우선한다. | 실제 설계·수명 요청, 인용·코드·경로·제외 요청, stale 진단, 기존 section·token 한계 |
| schema / 타입 | 공통 계약 → stateless validator | 생성 schema의 지원되지 않는 regex를 제거하고 서버의 공백 검사와 제공된 obligation 배열 타입 검사를 유지한다. | 공백·다중 행 문자열, 여섯 optional 필드, 잘못된 mode, 작은 정상 패킷 |
| 설치 결과 | 독립 skill installer | 게시 성공 뒤 cleanup 실패는 경고다. 게시 실패의 주 오류와 기존 파일 복구는 유지한다. | backup cleanup fault, 게시 실패 뒤 기존 설치 복구 |

## 책임·경계·수명에서 유지한 조건

- 검증 정책의 원문은 `VALIDATION_POLICY` 한 곳에 있다. 생성 프리셋은 `model_guidance_prompt()`와 동일해야 한다는 회귀로 검사한다.
- 형식 검증기는 프로젝트 파일이나 외부 상태를 읽지 않는다. 소스 근거는 기존 읽기 도구가 제공하고 모델이 판단한다.
- 결과 예산은 `BudgetBroker`, 원문·archive·노출은 `WorkingContext`와 `EvidenceManager`, 보고는 `DeliveryController`, 생성·라운드 수명은 `PredictionLoop`, 표시 종료는 기존 tracker가 맡는다.
- 부분 보고의 `confirmedPairsOnly` 옵션은 개별 반환 pair만 다룬다. 복구·진행 집계의 기본 complete-batch 정책은 유지한다. pairing 규칙은 기존 `tool-exchange-index.js`를 재사용한다.
- UI를 종료해도 이번 라운드에 미완성 생성이 있었다는 관측은 지워지지 않는다. guard가 거부한 정상 요청과 미완성 생성은 구분한다.
- byte 예약은 추정치이며 최종 모델 호출은 기존 정확한 전체 입력 측정을 통과해야 한다. projection은 발췌·생략 범위를 기록하고 전체 원문 제공이나 모델 이해를 주장하지 않는다.
- 새 검증 세션, 별도 검증 모델, 서브 에이전트, 자동 mutation 재시도, 필수 static Validate gate를 제품에 추가하지 않았다. Auto는 기존 문서 수·token 상한 안에서 참고만 제공한다.
- archive와 부분 보고는 현재 파일 수정 권한이나 fresh-read receipt를 만들지 않는다. 검증 실패 뒤 부분 보고는 전체 조사 완료가 아니다.

## 독립 검토에서 발견해 함께 보정한 경계

1. 실제 Unreal `read_file` 응답에 공통 workspace의 `kind`가 없으므로 native 파일 본문이 보고에서 빠졌다. 기존 관찰 정규화와 trusted pair를 연결했다.
2. UI 정리 뒤 `hasUnfinished()`를 다시 읽으면 오류 분류가 달라졌다. 종료 전 관측을 라운드 내에서 고정했다.
3. 전체 history의 ambiguity를 보고에도 적용하면 이미 정상 반환된 근거가 빠졌다. 보고 전용 개별 pair 옵션으로 수정하고 복구 기본 정책은 유지했다.

최종 재검토에서는 지정한 수정 경로에 추가 결함을 확인하지 못했다. 이는 전체 시스템에 결함이 없다는 보장이 아니다.

## 실행한 검사

| 검사 | 결과 | 범위 |
|---|---|---|
| `npm test` — compactor | **506 passed, 0 failed** | 빌드 포함, 기존 전체 회귀와 이번 추가 경계 |
| pytest — evidence MCP / packet / skill installer / integrated installer / package | **143 passed** | 형식 검사, 프리셋, 격리 설치·fault injection, 패키징 |
| `npm run guidance:check` | 성공 | 문서 원본과 생성 bundle 일치 |
| 실제 Python stdio 서버 호출 | 성공, schema `1.1.2`, 단일 claim, stderr 없음 | repository 서버의 initialize / tools/list / validate / 종료 |
| `git diff --check` | 성공 | 기존 작업 트리 포함, Windows CRLF 설정 고려 |

핵심 재현 17개와 Python 단독 33개는 위 전체 검사에 포함되므로 총수에 중복 합산하지 않는다. 실제 생성 토큰 절감률이나 모델 준수율은 이 결과에 포함하지 않는다.

검사 출력과 최신 실행 대조 자료는 로컬 `artifacts/mcp-evidence-audit-20261004/`에 보관했다. 해당 디렉터리는 배포용 소스에 포함하지 않는다. 구조화된 변경 기록은 [검증 기록](evidence/mcp-evidence-contract-fix-20261004.json)에 남긴다.

## 적용 상태와 남은 실제 실행 확인

이번에는 저장소 소스·문서·테스트와 repository compactor 빌드까지 갱신했다. LM Studio에 설치된 compactor, 설치된 evidence-first skill, 저장된 프리셋, 실행 중 서버를 갱신하거나 재시작하지 않았다. 현재 연결은 설치된 skill의 MCP 스크립트를 사용하므로 저장소 수정만으로 그 프로세스가 바뀌지 않는다. 커밋·푸시·배포도 실행하지 않았다.

실제 적용 시에는 compactor 설치본, evidence-first 설치본, 해당 프리셋을 함께 갱신하고 새 스키마가 연결된 실행에서 아래를 확인해야 한다.

1. 최신 사용자 목적의 Auto 입력에 설계·수명 문서가 예산 안에서 선택되는지.
2. 작은 검증 인수가 출력 한도 안에 완성되고 스키마 변환 경고가 재발하는지.
3. 예산 여유가 있는 최초 원문이 실제 입력에 남고, 부족할 때 발췌 범위가 기록되는지.
4. 도구 거부·중단 시 대기 표시가 닫히고, 생성 실패 뒤 미완료 부분 보고가 표시되는지.

이 네 항목의 LM Studio UI·실모델 동작과 품질 개선은 **NeedsRuntimeProof**로 남긴다.
