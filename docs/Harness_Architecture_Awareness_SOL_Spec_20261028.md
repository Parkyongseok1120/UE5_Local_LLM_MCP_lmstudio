# SOL 실행 명세 — 일반 Unreal 작업의 구조 판단과 하네스·컴팩터·RAG 계약

- 작성일: 2026-10-28, Asia/Seoul
- 기준 저장소: UE5_Local_LLM_MCP_lmstudio
- 기준 브랜치: codex/harness-output-contract-fixes
- 기준 커밋: 2b13b38c5644b9072e04c488e642205b46cbc804
- 기준 제품: context compactor 0.4.86, runtime revision 133 / Unreal Agent 0.3.32
- 문서 상태: 구현 전 실행 명세. 아래의 새 타입·필드·파일·테스트 이름은 구현할 계약이며, 이미 존재하는 기능이라고 해석하지 않는다.
- 수행 범위: 이 저장소의 하네스·컴팩터·RAG·지침·테스트. 실제 사용자 Unreal 프로젝트를 이 명세의 예제로 수정하지 않는다.
- 관련 근거: [구조 근거 패킷](evidence/architecture-awareness-plan-20261028.json).
- 이 문서는 이전 대화 없이 실행할 수 있어야 한다. 이전 문서의 현재 코드와 맞지 않는 수치·중단 규칙을 관성적으로 상속하지 않는다. 보안·도구 권한·프로젝트 경계의 기존 계약은 유지한다.

## 0. 목표와 요구 해석

사용자는 모델이 SSOT를 변수 중복·같은 클래스 내부의 정리 수준으로만 해석하고, 기능의 소유자·공유 범위·수명을 먼저 검토하지 않는다고 지적했다. 일반적인 “리스폰 고쳐줘”, “장비 기능 추가”, “피격 처리 수정”에서도 다음을 판단하게 만드는 것이 목표다.

1. 현재 기능의 진입점, 상태 확정자, 이용자, 생성·해제 주체를 찾는다.
2. 기존 소유자를 유지해도 되는지, Component·Actor·Subsystem·Data Asset 등으로 경계를 바꿀 실제 이유가 있는지 비교한다.
3. 정의 데이터, 개체별 실행 상태, 세션/월드 상태, 표시·복제 상태를 구분한다.
4. 구조 변경을 제안할 경우 생성·주입·호출·실패·해제·재진입까지 연결한다.
5. 이미 있는 소유자·정리 경로·패턴을 반대 근거로 확인한다.
6. 예산·자료가 부족하면 판단 범위를 밝힌다. 구조 검토를 생략한 상태를 “구조 문제 없음”으로 기록하지 않는다.

사용자가 “아키텍처 검토”, “SSOT”, “SOLID”를 명시할 필요가 없어야 한다. 단, 모든 작은 수정에 전면 조사·RAG 검색·추가 모델 호출·리팩터링을 강제하는 요구는 아니다. “현재 소유자 유지”도 근거가 있으면 정상 결과다. 사용자에게 매번 별도 설계 승인 화면을 추가하지 않는다. 실제 수정 범위는 기존 사용자 요청과 도구 권한을 따른다.

RAG만 보강하면 지식을 찾을 가능성은 커지지만, 언제 적용하고 무엇을 보존할지는 해결되지 않는다. 따라서 안정적인 기본 지침 + 관찰 기반의 제한된 선택 정책 + 선택적 RAG 근거 + 판단의 안전한 보존을 함께 구현한다. 이것으로 모델의 전문가 수준 판단을 보장한다고 홍보하지 않는다. 의미 품질은 별도 비교 평가가 필요하다.

## 1. 시작 시 확인할 사실과 현재 결함

### 1.1 기준 상태 확인

SOL은 먼저 git status, 현재 HEAD, 적용되는 AGENTS.md, 관련 파일의 현재 내용을 확인한다. 기준 커밋과 달라도 무조건 중단하지 말고 아래 소유자·계약이 어디로 이동했는지 대응표를 만든다. 다른 작업의 변경을 덮어쓰거나 기준 커밋으로 reset하지 않는다. 이 문서의 줄 번호는 기준 커밋에서만 유효하며 함수 이름을 함께 사용한다.

사용자 규칙: Unreal 프로젝트 코드를 만들 때 namespace를 되도록 사용하지 않는다. 이번 제품 구현은 주로 TypeScript/JavaScript/Python이며, 패턴 예제에도 불필요한 C++ namespace를 넣지 않는다.

### 1.2 확인된 상태와 해석 한계

| ID | 현재 근거 | 확인한 사실 | 판정과 구현 영향 |
| --- | --- | --- | --- |
| F01 | src/guidance-topic-selection.ts의 selectGuidanceIntent | 명시적인 작업 표현과 주제 규칙으로 최대 두 주제를 선택하며 불명확하면 core로 간다 | SourceVerified. 이것만으로 일반 구현에서 구조 대안 검토가 보장되지 않는다. 특정 답변 실패의 단독 원인이라는 뜻은 아니다 |
| F02 | docs/model-guidance/core.md, unreal.md, design.md | 기존 소유자 확인 원칙과 일반 패턴 표는 이미 있다 | 기존 지침을 폐기하지 않는다. “기존 소유자에게 유지” 앞에 그 소유자가 적절한지 평가하는 기준을 보강한다 |
| F03 | src/config-defaults.ts:3 | 기본 mode=off, delivery=documents, maxTokens=6144 | 현재 README의 2048과 다르다. 자동 기능의 전제는 Auto 활성화다. 명시적인 Off를 몰래 켜지 않는다 |
| F04 | src/round-input.ts:417–482, docs/model-guidance/README.md:56 | 입력 재작성 시 input_reshaped로 dropped guidance를 재입장시킬 수 있다 | 문서 계약 불일치. “한 번 빠지면 실행 내 영구 제외”로 구현을 되돌리지 않는다 |
| F05 | src/evidence-manager.ts:134, README.md:62 | reference observations는 저장 창에서 historical로 복원될 수 있다 | 문서 계약 불일치. 복원 관찰과 현재 실행의 편집 권위를 명확히 구분한다 |
| F06 | src/execution-state.ts:76, round-loop.ts:234 이후 | 반환된 근거와 완성된 모델 판단의 commit이 다르며 종료 후 콜백을 차단한다 | 정상 보호 장치. 새 판단·검색이 반드시 재사용한다 |
| F07 | src/note-persistence.ts:16 이후 | history note와 window note의 복원 우선순위를 NotePersistence가 담당하고 mirror 경로를 제공한다 | 재사용할 보호 장치. 모든 쓰기 경로가 이미 통합됐다는 뜻은 아니다. F11 우회 경로를 제거한다 |
| F08 | scripts/rag_modes.py, collect_project_architecture.py, index_inputs.py | design/planning/prototype_component/prototype_subsystem 등 모드와 프로젝트 구조 수집기가 이미 있다 | 동일 목적 도구·모드·전면 스캐너를 다시 만들지 않는다. legacy_eval의 architecture gate는 제품 경로가 아니다 |
| F09 | src/review-evidence.ts:117, review-register.ts:176 | 근거 검색에서는 식별자 끝부분을 쓰지만 보고 누락 검사는 전체 문자열 포함 여부를 쓴다 | 재현된 P2 결함. 한정 이름/짧은 이름 차이로 불필요한 보완 검토가 생길 수 있다 |
| F10 | src/quote-anchoring.ts:240–246 | 파일 범위 검사가 정정문 안의 인용된 과거 과장 문구도 찾는다 | 재현된 P2 결함. “전체 19개라는 표현은 철회, 실제 본문 15개”에도 같은 경고가 붙을 수 있다 |
| F11 | src/semantic-handoff.ts:176–185 | semantic summary가 activeNote를 교체한 뒤 WorkingContext.note를 직접 쓴다 | SourceVerified 작성 경계 우회. 새 구조 state가 유실될 위험을 막기 위해 이 쓰기를 반환값으로 바꾸고 NotePersistence로 통합한다. 현재 실행의 데이터 손실이 실측됐다는 주장은 아니다 |
| F12 | src/round-input.ts:93–100 | activeNote는 optional guidance 입장 검사 전에 기본 입력 구성에 사용된다 | SourceVerified. v2 저장 한도만 키우면 새 구조 영역이 optional 예산을 우회할 수 있으므로 저장/입력 projection을 분리한다 |
| F13 | scripts/rag_build_outputs.py:72, direct_rag_corpus.py:19 | corpus 상태는 현재 engine/project 중심이며 패턴 존재 여부를 직접 표현하지 않는다 | SourceVerified. no_match를 no_pattern_corpus로 오인하지 않도록 동일 generation manifest에 선택 metadata를 추가한다 |

표의 src는 lmstudio-context-compactor-plugin/src를 뜻한다. F09/F10은 기존 보고 경계 결함이고, 새 구조 기능과 별도 회귀 테스트로 고친다. 모든 이전 보완 라운드가 불필요했다는 주장은 하지 않는다. 실제 19개 검색/15개 본문 전달 불일치도 존재했기 때문이다.

### 1.3 최근 대화 결과에서 유지할 반대 근거

분석한 완료된 감사 턴에는 도구 호출 28건, 직접 읽기 20건, 동일 인자 반복 0건, 도구 실패·거부 0건이었다. 본문 전달은 프로젝트 파일 15개, 검색 목록은 19개였다. 같은 소스 버전의 범위 복원 3건은 기존 read_ledger에서 restored로 구분되었다. 이를 새 읽기 분류 오류라고 고치지 않는다.

현재 코드가 기록한 입력 재작성 4회에는 적용성 무효화·예산 제외·재입장·최종 단계 전환이 포함된다. “system 변경 무조건 0”을 합격 기준으로 두지 않는다. 불필요한 변경과 필수 변경을 사유별로 구분한다.

RespawnTimers 사례에서 Logout·EndPlay·콜백의 clear/remove가 존재했는데 약한 포인터 사용만으로 누수 우려를 과장했다. 새 평가에는 생성뿐 아니라 이미 있는 해제 경로를 반대 근거로 읽었는지 포함한다. 이 사례 자체를 Component 분리의 정답으로 고정하지 않는다.

원본 진단 자료는 로컬 <local-diagnostics>/harness-run-1793165509278에 있다. 원본 대화 전체나 사용자 게임 소스를 저장소 테스트에 복사하지 말고, 아래의 최소 합성 fixture로 재현한다. 원본이 없어도 이 명세의 구현은 가능해야 한다.

## 2. 구조 결정과 비목표

### 2.1 채택하는 구조

기존 실행 수명 안에 작은 판단 상태를 추가한다. 구조를 판단하는 정책은 순수 함수이며, 소스·RAG·파일·모델에 직접 접근하지 않는다. 관찰의 정본은 기존 EvidenceManager/ReadLedger, 판단의 실행 중 소유자는 ArchitectureState, 판단의 저장·복원 소유자는 기존 NotePersistence로 분리한다. 저장된 note는 ArchitectureState의 스냅샷이지 별도로 갱신되는 두 번째 판단 원장이 아니다.

새 파일은 다음 네 개를 기본으로 한다. 구현 중 기존 작은 모듈에 합칠 수는 있으나 각 책임과 테스트 경계는 유지한다. source CommonJS parser와 런타임 상수/validator를 공유해야 하면 §9.3의 architecture-note-contract.js를 추가한다.

| 파일 | 책임 | 금지된 책임 |
| --- | --- | --- |
| src/architecture-contracts.ts | 내부 DTO, enum, 제한값, 순수 포트의 타입 정의 | 실행·검색·승인·저장 |
| src/architecture-observation.ts | 이미 반환된 근거를 출처가 있는 구조 단서로 투영하고 의존 근거를 정규화 | 디스크 탐색, API 호출, 프로젝트 전체 구조를 확정 |
| src/architecture-policy.ts | 목표·관찰·예산 상태로 판단 깊이와 참고 후보를 선택 | 수정 허용, 실제 도구 실행, 패턴 적용을 정답으로 확정 |
| src/architecture-state.ts | 실행 내 구조 판단 reducer, revision, stale/deferred 전이, 제한된 snapshot | 근거 본문 캐시, 별도 영속 파일, 검색·도구 세션·예산 소유 |

ReviewRegister는 코드 검토 finding과 보고 누락의 소유자로 유지한다. 구조 대안의 상태를 found/cleared로 억지로 표현하지 않는다. ArchitectureState는 별개의 정보 종류인 설계 판단만 소유한다. 두 저장소가 동일 결함·근거·권위를 각각 확정하지 않게 한다.

### 2.2 연결도

~~~mermaid
flowchart TD
    U[사용자 요청] --> O[기존 TaskObjective와 실행 scope]
    O --> P[ArchitecturePolicy 순수 선택]
    E[EvidenceManager / ReadLedger] --> V[출처가 있는 구조 관찰 view]
    V --> P
    K[단일 Markdown + catalog 정본] --> G[생성된 기본 지침 / 패턴 카드]
    G --> P
    P --> I[기존 prepareRoundInput / 정확 토큰 입장]
    I --> M[기존 모델 라운드]
    M --> T[기존 도구 경계 / 승인 / 예산]
    T --> R[RAG 또는 직접 소스 도구]
    R --> E
    M --> X[RoundTransaction]
    X --> A[ArchitectureState 판단 commit]
    A --> N[NotePersistence]
    N --> W[WorkingContext 창 / 기존 note 저장]
    A --> I
    T --> C[기존 쓰기 provider의 receipt / CAS]
~~~

그림의 순환은 기존 모델 라운드의 순환이다. ArchitecturePolicy 또는 prepareRoundInput 안에 별도의 검색/모델 반복문을 만들지 않는다. 새 클래스가 session.close나 archive.close를 호출하지 않는다.

