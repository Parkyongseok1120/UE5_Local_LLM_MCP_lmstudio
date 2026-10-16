# 모델 행동 개선 구현 계획 — SSOT·SOLID·책임·계약·수명

- 작성일: 2026-09-29
- 기준 커밋: 4d88404 — 설계 참고 문서 선택 주입 기능 추가
- 상태: **P0–P5 소스 구현 및 로컬 계약 검증 완료. 구현 결과는 §19와 별도 구현 근거 기록에서 확인한다. P6 실제 모델 평가는 보류한다.**
- 범위: LM Studio 컴팩터의 선택적 참고 제공, 기존 도구 결과의 근거 표현, Unreal·Unity 적용, 배포·검증 계획.
- 현재 모델 품질 개선 효과: 미검증. 기존 테스트 통과와 모델 행동 개선을 구분한다.
- 관련 문서: [기존 연결 제안](Design_Guidance_Integration_Proposal_20260928.md), [참고 문서 정본](model-guidance/README.md), [이번 계획의 근거](evidence/model-behavior-plan-20260929.json).

## 1. 목표와 완료 조건

목표는 모델이 실제 코드와 버전·진단·요구를 연결하여 수정하고, 이미 실패한 추측을 반복하거나 기존 의미를 바꾸는 수정을 줄이는 것이다. 여러 프로젝트에서 기존 소유자와 계약을 찾아 사용할 수 있어야 한다.

| 사용자에게 필요한 변화 | 관찰할 행동 | 완료를 주장할 수 없는 근거 |
| --- | --- | --- |
| 헤더·API 추측 반복 감소 | 실제 표현식과 해당 버전의 정의를 확인하고 수정 | 안내 문서를 입력에 넣었다는 사실만으로 개선 확정 |
| 의미를 보존하는 수정 | 충돌 필터·권한·콜백 등의 의미 변경을 인식하고 요구에 맞게 처리 | 컴파일 성공만으로 기능 정상 확정 |
| SSOT·SOLID 적용 | 기존 변경 권한·소유자·생성/종료 경로를 확인하고 최소 변경 | Manager·인터페이스·클래스 개수 |
| 수명·실패 처리 개선 | 중복·취소·동시 실행·부분 실패·종료를 관련 범위에서 고려 | 성공 경로 코드만 존재 |
| 기본 코드 표현 개선 | 프로젝트 설정과 주변 코드의 형식에 맞춤 | 무관한 파일 전체를 재포맷 |
| Unreal·Unity 및 다른 프로젝트에 적용 | 확인된 엔진·패키지·프로젝트 범위의 근거 사용 | 특정 게임 사례를 외워 재현 |

이번 변경은 모델 가중치·RAG 인덱스·자율 에이전트 계층을 추가하지 않는다. 설계 점수, 필수 문답, 강제 static Validate, 자동 수정 반복, 새 승인 단계도 만들지 않는다. 기존 권한·경로 보호·동시 쓰기 검사는 그대로 해당 소유자가 판단한다.

구현 완료는 기능·계약 테스트 통과를 뜻한다. 모델 품질 개선 확인은 별도의 실제 사용 평가를 완료한 뒤에만 주장한다. 사용자 요청 없이 이번 계획 작업 중 LM Studio 호출·설정 변경·설치·게임 프로젝트 수정은 하지 않는다.

## 2. 기준 커밋에서 확인한 출발점

| ID | 소스에서 확인한 사실 | 설계에 주는 제약 |
| --- | --- | --- |
| E01 | design-guidance.ts는 명시된 모드와 ToolScope로 문서를 선택하고 문서 전체의 prefix 후보를 만든다 | 절 선택은 기존 선택 책임을 확장한다 |
| E02 | prediction-loop.ts에서 scope와 guidanceDocuments를 실행 시작 때 계산한다 | 매 라운드 자료 갱신과 실행의 프로젝트 바인딩 변경을 혼동하지 않는다 |
| E03 | round-input.ts가 압축 이후 선택적 문서를 넣고 ContextManager가 정확히 측정한다 | 자료를 위해 근거 이력을 더 압축하거나 출력 예약을 줄이지 않는다 |
| E04 | composeModelHistory는 전달된 instructions를 system 내용에 합친다 | 프로젝트 파일·로그·모델 해석을 같은 instructions 인자로 넘기면 안 된다 |
| E05 | EvidenceManager/WorkingContext에 관찰·출처·버전·범위·이력 보관·입력 가용성 경계가 있다 | 별도의 영구 사실 DB나 현재 파일 버전 저장소를 만들지 않는다 |
| E06 | continuity-model-notes.js의 v1은 decisions/rejectedHypotheses/openQuestions 등의 엄격한 형식과 총 4개·1500자 제한을 사용한다 | 새 필드를 임의로 추가하면 기존 파서와 보관 계약이 깨진다 |
| E07 | Unreal 빌드 응답은 diagnostics, 대상·구성, proof, 로그 경로, fingerprints를 제공한다 | 동일 로그를 컴팩터에서 별도 빌드 판정기로 다시 해석하지 않는다 |
| E08 | Unity compile 요청은 accepted/verification unknown이며 logs는 구독 후 관찰한 이벤트만 제공한다 | 요청 수락, 관찰된 컴파일 완료, Player 빌드 성공을 구분한다 |
| E09 | Unity Bridge는 editorSessionId/domainGeneration/compilationId 등을 이미 소유한다 | 새 세션 번호·컴파일 세대를 따로 만들지 않는다 |
| E10 | Direct 응답 계약은 서버 소유 workflow 지시를 제거하고 기존 staticValidate는 advisory/blocksBuild false다 | 참고 기능이 nextAction·requiredTool·검사 통과 게이트를 복원하면 안 된다 |
| E11 | 통합 패키지는 필수 파일을 명시적으로 열거한다 | 새 모듈·정본·생성기·테스트의 배포 포함을 확인한다 |

현재 상태가 위 책임 분리의 모든 상황을 이미 보장한다는 뜻은 아니다. 이번 계획은 읽은 경계를 재사용하는 제안이며, 미확인 동작은 아래 테스트로 확인한다.

## 3. 설계 결정

### 3.1 이번 구현에서 확정할 결정

1. 일반 원칙의 본문 정본은 docs/model-guidance의 Markdown이다. 생성된 TypeScript는 배포 산출물이다.
2. 사실의 정본은 원래 도구 관찰·소스·설정이고, 요약은 출처를 가진 파생 표현이다.
3. 자료 선택은 순수 함수로 구현한다. 도구 호출·LLM 호출·프로젝트 전환·예산 변경을 하지 않는다.
4. 전체 입력 적합성은 기존 ContextManager/BudgetBroker만 판단한다.
5. 기존 실행 scope를 실행 중 자동 변경하지 않는다. scope와 관찰이 충돌하면 해당 프로젝트 자료를 생략한다.
6. 원본 관찰, 사용자 요구, 모델 해석, 일반 원칙을 서로 승격시키지 않는다.
7. 처음에는 새 note 프로토콜·새 영구 저장 포맷을 만들지 않는다.
8. 관찰을 더 얻기 위한 숨은 파일 탐색·전체 저장소 스캔·자동 컴파일·추가 모델 호출을 넣지 않는다.
9. 반복 오류에 대한 정보는 제공하되 다음 도구와 수정 방법을 자동 강제하지 않는다.
10. 포맷의 1차 범위는 설정과 주변 예시의 전달이다. 자동 포맷터 실행은 별도 후속 범위다.

### 3.2 설정과 이전 버전 호환

기존 designGuidanceMode와 designGuidanceMaxTokens의 저장 키와 값은 유지한다. mode 기본값 Off, allowance 기본값 2048, 0이면 비활성을 유지한다.

새 설정은 하나만 추가한다.

| 설정 | 값 | 동작 |
| --- | --- | --- |
| designGuidanceDelivery | documents | 기존 문서 단위 동작. 누락·알 수 없는 값의 기본값도 documents |
| designGuidanceDelivery | focused | 선택한 주제 안에서 관련 절과 출처가 있는 관찰 요약을 선택 |

GUI 표시 이름은 Reference delivery, 항목은 Whole documents / Focused references로 한다. focused는 구현·품질 평가 전까지 실험 기능으로 설명한다. 기존 설정을 자동으로 focused로 이전하지 않는다.

catalogue v2 생성기는 기존 GuidanceDocument 배열과 새 절 배열을 함께 생성한다. documents는 기존 문서 선택 순서·prefix 후보·본문만의 revision 계산을 유지하며 관찰 요약을 넣지 않는다. P1에서는 표식 삽입 외에 본문을 재작성하지 않고, 표식을 제거한 정규화 본문이 기준 버전과 같은지 검사한다. 이후 내용 보강은 별도 변경으로 검토한다. focused 후보가 실패했을 때 documents 전체를 자동 대체 주입하지 않으며, 자료가 추가되기 전 기준 입력 B로 돌아간다.

mode=core는 공통 자료 범위를 유지한다. debugging/design/multiplayer 등의 주제를 자동으로 다른 주제로 바꾸지 않는다. 실패 진단은 사실 자료로 요약할 수 있으나 주제 밖의 전문 문서를 숨겨서 추가하지 않는다. 초기 버전에 자동 주제 분류 모드를 만들지 않는다.