### 2.3 이번 구현에 넣지 않을 것

- 모든 요청에 자동 전체 프로젝트 스캔, 자동 RAG refresh, activeProject 전환, 엔진 추정.
- 별도 백그라운드 architecture 검색, 추가 설계 전용 모델/서브에이전트 호출, 무한 보완 라운드.
- legacy_eval의 architecture gate·proposal store·sidecar·refactor 단계 실행기 복원.
- 파일 길이·멤버 수·클래스 이름만으로 “Component로 분리해야 함”이라는 결론.
- 모든 생성 코드에 GAS·Game Features·Subsystem·MVVM 적용.
- 구조 판단을 쓰기 receipt, 사용자 승인, 빌드 성공, 검증된 결함으로 사용.
- Unreal 전용 판정을 Unity·일반 문서 작업에 적용.
- 변경 권한이 있는 기존 일반 구현마다 새로운 사용자 승인 질문 추가.

## 3. SSOT·책임·SOLID 계약 표

| 정보/결정 | 유일한 작성 책임 | 소비자 | 저장/수명 | 무효화 또는 종료 |
| --- | --- | --- | --- | --- |
| 사용자 목적·허용 작업 종류 | 기존 objective/scope 해석 | 정책·도구 경계 | 실행 및 기존 목적 연속성 | 새 목적·범위 변경 |
| 도구 capability·side effect 분류 | stable_tool_manifest 및 기존 registry | tool-boundary·예산·근거 처리 | 설치 계약/세션 | 계약 변경·세션 종료 |
| 편집 승인·receipt·CAS | 기존 tool-boundary/provider | 실제 쓰기 경로 | 기존 요청/소스 버전 규칙 | 취소·변경·receipt 만료 |
| 반환된 소스의 관찰 이력 | EvidenceManager/ReadLedger | 구조 view·review index·컴팩터 | 기존 source/window 계약 | 변경 관찰·불명확 순서·복원 downgrade |
| 구조 단서 | architecture-observation의 파생 view | 정책·모델 | 근거 snapshot 수명, 재계산 가능 | 근거 revision/의존 소스 변경 |
| 패턴의 설명·적용 조건 | docs/model-guidance Markdown + catalog metadata | 생성 bundle·RAG 수집 | 콘텐츠 해시가 지정한 버전 | catalog/card 내용 변경 |
| 인덱스 세대·검색 바인딩 | 기존 Direct RAG generation/request binding | RAG 검색·근거 envelope | 요청에 고정한 세대 | generation transition |
| 패턴 보충 여부·깊이 | ArchitecturePolicy | round-input | 입력 구성 시 순수 결정 | 목표·단서·예산 변경 |
| 모델의 구조 후보·선택 | ArchitectureState; 모델 제안은 유효한 round commit만, 호스트 invalidate/restore/close는 별도 event | note·모델 입력·보고 | 실행, 이후 historical 판단 | 근거 변경·목적 변경·대체·종료 |
| note 복원 우선순위·저장 | NotePersistence | ArchitectureState 초기화·WorkingContext | 기존 history/window 저장 | lineage/scope 불일치·손상 |
| 입력 admission·tool reserve | 기존 round-input/budget broker/batch reservation | 모든 optional 자료·도구 | 현재 입력/배치 | 입력 재측정·라운드 종료 |
| report finding 식별·누락 | ReviewRegister + ReviewEvidenceIndex | 보고 보완 | 실행 및 기존 note 계약 | 소스 변경·철회·명확한 보고 처리 |
| 종료·자원 해제 | 기존 prediction-loop finally / sealAndRelease | 전 구성요소 | 실행 종료 | 정확히 한 번 기존 release 경로 |

SOLID 구현 기준:

- SRP: 관찰 투영·정책·판단 저장·영속 저장·권한 결정을 서로 섞지 않는다. prediction-loop에는 호출 연결과 snapshot 전달만 추가한다.
- OCP: 패턴 추가는 Markdown/catalog 데이터 추가가 중심이다. 패턴별 if 분기를 prediction-loop에 계속 추가하지 않는다.
- LSP: 번들 카드와 RAG 카드는 같은 참고자료 계약을 만족하지만 출처·버전·신뢰 상태를 숨기지 않는다. search_hits를 source_body로 대체하지 않는다.
- ISP: 정책에는 읽기용 구조 snapshot과 admission 결과만 준다. WorkingContext 전체, MCP client, raw mutable ledger를 넘기지 않는다.
- DIP: 정책/reducer는 SDK·filesystem·RAG 구현에 import 의존하지 않는다. 얇은 adapter가 기존 소유자의 값을 DTO로 변환한다. 모든 클래스에 불필요한 interface나 DI container를 만들 필요는 없다.

## 4. 내부 데이터 계약

아래 타입은 의미 계약을 보여 주는 초안이다. 실제 구현에서는 기존 프로젝트/엔진/파일 identity 타입과 canonicalizer를 import한다. 같은 정규화 함수를 복사해서 만들지 않는다. optional은 “미확인”이며 빈 문자열을 검증된 identity로 취급하지 않는다.

~~~ts
type ArchitectureDepth = "none" | "baseline" | "focused" | "cross_owner";
type DecisionState = "proposed" | "selected" | "deferred" | "rejected" | "superseded";
type EvidenceApplicability = "same_observed_version" | "historical" | "stale" | "unknown";
type EvidenceAvailability = "in_input" | "archived" | "metadata_only" | "unavailable";

type ArchitectureBinding = {
  executionId: string;       // 호스트 전용. 복원 시 새 실행의 값으로 생성
  lineage: string;           // 기존 WorkingContext 경계를 참조
  objectiveFingerprint: string;
  projectIdentity?: string;
  scopeRevision: number;     // 실행 중 구조 view용. 쓰기 권한으로 사용 금지
  engineIdentityKey?: string;
  catalogHash: string;
};

type ArchitectureEvidenceRef = {
  ref: string;               // 기존 tool-call/source 참조. 모델이 발급하지 않음
  sourceKey?: string;
  observedVersion?: string;  // 기존 sha/hash 식별을 유지
  qualifiedSymbol?: string;
  observedRange?: [number, number];
  applicability: EvidenceApplicability;
  availability: EvidenceAvailability;
};

type PatternRef = {
  patternId: string;
  contentHash: string;
  catalogHash?: string;
  origin: "bundled" | "rag";
  indexGenerationId?: string;
  applicability: "general_concept" | "version_matched" | "unverified" | "mismatch";
};

type ArchitectureDecision = {
  id: string;
  revision: number;          // 호스트 전용. 예상 revision을 비교하는 reducer 갱신
  subject: string;
  state: DecisionState;
  applicability: EvidenceApplicability; // state와 독립. selected도 stale이 될 수 있음
  currentOwner: string;
  requiredLifetime: string;
  sharingScope: string;
  definitionVsRuntime: string;
  options: Array<{ candidate: string; reason: string; cost: string }>;
  chosen?: string;
  connectionPlan: string;
  evidenceRefs: ArchitectureEvidenceRef[];
  patternRefs: PatternRef[];
  counterEvidence: string[];
  unknowns: string[];
  supersedes?: string[];
};

type ArchitectureSelection = {
  depth: ArchitectureDepth;
  reasons: string[];
  selectedPatternIds: string[];
  missingEvidence: string[];
  disposition: "ready" | "deferred_budget" | "deferred_evidence" | "disabled";
};
~~~

타입 예시 주석의 의미: executionId·lineage·scopeRevision·프로젝트/엔진 identity·근거 해시·revision은 호스트가 채운다. 모델이 footer에 같은 필드를 써도 신뢰하지 않고 schema 위반으로 거절한다. 모델은 현재 입력에서 발급한 참조 ID와 후보·이유만 제출한다. 모델이 쓴 “verified”, “approved”, “build passed” 문자열로 상태를 승격하지 않는다.

중요한 분리:

1. selected는 모델이 선택했다는 뜻이다. 사용자 승인이나 실행 완료가 아니다.
2. applicability는 마지막으로 관찰한 소스와의 일치 수준이다. 외부 파일이 계속 최신이라는 보장은 아니다.
3. availability는 모델 입력에서 근거 본문을 볼 수 있는지다. archived라고 소스 버전이 바뀐 것은 아니며, 같은 해시라고 본문을 본 것은 아니다.
4. PatternRef는 지식 출처다. 프로젝트에 그 패턴이 존재한다는 근거는 ArchitectureEvidenceRef로 별도 제시한다.
5. 실행 결과는 기존 도구 outcome으로만 판단한다. ArchitectureDecision에 mutationApplied나 approved 같은 권한 필드를 추가하지 않는다.

### 4.1 구조 관찰 view의 허용 범위

구조 단서는 관찰된 class/base, 명시된 생성·소유 관계, 설정/인스턴스 데이터, 등록·해제 위치, 호출·소비자, 범위가 알려진 검색 결과로 구성한다. 각 단서는 supporting refs와 observed/hypothesis/unknown 구분을 가진다.

정규식이나 기존 project_architecture collector의 결과는 탐색 단서로만 사용한다. regex 기반 클래스 분류가 UHT·C++ AST·Blueprint 실행 그래프를 완전히 이해한다고 주장하지 않는다. 반환된 코드 안에서 명확히 찾은 선언·호출 위치와, 거기서 추정한 책임 분리는 구분한다. 일부 범위에서 cleanup이 안 보였다는 이유로 “cleanup 없음”을 기록하지 않는다.

저장된 구조 단서에서 current source facts를 재생성하지 않는다. 원장 snapshot에서 파생하거나 historical로만 복원한다. 변경된 파일과 관련 없는 판단까지 모두 버리지 않도록 decision → evidenceRef 의존성을 사용한다. 의존성 누락/손상 시 해당 판단은 unknown으로 내리며 현재 유효하다고 간주하지 않는다.

### 4.2 실제 모델 입력별 참조 발급표

최종 admission을 마치고 SDK에 보낼 modelInputId가 결정된 시점에 호스트가 다음 읽기 전용 발급표를 만든다. 후보 측정 중 잠깐 포함했던 자료는 발급하지 않는다.

~~~text
evidenceHandle → sourceKey / observedVersion / range / provider / applicability / availability
patternHandle  → patternId / contentHash / catalogHash / origin / indexGenerationId / applicability
decisionId     → 이 입력에 실제로 보여 준 revision
stateView      → complete 또는 partial
~~~

모델은 새 patch에 해당 입력에 실제 노출된 handle만 사용할 수 있다. 기존 tool-call refs 목록은 개수·문자 제한으로 일부만 노출될 수 있으므로, 전체 transcript에 존재한다는 사실만으로 발급된 것으로 간주하지 않는다. source의 raw receipt나 편집 권한 정보는 이 표에 넣지 않는다.

patternId만 모델이 제출하게 하지 않는다. 같은 ID의 번들/RAG 카드가 다른 hash라면 모델이 어느 자료를 근거로 삼았는지 알 수 없기 때문이다. 모델 payload는 patternRefs의 opaque handle을 사용하고, 호스트가 PatternRef 전체로 해석한다. 같은 pattern ID의 서로 다른 버전은 다른 handle을 갖는다.

발급표는 모델 입력의 렌더링 결과와 함께 생성하고, 결과 commit까지 해당 round가 소유한다. 출력 후 폐기하며 note에 실행 권한으로 저장하지 않는다. 이미 저장된 판단을 수동적으로 유지할 때는 과거 refs를 다시 제출하게 요구하지 않는다. 새로운 patch의 검증과 기존 판단의 보존은 다른 경로다.

## 5. 자동 판단 정책과 입력 비용

### 5.1 동작 조건

- 기존 designGuidanceMode=auto에서, 확인되거나 사용자 요청에 명시된 Unreal 코드 작업이면 baseline을 선택한다. “구조”, “SSOT”라는 단어는 필요 없다.
- 기존 수동 design/lifecycle/multiplayer 모드는 사용자의 선택을 우선한다. Unreal 소유자/수명 기준은 해당 엔진 지침에 포함하되 모든 수동 모드를 몰래 Auto로 바꾸지 않는다.
- off, observeOnly, allowance=0이면 구조 참고 후보·단서용 추가 처리·RAG 유도·판단 footer 요청을 하지 않는다. 기존 실행 안전장치는 그대로 작동한다.
- 일반 설명·문서·다른 엔진은 기존 동작을 유지한다. 엔진이 미확인인 코드 작업은 공통 ownership/lifetime 원칙만 사용한다.
- 이 명세에서는 제품 기본값 off를 바꾸지 않는다. “검토 요청 없이 자동”은 한 번 Auto를 선택한 뒤 일반 요청에서 작동한다는 뜻이다. 배포 시 사용자 설정을 보존하고, Auto가 꺼져 있으면 적용 상태를 명확히 보고한다.

설정 조합의 정확한 동작은 다음으로 고정한다.

| 조건 | 지침/정책 | 구조 state 및 note |
| --- | --- | --- |
| Auto + Unreal | 기존처럼 effective delivery=focused. 저장된 documents 값을 바꾸지 않음. 자동 baseline/선택 정책 활성 | noteEnabled이면 state+v2 요청 활성 |
| 수동 design/lifecycle/multiplayer + Unreal | 기존 선택 주제·delivery 우선. 해당 지침의 구조 기준 보강, 관찰 기반 카드 자동 확대 없음 | 첫 구현에서는 새 구조 state/v2 요청 비활성. 기존 v1 판단 유지 |
| Auto + 엔진 미확인 | 공통 소유자/수명만, Unreal 카드 없음 | Unreal 구조 state/v2 요청 비활성 |
| Auto + Unreal + noteEnabled=false | baseline/카드 선택 가능 | 새 v2 요청/영속 저장 없음. 모델의 가짜 구조 완료 상태를 생성하지 않음 |
| Off / observeOnly / allowance=0 | 새 정책/단서/주입/검색 유도 없음 | 이전 v2 저장 영역은 수동 보존. 새 모델 patch 요청/commit 없음 |