Off/Observe only/allowance 0에서는 새 자료의 분석·후보 조립·추가 측정을 수행하지 않는다. 실제 도구의 기존 진단 결과는 정상적으로 반환된다.

focused의 단계별 제공 정책도 고정한다. 일반 작업 라운드와 기존 bounded audit의 정상 조사 라운드에는 scope에 맞는 읽기 전용 자료 후보를 허용한다. research recovery, tool-planning retry, reasoning recovery, 강제 최종 보고에는 focused 자료를 새로 넣지 않는다. documents는 기존 코드의 단계별 동작을 유지한다. 자료 생략이 실행 phase를 바꾸지는 않으며 원래 오류·관찰 이력은 기존 보존 정책을 따른다.

## 4. 책임 배치와 의존 방향

| 책임 | 기존 소유자 또는 최소 확장 위치 | 입력 | 출력 | 맡기지 않을 일 |
| --- | --- | --- | --- | --- |
| 정본 편집·생성 검증 | Markdown/catalog.json/build-design-guidance.cjs | 문서·메타데이터 | 정적 배포 번들 | 런타임 파일 탐색 |
| 자료 선택 | design-guidance.ts | 주제·scope·검증된 선택 신호 | 순서가 있는 완결된 자료 후보 | 토큰 승인·다음 행동 결정 |
| 반환 관찰의 정규화 | EvidenceManager + 작은 순수 변환 모듈 | 완료된 요청/결과·provider·scope | 읽기 전용 관찰 요약 | 도구 실행·재시도·프로젝트 소유권 |
| 원본 보관·노출 기록 | 기존 WorkingContext/가용성 경계 | 실제 도구 결과 | 기존 archive refs·노출 기록 | 요약을 fresh read로 인정 |
| 모델 판단의 연속성 | 기존 continuity-model-notes | 선택적 모델 메모와 refs | prior assistant judgment | 구조가 옳다는 자동 판정 |
| 입력 조립·측정 | round-input/ContextManager | 기준 입력·선택 후보 | 측정된 입력 또는 기준 입력 | 품질 검사 게이트 |
| 공유 예산 | BudgetBroker | 전체 입력·출력 예약·도구 예산 | 기존 적합성 판정 | 자료 주제 해석 |
| 엔진 관찰 | Unreal 도구/Unity Bridge 및 adapter | 실제 명령·Editor 이벤트 | 기존 결과와 필요 시 가산 필드 | 모델 작업 순서 통제 |

호출 관계는 관찰 → 읽기 전용 요약 → 자료 후보 → 기존 입력 조립 → 기존 모델 실행이다. 요약·선택 함수가 역으로 실행기나 권한 소유자를 호출하지 않는다.

SSOT는 서로 다른 종류의 상태를 한 전역 객체에 합치는 뜻으로 사용하지 않는다. 각 사실의 결정 경로를 한 곳으로 유지한다. SRP는 위 표의 변경 이유를 나누는 데 사용한다. OCP는 새 문서·적용 범위를 metadata로 확장하는 데 사용한다. LSP는 공급자가 달라도 출처·미확인·부분 결과의 의미가 같도록 한다. ISP/DIP는 읽기 전용 자료 선택에 실행기 전체를 주입하지 않는 것으로 적용한다.

새 클래스·이벤트 버스·DI 컨테이너·범용 규칙 엔진은 필요하지 않다. 신규 런타임 파일 후보는 순수 타입/정규화/표현 함수용 최대 세 모듈이며, 기존 파일에 자연스럽게 들어가는 기능은 그곳에 둔다.

### 내부 인터페이스의 최소 변경

- GuidanceCandidate는 기존 ids/instruction을 유지하고 선택적인 referenceData 배열을 추가한다. documents 후보는 기존 필드만 사용한다.
- addOptionalReferences의 조립 callback은 현재 instruction 인자 뒤에 선택적인 referenceData 인자를 추가한다. 기존 한 인자 호출부는 그대로 동작하도록 한다. 이 함수의 예산 판단 책임을 다른 곳으로 옮기지 않는다.
- createInputAssembler의 profile에 referenceData를 추가한다. composeModelHistory에도 마지막 선택적 인자로 전달하여 기존 호출부의 의미를 유지한다.
- referenceData 항목은 허용된 kind, origin/ref, 관찰 당시 scope, 제한된 typed payload, coverage만 갖는다. 임의 role/systemInstruction/command/nextTool 필드는 받지 않는다.
- 기존 selectedIds/omittedIds는 문서 또는 절 식별로 유지하고 파생 자료는 selectedDataIds/omittedDataIds에 가산 기록한다. 추가 토큰은 두 종류의 합이다.
- 원본 공개 도구 응답·note v1·기존 함수의 기본 동작은 새 내부 타입을 모르는 소비자에게도 기존 의미를 유지한다. 파생 타입의 새 버전을 모르면 원본만 사용한다.

후보의 instruction/referenceData/출처 집합은 하나의 불변 값으로 채택·폐기한다. data를 먼저 B에 붙인 뒤 instruction만 축소하는 구현은 허용하지 않는다. 모든 후보가 탈락하면 data 슬롯도 없는 원래 B를 반환한다. selectedIds/selectedDataIds는 최종 실제 입력에 존재하는 내용과 일치해야 한다.

## 5. 문서 절과 적용 범위 계약

### 5.1 정본 형식

catalog schemaVersion을 2로 확장하되 문서 ID/파일명/엔진 필드는 유지한다. Markdown에는 안정적인 절 ID의 시작·끝 표식을 추가한다. 표식은 코드 블록 밖에서만 해석한다. 런타임에서 자연어 heading을 추측하여 나누지 않는다.

    <!-- guidance-section: preserve-semantics -->
    절의 완결된 본문
    <!-- /guidance-section: preserve-semantics -->

catalog에는 절 ID, 주제, 적용 엔진/패키지, 필요한 동반 절, 안정적인 우선순위, 지원하는 관찰 신호를 기록한다. 원칙 본문을 catalog나 도구 설명에 복사하지 않는다.

생성 데이터의 최소 의미는 다음과 같다.

| 필드 | 계약 |
| --- | --- |
| id | 문서ID/절ID 조합으로 전역 유일 |
| documentId / source | 정본 문서와 절 위치 |
| revision | 정규화한 본문·적용 조건·동반 절을 포함한 해시 |
| topics / engine | 적용 가능한 기존 모드 목록과 공통/Unity/Unreal |
| applicability | 확인된 버전·패키지 조건. 조건 미확인은 적용 성공이 아님 |
| requires | 함께 넣어야 의미가 보존되는 절 ID 목록 |
| signals | 제한된 내부 관찰 신호 enum. 임의 JS·regex 실행 금지 |
| text | Markdown 정본에서 가져온 완결된 본문 |

requires의 전이 폐쇄를 하나의 원자적 묶음으로 취급한다. 순환 의존·없는 ID·서로 충돌하는 적용 조건은 생성 시 오류로 처리한다. 절 중간을 토큰에 맞춰 잘라 넣지 않는다.

동반 절은 현재 명시 주제에 속하거나 공통 core 절이어야 한다. 엔진 절에도 topics를 명시하고 엔진 이름만으로 주제 경계를 우회하지 않는다. 폐쇄에 포함된 모든 절의 applicability를 확인한다. 폐쇄 전체가 최대 6개 절 한도를 넘거나 일부 조건이 미확인이면 묶음 전체를 생략하고 dependency_limit/applicability_unknown 이유를 남긴다. requires를 끊어 부분 묶음을 넣지 않는다.

### 5.2 생성 단계 검사

- 중복/빈 ID, 허용되지 않은 경로, 파일명 불일치, 절 중첩·누락·끝 표식 불일치.
- 코드 예시 안의 가짜 표식, CRLF/LF, UTF-8 한글, BOM 정책, 마지막 줄바꿈.
- 존재하지 않는 동반 절, 순환, 문서와 절 엔진 불일치, 모르는 신호.
- 메타데이터 변경도 revision을 바꾸는지, 정본과 생성 번들이 일치하는지.
- 기존 문서 단위 모드에서 표식이 제거된 본문을 제공하고 원칙 내용은 보존하는지.
- 예제 프로젝트 현황·절대 사용자 경로가 공통 번들에 포함되지 않는지.

이 검사는 플러그인 개발·패키징 시 수행한다. 사용자의 게임 코드에 정적 검사 게이트로 연결하지 않는다.

### 5.3 초기 절 구성

| 주제 | 초기 절 후보 |
| --- | --- |
| core | 근거와 확인 수준, 기존 소유자 우선, 의미 보존, 간결한 표현 |
| design | 정의/실행/표시 상태, 변경 경로, 실패·중복, 동시 작업과 종료 |
| debugging | 오류→표현식→정의, 검색 실패 구분, 같은 진단의 재발, 결과 수준 |
| code-style | 프로젝트 설정 우선, 주변 형식, 관련 변경 범위 |
| lifecycle | Unreal/Unity에서 확인할 생성·활성화·종료 경계 |
| multiplayer | 권한/소유 연결/로컬 제어, 지속 상태/효과, 시각, 중복과 접속 수명 |

FDamageEvent 같은 사례는 설명용 예시다. 특정 심볼명·프로젝트명에 반응하는 전용 해결 규칙은 만들지 않는다.

## 6. 선택 알고리즘과 입력 예산

### 6.1 각 라운드의 처리 순서