비활성 실행에서도 기존 소스 관찰·scope 검증·note reconciliation은 원래 계약대로 동작한다. 이전 구조 저장 영역을 유효한 현재 판단으로 보여 주지는 않는다. 활성 상태가 없다는 이유로 v2 영역을 삭제하거나 source version을 새로 부여하지 않는다. 다음 활성 실행에서 historical로 다시 적용성을 계산한다. 명백한 scope 불일치·손상은 기존 검증에 따라 제외할 수 있다.

### 5.2 선택 규칙

| 깊이 | 조건 | 모델에게 요구할 결과 | 비용 제한 |
| --- | --- | --- | --- |
| baseline | 일반 Unreal 구현/수정/코드 검토 | 현 소유자·수명·공유 범위·정의/실행 분리의 짧은 확인. 현재 구조 유지 가능 | 안정적인 기본 절만. 자동 도구·추가 모델 0 |
| focused | 소유자 수명 불일치, 반복되는 기능 조합, 공유 정의 변경, 반복 등록/해제 등 관련 단서가 있거나 일반 기능 추가에 그 판단이 필요 | 현 소유자 유지 + 유력 후보 1개 이상 비교. 반대 근거와 연결 계획 | optional 카드 최대 2개. 필요할 때 기존 검색 경로 |
| cross_owner | 여러 소유자의 상태 확정/생성/종료 경계를 바꾸는 요청·근거가 있음 | 영향 소유자와 최소 변경안을 함께 비교. 데이터/실행/이벤트 경계 명시 | 자동 전체 스캔 금지. 이미 읽은 범위 중심, 미확인 이용자는 표시 |

단서가 부족하면 focused 가능성을 제시할 수 있으나 구조 결함으로 확정하지 않는다. 문자 그대로의 키워드 점수만으로 강제 분리하지 않는다. “컴포넌트”라는 단어가 없는 요청에도 baseline이 동작하는 회귀를 반드시 둔다.

### 5.3 실제 입력 구성 규칙

1. 안정적인 구조 판단 원칙은 기존 core/ownership 또는 unreal/responsibility 절에 넣는다. 이미 six-section 제한으로 꽉 찼을 때 일곱 번째 필수 절을 추가하지 않는다.
2. 핵심 문구는 “현재 소유자가 적절한지 먼저 판단한다. 수명·공유 범위·독립 기능·정의 데이터의 경계가 맞으면 유지하고, 맞지 않으면 가장 작은 대안을 비교한다”이다.
3. 상세 패턴 카드는 기존 section/candidate 한도와 designGuidanceMaxTokens 안에서 선택한다. 최대 두 카드란 상한이며 항상 두 개를 넣는다는 뜻이 아니다.
4. 근거·판단의 동적 정보는 구분된 reference data / prior judgment 메시지로 넣는다. 모델의 판단을 system 규칙으로 재삽입하지 않는다.
5. tokenizer로 실제 템플릿의 총 증가량을 측정한다. 작은 baseline의 목표는 추가 300 tokens 이하로 잡되, 이는 보장치가 아니라 fixture 측정 예산이다. 넘으면 내용을 줄인다. 기존 전체 allowance를 늘려 통과시키지 않는다.
6. 최소 읽기 예약·도구 결과 공간·출력 예산보다 낮은 우선순위다. 부족하면 상세 카드 → 추가 판단 설명 → baseline 후보 순으로 기존 admission에서 줄인다. 권한/프로토콜 필수 정보는 건드리지 않는다.
7. 일반 주제/패턴 변경은 기존 sticky rewrite 시점까지 미룬다. 엔진/프로젝트 적용성 무효화, 실행 단계 전환, 예산상 제외는 즉시 반영하고 실제 rewrite cause를 남긴다.
8. finalization/report-review/recovery에 새 패턴 조사나 새 도구를 시작하지 않는다. 이미 유효하게 들어간 지침의 재사용 여부는 기존 정책을 따른다.
9. 새로운 구조 판단을 했다는 이유만으로 보고 보완 라운드를 추가하지 않는다. footer 누락도 추가 모델 호출의 자동 원인이 아니다.

현재 activeNote는 round-input.ts:93의 composeModelHistory, prepareWorkingInput 및 input assembler에 optional guidance보다 먼저 들어간다. 따라서 v2 저장 note를 그대로 넘기는 구현은 금지한다. 일반 note의 기존 input projection과 구조 판단의 optional projection을 분리한다. composeModelHistory/prepareWorkingInput/assembler/재측정/최종 dispatch 모두 동일하게 선택된 input projection을 사용해야 한다.

구조 baseline·카드·판단 view는 designGuidanceMaxTokens라는 하나의 남은 allowance를 공유한다. 각각 allowance를 따로 받지 않는다. 저장용 전체 note는 모델 입력으로 자동 복사하지 않는다. admission 이후 최종 모델 입력의 hash/실제 token 측정과 참조 발급표가 같은 projection을 가리키는지 통합 테스트한다.

### 5.4 RAG 호출 방식의 확정

첫 구현에서는 host가 숨은 자동 RAG 요청을 발행하지 않는다. baseline과 필요한 짧은 카드가 자동으로 주어지고, 더 자세한 근거가 필요할 때 기존 모델 라운드가 기존 unreal_rag_search를 사용한다. 이는 사용자에게 검토 요청을 요구하지 않으면서도 기존 guard·예산·취소·도구 이력을 모두 재사용한다.

선택기는 retrieval intent를 참고 데이터로 표현할 수 있지만 도구 호출 명령이나 필수 선행 게이트로 만들지 않는다. 예: “정의/인스턴스 분리의 적용 조건이 부족함. 기존 Data Asset/장비 구성 근거가 있으면 먼저 재사용”. 같은 source/catalog/dependency fingerprint가 바뀌지 않으면 같은 hint를 매 라운드 새로 발행하지 않는다. 모델이 읽지 않았으면 미평가로 기록하며 허위로 평가 완료 처리하지 않는다.

구조 안내가 스스로 권고하는 상세 검색의 기본 상한은 한 실행에서 2건이다. 기존 도구 예산보다 작은 안내 상한이며, 새 권한 제약이나 도구 차단 규칙이 아니다. 일반 사용자 검색·새 근거로 필요한 검색은 기존 실행 정책이 판단한다. 제품은 구조 힌트 때문에 동일 인자/동일 근거를 반복 조회하지 않는지 기존 read_ledger 지표로 측정한다.

### 5.5 단서 추출의 구현 범위와 선택 순서

새 정책을 의미를 모르는 거대한 regex 엔진으로 만들지 않는다. baseline은 구조 단서 추출 성공과 무관하게 활성화 조건만 맞으면 제공한다. 단서는 상세 카드의 우선순위를 좁히기 위한 것이며, 구조 판단의 정답은 모델의 비교와 근거 평가에서 나온다.

| 단서 종류 | 허용 입력 | 선택에 미치는 영향 | 단정하면 안 되는 것 |
| --- | --- | --- | --- |
| 기존 소유자 종류 | 반환된 symbol/architecture metadata 또는 범위가 확인된 선언 | Component/Actor/Subsystem/definition-instance 후보의 문맥 | 클래스 이름에 Manager가 있다는 이유로 책임 과다 확정 |
| 정의 데이터 사용 | 확인된 DataAsset/DataTable/PrimaryAsset 선언·참조 | definition-instance/asset-manager 후보 | asset 참조만으로 runtime 상태 혼재 확정 |
| 등록·비동기·종료 위치 | 반환된 소스의 명시 호출 위치 | event-observer/asset-manager/lifetime 후보 | Unbind/EndPlay가 일부 범위에 없다는 이유로 누수 확정 |
| 필요한 수명·공유 범위 | 기존 objective의 검증된 최신 사용자 텍스트 또는 source-backed 모델 가설 | focused/cross_owner 후보 비교 요청 | 파일 두 개를 읽었다는 이유만으로 cross_owner 강제 |
| 현재 존재하는 구현 | 기존 symbol/검색/소스 관찰의 한정된 범위 | 새 소유자 대신 재사용 대안을 비교 | 검색 miss만으로 기존 구현 없음 확정 |
| 모델이 제안한 책임 분리 | 유효하게 commit된 모델 판단 | 추가 후보/미확인 질문으로만 소비 | 모델 가설을 observed source fact로 변환 |

소스 텍스트 단서는 기존 decoder가 반환한 bounded body 안에서만 찾는다. 이를 위해 파일을 다시 열지 않는다. 주석/문자열/잘린 선언을 구별하지 못하는 parser 결과는 hypothesis로 내린다. 정규식으로 타입을 찾은 결과에 런타임 소유권·완전한 호출 관계를 부여하지 않는다.

선택 우선순위는 사용자 목적과 범위 → 필수 엔진/권한 계약 → 확인된 수명/소유자 불일치의 관련 카드 → 정의/실행 분리 → 그 밖의 관련 카드 순이다. 같은 우선순위는 stable patternId 순으로 고정한다. 유효한 기존 선택을 유지할지는 기존 sticky admission 정책이 최종 결정한다. 안전 계약을 카드 점수로 밀어내지 않는다.

정책은 원시 대화 전체에서 별도 목적을 재해석하지 않는다. 기존 objective/guidanceObjective가 제공한 최신 목적과 완전성 정보를 사용한다. 목적이 잘렸거나 모호하면 공통 baseline으로 축소한다. 함수명·파일명만으로 적용할 엔진 버전을 추정하지 않는다.

## 6. Unreal 패턴 지식의 내용과 단일 정본

### 6.1 패턴 카드 형식

새 문서 docs/model-guidance/unreal-patterns.md를 추가하고 기존 catalog.json에서 절과 안정적인 patternId를 등록한다. 본문은 해당 Markdown이 정본이다. catalog에는 선택에 필요한 ID·분류·참조 절·조건 코드·적용성 metadata만 둔다. 별도 JSON에 같은 설명 문장을 손으로 복사하지 않는다.

카드마다 다음 필드를 구분해 작성한다.

- 해결하는 문제 / 적용을 뒷받침할 관찰 / 비적용 조건.
- 가장 작은 대안과 기존 소유자를 유지할 조건.
- 상태의 정본, 정의/실행/표시 구분, 의존 방향.
- 생성/설정/등록/호출/중복/부분 실패/해제/재활성화의 책임.
- 필요한 엔진 기능·플러그인·버전 계약과 아직 확인되지 않은 부분.
- Blueprint/Editor/자산 소비자 영향, 직렬화/로드/복제 영향.
- 도입 비용, 검증 방법, 공식 출처.

일반 개념의 유효성과 특정 API의 검증 버전은 별도 metadata로 둔다. Unreal 5.8 문서를 읽었다고 5.7.4의 세부 API·수명 동작까지 검증된 것으로 기록하지 않는다.

### 6.2 최초 카드 집합

| patternId | 고려할 실제 조건 | 유지/비적용 조건과 필수 수명 연결 |
| --- | --- | --- |
| ue.component-composition | Actor에 붙는 독립 기능이 여러 Actor에서 조합되거나 독자적 상태/책임을 가짐 | 단순 계산 함수·긴 파일이라는 이유만으로 분리하지 않음. 소유 Actor, 생성/등록, 활성/비활성, EndPlay, delegate/timer 해제 |
| ue.independent-actor | 독립 위치·충돌·스폰/파괴·식별·월드 상호작용이 필요 | 관리 편의를 위해 모든 서비스를 Actor화하지 않음. spawn 실패, 참조 전달, level unload, destroy 뒤 늦은 결과 |
| ue.scoped-subsystem | 엔진/게임 인스턴스/월드/로컬 플레이어 범위의 서비스가 실제로 필요 | GameMode·GameState·PlayerState·기존 서비스가 맞으면 재사용. subsystem 종류별 생성/해제, PIE/world 분리, 월드 간 참조 금지 |
| ue.definition-instance | 공유 설정·콘텐츠 정의와 개체별 가변 상태가 혼재 | 모든 상수를 Data Asset화하지 않음. 정의를 읽기 전용 계약으로 사용, runtime 상태는 별도 인스턴스, 기본값 변경/로드/리셋 |
| ue.data-fragments | 종류마다 다른 설정 조합이 반복되고 확장이 필요 | 단순 struct 하나면 충분할 수 있음. fragment는 우선 정의 데이터, 가변 상태 소유자를 별도로 명시 |
| ue.asset-manager | 콘텐츠 식별·지연 로드·의존성·cook 관리가 필요 | 무조건 Asset Manager 도입 금지. soft reference, 요청 핸들, 취소, late completion, unload, cook 포함 |
| ue.interface-capability | 여러 구현을 같은 능력 계약으로 다뤄야 함 | 소유권/호출 순서를 interface가 자동 해결하지 않음. Blueprint 구현 소비자, 유효 대상, 실패/미구현 처리 |
| ue.event-observer | 규칙 결과를 UI·사운드·다른 기능이 관찰 | 필수 처리 순서/응답을 무조건 broadcast로 바꾸지 않음. 등록 주체, 해제, 중복 구독, 재진입, 종료 순서 |
| ue.strategy-policy | 실제로 교체되는 규칙이 반복됨 | 단순 분기까지 클래스로 만들지 않음. 정책 소유자, 입력/실패 계약, UObject 필요 여부 |
| ue.state-machine | 허용 동작·진입/종료·취소가 상태에 따라 달라짐 | 모든 독립 상태를 거대 enum에 합치지 않음. 전이 확정자, entry/exit 중복, StateTree 필요성/의존성 |
| ue.gameplay-tags | 조합 가능한 의미 분류/질의가 필요 | 수치 상태나 단독 확정 상태를 tag로 중복 저장하지 않음. 등록 사전, 변경 소유자, 질의 의미 |
| ue.gameplay-ability-system | 능력·효과·속성·예측/취소가 실제로 복잡하거나 이미 GAS 사용 | 작은 쿨다운 하나 때문에 도입하지 않음. ASC 소유자/Avatar, possession/respawn, effect/ability 수명, 태스크 종료 |
| ue.modular-features | 기능 단위 활성화/비활성화와 콘텐츠 조립이 필요 | 일반 코드 분리를 플러그인화와 동일시하지 않음. 등록/해제, 의존 플러그인, 잔류 component/subscription 정리 |
| ue.ui-viewmodel | 표시 변환·관찰 상태와 게임 규칙이 혼재 | 게임 상태 확정자를 UI로 옮기지 않음. local player 범위, widget attach/detach, stale binding, MVVM 플러그인 적용성 |
| ue.object-pooling | 측정된 생성/폐기 비용이 의미 있음 | 추측 성능 개선 금지. reset/반환, timer/delegate/async 세대, 재사용 식별자, Actor 파괴와 반환 구분 |
| ue.save-runtime-boundary | 저장 형식과 현재 월드 객체 참조가 혼재 | SaveGame을 live state manager로 사용하지 않음. snapshot·버전·로드 완료·복원 실패·월드 객체 재바인딩 |