1. 기존 압축·복구·가용성 처리를 끝내고 기준 입력 B를 조립·측정한다.
2. focused가 꺼져 있거나 B가 실행 불가능하면 선택 작업을 건너뛴다.
3. 이번 실행의 고정 scope와, 같은 scope에 속한 기존 관찰의 snapshot을 얻는다.
4. 명시된 주제에 해당하는 절 중 적용 조건을 만족하는 절만 남긴다.
5. 현재 관찰과 연결되는 좁은 계약을 우선하고 공통 필수 동반 절을 결합한다.
6. 중복을 제거하고 최대 4개 후보 묶음을 만든다. 전 조합 탐색을 하지 않는다.
7. 후보마다 항상 동일한 B의 원래 history에서 조립한다. 앞 후보가 추가한 본문을 누적하지 않는다.
8. 기존 addOptionalReferences 경계에서 전체 입력 증가분과 기존 watermarks를 확인한다.
9. 맞는 첫 후보를 사용하고, 전부 맞지 않으면 B를 그대로 사용한다.
10. 최종 후보의 modelInputId·포함 ID·추가 토큰·생략 이유를 기록한다.

중요한 현재 오류가 담긴 짧은 요약과 관련 계약 → 선택된 주제의 기본 절 → 일반 보충 설명 순으로 후보를 만든다. 각 후보에서 출처와 필수 동반 절은 함께 유지한다. 특정 엔진이라는 이유만으로 그 엔진 문서 전체를 우선하지 않는다.

1차 자동 선택 신호는 diagnostic_present, diagnostic_recurred, observed_source_changed, source_body_unavailable, compilation_pending, operation_outcome_unknown으로 제한한다. 신호는 위에서 정의한 원본 상태·범위·동일 대상의 진단 비교에서만 생성한다. assistant 추론문·파일명·오류 메시지의 일반 키워드로 “타이머 설계 변경”이나 “서버 권한 구조”를 추정하지 않는다. 이런 주제는 명시된 mode와 문서 metadata가 담당한다.

적용 가능한 묶음은 (확인된 신호와 일치, 명시 주제의 기본 절, 보충 절) 순으로 정렬하고 동률은 catalog priority와 절 ID로 결정한다. 임의 점수 학습·랜덤 순환·추가 모델 분류는 없다. 네 후보는 아래 순서로 고정하며 중복 후보는 제거한다.

| 후보 | 포함 내용 |
| --- | --- |
| C1 | 제한 내의 현재 관찰 자료 + 최우선 계약 묶음 + 최대 두 보충 묶음, 전체 절 수 한도 준수 |
| C2 | 최신 관련 진단 한 그룹과 필수 출처/scope + 최우선 계약 묶음 |
| C3 | 최우선 계약 묶음만 제공 |
| C4 | 공통 최소 절만 제공 |

관련 진단이 없으면 C2의 data는 비우고, 최우선 주제 묶음이 없으면 core로 수렴시킨다. 진단 항목을 빼는 경우 부분 범위 표시는 유지한다. C1~C4는 우선순위가 낮은 완결된 항목을 생략하는 후보이며, 문장·JSON·동반 계약을 중간에서 자르는 후보가 아니다.

### 6.2 자원 한도 초기안

아래 값은 품질이 검증된 최적값이 아니라 구현의 유한성을 위한 시작값이다. 한 곳의 상수로 정의하고 테스트/telemetry가 같은 값을 참조한다.

| 항목 | 초기 한도 |
| --- | --- |
| 이번 기능이 더하는 총 입력 | 기존 designGuidanceMaxTokens, 기본 2048 |
| 추가 전체 후보 조립 | 라운드당 최대 4회 |
| 선택 절 | 동반 절 포함 최대 6개 |
| 관찰 후보 | 실행 내 최근 관련 완료 요청/결과 최대 16쌍 |
| 표시할 진단 그룹 | 최대 3개, 구조화 항목 최대 12개 |
| 표시할 프로젝트 메타데이터/근거 항목 | 최대 8개 |

2048은 절 본문·wrapper·출처·새 관찰 요약을 합친 실제 입력 증가분에 적용한다. 기존 note 프로토콜의 입력은 기존 예산 체계에서 이미 계산되며 복사하여 다시 주입하지 않는다. 새로운 별도 공유 버짓·GPU 최소 context 값을 만들지 않는다.

4회 후보 조립은 tokenizer 호출 4회라는 뜻이 아니다. 기존 조립기의 metadata 재조립·정확 측정 비용까지 telemetry로 관찰한다. 길이 추정은 후보 정렬 보조에만 쓰고 승인 근거로 쓰지 않는다.

출력 예약·도구 결과 여유·현재 사용자 요구·근거 본문을 선택 자료 때문에 축소하지 않는다. baseline이 이미 기존 low-water 조건을 만족하지 못하면 기존 복구 정책을 따른다. 참고 기능이 추가 복구나 semantic handoff를 호출하지 않는다.

### 6.3 반복·중복·기아 처리

- 중복 키는 절 ID+revision이며, 같은 ID의 충돌 revision은 현재 번들 버전을 선택하고 충돌을 기록한다.
- 같은 입력 안에서 중복 제공하지 않는다. 이전 라운드에 전달했다는 이유만으로 이번 입력에서 생략하지 않는다.
- 반대로 역사에 원문이 이미 존재하는지는 내부 ID와 실제 본문 지문으로 확인한다. 문서 제목 언급만으로 이미 제공했다고 간주하지 않는다.
- 새 진단이 사라진 상태를 관찰하면 그 진단용 강조는 내린다. 시간이 지났다는 이유만으로 해결됐다고 보지 않는다.
- 계속 같은 자료가 공간을 차지하면 선택 이유와 omittedIds로 관찰한다. 무작위 순환·매 라운드 모든 절 교체를 하지 않는다.

## 7. 관찰 요약과 오류 피드백

### 7.1 원본과 파생 값

EvidenceManager의 읽기 전용 관찰 snapshot에서 내부 표현을 만든다. 원본 결과의 공개 MCP 스키마를 전부 하나로 바꾸지 않는다. 엔진 adapter가 이미 가진 결과를 명시적 provider와 도구 capability에 따라 변환한다. 도구 이름만 같은 외부 provider의 응답은 같은 계약으로 처리하지 않는다.

| 내부 정보 | 의미와 미확인 처리 |
| --- | --- |
| origin | provider, tool, 실행ID+고유 toolCallId. 중복·매칭 실패 ID는 관계 추론에서 제외 |
| scope | 확인된 project/engine 및 필요한 세션 식별. 불일치하면 활성 자료에 포함하지 않음 |
| operationKind | Unreal build / Unity script compilation / tool transport 등의 확인된 종류 |
| status | accepted, pending, completed, failed, canceled, outcome_unknown을 원본 의미대로 유지 |
| diagnostics | 원본 위치·코드·메시지·severity·해당 compilation/target. 파싱 불가 원문도 보존 |
| coverage | 전체/부분/미확인, 생략 수, 구독 후 이벤트 범위, truncation |
| sourceState | 읽은 파일 해시·관찰한 버전. 전체 빌드 입력의 동일성을 뜻하지 않음 |
| refs | 실제 반환 결과 또는 기존 archive ref. 본문 가용 여부를 별도 표시 |

빈 결과를 성공으로 승격하지 않는다. 구현되지 않은 종류는 unsupported로 선택에서 제외하고 기존 원본 도구 결과는 계속 전달한다.

전달 상태와 작업 결과는 내부에서도 두 축으로 둔다. delivery는 returned/transport_error/canceled/unknown이고 operationStatus는 accepted/pending/completed/failed/canceled/outcome_unknown/unknown이다. Unreal의 실패 빌드 결과가 정상 반환되면 returned+failed, Unity compile 수락은 returned+accepted, RPC timeout 뒤 실행 여부가 불명확하면 transport_error+outcome_unknown이다. 원본이 둘 중 하나를 증명하지 않으면 unknown을 사용한다. 뒤늦은 취소로 이미 반환된 결과를 취소 결과로 덮어쓰지 않는다.

### 7.2 연결할 수 있는 사실과 연결할 수 없는 사실

다음은 근거가 있을 때 전달할 수 있다.

- 같은 대상/구성에서 같은 진단 코드·위치·메시지가 다시 관찰됨.
- 두 관찰 사이에 특정 파일의 수정 결과가 반환됨.
- 진단이 가리킨 파일/범위를 현재 입력에서 읽을 수 있음 또는 본문이 생략됨.
- 검색 도구가 실패함, 제한된 검색에서 결과가 없음, 검색 범위가 일부임.

다음은 자동으로 확정하지 않는다.

- 사이에 있었던 패치 때문에 오류가 생겼거나 해결됐다는 인과관계.
- 같은 진단이면 같은 근본 원인이라는 결론.
- 일부 파일 해시가 같으면 모든 헤더·빌드 옵션·생성 코드도 같다는 결론.
- 다음 빌드의 첫 오류가 바뀌었으면 이전 오류가 해결됐다는 결론.
- 심볼 검색에 없으면 엔진에 그 API가 없다는 결론.

출력 예시는 “관찰 A와 B에 동일 진단이 있음. 사이에 X.cpp 수정 결과가 반환됨. 원인 관계는 미확인”처럼 사실에 한정한다. 참고 절은 정의와 사용 표현식을 확인하도록 안내하지만 requiredNextTool을 생성하지 않는다.

### 7.3 수집 위치와 순서

기존 prediction-loop의 captureReturned 이후 완료된 요청/결과를 처리한다. 새로운 모델 callback listener·전역 이벤트 버스를 만들지 않는다. 실패한 prediction에서 이미 반환된 peer 결과도 기존 반환 계약에 따라 보존하고, 미반환 호출을 성공·실패로 보충 생성하지 않는다.

captureReturned의 기존 archive 대상은 observation-only 판정에 연결되어 있다. 진단 요약에 빌드/수정 결과가 필요하다고 이 보관 필터를 넓히지 않는다. 같은 EvidenceManager 안의 별도 좁은 ingest/snapshot 경로로 실제 완료 결과에서 허용 필드만 읽고, 원본 보관·쓰기 receipt·입력 노출 정책은 유지한다. 연결할 요청/결과는 실제 캡처한 쌍에 한정한다.

모델이 제안한 arguments와 guard/binding 이후 실제 실행한 대상이 다를 수 있다. 반환된 canonical scope/실행 metadata를 우선하며, 제안 arguments만으로 다른 프로젝트의 결과를 현재 프로젝트에 귀속시키지 않는다. 동일 toolCallId를 두 provider가 공유하는 등 고유성을 증명할 수 없으면 해당 관계 요약을 생략한다.

정규화에는 기존 tool result decoder를 사용하고 크기 제한을 적용한다. 거대한 raw Git/파일 결과를 새 분석 때문에 치환하거나 최초 소비자용 원문 보존을 없애지 않는다. no_new_information·repeatReceipt·archive projection은 새 원문 관찰로 인정하지 않는다. 이를 표시하는 경우 참조/과거 관찰이라는 의미를 유지한다.

동일 배치의 병렬 결과는 도착 순서만으로 인과 순서를 정하지 않는다. SDK 요청 순서·완료 ID·원본 run/compilation 정보를 함께 사용한다. 여러 개의 빌드/변경이 겹치면 관계는 ambiguous로 남긴다.

실행 내 파생 인덱스는 EvidenceManager의 수명에 귀속시키고 상한을 둔다. 영구 저장은 기존 archive의 보관 대상에 대해서만 기존 경로가 담당한다. 빌드·수정 결과가 보관되었다고 가정하지 않으며, 원본이 보관되지 않았거나 이력에서 사라졌으면 재조회 가능한 archive ref를 표시하지 않는다. 새 영구 진단 DB를 만들지 않는다. 과거 원문이 생략되어 확인할 수 없으면 이전 시도를 지어내지 않고 현재 관찰만 제공한다.

## 8. 엔진별 결과 의미

### 8.1 Unreal

기존 direct-build-response.js의 진단 추출·build-proof 결과를 재사용한다. 필요한 구조화 항목이 부족하면 생산자에서 가산 필드로 보강하고 기존 diagnostics 문자열 배열·summary·proof 의미는 유지한다.

정규화한 진단 배열과 기존 문자열 배열은 하나의 파싱 결과에서 생성한다. 컴팩터에 또 다른 MSVC/UHT/Clang 로그 파서를 만들지 않는다. 모르는 형식은 원문+unknown으로 반환한다.

- 프로젝트, 엔진 association/root, target/platform/configuration을 구분한다.
- 요청된 association과 실제 엔진 버전 확인을 구분한다. 5.7 문자열만으로 정확한 패치 버전을 가정하지 않는다.
- up-to-date 0 actions는 해당 실행의 결과로 표시한다. 변경 파일이 실제 컴파일됐다는 증거로 확대하지 않는다.
- diagnostics 상한에 도달하면 부분 결과임을 표현한다. 첫 오류와 전체 로그 경로를 유지한다.
- read/search/read_symbol 같은 기존 반환값에 없는 정보는 자동 추측하지 않는다.
- static_validate_project의 advisory/blocksBuild false 계약과 도구 승인 경로를 변경하지 않는다.

### 8.2 Unity

기존 Bridge/Observations가 가진 compiler event와 session 정보를 사용한다. unity_editor(action=compile)의 accepted는 요청 수락이며 Player 빌드나 컴파일 완료가 아니다.

- editorSessionId, domainGeneration, compilationId, operationId는 각각의 원래 의미를 유지한다.
- compilationIdAtRequest를 새 컴파일 ID로 해석하지 않는다.
- 로그 행의 compilationId와 응답의 현재 compilationId/outcome이 서로 다를 수 있으므로 행별 식별을 우선한다.
- 구독 후 이벤트만 수집했다는 범위, droppedCount, 행 truncation, 페이지 누락을 표시한다.
- completed_without_observed_errors를 전체 Player 빌드 성공으로 표시하지 않는다.
- sourceAssemblyVerification=unknown은 그대로 유지한다.
- domain reload 뒤 이전 이벤트는 과거 관찰로만 사용한다. 현재 객체 handle·컴파일 상태로 재사용하지 않는다.
- RPC timeout의 outcome_unknown은 실패 확정·자동 재실행 근거가 아니다. 기존 operation 조회 계약을 유지한다.

초기 구현은 기존 관찰로 표현할 수 있는 범위만 제공한다. 정확한 컴파일 시작/종료 세대가 꼭 필요한데 현재 응답으로 증명할 수 없다면 미확인으로 표시한다. 별도 Bridge 확장이 필요할 때만 해당 단계에서 가산 필드와 호환 테스트를 함께 구현한다.

### 8.3 엔진·패키지 적용성

엔진 확인과 프로젝트 식별, 패키지 설치와 실제 사용을 구분한다. Unity의 manifest 선언, lock의 해석된 버전, 코드에서 사용하는 API는 서로 다른 근거다. 여러 네트워크 패키지가 설치돼 있으면 하나를 활성 프레임워크로 단정하지 않는다.

자료 선택을 위해 플러그인이 새로 프로젝트 파일을 몰래 읽지 않는다. 정상 도구 흐름에서 얻은 완전한 설정 관찰이 있을 때만 좁은 메타데이터 변환을 한다. 불완전한 JSON 조각·누락된 lock·git 과거 버전은 현재 패키지 확정 근거로 사용하지 않는다.

버전이 없거나 범위를 판정할 수 없으면 버전 무관 공통 절만 사용한다. 패키지별 규칙은 자료에 근거와 적용 범위가 준비된 것부터 추가한다.

## 9. 프로젝트 사실과 변경 계약

### 9.1 자동으로 표현하는 범위

1차 프로젝트 자료는 이미 확인된 project/engine/package 식별, 실제 읽은 파일·범위·해시, 현재 진단과 관련 근거의 가용성이다. 이것은 자연어로 전체 아키텍처를 자동 추출하는 기능이 아니다.

예를 들어 PlayerState가 체력의 정본이라는 문장을 클래스명·주석·파일명만 보고 자동 생성하지 않는다. 다음 세 종류를 구분한다.

| 종류 | 예시 | 취급 |
| --- | --- | --- |
| 원본 관찰 | 반환된 함수 본문·선언·호출부 | 실제 출처와 확인한 버전·범위로 표현 |
| 명시된 요구·프로젝트 설명 | 사용자가 서버 권한 방식이라고 요구함, 프로젝트 문서가 소유자를 명시함 | 요구 또는 문서의 주장으로 표시. 구현 증거와 구분 |
| 모델 해석·제안 | 이 함수가 체력의 결정권자라는 해석, 새로운 전이 함수 제안 | 기존 assistant 판단/메모. 확정 사실로 승격하지 않음 |

프로젝트 현황 템플릿은 유지하되 항목을 전부 채워야 진행하는 계약을 만들지 않는다. 자동 읽기는 추가하지 않는다. 모델이 정상 도구로 해당 파일을 읽었을 때도 그 내용은 프로젝트가 작성한 설명이다. 소스·실행 관찰과 충돌하면 양쪽 근거를 보이고, 최신 날짜만으로 한쪽을 진실로 선택하지 않는다.

### 9.2 변경 계약을 표현하는 방법

큰 변경에서 목표·기존 담당자·보존할 의미·실패/중복·정리 책임을 짧게 검토하도록 해당 문서 절에 안내한다. 새로운 필수 계획 양식이나 도구 전행 조건을 만들지 않는다.

변경 계약은 우선 일반 답변의 짧은 설명 또는 기존 decisions/openQuestions에 담을 수 있다. 아직 확인하지 않은 담당자는 질문/가설로 남긴다. 결정이 바뀌면 기존 status/supersedes 계약을 사용한다. 필드명 해석으로 자동 상태 머신을 만들지 않는다.

기존 v1은 최종 답변이나 수신한 assistant 메시지의 선택적 footer를 처리한다. 따라서 이 계획은 모든 수정 전에 메모가 반드시 생성된다고 가정하지 않는다. 메모가 없거나 형식이 맞지 않아도 기존 작업은 진행한다. 추가 LLM 호출로 빈 항목을 채우지 않는다.

### 9.3 모델 판단의 최신성

refs가 실재한다는 사실과 그 판단이 아직 맞다는 사실은 다르다. 파일 읽기 근거가 확인된 뒤 관련 파일이 수정/삭제/충돌 관찰되면, 그 근거만으로 현재 구조를 설명하는 파생 요약은 제외한다.