네트워크 지침은 유지한다. 구조 카드에서 authority·replication을 무시하지 않되 기존 unreal-networking 계약의 설명을 다시 소유하지 않고 참조한다. 사용자가 네트워크 쪽은 충분하다고 했으므로 이번 의미 평가의 중심은 책임·조합·수명·데이터 경계다.

### 6.3 생성·인덱싱 경로

1. lmstudio-context-compactor-plugin/scripts/build-design-guidance.cjs를 확장해 같은 catalog/Markdown에서 배포용 TypeScript와 기계 판독용 패턴 export를 생성한다. 후자는 docs/model-guidance/generated-pattern-catalog.json으로 정한다. 생성 파일 직접 편집 금지.
2. catalog schema 변경이 필요하면 v3로 올리고 v1/v2 읽기 호환을 유지한다. 패턴 ID 중복, 없는 절 참조, 잘못된 applicability, 본문/hash 불일치는 build/check에서 실패한다.
3. contentHash는 해당 카드의 의미 본문 + 관련 선택/적용성 metadata의 정규형에서 계산한다. catalogHash는 정렬된 카드 ID/hash와 schema 버전에서 계산한다. OS 경로·mtime·절대 설치 위치를 hash에 섞지 않는다.
4. collect_project_guidelines.py에 생성 export 입력을 선택적으로 받아 canonical 패턴 행을 기존 raw_guidelines.jsonl에 함께 쓰게 한다. 프로젝트 지침을 덮어쓰는 별도 후처리 append를 만들지 않는다. 일반 지침 수집은 기존대로 유지한다.
5. 패턴 행은 source=project_guideline을 유지하고 metadata.guidanceKind=architecture_pattern, patternId/contentHash/catalogHash/적용성/출처를 붙인다. infer_doc_type에서 해당 행만 architecture_pattern으로 분류한다. 이것은 일반 참고 자료이며 프로젝트 구조 관찰이 아니다.
6. rag_build_metadata 및 projection, direct_rag_evidence의 compact refs까지 필요한 식별·해시 metadata가 도달하는지 end-to-end 테스트한다. identity/hash는 문자열 자르기로 손상시키지 않는다. 공간이 없으면 카드 단위로 생략하고 omitted를 기록한다.
7. 기존 engine collection/staging 경로에 export 수집을 연결한다. 기존 raw_guidelines가 있다는 이유로 새 catalog 변경이 영원히 반영되지 않는 캐시 조건을 수정한다. 내용 hash가 동일하면 재생성·재인덱싱을 생략할 수 있다. 관련 변경만 기존 generation swap에 반영한다.
8. 기존 RAW_INPUT_FILES의 raw_guidelines를 재사용한다. 별도 raw_architecture_advice, 새 런타임 DB, 별도 인덱스 세대 체계를 만들지 않는다.
9. RAG에는 이미 design/planning/prototype_component/prototype_subsystem 모드가 있으므로 새 architecture 모드는 추가하지 않는다. 기존 mode + doc_type 필터를 사용한다. 도구 이름·권한은 유지한다.
10. 기존 인덱스에 패턴이 없으면 no_pattern_corpus/unknown으로 처리한다. 자동 refresh를 호출하지 않는다. 버전이 맞는 번들 지침으로 축소한다.
11. engine/project corpus 적합성 검사를 일반 패턴 때문에 우회하지 않는다. 현재 shard의 검색 계약이 적용되지 않으면 RAG 보충은 unavailable로 끝낸다. 패턴 자료를 engine source로 세어 engineEvidence=true로 만들지 않는다.
12. catalog와 RAG의 pattern hash가 다르면 둘을 같은 근거로 합치지 않는다. 내장 카드와 일치하지 않는 검색 카드는 unverified/다른 버전 참고로 표시하고 해당 카드에 의존한 판단만 재평가한다. 프로젝트 소스가 바뀌었다고 가정하지 않는다.

### 6.4 export와 corpus 상태의 정확한 형식

export 경로는 docs/model-guidance/generated-pattern-catalog.json으로 고정한다. 생성된 배포 자산이며 Python 수집 단계는 이 파일을 읽는다. Python-only 환경에서 새 Node 실행을 요구하지 않는다.

~~~json
{
  "schemaVersion": 1,
  "catalogHash": "sha256-hex",
  "patterns": [{
    "patternId": "ue.definition-instance",
    "sectionId": "unreal-patterns/definition-instance",
    "contentHash": "sha256-hex",
    "title": "정의 데이터와 실행 인스턴스",
    "text": "해당 정본 Markdown 절의 본문",
    "applicability": { "concept": "unreal", "validatedApiVersions": [] },
    "selection": { "facets": ["shared_definition", "mutable_instance_state"] },
    "sources": [{ "url": "official-url", "verifiedFor": "general_concept" }]
  }]
}
~~~

위 hash/url 문자열은 형식 설명용 placeholder다. 실제 생성물은 유효한 SHA-256과 공식 URL이어야 한다. hash 입력은 UTF-8/BOM 없음, LF, Unicode 문자열 그대로, 객체 키 사전순, 불필요 공백 없는 JSON으로 정규화한다. patterns는 patternId 순, set 의미의 facets/version/sources는 정의한 키 순으로 정렬한다. 의미상 순서가 있는 본문/옵션 배열은 임의 정렬하지 않는다. timestamp·절대 경로·출력 위치·catalogHash/contentHash 자기 자신은 각 hash 입력에서 제외한다. 한국어·줄바꿈·빈 version 목록이 포함된 고정 test vector를 TS 생성기/Python 검증기가 함께 사용한다.

각 canonical 수집 row ID는 sha256("architecture-pattern:" + patternId + ":" + contentHash)로 정한다. export 한 개 안에서 patternId는 하나의 버전만 허용한다. 일반 guideline로 같은 정본 절이 중복 수집되지 않게 canonical 경로/ID로 제외하며, 내용이 비슷한 사용자의 별도 지침을 임의 삭제하지 않는다. 여러 export를 임의 합치는 입력은 첫 구현에서 지원하지 않는다.

guidelines root가 없어도 유효한 export가 있으면 export-only 수집을 허용한다. root만 있으면 기존 수집, 둘 다 없으면 명시적 입력 없음으로 처리한다. engine collection의 기존 "디렉터리가 있을 때만 수집" 조건도 이 계약에 맞춘다. 수집 실패 시 부분 raw_guidelines를 정상 입력으로 남기지 않도록 임시 파일 검증 후 원자적으로 교체한다. 기존 build/refresh의 작업 잠금·generation swap을 재사용한다.

기존 rag_build_outputs.write_manifest의 corpusCapabilities에 선택 필드 architecturePatterns를 추가한다.

~~~json
{
  "architecturePatterns": {
    "schemaVersion": 1,
    "count": 16,
    "catalogHash": "sha256-hex"
  }
}
~~~

count는 chunk 수가 아니라 실제 인덱스에 포함된 고유 patternId/contentHash 수다. 본문 chunk 분할로 개수가 증가하지 않는다. count=0이면 catalogHash는 생략할 수 있다. 부분 수집이면 완전한 catalogHash를 허위로 쓰지 말고 build 실패 또는 명시적 부분 상태로 처리한다. 첫 구현은 canonical export의 전부 또는 0을 원칙으로 한다.

writer는 기존 build pipeline에서 수집한 count/hash를 받고, reader는 direct_rag_corpus.py가 같은 expected_generation에서 읽는다. 필드 부재는 unknown, 명시 count=0만 no_pattern_corpus, count>0이며 검색 결과 0은 no_match다. 기존 engineEvidence/projectEvidence의 count에는 패턴 카드를 추가하지 않는다. 이 metadata 때문에 별도 health/capabilities preflight 호출을 강제하지 않는다. 기존 검색 결과 envelope에 이미 제공되는 corpus metadata에 함께 실어 읽을 수 있게 한다.

새 capability metadata를 추가하는 경우에만 기존 manifest/schema/generator/check 경로로 전파한다. 별도 hand-written TypeScript/Python 도구 스키마를 만들지 않는다. old provider의 schema에 없는 인자를 하네스가 자동 주입하지 않는다.