1차 구현에서 note v1 필드나 저장 스키마는 바꾸지 않는다. 다음 정책으로 고정한다.

- reviewClaims는 기존 구조화 path/hash/refs 검증과 프로젝트 범위 검증을 재사용한다.
- 일반 decisions/rejectedHypotheses/openQuestions는 파일 수정만으로 자동 삭제하지 않는다. 앞으로 보존할 설계 결정까지 지울 수 있으므로 문자열 의미 분류기를 추가하지 않는다. 기존 objective/project/ref 검증과 명시적 status/supersedes를 유지한다.
- 대신 이 판단 배열을 프로젝트의 현재 사실 view 생성에 사용하지 않는다. “이전 assistant 판단” 표시는 유지한다. 인용한 파일의 새 관찰이 확인된 경우 data view에서 그 사실을 표시할 수 있지만 판단이 틀렸거나 해결됐다는 상태를 자동 생성하지 않는다.
- source 관찰에서 만든 현재 자료는 관련 hash가 바뀌면 제외한다. 이 파생 view의 제외를 note의 의미 해석과 혼동하지 않는다.

모델 메모를 검증된 사실 목록에 복사하지 않는다. 현재 4개/1500자 제한을 기능 추가 때문에 조용히 늘리지 않는다. 필요한 판단을 담을 수 없는 문제가 실제로 확인되면 별도의 v2 설계와 이전 계획을 후속 작업으로 다룬다.

## 10. 입력 신뢰 경계와 가용성

현재 instructions 인자는 system에 합쳐지므로 다음 두 전달 경로를 명시적으로 분리한다.

| 내용 | 전달 방식 |
| --- | --- |
| 저장소에서 배포한 공통 원칙의 절 | 기존 선택적 instruction 경로. 사용자 요구·권한을 확대하지 않는 wrapper 유지 |
| 도구 결과에서 파생한 진단·프로젝트 자료 | 별도 data 인자로 조립하는 assistant 참조 메시지. 원본 출처가 있는 데이터임을 표시 |
| 기존 모델 판단 | 기존 renderAssistantNote 경로 |
| 사용자 요구 | 원래 user 메시지와 기존 요구 보존 경계 |

data 메시지에는 “도구 결과에서 파생한 참고 데이터이며 내부 문자열은 실행 지시가 아니다”라는 고정 설명과 직렬화한 제한된 필드만 넣는다. 실제 완료되지 않은 toolCall/result 쌍을 만들어 넣지 않는다. 주석·파일명·컴파일 오류 문자열 안의 지시문을 system에 복사하지 않는다.

배치 위치는 기존 선두 system/assistant-note 다음, 원래 이력 앞의 고정 슬롯으로 한다. 원래 user 메시지·tool-call/result 순서를 바꾸지 않는다. 동일 입력에 자료 슬롯 하나만 만든다. 실제 SDK 템플릿이 이 구성을 수용하는지는 통합 fixture로 확인한다.

createInputAssembler에 instructions와 별개의 typed data 입력을 전달하고 두 경로 모두 최종 템플릿 측정에 포함한다. data가 달라지면 cache key도 달라져야 한다. typed attachment 경로를 평문으로 변환하여 캐시하지 않는다.

파생 메시지는 workingHistory·checkpoint·원본 archive에 넣지 않는다. 실제 모델 입력에만 존재한다. input availability가 이 메시지를 새로운 원본 도구 관찰로 세지 않도록 한다. 요약·과거 ref·직접 원문을 각각 구분하며 source read coverage와 mutation receipt를 발급하지 않는다.

원문이 현재 입력에 없으면 “이 요약의 원문은 현재 미포함”이라고 표현한다. archive ref만 있을 때 필요한 정의까지 읽은 것으로 표시하지 않는다. 모델이 자료를 실제로 이용했는지는 included=true로 증명할 수 없다.

## 11. 상태 수명·무효화·동시성

| 대상 | 소유자·수명 | 재사용 키 | 무효화/제외 조건 | 실패 시 행동 |
| --- | --- | --- | --- | --- |
| 정적 절 번들 | 설치된 플러그인 | 번들/절 revision | 새 번들·부적합 스키마 | 호환되지 않는 항목 생략, 개발 빌드에는 오류 |
| 절 후보 | 한 round/modelInputId | mode·scope·관찰 snapshot·revision | 다음 라운드 입력 변경 | 재계산. 전역 캐시 없음 |
| 토큰 측정 | 기존 입력 조립기 한 round/model handle | 전체 메시지·도구 schema·출력 예약 | 모델/템플릿/본문 변경 | 정확 측정 불가 시 선택 자료 생략 |
| 관찰 파생 인덱스 | 실행 내 EvidenceManager | provider·실행ID·고유 호출ID·원본 식별 | 실행 종료·scope 충돌·상한 초과 | 원본은 기존 경로 보존, 요약만 생략 |
| 파일 관련 설명 | 원래 관찰+기존 reconciliation | canonical project/path·실제 파일 hash·범위 | 수정/삭제/충돌 관찰 | 현재 설명에서 제외, 과거 기록만 유지 |
| Unity 런타임 자료 | Bridge가 제공한 session/generation | project+editorSession+generation, 필요 시 play/compile ID | Domain/Play/Editor 전환 | 현 상태로 재사용 금지 |
| 명시적 요구 | 기존 사용자 이력/목표 경계 | 해당 요구의 실제 메시지 | 사용자 수정·목표 교체 | 새 요구 우선, 원문 보존 |
| 모델 판단 | 기존 note store/reconcile | objective·project·검증된 refs | supersedes·scope/관련 근거 변경 | 제외 또는 미확인. 사실로 승격 금지 |

현재 파일이 외부 편집으로 바뀌었지만 아직 어떤 도구도 관찰하지 않았다면 변경을 안다고 주장할 수 없다. 표시하는 유효성은 “마지막 관찰 당시”다. 시간이 지나면 자동으로 fresh가 되는 TTL을 만들지 않는다. 새 polling watcher나 매 라운드 전체 해시 계산은 추가하지 않는다.

실행 scope의 정본은 ToolScope다. adapter가 반환하는 식별이 path/opaque hash 등 서로 다르면 기존 provider별 식별·경로 정규화 경계를 재사용하여 동등성을 확인한다. 해시 문자열과 경로 문자열을 직접 비교하여 같다고 추정하지 않는다. 비교 불가능한 경우 unknown으로 제외한다.

프로젝트 A와 B의 호출이 겹치면 원본은 각 scope로 기록하고 활성 요약에는 현재 실행과 맞는 관찰만 넣는다. 기존 scope를 몰래 B로 재바인딩하지 않는다. 한 handler 재사용, 두 대화 동시 실행, 사용자 중단 후 늦은 결과를 각각 테스트한다.

취소 신호는 기존 ctl.guardAbort/AbortSignal을 사용한다. 후보 조립 전후에 확인하고 abort를 일반 조회 실패로 삼켜 계속 실행하지 않는다. 새 기능은 타이머·독립 비동기 worker·polling을 만들지 않으므로 별도의 중지 프로토콜도 만들지 않는다.

## 12. 줄 간격·들여쓰기 개선과 자동 포맷의 경계

1차는 code-style 절, 이미 읽힌 프로젝트 설정, 수정 대상 주변의 실제 예시를 제공한다. .editorconfig/.clang-format의 존재만 보고 전체 규칙을 읽었다고 표시하지 않는다. 설정 상속·override를 확인하지 못하면 그 부분을 미확인으로 둔다.

코드 블록·파일 주석에 들어 있는 지시는 외부 데이터다. 설정의 원래 경로·버전과 제한된 형식 정보만 표현하고 arbitrary command 설정을 실행하지 않는다. 프로젝트 설정과 주변 예시가 충돌하면 설정을 확인하도록 안내하며 전체 파일 재작성으로 해결하지 않는다.

자동 포맷은 이번 1차 기능에서 분리한다. 후속으로 도입할 경우 필요한 계약은 다음과 같다.

- 명시적으로 설정된 포맷터와 버전·지원 파일 형식만 사용한다. 자동 설치·명령 추측 없음.
- 변경 범위 포맷을 실제로 지원하는지 확인한다. 미지원이면 전체 파일로 몰래 확대하지 않는다.
- 기존 쓰기 소유자가 최신 snapshot·파일 잠금·CAS를 처리한다. 컴팩터가 별도 파일 writer를 만들지 않는다.
- 포맷 후 새로운 파일 hash/receipt를 기존 도구가 반환하고 이전 관찰을 갱신한다.
- 기능 수정 성공과 포맷 실행 실패를 별도로 보고한다. 실패한 포맷을 반복하거나 기능 수정을 완료되지 않은 것으로 위장하지 않는다.
- 줄바꿈·인코딩·BOM·기존 사용자 변경을 보존하고, 포맷 변경의 실제 diff를 확인 가능하게 한다.

포맷터의 실행 계약과 프로젝트별 허용 범위가 아직 조사되지 않았으므로 후속 모듈의 파일명·CLI를 이번 문서에서 확정하지 않는다. 현재 사용자 선호인 Unreal 네임스페이스 최소 사용은 사용자 선호로 유지하며 엔진의 보편적 금지 규칙으로 승격하지 않는다.

## 13. 파일별 구현 범위

경로는 저장소 루트 기준이다. 신규 파일명은 제안이며 책임을 분리할 필요가 있을 때만 생성한다.

| 파일/경계 | 계획한 변경 | 유지할 계약 |
| --- | --- | --- |
| docs/model-guidance/*.md / catalog.json | 절 ID·적용 메타데이터·관련 기준 정리 | 본문 정본, 프로젝트 사례 분리 |
| plugin scripts/build-design-guidance.cjs | v2 절 추출/동반 절/해시 검증, v1 catalogue의 명시적 정규화 읽기 | 생성 결과 결정성·check 명령 |
| plugin src/generated-design-guidance.ts | 생성기로만 갱신 | 수동 편집 금지 |
| plugin src/design-guidance.ts | 기존 documents 경로 유지, focused 후보 순수 선택 | 파일·도구·LLM I/O 없음 |
| plugin src/reference-context.ts (신규 후보) | 내부 타입·순수 관찰 변환 및 제한된 snapshot 표현 | 실제 결과 의미 보존, unknown 유지 |
| plugin src/reference-rendering.ts (신규 후보) | 절과 data 표현 분리·출처/부분 상태 표시 | 외부 내용을 system에 승격하지 않음 |
| plugin src/evidence-manager.ts | 실행 수명 내 관찰 snapshot 읽기·제한된 인덱스 | 원본 storage/현재 노출 책임 재사용 |
| plugin src/evidence-identity.js / continuity-file-observations.js | 필요 시 기존 식별·최신성 helper 재사용/공개 | 두 번째 canonicalization 규칙 금지 |
| plugin src/round-input.ts | 매 라운드 focused 후보 공급, 기존 기준 입력에서 admission | 실행 phase·도구 승인 소유 금지 |
| plugin src/context-manager.ts | typed data 슬롯, 후보 전체 정확 측정, 기존 함수의 최소 확장 | 기존 캐시·가용성·watermark |
| plugin src/prediction-loop.ts | 기존 반환 경계에서 관찰 전달, 설정/선택 정보 연결 | ToolGuard/RecoveryCoordinator의 정책 불변 |
| plugin src/execution-contracts.ts / execution-config.ts / direct-config.ts | delivery 설정·필요한 내부 타입 | 기존 저장 키·Off 기본값·allowance |
| plugin src/continuity-model-notes.js | 기존 reviewClaims/범위 검증 재사용, 일반 판단 자동 의미 분류 금지 | v1 keys·크기·refs·판단/사실 구분 |
| Unreal src/direct-build-response.js / 기존 build proof 경계 | 진단 구조/부분 범위가 부족할 때 가산 정보 | 문자열 진단·summary·실행 결과·Direct 응답 계약 |
| Unity src/server.js / bridge-client.js | 필요한 경우 기존 결과의 scope/context 전달 보강 | 기존 permissions/operation/session 계약 |
| unity-editor-bridge/Editor/Observations.cs | adapter만으로 증명할 수 없는 범위 metadata가 실제 필요할 때만 가산 필드 | 구독 이벤트 범위·기존 compilation 소유권 |
| scripts/build_integrated_package.py / tests/test_integrated_package.py | 새 필수 파일과 생성 입력/테스트 포함 | 개인 프로젝트 현황·로그 배포 제외 |
| plugin package.json 및 관련 테스트 진입점 | 새 테스트가 실제 실행되는지 명시적으로 연결 | 별도 파일 테스트를 기존 npm test가 자동 탐색한다고 가정하지 않음 |

plugin은 lmstudio-context-compactor-plugin을, Unreal은 lmstudio-unreal-agent-mcp를, Unity는 lmstudio-unity-mcp를 뜻한다. 실제 구현 diff는 단계별로 위 표의 필요한 파일만 포함한다. BudgetBroker의 정책·ToolGuard 권한·도구 schema를 참고 기능 때문에 넓히지 않는다.

## 14. 단계별 작업과 종료 기준

### P0 — 계약·기준 입력 고정

- 기존 Off/documents 입력·승인·실패 처리 fixture를 확인한다.
- 테스트 실행 목록, 설치 패키지 필수 파일, schema 경계를 확인한다.
- 실제 모델을 부르지 않는 source/SDK fixture로 회귀 기준을 확보한다.
- 종료: 기존 behavior의 비교 지점과 새 데이터의 신뢰 경계가 테스트에 표현됨.

### P1 — 정본 절과 선택만 구현

- catalogue v2·생성기·동반 절·revision을 구현한다.
- delivery 설정과 focused의 명시 주제별 선택을 구현한다.
- 이 단계에서는 관찰 기반 강조·프로젝트 요약을 아직 연결하지 않는다.
- 종료: documents는 기존 동작을 유지하고 focused가 완결된 절을 예산 안에 전달함.

### P2 — 엔진 관찰 정규화와 입력 data 경계

- Unreal/Unity fixture에서 status·진단·범위·scope를 읽는 순수 변환을 구현한다.
- 필요한 생산자 가산 필드는 해당 엔진의 테스트와 함께 별도 변경으로 만든다.
- typed data 슬롯·정확 측정·현재 입력 가용성 제외 규칙을 구현한다.
- 종료: 외부 문자열이 system에 들어가지 않고, accepted/unknown/partial이 성공으로 바뀌지 않음.

### P3 — 같은 실행의 관찰로 관련 절과 피드백 선택

- captureReturned 이후 기존 소유자에 반환 관찰을 전달한다.
- 다음 라운드가 snapshot을 사용해 관련 절을 선택하도록 연결한다.
- 반복된 진단·수정의 시간적 관계만 제시하고 인과·필수 다음 도구를 만들지 않는다.
- 종료: 실제 prediction handler fixture에서 오류 후 다음 입력이 달라지고, 도구 승인·재시도·최종화 정책은 동일함.

### P4 — 프로젝트 자료·메모 최신성·표현 기준

- 이미 읽힌 엔진/패키지/파일 관찰의 작은 요약을 추가한다.
- 소스와 프로젝트 문서의 주장·모델 해석을 구분한다.
- 기존 reviewClaims의 hash 검증과 note 범위 검증을 재사용한다. 일반 판단은 현재 사실 view의 입력으로 사용하지 않는다.
- code-style 자료와 변경 계약 안내를 적용한다.
- 종료: 프로젝트 전환·소스 수정·Domain reload·본문 생략 상황에서 오래된 자료가 현재 사실이 되지 않음.

### P5 — 통합 패키지·회귀·사용 설명

- 정본과 생성물 일치, 패키지 필수 파일·설치 후 경로 독립성을 확인한다.
- 단위 fixture를 넘어 실제 handler의 end-to-end 입력·취소·권한 테스트를 수행한다.
- 새 설정의 기본값/되돌리기/미포함 이유를 문서화한다.
- 종료: 아래 계약 테스트와 저장소 필수 CI 통과. 이 시점의 결론은 구현 완료임.

### P6 — 실제 모델 행동 평가와 기본값 결정

- 사용자가 재개할 때 수행한다. 계획 수립 중 자동 실행하지 않는다.
- Off / 기존 documents / focused를 같은 조건에서 비교한다.
- 문서 선택 효과와 오류 데이터 효과를 분리하는 추가 실험은 필요할 때만 수행한다.
- 종료: 개선·퇴행·비용을 사례별로 보고하고, 일반 품질을 보장한다는 표현을 사용하지 않음.

순서는 P0 → P1 → P2 → P3 → P4 → P5이고, P6는 사용 가능할 때 별도로 진행한다. P2의 Unreal/Unity 생산자 보강은 서로 독립된 작은 변경으로 검토 가능하다. 각 단계가 통과한 뒤 다음 단계로 넘어가며, 새 일반 관리자부터 만드는 순서는 취하지 않는다.

권장 변경 단위는 문서/선택, 관찰/data 경계, 라운드 통합, 프로젝트 최신성, 패키징/회귀의 다섯 묶음이다. 각 묶음은 독립적으로 review/revert 가능하게 한다. Unity Bridge 변경이 필요하면 adapter 호환성 확인과 함께 별도 묶음으로 분리한다.

## 15. 회귀 테스트 목록

테스트는 실제 계약의 반례를 겨냥한다. 단순 문자열 존재만 확인하는 테스트로 상태·권한·가용성 보존을 대신하지 않는다.

| ID | 영역 | 입력/반례 | 기대 결과 |
| --- | --- | --- | --- |
| T01 | 설정 | Off, Observe only, allowance 0 | 추가 후보·측정·자료 분석 없음 |
| T02 | 호환 | delivery 키 누락/잘못된 값, 고정 정본 fixture | documents의 본문·선택 순서·prefix 후보·revision이 기준과 같음 |
| T03 | 정본 | 본문 또는 applicability만 수정 | 해당 revision과 생성 결과 갱신 |
| T04 | 절 | 중복/누락/중첩/코드블록 속 표식 | 잘못된 정본 거부, 예시 표식은 내용으로 유지 |
| T05 | 동반 절 | 순환/없는 ID/엔진 충돌/주제 이탈/폐쇄 6개 초과 | 정본 오류 검출 또는 묶음 전체 생략, 불완전 주입 없음 |
| T06 | 중복 | 같은 절 반복·다른 revision | 한 입력에서 정확히 한 버전, 충돌 관찰 가능 |
| T07 | 적용 | available_tools/ambiguous만 엔진 근거 | 엔진 전용 규칙을 확정하지 않음 |
| T08 | 패키지 | 선언만 있음/lock 일부/여러 네트코드 | 미확인 조건에 특정 패키지 규칙 강제 없음 |
| T09 | 주제 | mode=core 및 명시 주제와 다른 오류 | 주제를 자동 변경하지 않음 |
| T10 | 동적 선택 | 완료된 오류 결과가 다음 라운드에 도착 | 관련 후보 갱신, 기존 scope 고정 |
| T11 | 예산 | wrapper·출처·data까지 포함하면 초과 | 완결된 하위 후보 또는 기준 입력 사용 |
| T12 | 예산 | 모든 후보 실패, 탈락 후보에 data 존재 | instruction과 data 모두 없는 B로 복귀, 원문·근거·예약 유지 |
| T13 | 정확성 | tokenizer 실패/추정 context만 있음 | 선택 자료 생략. 실행 적합성은 기존 정책 |
| T14 | 비용 | 후보가 많음 | 최대 4 후보 조립, 조합 폭증 없음 |
| T15 | 캐시 | 같은 라운드 data 변경·model/template 변경 | 잘못된 이전 측정 재사용 없음 |
| T16 | 신뢰 | 파일 주석/진단에 system 지시문 | 외부 문자열이 system·권한 설정으로 승격되지 않음 |
| T17 | 가용성 | 요약만 있고 원문 archive에만 있음 | fresh read/전체 body coverage로 기록되지 않음 |
| T18 | 지속성 | 반복 입력·압축·다음 대화 | 파생 자료가 history/checkpoint에 중복 누적되지 않음 |
| T19 | 요청 연결 | 중복 toolCallId·없는 요청·미반환 결과 | 새 성공 결과/인과관계 생성 없음 |
| T20 | 병렬 | 같은 배치에 두 빌드·수정 결과 역순 도착 | 도착 순서로 전후 인과 확정 없음 |
| T21 | 실패 | parse 불가/모르는 provider·schema | 원래 결과 보존, 파생 기능만 생략 |
| T22 | 오류 | 같은 코드지만 대상/설정이 다름 | 같은 실행 실패로 병합하지 않음 |
| T23 | 오류 | 첫 오류만 달라짐·진단 일부 잘림 | 이전 오류 해결로 확정하지 않음 |
| T24 | Unreal | up-to-date 0 actions | 실제 컴파일 실행과 구분 |
| T25 | Unreal | 진단 상한·MSVC/UHT/Clang 미지원 줄 | 부분 범위/원문/unknown 유지 |
| T26 | Unity | compile accepted | completed/build succeeded로 바꾸지 않음 |
| T27 | Unity | 과거 compilation 행+현재 outcome | 서로 다른 컴파일 결과를 연결하지 않음 |
| T28 | Unity | droppedCount/truncation/구독 중간 시작 | 전체 오류 없음으로 단정하지 않음 |
| T29 | Unity | Domain reload 후 저장된 compiler events | 과거 출처 유지, 현재 객체·권한으로 재사용 없음 |
| T30 | Unity | RPC timeout/outcome_unknown | 자동 실행 재시도 추가 없음 |
| T31 | 수명 | 프로젝트 A 후 B, 같은 handler 재사용 | A의 프로젝트 사실·패키지 자료가 B로 누출되지 않음 |
| T32 | 수명 | 두 채팅 동시 실행·늦은 A 결과 | 실행별 분리, 잘못된 scope로 재귀속 없음 |
| T33 | 최신성 | 같은 path 다른 hash/삭제/충돌 | 관련 파생 현재 설명 제외 |
| T34 | 최신성 | 외부 변경 아직 미관찰 | 현재 확인했다고 과장하지 않고 마지막 관찰로 표시 |
| T35 | 메모 | v1 파일·잘못된 footer·없는 refs | 기존 파서 동작, 새 필드 필요 없이 정상 작업 |
| T36 | 메모 | 근거 변경 후 reviewClaim과 설계 decision 동시 존재 | stale reviewClaim 제외, decision의 의미 자동 분류/삭제 없음, 현재 사실로 승격 없음 |
| T37 | 사용자 | 요청 변경·첨부 기준 변경 | 기존 objective/criterion 경계 우선 |
| T38 | 취소 | 후보 조립 중·관찰 처리 직후 중단 | abort 전파, 추가 prediction/worker 없음 |
| T39 | 제어 | 기존 허용/거절/Allow all 도구 호출 | 참고 자료로 승인 상태 변경 없음 |
| T40 | 제어 | staticValidate findings 있음/미호출 | 새 필수 검사·build gate 없음 |
| T41 | 제어 | recovery/finalizing/출력 한도 | 새 복구 루프·추가 토큰 예약 없음, 정해진 생략 정책 |
| T42 | 표현 | 설정과 주변 예시 충돌·불완전 설정 | 임의 전체 재포맷/설정 실행 없음 |
| T43 | 배포 | 통합 zip만으로 실행/재생성 | 새 모듈 누락·개인 절대 경로 의존 없음 |
| T44 | 배포 | 이전 설정·이전 note·이전 도구 결과·v2 번들 | documents 호환 경로/선택 순서 유지, focused 실패는 B 반환 |
| T45 | 관찰 | included=true만 존재 | 모델 활용/품질 개선으로 보고하지 않음 |

정상 focused 입력이 빈 자료가 아닌 실제 본문·출처·관찰 요약을 포함하는 긍정 사례도 함께 둔다. 방어 테스트만 통과하면서 기능이 항상 생략되는 구현을 허용하지 않는다.

### 테스트 실행 경계

- 플러그인: 기존 guidance/input-availability/prediction-loop/tool-scope/continuity/working-context 테스트를 확장하고 새 파일은 npm test 또는 CI 명령에 명시적으로 포함한다.
- Unreal: direct-build-response/direct-response 및 관련 실제 진단 생산자 테스트를 실행한다.
- Unity: adapter runtime/bridge fixture와 관련 Bridge smoke를 사용한다. Node 대역 테스트만으로 Editor 실행을 확인했다고 쓰지 않는다.
- 패키지: guidance:check, TypeScript build, 통합 package 검사와 저장소 CI의 필수 항목을 실행한다.
- 실제 Editor·게임 네트워크 실행은 관련 단계에서만 수행하며, 미실행이면 결과에 남긴다.

## 16. 실제 모델 평가 계획

초기 평가 세트는 12개 서로 다른 사례를 마련한다. Unreal 4개, Unity 4개, 엔진 독립 4개로 구성하고, 기존에 실패했던 게임 코드와 별개 이름·구조의 사례를 포함한다. 사례 정답과 평가는 모델 입력에 넣지 않는다.

| 영역 | 예시 평가 과제 |
| --- | --- |
| Unreal | 헤더/매크로 근거 확인, Trace 의미 보존, 동시 리스폰/부분 실패, 원격 Client 권한 경로 |
| Unity | 2D/3D API 의미, 비활성화와 비동기 종료, 확인된 네트코드 패키지 계약, compile accepted/완료 구분 |
| 공통 | 설정 선언과 실제 초기화 연결, 기존 소유자 재사용, 취소/중복 작업, 포맷 일관성 |

같은 모델·양자화·실제 로드 context·시스템 프롬프트·추론 설정·도구 집합·소스 snapshot·출력 상한을 기록한다. Off/documents/focused를 독립 대화로 실행하고, 이전 조건의 대화나 결과가 다음 조건에 섞이지 않게 한다. 순서 효과를 줄이도록 사례별 조건 순서를 균형 있게 바꾼다.

각 조건 2회 반복을 시작안으로 하며 첫 3개 사례로 평가 절차 오류부터 확인한다. 사용자가 실제 평가를 원할 때 자원 상황에 맞춰 실행한다. temperature=0 또는 같은 seed라도 완전한 결정성을 가정하지 않는다.

평가 축은 정확한 근거 확인, 의미 보존, 책임/수명 처리, 불필요한 추상화, 형식, 완료 주장, 도구/빌드 반복, 소요 시간·입력/출력 토큰이다. 단순 키워드 출현 점수로 SSOT/SOLID 준수를 대신하지 않는다. 조건을 숨긴 답변을 근거와 함께 검토하고 유의미한 실패를 사례별로 기록한다.

안전한 출시의 최소 조건은 공급 계약 회귀 없음, 새 승인/검사 교착 없음, 관찰 사실의 잘못된 승격 없음이다. 행동 개선은 작업 성공률·중대한 의미 변경·비용을 함께 보고 판단한다. 소수 표본의 평균 상승만으로 기본값을 바꾸지 않는다. 결과가 섞이면 documents 기본값을 유지하고 효과가 없는 절/요약을 줄인다.

지금은 평가 설계만 수행한다. 실제 모델 실행, 플러그인 설치, GUI 값 변경은 이 문서 작성 완료와 별개다.

## 17. Telemetry·배포·되돌리기

기존 design_guidance_input 이벤트를 가산 확장한다. executionId/modelInputId/roundIndex, mode/delivery, bundle revision, selected/omitted IDs, 선택 이유, 추가 토큰, 조립 시도 수, 원본/파생 가용성 정도만 기록한다.

생략 이유는 disabled, incompatible_scope, applicability_unknown, dependency_limit, missing_source, unsupported_result, budget_omitted, measurement_unavailable, assembly_failed 등의 제한된 값으로 표현한다. 취소는 기존 중단 경로로 전파한다. 오류 메시지·소스 전문·인증 토큰·receipt·Editor 인증정보를 새 telemetry에 복사하지 않는다. 경로는 필요한 기존 fingerprint/ID를 우선한다.

새 설정의 documents 값으로 즉시 기존 전달 방식에 돌아갈 수 있어야 한다. Off는 참고 기능 전체를 끈다. 도구 생산자에 추가한 진단 필드는 실행 정책을 바꾸지 않으므로 기존 소비자와 호환을 검증한다. strict output schema가 있는 경계라면 해당 schema와 consumer 테스트를 함께 갱신한다.

원래 note/archive를 재작성하거나 DB migration을 하지 않으므로 롤백 시 기존 저장 자료를 지울 필요가 없다. 배포 번들에는 schema 버전이 있고 모르는 버전은 조용히 잘못 해석하지 않는다. codegen과 runtime은 같은 패키지 revision으로 배포한다.

focused 배포 후 문제 발생 시 먼저 documents로 되돌리고 원본 도구 결과와 해당 입력 telemetry로 재현한다. 사용자 프로젝트의 파일·설정·노트를 자동 삭제하여 복구하지 않는다.

## 18. 재검토한 위험과 범위 결정

| 위험/대안 | 결정과 이유 |
| --- | --- |
| 일반 문서만으로 이미 충분할 수 있음 | documents를 기준선으로 유지하고 focused를 opt-in으로 비교 |
| 관련 절 자동 선택이 틀릴 수 있음 | 명시 주제 안의 제한된 관찰 신호, 미확인은 공통 절, 원본 근거 우선 |
| 많은 관찰 요약이 원문을 밀어냄 | 하나의 2048 추가 한도·후보 상한·기준 입력 fallback |
| 프로젝트 요약이 낡은 사실 DB가 됨 | 원본 refs·마지막 관찰 표시·실행 수명·파생 view, 새 영구 DB 없음 |
| 의미 보존을 자동 판정하려다 코드 분석기가 커짐 | 일반 의미 검사 엔진은 만들지 않고 근거·요구·변경 계약만 제공 |
| note v1 확장으로 파서·압축·저장이 불일치 | 처음에는 기존 필드와 한도 재사용, v2는 별도 필요성 검증 |
| Unity/Unreal 응답 강제 통일로 상태 의미가 손실됨 | 내부의 좁은 변환만 공통화, 원본 상태·scope·unknown 보존 |
| 문서가 권한 또는 필수 다음 도구 지시가 됨 | 기존 Direct 응답·ToolGuard 계약에 회귀 테스트 추가 |
| 새 입력이 무시돼 효과가 없는데 성공이라 보고 | 포함 여부와 실제 모델 활용·품질을 별도 평가 |
| 모든 개선을 한 번에 넣어 원인 추적이 불가 | P1~P5 독립 변경·선택적 연결·분리된 평가 |

독립 소스 검토에서 확인한 system 승격 위험, 실행 scope 고정, note v1 제한, Direct workflow 금지, Unity 결과 형태 차이를 계획에 반영했다. legacy_eval의 retry_feedback 호출을 현재 Direct 경로의 기능으로 재사용한다고 가정하지 않는다.

이 계획은 현재 구조에서 구현할 책임과 실패 처리까지 확정한 제안이다. 실제 모델 효과, 정확한 절별 토큰 비용, 특정 Unity 프로젝트의 네트코드 패키지, 자동 포맷터 실행 계약은 아직 확인하지 않았으며 각 단계의 명시된 범위에서만 검증한다.


## 19. 2026-09-29 구현 결과

사용자의 “진행해”에 따라 P0–P5를 소스에 반영했다. 실행 중인 LM Studio 설치본·설정과 게임 프로젝트는 변경하지 않았다. 아래 기록은 현재 소스와 로컬 SDK/도구 fixture 검증 범위다.

### 구현한 연결

1. **P0/P1:** 기존 9개 documents 본문과 revision을 보존했다. catalogue v2에 33개 절, 적용 조건, 동반 절을 두고 생성기로 검증한다. `designGuidanceDelivery`는 `documents`가 기본이며 `focused`를 명시해야 새 경로가 동작한다.
2. **P2:** Unreal build diagnostics는 기존 생산자 파서를 유지하면서 `diagnosticCoverage`를 가산했다. Unity adapter는 기존 프로젝트 binding을 runtime 결과에 포함하고 실제 receipt conflict의 대상/path를 제공한다. Bridge C#의 권한·컴파일/세션 소유권은 그대로다.
3. **P3:** `captureReturned` 이후 실제 완료 요청/결과를 실행별 EvidenceManager에 전달한다. 원본 archive 대상 필터는 observation-only로 유지한다. 다음 일반 라운드에서 순수 selector가 후보를 만들고 기존 ContextManager/BudgetBroker가 전체 instruction+data를 측정한다.
4. **P4:** 파일 수정·삭제·충돌·부분 적용·rollback 미완료 뒤 이전 설정 요약을 제외한다. 같은 batch의 동일 파일 read/write 및 서로 다른 Unity session/generation은 최신성을 확정하지 않는다. 후속 batch의 정상 관찰로 복원한다. note v1 스키마와 reviewClaims reconciliation은 확장하지 않았다.
5. **P5:** 새 테스트를 npm test에 명시하고 status의 런타임 파일 목록 및 통합 패키지 목록을 갱신했다. 패키지 문서의 깨진 sources 링크도 수정했다. 실제 설치·GitHub CI·플랫폼별 Editor 실행은 이번 로컬 검증과 별도다.

### 책임과 수명

| 역할 | 담당 | 포함하지 않는 책임 |
| --- | --- | --- |
| 문서 정본/절 metadata | docs/model-guidance + 생성기 | 실행 중 프로젝트 탐색 |
| 후보 선택 | design-guidance.ts | 예산·권한·다음 도구 선택 |
| 실행별 관찰 수명 | EvidenceManager | 새로운 영구 기록 DB |
| 파생 자료의 좁은 변환 | reference-context.ts | 원본 보관, receipt 발급, 인과/소유자 추론 |
| 외부 데이터 표현 | reference-rendering.ts | system 지시문 생성 |
| 입력 조립/허용량 | 기존 ContextManager/BudgetBroker | 모델 목표 재정의 |
| 도구 허용·재시도·복구 | 기존 ToolGuard/ExecutionState/RecoveryCoordinator | 참고 자료에 의한 추가 gate |

### 독립 검토에서 찾아 수정한 경계

- scope가 `files[]`에만 있는 Unreal bundle 응답 누락.
- Unity receipt conflict 및 구형 scope 없는 Unreal conflict 뒤 이전 설정 사실 유지.
- 같은 batch의 읽기·수정 순서를 결과 배열 순서로 판단하는 오류.
- 과거 compile accepted/compiling 관찰이 현재 pending 신호를 계속 만드는 오류.
- 축소 후보의 자료 생략 개수 누락.
- 대상 불명 부분 실패 뒤 같은 batch의 읽기가 이전 설정을 되살리는 오류.
- 같은 batch의 Unity generation 충돌에서 마지막 배열 항목을 최신으로 선택하는 오류.
- Unreal ROLLBACK_INCOMPLETE의 파일 불확실성 누락.

각 경계는 순수 관찰 fixture와 관련 adapter 테스트에 포함했다. source body 가용성은 현재 SDK 입력에 남아 있는 실제 결과로만 판단한다. 과거 archive 참조를 새로 만들거나 요약을 원문으로 표시하지 않는다.

### 검증 범위와 제한

테스트 실행 결과는 `docs/evidence/model-behavior-implementation-20260929.json`과 연결된 로그에 기록한다. 기존 note/objective/attachment/approval/working-context 회귀는 기존 전체 plugin suite로 확인하고, 새 기능은 focused/reference-context 및 실제 prediction handler fixture로 확인한다.

- 도구 이름이 같아도 알려진 provider·고유 요청/결과·반환 scope가 확인되지 않으면 파생 자료에서 제외한다.
- 모르는 엔진 버전·부분 package lock·복수 netcode 설치에서는 특정 framework 사용을 확정하지 않는다. Unreal의 engineAssociation은 실제 patch 버전으로 쓰지 않는다.
- 일반 소스의 소유자/상태 머신/네트워크 권한을 자동 추출하지 않는다. 실제 코드와 사용자 요구에 대한 모델의 판단은 계속 필요하다.
- 기존 SDK는 요청을 묶어 반환한다. host가 request 메시지를 별도로 분할한 비표준 입력은 참고 가용성을 보수적으로 누락할 수 있으며, 원본 메시지·보관 경로는 유지된다.
- 문서 포함/테스트 통과로 모델 품질 개선을 수치화하지 않는다. P6 비교 평가와 실제 Unity/Unreal 실행은 하지 않았다.


최종 로컬 결과: plugin 전체 465건 통과, 마지막 상태 조건 보강 뒤 관련 53건/handler 7건 재통과, Unreal 248건 통과/플랫폼 제외 1건, Unity 36건 통과/환경 제외 4건, 통합 package 18건 통과. TypeScript build, guidance:check, encoding, diff 검사를 통과했다. CI suite membership 54개 파일 검사는 구성 확인이며 GitHub CI 실행 결과가 아니다.