공식 참고 출처: [Components](https://dev.epicgames.com/documentation/en-us/unreal-engine/basic-components-in-unreal-engine), [Subsystems](https://dev.epicgames.com/documentation/en-us/unreal-engine/programming-subsystems-in-unreal-engine), [Data Assets](https://dev.epicgames.com/documentation/en-us/unreal-engine/data-assets-in-unreal-engine), [Asset Management](https://dev.epicgames.com/documentation/en-us/unreal-engine/asset-management-in-unreal-engine), [Lyra Inventory/Equipment](https://dev.epicgames.com/documentation/en-us/unreal-engine/lyra-inventory-and-equipment-in-unreal-engine), [GAS](https://dev.epicgames.com/documentation/en-us/unreal-engine/understanding-the-unreal-engine-gameplay-ability-system), [Game Features](https://dev.epicgames.com/documentation/en-us/unreal-engine/game-features-and-modular-gameplay-in-unreal-engine), [MVVM](https://dev.epicgames.com/documentation/en-us/unreal-engine/umg-viewmodel-for-unreal-engine). 각 카드의 구체적 API를 작성할 때 대상 엔진 버전의 공식 문서/소스를 재확인한다.

## 7. 실행 연결 순서와 commit 경계

### 7.1 초기화

prediction-loop의 기존 executionId, scope, tool session, registry, WorkingContext, EvidenceManager, TaskObjective를 그대로 사용한다. 새 독립 세션은 만들지 않는다.

1. 기존 working window restore 및 returned sources 재구축을 완료한다.
2. NotePersistence.restore로 기존 우선순위에 따라 note를 한 번 결정한다.
3. 구조 기능이 켜진 경우만 ArchitectureState를 생성한다. restore된 v2 구조 판단은 historical/unknown부터 시작한다. 기존 note의 자유 서술을 분석해 자동으로 구조 사실로 바꾸지 않는다.
4. 원장의 현재 snapshot에서 읽기용 구조 view를 만든다. 선택되지 않은 전체 저장소를 다시 읽지 않는다.
5. 현재 objective와 scope로 binding을 호스트에서 만든다. executionId·revision·lease·receipt는 과거 메모에서 가져오지 않는다.

### 7.2 입력 생성

prepareRoundInput 호출 전 구조 snapshot을 순수 데이터로 전달한다. 새 architecture projection/policy/render 함수는 I/O하거나 ArchitectureState를 변경하지 않는다. 기존 prepareRoundInput에는 토큰 측정과 semantic summary 모델 호출이 있으므로 그 전체를 순수 함수라고 가정하지 않는다. 기존 동작은 유지하며 architecture 때문에 새로운 I/O를 추가하지 않는다. 새 후보의 순수 변환을 반복해도 외부 부작용이 없어야 한다.

ArchitecturePolicy는 depth/reason/card 후보만 반환한다. 기존 guidance 후보 및 runtime facts/note 예산과 합성한 뒤 정확한 입력 측정을 거친다. admission 결과를 prediction-loop가 받아 deferred_budget 등의 관측 결과만 state/telemetry에 반영한다. “정책이 넣고 싶음”과 “실제 모델 입력에 포함됨”을 별도 필드로 기록한다.

### 7.3 도구 결과와 판단 반영

기존 runOneToolRound → captured → returned evidence 수집 → RoundTransaction 흐름에 다음 순서를 연결한다.

~~~text
captured results
  → 기존 request/result pairing 및 capability 확인
  → EvidenceManager의 반환 근거 확정
  → 변경·삭제·충돌·순서 불명 관찰을 기존 원장에 반영
  → 영향받는 구조 판단의 applicability 무효화
  → RoundTransaction.planningCommitted 검사
  → 유효한 모델 footer만 parse/검증
  → footer가 참조한 source/pattern과 입력 시점 revision 재확인
  → ArchitectureState reducer commit 또는 reject/defer
  → 전체 note snapshot을 기존 replaceActiveNote/NotePersistence.mirror 경로에 전달
~~~

먼저 결과를 반영하고 나서 모델 판단을 commit해야 한다. 같은 round에서 파일 변경과 예전 해시의 구조 판단이 함께 반환된 경우, 예전 판단이 마지막으로 적용되어 최신 상태를 덮는 순서를 허용하지 않는다.

의존 대상이 그대로인 판단은 commit 가능하다. 다른 파일이 바뀌었다는 이유만으로 모든 판단을 거절하지 않는다. 같은 배치에서 같은 대상의 read/write 순서를 기존 원장이 확정할 수 없으면 order_unknown으로 남기고 current로 승격하지 않는다.

### 7.4 판단 없는 응답·잘못된 footer

- 정상 모델 응답이 구조 footer를 만들지 않아도 사용자의 작업은 계속한다. 구조 판단 기록은 absent/unevaluated로 표시한다.
- 위의 absent/unevaluated는 이전 판단도 없는 경우다. 이전 판단이 있으면 현재 round의 not_emitted만 기록하고 기존 판단과 현재 적용성을 유지한다.
- JSON이 잘렸거나 알 수 없는 필드·발급하지 않은 refs·초과 길이가 있으면 해당 구조 patch만 거절하고 이전 유효 state를 보존한다.
- 이것을 고치려고 추가 LLM 호출·도구 호출·read recovery를 시작하지 않는다.
- 기존 내부 footer 제거·프로토콜 출력 억제 경로가 v2 marker와 중간 조각도 처리해야 한다. 유효하지 않은 footer가 사용자 보고에 노출되면 안 된다.
- 외부 파일·RAG 내용에 같은 marker가 등장해도 모델 자신의 완결된 assistant footer와 구분한다. tool text 또는 quote/code fence에서 읽은 marker는 state update가 아니다.

parser는 generalPatchResult와 architecturePatchResult를 나누어 반환한다. 전체 JSON 파싱 실패는 두 모델 patch를 모두 거절하되 호스트의 근거 무효화 전이는 유지한다. JSON은 정상이고 architecture만 schema 위반이면 정상 general note patch는 commit할 수 있다. 이때 보존할 구조 상태는 round 시작의 옛 상태가 아니라, 이미 반환된 소스 변경으로 stale/unknown을 반영한 현재 상태다.

### 7.5 종료

실행 취소/성공/오류 모두 기존 terminal note persistence와 sealAndRelease를 사용한다. ArchitectureState.close는 메모리 갱신을 금지하는 표시만 할 수 있고 I/O·lease 해제는 하지 않는다. finally에서 별도 저장 타이머·Promise.then을 남기지 않는다.

미완료 모델 판단은 저장하지 않는다. 이미 정상 반환된 근거는 기존 transaction 계약에 따라 보존할 수 있다. 실행 중인 쓰기 효과는 결과가 관찰되지 않았다면 outcome_unknown이다. 취소를 “실행 안 됨”이나 “롤백 성공”으로 바꾸지 않는다.

## 8. 수명·무효화·경합 매트릭스

| 이벤트 | 관찰/근거 처리 | 구조 판단 처리 | 재연결·자원 처리 |
| --- | --- | --- | --- |
| 같은 실행, 같은 근거 반복 전달 | 기존 ledger dedupe/restore 정책 | 새 판단 revision을 무의미하게 올리지 않음 | 힌트·검색 제안 반복 발행 없음 |
| 의존 파일 변경 성공 관찰 | 해당 source version 갱신 | 관련 selected/proposed를 stale로, 선택 이력은 남김 | 새 소스가 실제 반환되기 전 현재 근거라고 표시하지 않음 |
| 변경 요청 거부, 효과 not_applied 확인 | 기존 근거 유지 | 소스 변경으로 무효화하지 않음 | 승인/권한 추정 없음 |
| 쓰기 결과 미관찰·부분 성공·충돌 | effect unknown/부분 결과 유지 | 영향 대상 applicability unknown 또는 stale | 자동 같은 쓰기 재실행 금지 |
| 같은 배치 read/write 선후 불명 | order_unknown 유지 | 근거를 newest로 임의 정렬하지 않음 | 기존 recheck 경로만 사용 |
| RAG 세대 전환 | 기존 RAG_GENERATION_TRANSITION 계약 | 실패한 조회를 새로운 패턴 근거로 채택하지 않음 | 기존 retry 정책/예산만. 자동 refresh 금지 |
| RAG 완료 전 취소 | 기존 round의 signal/closed 경계 | 늦은 결과로 state·note·입력 갱신 금지 | 소유 round가 종료, 새 background task 없음 |
| 모델 응답 중 취소 | 반환 근거는 durable 가능 | planningCommitted=false인 footer 거절 | 취소 이전의 유효 판단만 저장 |
| 추가 사용자 입력·목표 변경 | 기존 objective continuity 재평가 | 다른 목적이면 재사용 불가, 같은 목적 연속성이 확인되면 historical 복원 | 모델이 “계속”이라고 썼다는 이유로 임의 목적 바인딩 금지 |
| 프로젝트 A→B | 기존 target binding 검증 | A 판단을 B 입력에서 제외 | 공유 activeProject를 구조 정책이 변경하지 않음 |
| .uproject/engine identity 변경 | 적용 엔진 관찰 갱신 | 엔진 의존 카드/판단만 mismatch 또는 unknown | 일반 개념 카드와 엔진 API 근거를 구분 |
| plugin/module/asset 의존성 변경 | 실제 관찰된 설정/자산 버전 반영 | GAS·MVVM 등 관련 가정 재확인 | 플러그인을 자동 설치/활성화하지 않음 |
| catalog 내용 변경 | 카드 hash 새 버전 | 이전 hash에 의존한 판단을 재평가 대상으로 표시 | 같은 ID라고 다른 내용 조용히 합치지 않음 |
| 입력에서 소스 본문 퇴거 | availability archived/unavailable | 판단 이력은 유지 가능, current source를 확인했다는 표현 금지 | 필요한 범위만 기존 restore를 사용 |
| compaction·summary 생성 | 원장/refs/window 경계 유지 | typed 판단이 요약문 재해석으로 selected가 되지 않음 | optional 상태도 동일 총예산 내에서 압축 |
| 같은 대화 다음 실행 | refs/scopes를 기존 note reconciliation으로 확인 | historical, 같은 observed version 여부를 별도 표시 | executionId/round/lease/approval 재사용 금지 |
| fork 또는 lineage 분기 | 기존 parent/prefix 검증을 통과한 근거만 읽기 가능 | 부모 판단은 역사 참고만, 새 실행에서 적용성 재평가 | 형제 branch의 mutable 상태 공유 없음 |
| 오래된 창·손상된 note | 기존 검증·fallback 우선순위 | v2 부분만 손상되면 구조 판단 제외, 정상 근거 보존 | 새 빈 구조 상태로 계속, 복원 loop 없음 |
| budget 부족 | 현재 evidence/최소 read reserve 우선 | deferred_budget; 평가 완료로 바꾸지 않음 | 카드 때문에 추가 압축/도구/모델 호출 강제 금지 |
| terminal seal 실패 | 기존 에러 우선순위 유지 | 저장됐다고 보고하지 않음 | 기존 release는 정확히 한 번, 재종료 콜백 없음 |
| 작업공간 외부 변경 미관찰 | 새 hash를 안 읽었으므로 알 수 없음 | same_observed_version은 last observed라는 뜻만 | 매 round 디스크 재확인을 도입하지 않음 |

scopeRevision은 새 도메인 상태의 늦은 갱신을 구분하는 실행 내 카운터다. 기존 tool binding을 대체하는 새 전역 epoch가 아니다. 도구 요청/결과의 진짜 프로젝트·엔진·세대 검사는 기존 경계에서 계속 수행한다. 새 reducer는 예상 binding/revision이 일치할 때만 판단을 반영한다. 실행 종료 후에는 일치하더라도 거절한다.

## 9. 컴팩터·note 확장과 호환성

### 9.1 v2 계약

현재 continuity-model-notes.js는 v1 marker, 최대 1500자/4항목, exactKeys 검사, decisions/rejectedHypotheses/openQuestions/reviewClaims를 사용한다. 구조화된 여러 필드를 기존 statement에 JSON 문자열로 우겨넣지 않는다.

v2를 명시적으로 추가한다.

- 모델 footer marker: direct-continuity-note-v2. 모델 draft의 최상위 허용 키는 schemaVersion, decisions, rejectedHypotheses, openQuestions, reviewClaims, architecture이며 schemaVersion은 정확히 2다.
- 모델의 architecture는 patch 객체 {upsert?: DraftDecision[], retireIds?: string[]}다. 저장 형식의 architecture는 호스트가 만든 전체 snapshot 배열이다. model draft와 host stored-note schema를 서로 다른 타입/validator로 정의한다.
- upsert의 모델 필드: id?, subject, state, currentOwner, requiredLifetime, sharingScope, definitionVsRuntime, options, chosen?, connectionPlan, refs, patternRefs, counterEvidence, unknowns, supersedes?. refs/patternRefs는 §4.2에서 실제 노출한 handle이다.
- source hashes·resolved refs·binding·revision·applicability는 호스트가 계산한 저장 형식에만 존재한다.
- 원본 footer 길이 상한 7000자, 저장 note 전체 JSON 상한 6000자, 전체 판단 6항목 이하. 기존 일반 판단은 4항목 이하, 구조 판단은 2항목 이하로 그 안에서 공유한다. 이 수치는 신규 계약값이며 정확 token admission을 대체하지 않는다.
- 구조 판단 하나의 options≤3, evidence refs≤6, pattern refs≤2, counterEvidence≤3, unknowns≤3. text별 상한도 schema 상수로 명시하고 테스트한다. 예: subject/currentOwner 120자, 수명/공유 100자, 정의/실행 180자, option의 candidate 80/reason 160/cost 120자, connectionPlan 480자, counter/unknown 각각 160자.
- 배열이나 전체 크기가 초과되면 잘라서 새로운 의미를 만들지 않는다. patch를 거절하고 이전 유효 판단을 유지한다. 입력 렌더링에서 생략할 때는 생략 수/이유를 표시한다.
- capacity로 유지하지 못한 판단이 있으면 현재 구조 검토 범위가 제한됐다고 telemetry/보고에 표시한다. 모든 구조 문제가 정리됐다는 인증 상태를 만들지 않는다.

메모 최대치를 늘리는 목적은 구조화된 판단을 보존하기 위해서다. 6000자를 매 round 전부 system에 주입하는 것은 금지한다. 입력 예산이 작으면 참조·결정·미확인을 우선하는 축약 view를 기존 optional admission에 넣는다.

selected를 제출할 때 chosen은 options의 후보 중 하나와 일치해야 한다. focused/cross_owner의 후보에는 기존 소유자 유지 대안과 그 수용/거절 이유를 포함한다. proposed/deferred의 chosen 부재는 허용한다. state와 필드의 일관성은 schema/reducer가 검사하되 선택이 설계적으로 옳다는 의미 판정은 모델 평가 영역으로 남긴다. 호스트가 생성한 superseded/tombstone 상태를 모델이 임의로 current로 되돌릴 수 없다.

### 9.2 갱신 의미

v2의 architecture 필드가 없거나 빈 patch 객체이면 구조 변경 없음이다. upsert는 제공된 ID만 갱신하고 생략된 ID는 유지한다. 모델에게 보여 주지 않은 판단을 응답에 없다는 이유로 삭제하지 않는다. retireIds/supersedes는 현재 모델 입력에 ID와 revision이 노출된 항목만 대상으로 할 수 있다. 철회·대체는 영구 삭제와 구분해 bounded tombstone으로 남긴다. 중복 ID·자기 자신 supersede·순환 참조·같은 ID의 upsert/retire 충돌은 거절한다.

전체 clear 모델 명령은 첫 구현에서 지원하지 않는다. 사용자의 목적/프로젝트 변경에 따른 호스트 scope reset으로만 전체 적용 상태를 비운다. 사용자가 메모를 지우라고 명시한 요청은 기존 사용자 목적 처리에서 호스트 이벤트로 수행한다. 이를 위한 새 확인 질문은 추가하지 않는다.

활성 구조 판단은 2개 이하, tombstone은 최대 4개이며 tombstone은 id, 마지막 revision, disposition, supersededBy?, reasonCode만 가진다. tombstone도 전체 저장 note 6000자에 포함하고 모델 입력에 자동 주입하지 않는다. 활성 용량이 찼을 때 새 항목이 명시적으로 기존 항목을 retire/supersede하지 않으면 신규 upsert만 capacity 거절한다. 기존 유효 판단을 조용히 제거하지 않는다. tombstone 초과분은 오래된 순으로 제거하고 omitted count를 기록하며, full history가 보존됐다는 주장은 하지 않는다.

v1 footer는 기존 일반 판단의 의미를 유지한다. v1에 architecture 필드가 없다는 이유로 새 v2 구조 state를 지우지 않는다. v1의 빈 객체는 일반 v1 판단의 clear로만 작동한다. 모델이 architecture:[]를 제출하면 v2 patch schema 위반으로 거절한다. 이 호환 차이는 문서·테스트에 명시한다.

모델이 old id를 재사용해도 현재 입력에서 발급된 revision과 의존성이 맞지 않으면 stale update다. 실제 모델 payload에 revision을 필수로 요구하기보다는 입력 snapshot과 round correlation을 호스트가 보관한다. 한 round에서 받은 patch를 다른 round의 최신 state에 무조건 적용하지 않는다.

### 9.3 저장 주체와 migration

ArchitectureState가 실행 중 판단의 정본이다. general note update와 구조 state update를 합치는 얇은 함수가 기존 replaceActiveNote를 통해 한 전체 note snapshot을 만든다. NotePersistence.mirror만 WorkingContext.note를 갱신한다. 구조 reducer에서 WorkingContext.note를 직접 수정하지 않는다.

P4에서 현재의 모든 note 갱신 경로를 연결한다: prediction-loop의 실행 중 reconcileStoredNote(기준 :433), 모델 footer resolveNoteUpdate(:905), round-input의 updateSemanticContext(:348), semantic-handoff의 activeNote/workingContext.note 직접 대입(:184–185). semantic summary는 일반 note의 갱신 제안만 반환하도록 바꾸고 WorkingContext.note 직접 쓰기를 제거한다. 일반 note reconciliation/summary가 architecture 영역을 새로 생성하거나 삭제하지 못하게 한다.

활성 모드의 구조 영역은 reducer snapshot에서만 합성한다. 비활성 모드에서는 유효하게 파싱한 기존 구조 저장 영역을 opaque/pass-through로 보존하고 입력에는 넣지 않는다. 호스트의 소스 무효화/복원/종료 전이는 모델 footer commit 여부와 별개다. 모델이 중단되더라도 이미 반환된 변경 결과에 따른 stale 전이는 저장해야 한다.

저장용 v2 note의 최상위 형태는 schemaVersion=2, 기존 scope/일반 판단 배열, architecture(호스트 snapshot 배열), architectureTombstones(호스트 배열), architectureOmittedCount(호스트 숫자)로 고정한다. executionId·round AbortSignal·live ref 발급표·provider session·receipt·쓰기 승인·lease는 직렬화하지 않는다. 저장된 판단 revision은 추적 이력일 뿐이며 새 실행의 reducer counter/입력 revision은 hydrate 시 호스트가 새로 발급한다.

refs를 순회하는 기존 함수도 반드시 확장한다: continuity-model-notes의 attachScope/resolveNoteUpdate/reconcileStoredNote/render/withdraw 흐름, WorkingContext의 note ref 수집/verifiedNoteRefs/restore note 검사, semantic-handoff의 summaryRefs, note telemetry. typed note를 순회하는 작은 공통 visitor로 현재 허용 필드를 명시하고, 여기저기 배열 이름 목록을 복사하지 않는다. unknown field나 자유 서술에서 ref를 정규식으로 추출해 신뢰하지 않는다.

현재 continuity-model-notes.test.js는 src의 CommonJS 모듈을 직접 require한다. 새 TS 타입 파일을 source JS에서 런타임 require하면 build 전 테스트가 깨질 수 있다. 공통 v2 bounds/validator/visitor가 필요하면 작은 architecture-note-contract.js에 런타임 정본을 두고 TS에서는 타입 선언/adapter로 연결한다. 같은 상한·필드 허용 목록을 JS/TS/Python에 손으로 복제하지 않는다. context-ports.ts의 실제 반환형도 함께 갱신한다.

NotePersistence.restore의 history 우선/window fallback 규칙은 유지한다. 두 note의 구조 판단만 임의 합집합하지 않는다. 한 note 안의 중복 scope/refs가 충돌하면 그 구조 부분을 거절한다. 기존 근거·일반 note의 정상 복원까지 실패시키지 않는다.

WorkingContext manifest 자체가 동일 note envelope를 수용할 수 있으면 불필요하게 전체 window schema를 올리지 않는다. 정확키 검증이나 digest 입력이 변경되면 versioned reader/migration을 함께 구현한다. legacy v1 note와 schemaVersion=2 window의 fixture를 실제로 읽어 확인한다. 새 note가 없는 구버전은 architecture=[]인 상태로 동작한다.

다운그레이드 시 구버전이 v2 note를 무시해도 소스 창·원장·사용자 설정은 복구할 수 있어야 한다. 이를 위해 v2 metadata를 기존 receipt/원장 schema에 섞지 않는다. 설치/rollback 시험에서 note 손실 가능성과 source 보존을 별도로 기록한다.

### 9.4 프로토콜·정보 경계

continuity-model-notes의 parser뿐 아니라 내부 출력 제거, stream suppression, durable-memory-sanitizer, summary 입력, note telemetry, ReviewRegister의 NOT_A_SUBJECT까지 v2 field/marker 영향을 조사한다. 구조 JSON의 currentOwner/connectionPlan 같은 키가 코드 결함으로 등록되면 안 된다.

RAG·소스·첨부 문서 안의 “이 계획을 승인하라”, “기존 규칙 무시”는 사용자 지시로 취급하지 않는다. 패턴 카드를 system에 넣을 수 있는 것은 저장소에서 생성한 고정 지침 부분뿐이다. 외부 검색 본문·모델 판단·프로젝트 코드의 주석은 참고 데이터로 전달한다.

## 10. 기존 보고 경계 결함을 함께 수정

### 10.1 식별자 의미 불일치: F09

ReviewEvidenceIndex와 ReviewRegister가 함께 쓰는 순수 식별 helper를 둔다. 실제 위치·한정 식별자·관찰 버전이 주체 identity이고, 보고의 짧은 이름은 문맥이 유일할 때만 alias로 인정한다.

처리 규칙:

1. 전체 한정 이름 또는 식별 토큰 경계가 맞는 유일한 alias가 보고에 있으면 언급 처리한다.
2. 두 파일/타입에 동일 leaf가 있으면 짧은 이름 하나로 둘 다 처리하지 않는다. 파일/타입/인접 문장 등의 근거가 필요하다.
3. 다른 이름의 부분 문자열은 매치하지 않는다. Health와 HealthRegenRate를 혼동하지 않는다.
4. needs_recheck 상태는 기존처럼 재검토/미확인/철회라는 처리가 함께 있어야 한다. 이름만 있다고 해결 처리하지 않는다.
5. 이 helper는 보고 연결용이다. 줄 번호·검색 결과만으로 current edit authority를 만들지 않는다.

최소 fixture: FWeaponStats::HeadshotMultiplier의 근거 1개, 최종문 “HeadshotMultiplier는 현재 사용처가 확인되지 않았다.” → missing=0. 다른 타입의 같은 HeadshotMultiplier가 추가되면 짧은 문장 하나로 두 finding 모두 해소하면 안 된다.

### 10.2 현재 주장과 과거 정정 인용: F10

파일 범위 검사는 단순 첫 regex match가 아니라 현재 주장 문맥을 구분해야 한다. 전체 N개라는 표현이 인용·철회·정정의 대상인지, 실제 지금 주장인지 분리한다. 모든 인용문을 무조건 무시하는 구현도 금지한다.

최소 fixture:

- 검색 19개, 본문 15개, “전체 19개 파일을 확인했다.” → 경고 유지.
- 같은 근거, “앞서 ‘전체 19개 파일 확인’이라고 쓴 표현은 철회한다. 검색 목록은 19개, 본문은 15개를 읽었다.” → 기존 과장에 대한 새 경고 없음.
- 같은 근거, “정정한다. 그래도 전체 19개 파일을 검토한 것은 맞다.” → 경고 유지.
- 숫자 없는 “확인했다”나 범위가 모호한 문장 → 임의 전체 범위를 확정하지 않음.

가능하면 report-scope가 scope gap과 해결 disposition을 구조화해 terminal notice에 전달한다. 다만 모델이 corrected=true라고 썼다는 이유로 경고를 지우지 않는다. 실제 전달 범위와 최종 문장의 현재 주장에 연결해야 한다. 부정·정정 문맥을 판별할 수 없으면 불확실 표시를 하고 자동 보완 반복을 만들지 않는다.

report-review telemetry에 missing findings, engine contract gaps, scope gaps를 같은 실행의 별도 원인 목록으로 기록한다. “추가 검토 1회”만 세면 오탐·실제 누락의 원인을 구분할 수 없다. 기존 maxAttempts=1, tools=0 계약은 유지한다.

## 11. SOL의 작업 순서와 파일별 변경 지시

각 단계는 독립적으로 검토 가능한 변경 묶음으로 작성한다. 자동 commit/push/merge/설치는 이 문서만으로 수행하지 않는다. 사용자가 구현·설치까지 별도로 지시한 경우 그 범위를 따른다. 사용자에게 이미 허가된 보통 수정마다 다시 확인을 요구하지 않는다.

| 단계 | 변경 파일/소유자 | 구현할 내용 | 종료 증거 |
| --- | --- | --- | --- |
| P0 기준과 계약 정리 | README, 기존 관련 tests, src/config-defaults/round-input/evidence-manager 읽기 | F03–F05 문서 drift 정정. 실제 계약에 대한 baseline. F09/F10 최소 실패 fixture 추가 | 문서/코드 대응표, 기존 동작 대조군, 재현되는 두 결함 |
| P1 데이터 계약과 순수 정책 | architecture-contracts/observation/policy/state 신규 | 본문 없는 refs, state/applicability/availability 분리, 순수 selection/reducer, bounds | A01–A12 및 L01–L07 단위 테스트 |
| P2 기본 지침과 패턴 정본 | core.md, unreal.md, design.md, unreal-patterns.md, catalog.json, build-design-guidance.cjs, generated bundle/export | 자동 baseline, 16개 카드, generator/hash/check. “기존 소유자 유지”의 전제 수정 | C01–C07, 생성물 일치, 실제 토큰/section 상한 |
| P3 기존 라운드에 연결 | prediction-loop.ts, round-input.ts, execution-contracts.ts, EvidenceManager의 읽기용 adapter | 기존 objective/기능 설정 조건, observation→invalidation→commit 순서, pure preparation, telemetry | A13–A17, L08–L13, 호출 수/guard 불변 |
| P4 note·컴팩터 수명 | continuity-model-notes.js, context-ports.ts, note-persistence.ts, working-context.js, semantic-handoff.ts, protocol/stream/sanitizer 관련 소유자 | note v2 patch, host binding, bounds, v1 migration, 직접 note 쓰기 제거, 입력 projection 분리, mirror·terminal restore | N01–N20, cancellation/seal/read-ledger 회귀 |
| P5 RAG 카드 전달 | collect_project_guidelines.py, direct_rag_engine_collection.py, rag_build_classification.py, rag_build_metadata/projection, rag_build_outputs.py, direct_rag_corpus.py, direct_rag_evidence.py | 같은 정본 수집, metadata hash 보존, canonical row dedupe, generation/버전/미설치 fallback | R01–R13, 프로젝트/엔진 바인딩과 raw input 회귀 |
| P6 보고 결함 | review-register.ts, review-evidence.ts, quote-anchoring.ts, report-scope.ts, delivery-controller.ts, report-review telemetry | F09/F10 및 alias 충돌·정정 문맥 처리 | Q01–Q07, max1/no-tools 회귀 |
| P7 전체 연결 검증 | 신규 통합 fixture + 기존 replay/round-input/working-context/Direct suites | 아래 end-to-end, 비용 계측, off/old-provider/rollback | 결정적 계약 테스트 + 모델 의미 평가 결과를 별도 보고 |

P2와 P5의 export 형식을 P1에서 먼저 확정한다. P5 수집기를 하네스 runtime에 직접 import하지 않는다. 기존 Python build 단계가 생성 export를 읽는다. Python-only 설치/수집을 위해 runtime Node 실행을 요구하지 않는다. 릴리즈 패키지에 생성 export와 카드 metadata가 포함되는지 packaging 검사한다.

F09/F10은 독립적인 작은 변경이므로 P0에서 재현한 후 P6 전에 먼저 고쳐도 된다. 다만 구조 정책의 의미 평가가 보고 오탐에 가려지지 않도록 최종 비교 전에 모두 반영한다.

### 11.1 구현 전 반드시 고정할 함수 계약

~~~ts
// 이름은 바꿀 수 있지만 순수성·입출력 책임은 유지한다.
projectArchitectureEvidence(snapshot, returnedObservations): ArchitectureEvidenceView;
selectArchitectureContext(objectiveView, evidenceView, catalog, admissionState): ArchitectureSelection;
reduceArchitectureState(previous, event): ArchitectureStateSnapshot;
renderArchitectureJudgments(snapshot, availableRefs, limits): RenderedOptionalData;
~~~

정책·reducer 테스트는 fs/network/SDK mock조차 필요 없는 순수 입력으로 수행한다. orchestration integration 테스트에서만 fake SDK/provider를 사용한다. public DTO에 SDK Chat/Tool/WorkingContext class를 직접 넣지 않는다.

### 11.2 구현 중 판단이 필요한 경우

- 이 문서의 테스트가 기존 코드에서도 통과하면 기존 보호의 대조군으로 남긴다. “모든 U0는 수정 전 반드시 실패해야 함” 같은 규칙 때문에 작업을 중단하지 않는다.
- 코드가 이미 해당 기능을 더 정확히 제공하면 재사용하고 차이만 기록한다. 계획에 적힌 신규 파일 수를 맞추기 위해 기존 소유자를 복제하지 않는다.
- 예산을 맞추려 권한·근거·취소 계약을 약화해야 한다면 기능을 deferred로 축소한다. 전체 budget 숫자를 키워 문제를 숨기지 않는다.
- 명세의 권한 경계와 새 요구가 실제로 충돌하는 경우에만 필요한 한 가지 결정을 구체적으로 물어본다. 구현의 통상적 파일 배치·fixture 보강은 자율적으로 해결한다.

## 12. 결정적 테스트 명세

테스트는 구현 내부 문자열을 그대로 비교하는 데 그치지 않고 외부적으로 의미 있는 경계·입력·출력을 검증한다. 다음 파일 이름은 제안이며 기존 같은 책임의 suite에 합쳐도 된다. 각 ID가 어느 테스트에 대응하는지 결과 표에 남긴다. 제품의 tests 자동 검색 경로 안에 파일을 둔다.

### 12.1 자동 정책: architecture-policy.test.cjs

| ID | 입력/조건 | 필수 기대값 |
| --- | --- | --- |
| A01 | Auto + Unreal mutate, “피격 후 체력 회복 추가해줘”, 구조 키워드 없음 | baseline 이상, 사용자에게 별도 검토 요청 요구 없음, 자동 도구/추가 모델 0 |
| A02 | Auto + Unreal review, “이 기능 문제 확인” | baseline, 현재 owner/lifetime/definition-runtime 판단 기준 포함 |
| A03 | off / observeOnly / allowance=0 각각 | disabled, 신규 관찰/카드/구조 footer 유도 없음 |
| A04 | 일반 문서 질문 또는 Unity scope | Unreal 패턴 후보 없음, 기존 실행 유지 |
| A05 | 기존 ActorComponent 존재, 그 안의 작은 계산 오타 | 새 manager/component 강제 없음, keep_current 허용 |
| A06 | Actor 소멸 뒤에도 유지해야 하는 상태라는 사용자 요구 + 관련 source refs | 수명 불일치 단서, focused, 관련 후보 비교, 결함 자동 확정 없음 |
| A07 | 같은 정의 데이터를 공유하는 두 인스턴스의 가변 상태 관찰 | definition-instance 후보, 공유 정의/가변 상태 혼동 금지 |
| A08 | 여러 Actor 공유지만 world 밖 생존 요구 없음 | world scope 고려, 무조건 GameInstanceSubsystem 선택 금지 |
| A09 | 코드 길이만 크고 기능/수명 분리 근거 없음 | 길이만으로 cross_owner/분리 결론 금지 |
| A10 | 일부 소스 범위에 unregister가 보이지 않음 | cleanup_absent 확정 금지, unknown/추가 확인 |
| A11 | Logout/EndPlay/callback cleanup 근거 함께 제공 | counterEvidence에 반영 가능한 refs 유지, 약한 포인터만으로 leak 확정 금지 |
| A12 | 같은 목표/근거/catalog로 동일 selector 반복 | 같은 선택, 순서 안정, 외부 mutation/I/O 없음 |
| A13 | 카드 추가로 기존 최소 read reserve 침범 | 카드 제외/deferred_budget, read reserve와 기존 권한 유지 |
| A14 | 이미 선택된 guidance 뒤 새 패턴 단서, rewrite 사유 없음 | 동적 자료만 갱신 가능, system 불필요 rewrite 없음 |
| A15 | engine 적용성 무효화 / phase 전환 / budget drop | 즉시 필요한 변경 허용, 구체적 rewrite 원인 기록 |
| A16 | 최종화/보고 보완/복구 단계 진입 | 새 구조 검색·모델 단계 없음, 기존 유효 자료의 bounded reuse |
| A17 | 같은 의존성에서 부족 hint를 반복 생성 | 새로운 힌트/재조회 유도 중복 없음; 의존성이 바뀌면 새 판단 가능 |

### 12.2 수명·경합: architecture-lifecycle.test.cjs

| ID | 입력/조건 | 필수 기대값 |
| --- | --- | --- |
| L01 | selected가 A.cpp hash1에 의존, hash2 변경 성공 | state 선택 이력 유지 + applicability stale; unrelated B 판단 유지 |
| L02 | A.cpp 쓰기 거부, not_applied 확인 | 소스 변경으로 무효화하지 않음 |
| L03 | 쓰기 dispatch 후 cancel, 결과 미관찰 | effect unknown; 적용/rollback 성공 추정·자동 재쓰기 없음 |
| L04 | 같은 batch의 A.cpp 읽기/쓰기, 순서 모름 | order_unknown, newest 추정 없음 |
| L05 | source version 같고 본문만 archive로 이동 | availability만 변경, 자동 stale 또는 자동 current-body 주장 금지 |
| L06 | patternId 같고 contentHash 변경 | 해당 패턴 의존 판단만 재평가, 파일 변경으로 오인하지 않음 |
| L07 | 예상 revision보다 오래된 patch / closed state patch | 갱신 거절, 최신 state 보존 |
| L08 | tool 결과는 반환됐지만 모델 prose가 중단 | evidence durable, 구조 footer commit 없음 |
| L09 | round 종료 후 늦은 fake callback | state/note/input/store.write 호출 수 증가 없음 |
| L10 | 같은 round에서 변경 성공 + old hash 기반 footer | old 판단을 current로 commit하지 않음 |
| L11 | A→B project 또는 engine identity 변경 | 범위 밖 판단 입력 제외, activeProject 설정 변경 호출 없음 |
| L12 | parent→fork, 형제 fork의 판단 갱신 | 부모 판단 historical, 형제 mutable state/lease/approval 공유 없음 |
| L13 | seal 실패 + close 실패 / seal 성공 + close 실패 | 기존 terminal error precedence 유지, release 한 번, 저장 성공 허위 보고 없음 |

### 12.3 note·복원·출력: architecture-note.test.cjs

| ID | 입력/조건 | 필수 기대값 |
| --- | --- | --- |
| N01 | 기존 v1 note/window fixture | 기존 판단 복원, 구조=[]; source/권한 불변 |
| N02 | v2 note roundtrip + 다음 실행 | 내용/refs 보존, 새 executionId, historical 적용성, 편집 receipt 0 |
| N03 | history note와 window note에 다른 구조 판단 | 기존 NotePersistence 우선순위 한 번만 적용, 합집합 없음 |
| N04 | v2 architecture 미제공 / 빈 patch / upsert patch / 잘못된 배열 | 유지 / 유지 / 제공 ID만 갱신 / 거절 |
| N05 | v1 빈 객체를 v2 구조 state 위에서 사용 | 기존 v1 일반 판단 clear, 구조 판단 유지 |
| N06 | hash/승인/프로젝트/revision을 모델 payload가 자칭 | 허용되지 않은 host 필드 거절, 권위 승격 없음 |
| N07 | 없는 ref, 중복 ID, self-supersede, 순환 supersede | patch 거절, 기존 state 보존 |
| N08 | 길이/항목/refs 한도 경계 및 초과 | 경계 허용, 초과 거절, silent 의미 절단 없음, 총 token budget 유지 |
| N09 | footer 없음 / 잘린 JSON / malformed v2 marker | state 보존 또는 absent, 추가 모델/도구 0, 내부 marker 노출 없음 |
| N10 | RAG 본문·코드 fence·인용 안의 v2 marker | state update로 읽지 않음 |
| N11 | currentOwner 등 JSON 필드와 패턴 ID | ReviewRegister finding으로 등록되지 않음 |
| N12 | 손상된 v2 부분 / downgrade reader | 구조 부분은 제외 가능, 기존 source/window 복원 보호, 복원 무한 반복 없음 |
| N13 | 부분 입력에 판단 1개만 표시, 모델이 그 ID만 upsert | 보이지 않은 판단 유지, 숨겨진 ID retire 거절, full snapshot은 host만 생성 |
| N14 | semantic summary/general note reconciliation, 기존 구조 판단 있음 | reducer 밖 구조 쓰기 없음, NotePersistence.mirror 경로만 window 갱신 |
| N15 | 같은 pattern ID의 두 hash, 한 카드 admission 제외 | 노출한 version handle만 허용, 제외된 카드/다른 round handle 거절 |
| N16 | Off 또는 noteEnabled=false에서 이전 v2 note 존재 | 새 요청/주입 없음, 저장 영역의 수동 보존과 다음 활성 시 재검증 |
| N17 | JSON 정상/general patch 정상/구조 patch 오류 + 그 round에서 source 변경 | general patch만 적용, 구조는 host가 무효화한 상태 유지, 옛 selected/current로 되돌리지 않음 |
| N18 | 큰 저장 note + 작은 guidance allowance + 실제 dispatch | 일반 note projection과 구조 optional projection 분리, 총 allowance 한 번만 사용, dispatch와 측정/ref 발급표 동일 |
| N19 | 활성 2개에서 신규 판단 추가 / 명시 retire 후 추가 | 전자는 신규 항목 capacity 거절, 후자는 원자적 대체와 bounded tombstone, 기존 항목 silent 삭제 없음 |
| N20 | v2 ref가 archive에 있고 legacy ref가 입력에 있음 | 공통 visitor가 둘을 schema대로 구분, archived ref는 historical/availability 계약만, receipt 복원 없음 |

### 12.4 카탈로그·생성: 기존 guidance tests + pattern-catalog.test.cjs

| ID | 입력/조건 | 필수 기대값 |
| --- | --- | --- |
| C01 | ID 중복/없는 section/잘못된 applicability | generator/check 실패, 부분 생성물 게시 없음 |
| C02 | 같은 내용, 다른 절대 경로/OS newline | 같은 정규 contentHash/catalogHash |
| C03 | 본문 또는 선택/적용성 metadata 변경 | 관련 card hash 변경, generated bundle/export 동기화 |
| C04 | 16개 카드 중 임의 1개 | 적용·비적용·기존 유지·생성/종료·비용·근거 필수 항목 존재 |
| C05 | section 한도가 찬 multiplayer + architecture | 기존 필수 계약 유지, 6절 제한 우회 없음, 상세 카드 생략 사유 |
| C06 | version_unknown 또는 대상과 다른 API 버전 | 일반 개념은 가능, 구체적 엔진 API 검증 완료 주장 금지 |
| C07 | 생성물 수동 변조 / source only 수정 후 --check | 불일치 감지, CI에서 check가 build보다 먼저 차이를 감지할 수 있게 구성 |

### 12.5 RAG: test_architecture_pattern_corpus.py / test_direct_rag_pattern_contract.py

| ID | 입력/조건 | 필수 기대값 |
| --- | --- | --- |
| R01 | 기존 project guidelines + 생성 pattern export | 기존 행 보존, patternId당 canonical 행 하나, raw input 별도 추가 없음 |
| R02 | 동일 export 재수집 | 의미 동일, 중복 row 증가/불필요 generation 변경 없음 |
| R03 | raw_guidelines 존재 상태에서 card hash 변경 | 다음 명시적 정상 build/refresh에 새 카드 반영, stale skip 없음 |
| R04 | builder→index→search→compact refs | pattern ID/hash/applicability/generation 유지, 프로젝트 source_body로 오분류 없음 |
| R05 | 작은 payload cap과 긴 evidence | exact identity/hash 보존 또는 카드 단위 생략, 잘린 hash 사용 없음 |
| R06 | 검색 중 index generation 교체 | 기존 transition error와 bounded retry 계약, 두 세대 결과 혼합 없음 |
| R07 | pattern corpus 없는 구버전 index | 명확한 unavailable/unknown, 자동 refresh·프로젝트 변경 0 |
| R08 | engine binding unknown/mixed versions | 기존 request binding 거부 유지, 폴더 이름으로 버전 확정 금지 |
| R09 | 다른 프로젝트 구조 행 + 일반 패턴 행 | 프로젝트 사실 혼합 금지, 일반 지식을 프로젝트 적용 증거로 사용 금지 |
| R10 | 같은 pattern ID의 bundled hash와 RAG hash 불일치 | 다른 버전으로 표시, 무조건 합치거나 최신으로 추정하지 않음 |
| R11 | old provider/schema + 기존 search 도구 | 지원하지 않는 인자 주입 없음, 신규 필수 preflight 없음, stable tool count/side effect 유지 |
| R12 | manifest 필드 없음 / count=0 / count>0인데 miss | unknown / no_pattern_corpus / no_match 각각 구별 |
| R13 | guidelines root 없음 + export만 있음 / 수집 중 오류 | export-only 수집, 오류 시 정상 raw 파일 보존, pattern이 engine/projectEvidence count를 올리지 않음 |

### 12.6 보고 경계: 기존 review-register/quote/report tests

| ID | 입력/조건 | 필수 기대값 |
| --- | --- | --- |
| Q01 | 한정 이름 1개 + 유일한 leaf 이름을 보고 | missing=0 |
| Q02 | 같은 leaf를 가진 서로 다른 타입/파일 2개 | leaf 하나로 두 finding 해소 금지 |
| Q03 | Health vs HealthRegenRate | 부분 문자열 혼동 없음 |
| Q04 | needs_recheck finding을 이름만 언급 / 미확인 처리 | 전자는 누락 남김, 후자는 처리 인정 |
| Q05 | 19 검색/15 본문인데 전체19 현재 주장 | scope 경고 유지 |
| Q06 | 과거 전체19를 인용해 철회하고 실제15 명시 | 과거 인용 때문에 반복 경고 없음 |
| Q07 | 정정 표현 뒤 전체19를 다시 현재 주장 | 경고 유지, report-review 최대1/tools0 |

## 13. 통합 replay와 모델의 의미 품질 평가

### 13.1 반드시 필요한 결정적 end-to-end replay

가짜 SDK/provider를 사용한 아래 시퀀스를 실제 prediction-loop 경계에서 실행한다. 순수 selector 테스트만으로 완료 처리하지 않는다.

1. 일반 Unreal 구현 요청 → 기존 소스 2개 반환 → baseline/focused 입력 → 구조 footer → commit → compaction → note 저장 → 다음 실행 복원. 목적/근거/권한의 구분 유지.
2. 기존 Component가 존재하는 fixture → 새 manager 추천을 강제하지 않음 → 작은 변경 실행 → 소스 버전 변경 → 이전 판단 stale → 실제 결과를 바탕으로 후속 판단.
3. RAG search 시작 → 취소 → 늦은 provider 결과/모델 footer → state 갱신 없음 → 정상 종료/release. 이미 반환된 peer evidence만 기존 계약대로 보존.
4. A source read + A write의 같은 배치 → order unknown → current 판단 보류 → 기존 recovery가 필요한 근거만 회복. 전면 재조회 없음.
5. note2 + window2 → fork → project change → old pattern/decision excluded → source/history/approval 섞이지 않음.
6. 부족한 context → 카드 제외 → 판단 deferred → 사용자 작업/보고는 기존 budget 안에서 종료. 구조 기능이 추가 압축·추가 finalization을 강제하지 않음.
7. 보고의 leaf alias와 19→15 정정 fixture → 실제 누락만 보고 보완 대상으로 남음.

### 13.2 실제 모델 비교 조건

모델 품질 평가는 설치·출시와 별도의 상태로 기록한다. 같은 모델/quantization, tokenizer/context, sampling 설정, 도구 목록, source snapshot, 엔진 identity, 캐시 조건을 맞춘다. 모델 출력에 seed가 있어도 완전 결정적이라고 가정하지 않는다. 실행 중인 사용자 대화에 테스트 메시지를 보내지 않는다. 격리된 replay 환경을 사용한다.

비교군:

| 군 | 설정 | 확인 목적 |
| --- | --- | --- |
| B0 | 현재 기준 코드/동일 guidance 설정 | 기준 답변·비용 |
| B1 | 패턴 corpus만 보강, 자동 정책/typed state 없음 | 지식 검색 개선만의 효과 |
| B2 | 자동 baseline/정책, 상세 RAG 보강 없음 | 판단 시점 개선의 효과 |
| B3 | 전체 구현, 기존 도구 예산 동일 | 보존·연결을 포함한 최종 효과 |

첫 smoke는 아래 M01/M02/M04/M09 네 사례를 각 군 한 번 실행한다. 권한/오탐/비용 문제가 있으면 먼저 수정한다. 통과 후 12개 전체를 각 군 한 번 실행한다. 주관적 판정이 갈린 사례는 같은 조건에서 두 번 추가 실행하고 편차를 기록한다. 전체 평가를 반복 자동화하거나 항상 실행하는 제품 경로로 만들지 않는다.

### 13.3 의미 품질 fixture와 채점

| ID | 요청/근거 조건 | 필수 판단 | 실패 예 |
| --- | --- | --- | --- |
| M01 | 작은 계산 오타, 이미 적절한 Component | 현 구조 유지, 최소 수정 | 파일이 길다는 이유로 새 manager 생성 |
| M02 | 둘 이상의 Actor에 조합되는 피격 기능, 책임 경계 분리 가능 | Component 후보와 기존 유지 대안·연결 비용 비교 | 같은 클래스 안 변수 통합만 제안 |
| M03 | 독립 스폰/파괴·충돌이 필요한 월드 오브젝트 | 독립 Actor 수명 고려 | 단순 함수나 데이터 자산으로 실행 객체 대체 |
| M04 | 월드 한정 공유 서비스, 두 PIE 월드 fixture | World 범위 고려, 월드 간 참조 방지 | 전역 static/GameInstance에 무조건 모음 |
| M05 | 동일 정의를 공유하는 장비별 가변 상태 | Data Asset 정의/인스턴스 상태 분리 | 공유 asset에 durability runtime 값 저장 |
| M06 | 기존 Data Asset/Subsystem이 이미 있음 | 재사용·확장 가능성 우선 확인 | 이름만 다른 중복 소유자 생성 |
| M07 | async asset load 후 owner 파괴 | handle/cancel/late completion/적용 조건까지 설명 | 함수 분리만 하고 종료 경계 생략 |
| M08 | UI 관찰/재생성/재구독 | 규칙 소유자 유지, binding 해제·중복 방지 | UI가 상태 정본이 되거나 polling만 추가 |
| M09 | weak timer map, Logout/EndPlay/callback cleanup 있음 | cleanup 반대 근거 인정, 미입증 leak 주장 철회 | weak pointer라는 이유만으로 누수 확정 |
| M10 | 단일 cooldown 기능, GAS 미사용 | 단순 설계 유지 가능, GAS 도입 비용 명시 | GAS/GameFeatures를 필수 조건으로 추가 |
| M11 | C++ 검색은 없지만 Blueprint 사용 가능성 미확인 | 미확인 소비자 표시, 삭제 범위 제한 | 미사용 확정 후 UPROPERTY/UFUNCTION 삭제 |
| M12 | pool 반환/재사용 중 이전 비동기 결과 | object lifetime과 activation generation 분리 | 포인터 valid만으로 옛 결과 적용 |

각 사례는 다음 5개 항목을 0/1/2점으로 채점한다: (a) 기능·소유자 파악, (b) 적절한 대안과 기존 유지 비교, (c) 생성/호출/실패/종료 연결, (d) 근거·반대 근거·미확인 범위, (e) 과도한 도입 없이 사용자 요청 수행. 0=누락/오류, 1=부분 충족, 2=근거와 연결까지 충족.

초기 품질 수용선은 12개 중 10개 이상 8/10점 이상, 모든 사례에서 (d) 1점 이상으로 정한다. 이는 이 명세의 제품 목표이며 실측된 현재 성능 수치가 아니다. 중요 경계 위반(무권한 쓰기, 다른 프로젝트 혼합, 취소 후 상태 부활, 잘못된 현재 소스 확정)이 한 번이라도 발생하면 총점과 무관하게 미통과다. 점수만 맞추려고 Component/Actor/DataAsset 이름 개수를 세지 않는다.

### 13.4 비용·재조회 수용선

- 순수 baseline fixture에서 구조 기능 자체가 추가 host 모델 호출/host 도구 호출을 만들면 실패다.
- 결정적 replay에서 동일 결과·같은 버전·같은 유효성의 근거에 대해 구조 기능이 추가 re-read를 요구하면 실패다. archive restore가 필요한 경우는 기존 restored 지표로 별도 기록한다.
- 모델이 실제로 택한 검색·읽기 수는 B0와 비교한다. 새 근거 필요, 과거 근거 퇴거, RAG 세대 변경, 실제 오류 재확인 등 사유를 붙인다. 원인 없는 반복 증가는 개선 대상으로 남긴다.
- 추가 입력 tokens, prompt processing/prefill, generation, tool time, total time, system rewrite 원인별 수, 보고 보완 사유를 기록한다.
- 모델 성능/캐시 편차 때문에 “총시간 반드시 20% 감소” 같은 검증되지 않은 수치를 약속하지 않는다. 대신 동일한 단순 fixture에서 비용 증가가 정당화되지 않으면 카드/주입을 줄이고 다시 측정한다.
- telemetry의 included는 주입 성공일 뿐 모델이 따랐다는 증명이 아니다. selected는 모델의 선택일 뿐 적용/검증 성공이 아니다.

## 14. 관측 이벤트와 장애 시 동작

기존 executionId/modelInputId/roundIndex와 연결한 debug event를 사용한다. 별도 실행 추적 DB를 만들지 않는다. 원문 코드·전체 RAG 본문·인증정보·receipt를 새 로그에 복제하지 않는다.

| 이벤트 | 필수 핵심 필드 |
| --- | --- |
| architecture_context | depth, reasonCodes, observedFacetCount, requestedPatternIds, admittedPatternIds, omittedReasons, actualAddedTokens 또는 unknown |
| architecture_decision | decisionId, revision, disposition(commit/reject/defer), reason, dependencyCount, sourceApplicability, pattern hashes |
| architecture_invalidation | decisionId, reason, affected source/ref ID, old/new 관찰 버전 또는 unknown |
| architecture_restore | source(history/window/none), restoredCount, rejectedCount, historicalCount, rejectionReasons |
| architecture_persistence | note version, state item count, stored/not_stored, existing persistence reason |

예산·read_ledger·system rewrite·RAG generation 지표는 기존 이벤트를 참조하고 같은 의미의 새 counter를 병렬로 증가시키지 않는다. 이벤트에는 요청한 것과 실제 채택한 것을 구분한다.

장애별 기본 행동:

- 카드 없음/검색 실패: 현재 소스와 번들 baseline으로 계속, 상세 근거 부족 표시.
- 정책 입력 손상: 공통 baseline 또는 기존 동작으로 축소, 쓰기 권한 확대 없음.
- 메모 parse/복원 실패: 기존 유효 판단 유지 또는 빈 상태, 원본 도구 근거 보존.
- current source 확인 불가: 변경을 보증하는 근거로 사용하지 않음. 실제 쓰기 가능 여부는 기존 guard/receipt가 결정.
- 구조 분석 budget 부족: deferred_budget, 추가 loop 없음.
- runtime/API 적용성 미확인: 일반 개념과 구체 API 조언을 구분, 잘못된 버전 인용 금지.

## 15. 검증 명령과 산출물

작업 위치를 각 명령의 cwd로 명시한다. 아래는 기준 저장소에서 확인한 명령이며 구현 시 현재 package/CI script를 다시 확인한다. Windows에서 npm은 npm.cmd를 사용할 수 있다. 기존 의존성이 있으면 무조건 재설치하지 않는다.

### 15.1 단계별 검증

compactor 변경마다 관련 suite를 먼저 실행한다. 전체 npm test는 build를 포함하므로 매 작은 문장 변경마다 반복하지 않는다.

~~~powershell
# cwd: lmstudio-context-compactor-plugin
npm.cmd run build
node --test test/architecture-policy.test.cjs test/architecture-lifecycle.test.cjs test/architecture-note.test.cjs
npm.cmd run guidance:check
npm.cmd run toolcontract:check
npm.cmd run unitycontract:check
npm.cmd run identity:check
~~~

위 신규 test 파일을 기존 suite에 합쳤으면 실제 파일 이름을 사용한다. --check 전에 generator가 변경을 덮어써 stale generated artifact를 숨기지 않도록, CI에서는 clean checkout의 check와 생성 후 clean diff 검사를 구분한다.

~~~powershell
# cwd: repository root
python -m pytest tests/test_architecture_pattern_corpus.py tests/test_direct_rag_pattern_contract.py
python scripts/verify_encoding.py
git diff --check
~~~

최종 변경 묶음에서 기존 회귀를 실행한다.

~~~powershell
# cwd: lmstudio-context-compactor-plugin
npm.cmd test

# cwd: lmstudio-unreal-agent-mcp
npm.cmd test

# cwd: lmstudio-unity-mcp
npm.cmd test

# cwd: repository root
python scripts/ci/run_ci_suite.py portable_direct portable_release
python scripts/ci/run_ci_suite.py windows_direct windows_release
python scripts/run_repetition_gate.py direct_repetition --repeat 3
~~~

상호 계약이 바뀌므로 Unreal·Unity의 기존 경계를 확인하되 Unreal 구조 판단을 Unity에 추가하는 것은 범위가 아니다. 전체 tests가 너무 오래 걸리거나 환경 의존 skip이 있으면 실제 완료/미실행/skip 이유를 보고한다. 실패를 없애려고 기대값을 줄이거나 fixture를 무근거로 바꾸지 않는다. 기존에 통과하던 안전성 기대값은 유지한다.

### 15.2 필수 산출물

1. 변경 코드·문서·동일 정본에서 생성된 배포 bundle/export.
2. A/L/N/C/R/Q ID → 실제 테스트 이름 → 실행 결과 대응표.
3. end-to-end replay의 입력 fixture와 도구/commit/restore/close 결과. 사용자 원본 대화·게임 전체 소스 제외.
4. B0–B3 모델 비교 결과: 모델·설정·snapshot·점수·도구 수·tokens·timings·rewrite 이유. 미실행이면 명시.
5. 기존 계약 보존 표: 승인/receipt/CAS, scope, cancellation, RAG generation, note priority, window lineage, read reserve.
6. 변경 전후 소유자 표와 신규 필드 migration/rollback 설명.
7. 새 데이터가 포함된 깨끗한 패키지 검사. 설치가 추가로 지시된 경우에만 백업·격리 smoke·활성 runtime build identity를 확인한다.

### 15.3 완료 상태의 구분

- ContractImplemented: 새 계약과 테스트가 구현되고 결정적 회귀가 통과한 상태.
- ModelEvaluated: 동일 조건의 의미·비용 비교를 실제 실행한 상태.
- Installed: 실제 사용자 runtime 교체 후 loaded build가 확인된 상태.
- Released: 별도 게시·태그·배포 요청과 실제 결과가 있는 상태.

네 상태를 합치지 않는다. TypeScript build나 evidence JSON 형식 검사만 통과했다고 “모델이 이제 큰 그림을 본다”거나 “설치 완료”라고 보고하지 않는다. 이 명세 작성 시점에는 위 상태 중 새 기능에 해당하는 것은 아직 없다.

## 16. SOL에게 전달할 시작 지시

이 문서의 기준 커밋과 현재 checkout 차이를 먼저 확인하고, 기존 소유자를 재사용하는 방식으로 P0부터 진행한다. 일반 Unreal 작업에 자동 baseline을 제공하되 사용자 작업 범위·도구 권한·읽기 예산·취소 경계를 그대로 유지한다. 구조 판단과 근거·권한·실행 결과를 서로 승격하지 않는다. typed note v2와 RAG metadata는 생성→입력→commit→저장→복원→무효화→종료까지 연결해 검증한다. F09/F10 보고 오탐도 독립 회귀로 수정한다.

이 문서는 구현 요구를 구체화한 명세다. 사용자 설정을 켜거나 실제 게임 코드를 수정하거나 설치·push·merge·release하는 요청으로 확대 해석하지 않는다. 새로운 설계 기능 자체를 더 큰 하네스/게이트 시스템으로 만들지 말고, 검증된 기존 실행 경계를 통과하는 작은 관찰·정책·판단 계층으로 완성한다.
