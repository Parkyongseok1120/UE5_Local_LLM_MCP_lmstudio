# LM Studio 작업 보고·API 확인·수명 검토 개선 계획

작성일: 2026-10-01 (Asia/Seoul)  
기준 커밋: `6a0e9b7`  
상태: 로그와 현재 소스 검토를 마친 구현 제안. 이번 작업은 분석·계획 문서 작성이며 제품 소스, 게임 코드, LM Studio 설정을 수정하지 않는다.

## 1. 요청과 확인 범위

우선 해결할 대상은 실제 변경과 다른 완료 보고, 적용 엔진 API를 확인하지 못한 추측 수정과 반복 빌드, 수명·상태 전이의 불완전한 검토다. 기존 Auto 계획과 공유할 책임도 정한다. 모델 파라미터 변경이나 필수 정적 검사로 실행을 막는 방식은 제안하지 않는다.

검토 자료:

- 채팅 첨부: `C:/Users/sster/.codex/attachments/7057ee7f-378e-4ce1-b7fd-64f4e62759fc/붙여넣은 텍스트.txt` — 7,694줄.
- 서버 첨부: `C:/Users/sster/.codex/attachments/ead35800-120c-4909-8e06-8dafa366bfeb/붙여넣은 텍스트.txt` — 1,055줄, 2026-10-01 01:25:14–01:33:45.
- 현재 게임 프로젝트의 C++·Config·Git diff: `C:/Users/sster/Documents/Git/UE5_Latency-Resilient-Multiplayer-Combat`.
- 현재 compactor, Direct Agent, Direct RAG, 지침 정본·생성·선택·전달 소스.
- 로컬 `C:/Program Files/Epic Games/UE_5.7/Engine/Build/Build.version` 및 관련 엔진 선언·구현. 현재 설치 버전은 **5.7.4**다. 당시 실제 빌드 실행의 patch 버전까지 입증하는 자료는 아니다.

첨부의 과거 명령은 조사 자료로 취급했다. 일부 도구 카드에는 반환 본문이 비어 있다. 따라서 모델의 오류 설명과 실제 도구 payload를 구분한다. 현재 소스는 사건 이후 바뀌었을 가능성이 있으며 서버 로그 구간은 전체 채팅보다 짧다. 누락된 모델 입력, 설정, 도구 결과를 추측으로 채우지 않는다.

사용자의 성능 점수는 이번 관찰에 대한 평가다. 벤치마크 수치로 환산하지 않는다. 자기 검증에서는 사용자 지적 후의 정정과 완료 보고 전의 자발적 확인을 따로 평가한다.

## 2. 확인된 문제와 근거

### 2.1 실제 수정 종류가 완료 요약에서 바뀌었다 — 우선순위 1

채팅 5139줄은 GameState의 자기 헤더가 첫 include여야 한다는 오류를 설명한다. 5147–5149줄은 include 순서 수정, 5174–5182줄은 PlayerState의 같은 문제 수정 흐름이다. 현재 두 cpp의 Git diff도 include 순서 교환뿐이다. 이 변경은 컴파일 장애를 해결하는 의미가 있다.

그러나 7366–7367줄의 최종 표는 다음 기능 수정이 완료됐다고 보고한다.

- GameState: `bMatchStarted` 초기화, `StartMatch()`/`EndMatch()` 전이 추가.
- PlayerState: `BeginPlay()`의 `SetLifeSpan(0.f)` 제거.

두 심볼의 이 첨부 내 최초 출현은 각각 **7366줄·7367줄**, 최종 보고 자체다. 현재 HEAD·작업 트리와 이후 정정 내용도 해당 기능 변경을 뒷받침하지 않는다. 파일 변경 목록에서 의미를 재구성한 완료 보고가 잘못됐다. 이전의 모든 역사에서 존재하지 않았다고 확장하지 않는다.

모델은 사용자의 질문 후 diff를 확인하고 7597–7637줄에서 include 변경뿐이라고 정정했다. 이것은 후속 확인 능력의 근거지만 최초 완료 보고의 근거가 되지는 않는다.

### 2.2 API 이름·소유자·의미를 기억과 부분 진단으로 추정했다 — 우선순위 1

첨부에 보이는 도구 호출 표시는 `build_unreal_project` 17개, `unreal_symbol_lookup` 2개, `unreal_rag_search` 8개다. 이는 export의 카드 수이며, 모든 실행 payload·컴파일 시간을 검증한 집계가 아니다. 빌드를 전부 불필요하다고 평가하지 않는다. include 순서·실제 타입·시그니처 오류를 해결하는 정상 빌드도 포함된다.

문제는 새로운 선언 근거 없이 같은 유형의 가설을 바꿔 반복한 구간이다.

| 모델의 추정 또는 보고 | 직접 소스와 대조한 결과 |
|---|---|
| `EInputValueType::Vector2D` 및 헤더 후보 추측 | 설치된 Enhanced Input은 `EInputActionValueType::Axis2D`를 선언한다. 이름·값·모듈을 소비 API에서 먼저 확인해야 한다. |
| `AddMappingContext`를 EnhancedInputComponent 또는 PlayerController에 붙임 | 현재 선언 소유자는 `UEnhancedInputLocalPlayerSubsystem`이다. 수신 객체·LocalPlayer 준비 시점도 확인해야 한다. |
| 앞선 타입 오류만 보고 `GetEnhancedInputUser()`는 컴파일됐다고 추정 — 6094줄 | 앞선 진단은 이후 표현식의 성공 증명이 아니다. 실제 PlayerController 선언 확인이 필요하다. |
| RAG에서 `KeysTypes`가 안 나오므로 UE 5.7에 헤더가 없다고 확정 — 7155줄 | 제한된 검색의 실패는 전체 설치 소스에서 부재를 증명하지 않는다. |
| `EInputEvent`가 Pressed/Released 두 값만 있으며 5.7에서 변경됐다고 보고 — 7226·7253·7370줄 | 현재 enum에는 Repeat·DoubleClick·Axis도 있다. 과거 버전과 비교하지 않았으므로 변경 역사도 입증하지 못했다. |
| `SetLifeSpan(0.f)`가 즉시 또는 1초 후 객체를 파괴한다고 설명 — 7367·7529줄 | `AActor::SetLifeSpan`은 양수면 소멸 타이머를 예약하고 0 이하에서 기존 타이머를 해제한다. 헤더 주석도 0이면 파괴되지 않는다고 설명한다. |

따라서 단순한 엔진 버전 지식 부족 외에 **불완전한 근거를 확정으로 바꾸는 추론**이 있다. 해당 이름만 denylist에 추가하면 다른 API에서도 반복될 수 있다.

### 2.3 컴파일 통과를 위해 동작 계약을 바꾸고 실행을 완료로 보고했다 — 우선순위 1

6145줄부터 Enhanced Input의 불확실성을 피하기 위해 legacy fallback으로 경로를 바꾼다. 이런 변경은 에셋·매핑·부분 설정·입력 수명과 기존 동작의 비교가 필요하다. 컴파일 가능성만으로 적절한 대체임을 확인할 수 없다.

현재 Character.cpp 59–62줄은 `MoveForward`, `MoveRight`, `Turn`, `LookUp` 이름으로 legacy axis를 등록한다. 현재 프로젝트 Config에는 이 이름의 `AxisMappings`를 찾지 못했다. `AxisConfig`는 키 축 속성이고 이름 매핑과 다르다. 다른 설정 계층·에셋·런타임 등록으로 제공될 가능성은 남으므로 PIE에서 실패했다고 확정하지 않는다. 그러나 7373–7380줄의 “에셋 없이 즉시 플레이 가능” 보고는 빌드 결과만으로 증명되지 않는다.

관련 지침에는 다음 비교를 넣는다: 타입·인자·include 수정으로 의미 보존이 가능한지, 대체가 입력·권한·콜백·필터를 바꾸는지, 실제 설정과 실행 연결이 있는지. 변경된 동작은 사용자 요구와 대조하고 미검증 범위를 보고한다.

추가 입력 경로도 발견됐다. Enhanced 경로는 여섯 action이 있으면 bind 후 반환하지만, 현재 PlayerController의 SetupEnhancedInput은 cast·ready 로그뿐이고 CombatMappingContext 로드나 AddMappingContext 호출은 Source에서 찾지 못했다. 에셋·Blueprint의 별도 등록은 미확인이다. legacy LMB는 Pressed만 등록하고 ShootReleased를 등록하지 않아, 할당된 full-auto 무기를 사용하는 조건에서 놓음이 반복 사격 중단으로 이어지지 않는다. CurrentWeapon이 null인 기본 fallback에서는 해당 Tick 조건이 false라는 반대 근거가 있다. 준비 로그, action 존재, 시작 바인딩이 실제 등록과 종료 연결을 각각 증명하지 않는 사례다.

### 2.4 장기 상태의 불완전성과 완료 판단을 구분하지 못했다 — 우선순위 1

7397줄에서 모델은 원래 수정 계획이 현재 맥락에 없다고 말한다. 그 뒤에도 파일 목록에서 계획 완료를 재구성하고 같은 기능 주장을 반복한다. 계획 본문·변경 의미를 확인하지 못하면 완료 여부가 불명확한 것이다.

현재 continuity는 사용자 목적, 최근 도구 결과, 파일 버전 관찰, 빌드 결과, 이전 모델 판단을 이미 구분한다. 모델 판단의 ref가 완료 도구를 가리키는지 확인하지만 해당 판단의 진실이나 기능 완료를 판정하지 않는다. 이 경계를 유지하며 실제 수정의 좁은 근거를 더 남기는 방향이 적절하다.

단, 첨부에 해당 시점의 실제 compactor 입력과 지침 채택 기록이 없으므로 **compactor가 원래 계획을 제거해 환각을 유발했다는 인과관계는 미확인**이다. 지침이 선택·전달됐는지, Hybrid 요약이 실행됐는지도 현재 자료만으로 확정하지 않는다.

## 3. 수명 검토에서 놓친 사례

현재 게임 소스의 아래 사례는 범용 지침·회귀 사례를 구체화하는 근거다. 이번 요청은 이 게임의 코드 수정으로 확대하지 않는다. 소스의 조건과 실제 경기에서 발생한 결과를 구분한다.

| 사례 | 현재 확인 | 확인할 전이·반대 경로 |
|---|---|---|
| 둘 이상의 리스폰 예약 | GameMode.h 53줄의 단일 handle을 GameMode.cpp 99줄에서 재사용. UE SetTimer 계약은 기존 유효 handle의 예약 교체 | 플레이어별 예약 보존, 같은 플레이어 중복 요청, 취소·퇴장 |
| 늦은 콜백과 객체 종료 | 타이머 람다가 `this`와 Controller raw 포인터를 캡처. null 검사만 있음 | UObject 유효성, 월드 종료·퇴장 후 callback, 기존 종료 담당자의 취소 |
| 성공 전 상태 확정 | RespawnPlayer가 시작 위치·SpawnActor·Possess 확인 전 `ResetForRespawn()` 호출 | 실패 시 상태 복구, 새 Pawn 연결 성공 후 alive 확정 |
| 이전 Pawn의 수명 | UnPossess 후 새 Pawn 생성; 이전 Pawn 폐기·시체 유지 계약은 해당 경로에서 찾지 못함 | UnPossess와 Destroy 구분, 명시된 시체 정책·소유자 |
| 등록의 반대 연산 | TeamMembers에 추가·조회가 있으나 C++ Source에서 Logout/EndPlay 제거를 찾지 못함 | 퇴장·재접속·팀 이동 후 팀원 수, 포인터 수명·GC 추적 |
| 반복 사망·매치 종료 | 이미 dead인 PlayerState라도 Character가 `Die()`를 다시 호출할 수 있음. 개인 킬·예약 전에 종료/중복 방어 없음 | 일회성 사망 전이, 중복 RPC/피해, 종료 뒤 개인 점수·리스폰. 팀 점수에는 종료 방어가 이미 있음 |
| 초기화 시점 | BeginPlay에서 PlayerState가 있을 때만 체력 설정. 이후 준비에 대응하는 경로를 현재 Character에서 찾지 못함 | 서버 possession, 원격 복제, 재접속·리스폰. 초기화 코드가 전혀 없다는 이전 진단은 현재 소스와 다름 |

현재 코드에는 GameMode 기본 클래스 등록, PostLogin의 팀 배정 호출, 복제 등록, 체력 초기화 코드가 존재한다. 선언·등록·호출·상태 변경·원격 관찰을 따로 확인해야 한다. 공유 RepNotify를 쓴 사실만으로 오류라고 단정하지 않는다. 통합 UI 갱신 함수일 수 있으며 현재 본문은 주석뿐이다.

Unity에서는 같은 공통 기준을 적용하되 준비와 종료의 엔진 계약을 바꾼다. Awake/OnEnable/Start의 보장, Scene·Pool 대여·네트워크 Spawn, 구독·해제, Destroy/Disable/Despawn, 작업 취소·늦은 결과, Domain Reload를 구분한다. NGO·Entities·Mirror·Fusion의 버전과 권한 계약을 섞지 않는다. 이번 로그가 Unity 런타임 오류를 입증하는 것은 아니다.

## 4. 서버 로그가 말해 주는 것과 말해 주지 않는 것

- 13개의 slot launch와 13개의 release가 있고 release는 모두 `truncated = 0`이다. 해당 백엔드 기록에 truncation 표시가 없다는 뜻이다. compactor가 모든 계획·의미를 보존했다는 증명은 아니다.
- 101줄에는 `prompt state size 10289.126 MiB exceeds cache size limit 8192.000 MiB, skipping` 경고가 있다. 문자 그대로 prompt state의 cache 저장 한도를 넘겨 건너뛴 기록이다. 이것만으로 컨텍스트 부족이나 환각의 원인을 확정하지 않는다. 원인 조사는 prompt 처리 시간·cache 재사용 기록과 별도로 대조한다.
- JSON schema 변환 경고가 59줄에 나타난다. `pattern \S`를 지원하지 않아 임의 문자열을 허용한다고 설명한다. 현재 저장소의 evidence-first schema 생성기에도 같은 unanchored pattern이 있다. 설치된 서버의 당시 배포본·노출 schema까지 대조해야 경고 출처를 확정할 수 있다. 생성 grammar 제약과 서버의 실제 인자 검사를 구분한다.
- 01:30경 disconnect/reconnect, 01:33:45 disconnect가 있다. 중단 원인이 crash였는지 사용자/앱 동작이었는지 첨부만으로 확인할 수 없다.
- 모델 요청 이름은 `swift-1.5-qwen3.8-27b`다. KV/V cache의 Q5_1 설정은 첨부에 나오지 않는다. 낮은 양자화가 원인이라는 결론을 내리지 않는다.
- 현재 workspace의 5.8 기본 인덱스와 설치된 게임 엔진 5.7.4가 다르다. 그러나 Direct RAG는 선택 프로젝트의 버전으로 호환 sibling index를 고르는 코드가 있다. 기본 설정만 보고 사건에서 잘못된 인덱스를 썼다고 확정하지 않는다.

## 5. 구현 목표와 책임 배치

구현은 세 가지를 개선한다: 모델이 참조할 공통 판단 기준, 모델이 요청한 정확한 API 근거, 모델이 실제 변경을 설명할 수 있는 좁은 반환 근거. 이를 Auto의 제한된 주제 선택과 연결한다.

| 책임 | 기존 소유자 | 필요한 변화 |
|---|---|---|
| API·수명·완료 보고의 공통 최소 기준 | `docs/model-guidance/core.md`의 `core/proof` | 짧은 검증 기준을 한 곳에서 보강. 기존 모든 절의 requires와 축소 후보를 활용 |
| 엔진별 절차와 사례 | debugging/unreal/unreal-networking/unity/unity-networking 정본 | 선언·소유자·초기화·콜백·원격 관찰의 구체적 비교 보강 |
| 정본·배포 본문 | catalogue와 `build-design-guidance.cjs` | 정본에서 번들을 생성. 생성 파일 직접 편집 금지 |
| 활성 엔진 선택 | 기존 Node/Python 엔진 resolver | 선택된 root, association, Build.version provenance를 전달. 재탐지 규칙 복제 금지 |
| RAG 후보와 정확 일치 | `direct_rag_symbol.py`, `target_resolver.py` | 기존 status/exact/matchKind 재사용. index 검색 coverage 설명 보강 |
| 설치 엔진 선언 읽기 | 기존 `engine_header_evidence.py`와 Direct RAG symbol capability | 모델이 명시 요청하는 제한된 local 조회를 연결. plugin 범위·입출력 비용 보완 |
| 실제 수정·커밋 결과 | Direct Agent mutation/bundle commit | 실제 적용된 전후 내용에서 좁은 변경 근거 산출 |
| 변경 관찰의 유지·무효화 | 기존 file observations/continuity/reference context | 버전·프로젝트에 묶인 파생 근거를 제한적으로 보존 |
| 이전 판단과 정정 | 기존 model notes | 판단·supersedes 계약과 최신 사실 우선순위 유지. 자동 진실 판정 금지 |
| 입력 예산·채택 | ContextManager/BudgetBroker | 실제 측정과 기존 omission 경계 재사용 |
| 추론·승인·도구·종료 | 기존 prediction/tool 경계 | 이 계획은 별도 실행 주체를 추가하지 않음 |

SRP: 글 본문, 조회, 파일 변경, 관찰 수명, 예산, 실행의 책임을 각 기존 소유자에 둔다. OCP: 기존 도구에 선택적 조회 모드와 반환 필드를 추가한다. ISP: 모델 입력에는 전체 파일·상태 DB 대신 필요한 선언·변경 범위만 전달한다. DIP: selector는 관찰 snapshot을 소비하며 파일 읽기·빌드·모델 호출을 수행하지 않는다. 패턴 이름을 채우기 위해 계층이나 manager를 추가하지 않는다.

## 6. 변경 A — 공통 기준이 모든 활성 참고에 함께 들어가게 한다

모든 문서에 같은 문단을 복사하면 기준이 서로 달라지고 토큰이 반복된다. 현재 모든 전문 절은 `core/proof`를 요구하며 가장 작은 focused 후보도 이 절이다. 이 경로를 재사용한다.

`core/proof`를 다음 내용의 짧은 검증 기준으로 보강한다. API와 수명 절에는 세부 확인 방법을 둔다.

1. 새로 도입·변경하는 외부 API와 진단에 충돌한 계약은 적용 버전의 선언·수신 타입·인자·의미를 확인한다. 같은 버전·같은 계약의 확인 근거는 재사용한다.
2. 상태·콜백을 바꿀 때 초기화 준비, 권한, 중복, 실패, 취소, 종료·늦은 결과의 관련 경로를 확인한다.
3. 완료 항목은 실제 성공 반환과 적용 내용 또는 현재 소스/diff에 연결한다. 파일명·hash 변경·이전 요약만으로 수정의 기능명을 만들지 않는다.
4. 계획·반환·변경 의미를 확인하지 못한 항목은 미확인으로 남긴다. 반증된 이전 설명은 정정하고 기존 판단 note의 supersedes를 사용한다.
5. 소스 확인, 변경 적용, 특정 target의 빌드, 실제 실행을 구분한다. 검색 실패·일부 진단·up-to-date만으로 더 높은 확인 수준을 주장하지 않는다.
6. 지침은 참고 기준이며 도구 권한이나 별도 승인·필수 static Validate 조건을 추가하지 않는다.

실제 정본은 제목·표식 제외 900자 이내를 목표로 하고 사용 중 tokenizer로 최종 측정한다. 각 전문 절에 동일 문단을 반복하지 않는다. Off는 참고를 넣지 않는다. 참고 활성 상태에서도 allowance=0·예산 부족·기존 복구/최종화 생략 규칙은 유지한다. “모든 활성 후보에 최소 기준 포함”과 “모든 라운드에서 반드시 삽입”은 구분한다.

Finalization에 참고를 새로 넣기 위해 기존 경계를 바꾸지 않는다. 공통 기준은 허용된 일반 라운드에 전달하고 완료 근거는 기존 반환·continuity 경로로 유지한다. 최종 보고를 하네스가 semantic completion 기준으로 차단하지 않는다.

## 7. 변경 B — 모델이 정확한 엔진 선언을 요청할 수 있게 한다

### 7.1 현재 구조에서 재사용할 기능

`unreal_symbol_lookup`는 이미 `targetResolution.status`, `exact`, 후보의 `matchKind`를 반환한다. 유사 후보를 정확 일치로 부르는 새 resolver는 필요 없다. tool 설명과 작게 보존하는 응답에 기존 판단을 분명하게 전달한다.

여기서 exact는 이름·file stem·Unreal prefix 제거 등 문자열 일치의 종류다. read는 파일 존재 확인을 요구하지 않으며 expectedBaseType은 token 점수 가산이다. top_k로 제한된 후보에서 resolved됐다고 실제 receiver·부모·시그니처·전체 엔진의 유일성을 검증한 것은 아니다. local 선언 증거와 target 선택을 별도 출처로 유지한다.

현재 `lookup_engine_header_evidence`는 설치 엔진의 제한된 선언 근거를 읽는 helper다. 검색 결과 없음이 부재 증명이 아니라고 명시하며 containment·선언/시그니처 확인도 있다. 현재 Direct RAG symbol capability에서 이 helper를 호출하는 연결은 찾지 못했다.

그대로 연결하면 안 되는 제한도 있다.

- 실제 설치에서는 `Engine/Source`가 존재하면 plugin tree를 catalog에 넣지 않는다. Enhanced Input은 `Engine/Plugins/EnhancedInput`에 있으므로 이번 문제의 중요한 경로가 빠진다.
- 최초 파일 catalog와 owner 선언 탐색은 파일 수·시간 비용이 크다. 읽은 뒤 문자열을 자르는 현재 구현은 실제 파일 읽기 바이트 상한이 아니다.
- process-local catalog·negative declaration cache는 root 중심이다. 같은 경로의 엔진 갱신과 query coverage를 구분해야 한다.
- signature 추출은 현재 파일 전체에서 함수 이름을 검색하고 요청 owner로 qualified 이름을 합성한다. 같은 파일의 다른 class에 있는 동명 함수가 요청 owner의 선언으로 승격되지 않게 실제 선언 영역을 확인해야 한다.
- timeout·scan 생략·부분 탐색도 빈 결과로 cache될 수 있다. ready나 empty만 반환하지 말고 각 탐색의 완료 여부·중단 이유를 남겨야 한다.
- 헤더 선언은 전체 부모·매크로·런타임 효과를 모두 증명하지 않는다. 정확 선언과 호출의 실제 도달 가능성을 구분한다.

### 7.2 제안하는 공개 계약

기존 도구에 선택적 `sourceMode: index | engine_local`를 추가한다. 기본은 index다. local 모드는 정확한 symbol 또는 `Owner::Symbol`과 선택적 owner 정보만 받는다. 임의 검색 문장·경로·명령 실행으로 확대하지 않는다. 기존 `expectedBaseType`을 receiverType인 것처럼 재해석하지 않는다.

1. 기존 활성/명시 project resolver로 정확한 프로젝트와 엔진 root를 확인한다. 모호한 다중 root, 잘못된 association, root 미확인은 기존 실패 경계를 따른다.
2. Build.version이 있으면 major/minor/patch/changelist 및 그 파일 hash를 실제 관찰로 반환한다. association과 RAG manifest 버전은 각각 별도 출처다. association만 확인됐으면 patch/root 확인을 했다고 표시하지 않는다.
3. local 모드는 사용 가능한 RAG index를 전제하지 않는다. index가 없거나 stale이어도 선택 엔진의 read-only 선언 확인은 가능하게 한다. index 부재를 우회해 다른 엔진을 읽지는 않는다.
4. owner/header가 좁혀진 Engine Runtime과 요청에 관련된 확인된 plugin module subtree만 읽는다. 처음에는 Enhanced Input 경로를 포함하되 모든 설치 plugin의 반복 scan으로 확대하지 않는다. 허용된 module root는 resolver/catalog 담당자가 관리한다.
5. 기존 helper의 bounded 읽기와 catalog 수명을 보완한다. 제안 상한은 호출당 symbol 1개, 후보 4파일, 파일당 256 KiB 실제 읽기, 총 1 MiB, 발견 작업 3초, 결과는 기존 compact transport 한도 이내다. 범위·시간·바이트 상한 때문에 발견하지 못한 경우 coverage 제한을 명시한다. 임의로 검색을 확대하거나 상세 모드 재호출하지 않는다.
6. 실제 class/struct 선언 영역에서 owner·선언을 확인한 경우에만 source path/line, 실제 읽은 영역의 fingerprint, signature, source tier와 engine identity를 반환한다. fingerprint에는 hashScope/readByteRange/readBytes/completeFile을 포함한다. 전체 파일을 실제 상한 안에서 읽은 경우에만 full-file SHA라고 표시한다. prefix/부분 읽기를 header 전체 hash·fresh file receipt로 승격하지 않는다. 요청 owner와 관찰 owner를 구분한다. 제한된 어휘 탐색으로 선언 영역·상속·alias·매크로 조건을 확인하지 못하면 excerpt와 candidate 또는 unknown을 제공한다. 기존 targetResolution은 후보 식별에 재사용하며 별도의 signature proof로 해석하지 않는다. 함수 존재·가상 함수/매크로·호출 권한을 자동 확대 추론하지 않는다.
7. cached path 후보라도 내용은 요청 시 직접 읽고 읽은 범위의 fingerprint를 묶는다. 엔진 identity나 module 범위가 바뀌면 기존 cache clear 담당자를 통해 무효화한다. negative cache를 API 부재로 사용하지 않는다. 읽는 중 file identity/크기/mtime이 달라졌다면 불안정한 관찰로 표시하고 exact source snapshot으로 반환하지 않는다.

이 상한은 구현 fixture에서 실제 비용을 확인할 시작값이다. 큰 헤더에서 필요한 선언이 상한 밖이면 `coverage_limited`로 보고한다. 상한 확대를 자동 반복하거나 본문을 확인하지 못한 상태를 exact로 만들지 않는다. 파일 catalog 역시 bounded해야 하며 OS fallback에 timeout이 없는 무제한 walk를 실도구 경로에 사용하지 않는다.

취소·동시 요청·프로젝트 전환에서도 기존 요청 scope에 묶인 결과만 반환·관찰한다. same minor라도 다른 engine root의 결과를 합치지 않는다. 헤더 존재는 Build.cs 의존성, plugin enabled, Runtime/Editor 사용 가능성의 증명이 아니다. project plugin은 engine_local 범위 밖이므로 기존 프로젝트 읽기 도구의 containment를 사용한다.

cache는 helper 담당자의 process-local 최적화다. 제안 상한은 engine/module scope 8개, scope별 catalog path 4,096개다. 상한 밖의 목록은 coverage 제한으로 표시한다. 동일 version/root에서 파일 추가·삭제·이동도 있으므로 negative 결과는 탐색이 해당 좁은 범위에서 완료된 경우만 최대 30초 유지하고 다음 요청 때 만료를 확인한다. incomplete/timeout/cancelled miss는 cache하지 않는다. positive 후보 파일이 없어졌거나 선언이 달라지면 해당 후보를 무효화하고 같은 호출 상한 안에서 한 번 재확인한다. 백그라운드 TTL timer나 polling은 추가하지 않는다. 기존 명시 cache clear도 유지한다. 취소되거나 generation이 바뀐 뒤 끝난 탐색은 새 cache generation에 쓰지 않는다.

symbol cache도 scope당 256개로 제한한다. 실제 읽기에서 symlink/real path는 engine root뿐 아니라 선택된 module subtree의 containment를 다시 확인한다. 엔진 선택·Build.version 관찰·module 범위 변경을 기존 resolver가 반환했을 때 helper가 관련 cache key를 무효화하고, 명시 clear는 기존 helper 함수를 사용한다. 이 외 자동 감시자를 추가하지 않는다.

기존 evidence fit 함수는 top-level evidence만 줄인다. 새 local excerpt/signature를 별도 큰 metadata 배열로 넣으면 응답 전체가 한도 초과로 실패할 수 있다. identity·상태·생략 count를 우선하고 본문을 기존 assembly/detail budget 안에서 조립한다. include basename 후보도 정확 include 경로나 UBT 노출로 승격하지 않는다.

명시 local 요청은 모델이 고른 기존 도구 실행이다. Auto가 배경에서 조회·RAG·scan·빌드를 실행하지 않는다. 모델이 API 수정 가설을 고르고 필요한 근거를 요청하는 책임을 유지한다.

Unity는 먼저 공통 기준과 버전·패키지·asmdef·실제 callback 확인을 적용한다. 이번 단계에서 Unity Bridge에 확인되지 않은 동일 local source 모드를 추가하지 않는다. 기존 Unity 도구의 제한된 패키지 소스 읽기·해결 버전 관찰을 사용한다. 추가 API가 필요한지 해당 계약을 별도 확인한다.

## 8. 변경 C — 실제 적용 내용의 좁은 근거를 반환·유지한다

현재 `replace_in_file`은 성공 시 operation/path/occurrences, 전후 SHA, version snapshot을 반환한다. 이 값은 파일 변경의 성공을 입증하지만 “매치 시작 상태를 추가했다”는 의미는 입증하지 않는다. continuity도 path/hash 중심의 파일 관찰을 유지한다.

### 8.1 mutation 담당자에서 산출

성공 반환에 선택적 `changeEvidence`를 추가한다. 기존 CAS receipt의 의미를 바꾸지 않고 새 권한 token을 만들지 않는다.

- 전후 hash, canonical project/path, 실제 변경된 전후 line range와 제한된 diff excerpt.
- 출처는 요청의 newText가 아니라 **성공한 CAS/bundle commit의 실제 전후 내용**이다. 교체 미일치·CAS conflict·실패·rollback은 적용 완료로 기록하지 않는다.
- 생성·교체·bundle의 실제 committed file만 처리한다. partial/rollback 상태를 기존 outcome 계약에 맞춰 표시하며 전체 bundle 성공으로 확대하지 않는다.
- 기능 이름·목적·완료 점수·semantic verification 필드를 생성하지 않는다. include 변경을 include 변경으로 모델이 볼 수 있는 자료만 제공한다.
- 기본 상한: 파일당 최대 2개 변경 hunk, diff 본문 합계 768자. 생략된 hunk 수·본문 coverage를 별도 표시한다. 기존 응답 크기 한도를 우선하고 근거를 생략해도 mutation 성공 여부·hash·기존 receipt는 유지한다.
- diff는 실제 commit buffer에 대한 순수 유틸리티로 계산한다. 별도 파일 재읽기·Git 실행·모델 호출을 mutation 완료 후 추가하지 않는다. 큰 변경은 범위 metadata와 partial/omitted 상태만 남길 수 있다.

기존 replacement/bundle plan의 실제 적용 영역 정보를 먼저 재사용한다. 전체 파일의 범용 의미 diff나 무제한 비교 알고리즘을 새로 만들지 않는다. 반환 크기 제한 때문에 변경 본문을 넣지 못해도 기존 성공/실패·hash·receipt 계약은 유지한다.

changeEvidence는 보조 자료다. 계산 예외·형식화·serialization·보존 실패가 **이미 성공한 CAS/bundle commit**을 실패 또는 rollback으로 바꾸면 안 된다. 이러한 실패는 근거의 unavailable/omitted 상태로 처리하고 기존 성공/hash/receipt를 반환한다. atomicity·commit 성공의 기존 필수 처리와 보조 근거 처리를 분리한다.

같은 파일의 여러 bundle patch는 initial baseline→최종 committed buffer의 net 변경으로 기록한다. 중간 hunk를 최종 hash의 변경처럼 붙이지 않는다. oldText==newText 또는 서로 상쇄된 patch는 적용 성공과 실제 내용 변경 없음(`no_change`)을 구분한다.

### 8.2 기존 관찰 수명으로 유지

기존 compaction extraction/file observations/continuity에서 필드를 읽기 전용 파생 자료로 유지한다. 기존 evidence 원장이나 작업 계획 DB를 하나 더 만들지 않는다.

- 기존 canonical project·파일 hash·operation과 묶는다. 새 observation에서 hash가 바뀌면 이전 diff 본문을 그대로 current로 합치지 않는다. 새 필드가 없는 이전 서버의 응답도 정상 처리한다.
- no-change read, rename/delete, 실패·취소·중복·고아 반환, 다른 프로젝트의 같은 상대 경로를 확인한다. tool-call ID와 실제 반환의 짝은 기존 담당자가 확인한다.
- 영속 메모에는 raw fileVersionReceipt, snapshot token, 권한 capability를 저장하지 않는다. 기존 sanitizer를 재사용한다. readCoverage나 mutationSnapshotState를 fresh read로 승격하지 않는다.
- 파일 목록의 기존 64개 상한은 유지한다. diff 본문은 최근 최대 8파일, 합계 2,048자 이내의 완결된 hunk만 유지한다. 맞지 않는 것은 생략 상태를 남긴다. 기존 전체 메모·토큰 한도가 더 작은 경우 그 한도를 우선한다.
- 실제 근거의 존재는 모델 주장과의 연결 재료다. 함수가 호출됐는지·기능이 작동하는지는 모델이 소스/실행 근거로 확인한다. 하네스는 작업 완료를 채점하지 않는다.
- 이전 모델 note의 정정은 기존 id/status/supersedes 계약을 사용한다. note ref 존재 검사를 의미 진실 검사로 바꾸지 않는다. 사용자 목적·원래 계획이 불완전하면 완료 목록을 재구성하지 않는 기준을 전달한다.

병합 규칙은 다음처럼 고정한다. 검증된 same-hash read는 이전 실제 변경 근거를 유지할 수 있지만 original change의 시각·tool provenance를 그대로 둔다. 최신 read 시각을 변경 시각으로 덮지 않는다. hash 없는 관찰, CAS conflict, outcome unknown은 이전 diff를 현재 버전의 근거로 carry-forward하지 않는다. explicit canonical project/path가 일치하지 않거나 같은 batch에서 read/write의 순서를 확인하지 못하면 근거를 생략한다. 기존 담당자가 순차 dispatch와 전후 hash 연결을 확인한 경우에만 그 관찰 순서를 사용한다. 반환 배열/도착 순서·timestamp만으로 실행·commit 순서를 추정하지 않는다. merge spread로 이전 선택 필드가 의도치 않게 살아남지 않도록 허용 필드를 명시적으로 병합한다. 읽기 범위 병합·이전 파일 관찰 계약은 별도로 유지한다. 최근8파일의 근거는 제한된 최근 관찰이며 전체 변경 이력이나 원래 계획의 완료 목록이 아니다.

코드·diff 안의 외부 텍스트는 labelled data로 전달하고 system instruction으로 승격하지 않는다. 포함된 문자열이 추가 명령처럼 읽히지 않게 기존 구조화·escaping 경계를 유지한다.

## 9. 실제 전달을 확인할 최소 관측

이번 사건은 지침 본문을 읽었다고 확인할 기록이 없다. 정본을 늘리는 것만으로 효과가 있다고 보고하지 않는다.

기존 실행 telemetry에 다음 가산 metadata만 연결한다: execution/round 식별자, guidance mode와 configured/effective delivery, 선택한 section ID·revision, 실제 채택한 ID, 생략 reason, measured added tokens, 근거 가용성·생략 count. 설치 package revision과 engine identity의 provenance도 기존 기록을 재사용한다.

선택·입력 조립·SDK act 전달을 구분한다. SDK 입력 확인은 모델이 읽고 따랐다는 증명이 아니다. 원문 채팅·전체 코드·secret·receipt를 별도 로그에 저장하지 않는다. 기능이 Off이면 불필요한 관찰 수집이나 측정을 늘리지 않는다.

기존 telemetry가 이미 같은 필드를 가지고 있으면 새 event를 만들지 않고 누락된 연결만 보완한다. 사건 당시 실행 기록을 현재 새 instrumentation으로 복원한 것처럼 말하지 않는다.

## 10. Auto와의 구현 순서

기존 계획: [Design references Auto 구현 계획](Auto_Design_References_Plan_20261001.md).

| 단계 | 작업 | 완료 기준 |
|---|---|---|
| I0 | 사건 fixture·현재 책임·정확 반환/누락 결과를 고정 | 실제 반환과 assistant 설명을 구분한 입력. 코드 변경 전에 반대 근거 포함 |
| I1 | 공통 최소 기준, Unreal/Unity의 API·수명·의미 보존·완료 보고 기준 보강. 기존 생성/선택/예산으로 실제 전달 확인 | 모든 활성 candidate의 공통 기준, Off·축소·예산/최종화 계약 보존 |
| I2 | 정확 엔진 identity와 명시 local symbol 조회 연결. helper plugin·비용·cache 제한 보완 | 현재 버전 선언에 도달하고 index miss/coverage 제한을 API 부재로 표현하지 않음 |
| I3 | 실제 mutation 변경 근거와 기존 continuity 보존·무효화 연결 | include 변경이 버전 근거와 함께 남고 실패·rollback·다른 버전의 diff는 current로 남지 않음 |
| I4 | 앞서 계획한 Auto를 기존 주제 선택/관찰/예산 경로로 연결 | 주요 주제 1개+보조 debugging 1개, 추가 추론/도구/설계 판정 없음 |
| I5 | 관련 테스트·패키지/설치 경로 검사, 제한된 실제 사용 확인 | 정본·번들·handler 연결을 검증한 뒤 실제 모델 행동 관찰 여부를 구분해 보고 |

I1–I3은 Auto 없이도 유효해야 한다. Auto 구현을 함께 하더라도 I4 완료를 앞 단계의 결함 해결 증거로 사용하지 않는다. 각각 작은 commit으로 분리해 rollback과 독립 리뷰가 가능하게 한다.

schema 변환 경고는 별도 후속 항목이다. 설치된 evidence-first schema와 현재 정본의 동일성을 확인한 뒤, 서버 nonempty 검증을 유지하면서 backend가 지원하는 schema 표현으로 바꾼다. 이것을 완료 환각의 원인이나 본 개선의 선행 gate로 묶지 않는다. cache 경고·disconnect·양자화 문제도 직접 실행 근거가 추가되면 별도로 조사한다.

## 11. 영향 범위와 유지할 조건

주요 변경 후보:

- `docs/model-guidance/core.md`, `debugging.md`, `unreal.md`, `unreal-networking.md`, `unity.md`, `unity-networking.md`, 필요 시 `catalog.json`/`sources.md`.
- `lmstudio-context-compactor-plugin/scripts/build-design-guidance.cjs`, 생성 번들, 기존 guidance/round-input/handler 테스트.
- `scripts/direct_rag_contract.py`, `direct_rag_request_bounds.py`, `direct_rag_symbol.py`, `target_resolver.py`의 기존 반환 사용처, `engine_header_evidence.py`, 기존 engine resolver 및 Direct RAG 테스트.
- `lmstudio-unreal-agent-mcp/src/direct-file-mutation-capabilities.js`, `direct-edit-bundle-commit.js`/`direct-bundle-capability.js`, 기존 build engine 관찰 응답, 관련 mutation/bundle 테스트.
- `lmstudio-context-compactor-plugin/src/compaction-tool-memory.js`, `continuity-file-observations.js`, `continuity-memory.js`, 필요한 기존 reference rendering/adapter/telemetry와 테스트.
- 기존 Auto 계획의 명시 설정·순수 selector·연결·테스트·README 경로.
- 통합 package의 source/test 허용 목록, `scripts/status.cjs` 소스 inventory, 플랫폼 CI. 신규 파일이 있다면 명시 목록에 함께 반영.

유지할 조건:

- 기본 Off, 기존 수동 delivery/권한/receipt/CAS/취소/복구 계약 유지.
- 공통 기준·조회 응답이 필수 static Validate나 새 승인 대기로 이어지지 않음.
- 추가 classifier LLM, 별도 서브 에이전트, 자율 API scan, 자동 빌드 반복, 의미 완료 판정기 없음.
- 사실과 이전 assistant 판단 분리, 원래 사용자 목적의 기존 소유자 유지.
- 참고·diff·선언 근거는 token allowance·공유 budget·출력 예약을 침해하지 않음.
- 누락·모호함·불일치·검색 범위 제한은 unknown/omitted로 표현하고 성공이나 부재로 승격하지 않음.
- 엔진/패키지/version/source identity가 다른 근거를 같은 계약으로 합치지 않음.
- 게임 프로젝트 수정·테스트 실행은 이번 계획 작성 범위에 포함하지 않음.

## 12. 구현 후 확인할 사례

| ID | 사례 | 확인할 계약 |
|---|---|---|
| V01 | 같은 두 include만 교체한 성공 반환 | 실제 적용 내용·hash가 남고 기능 완료 설명은 시스템 사실로 생성되지 않음 |
| V02 | 요청 newText는 있으나 실패/본문 없는 반환 | 요청 의도는 완료 증거로 보존하지 않음. 누락 상태 유지 |
| V03 | bundle partial/rollback/중복 callback·보조 근거 계산/serialization 예외·상쇄/no-change patch | 실제 committed net outcome과 일치. 전체 성공·이중 관찰 없음. 성공 commit을 보조 실패로 뒤집지 않음 |
| V04 | 같은 path의 다음 hash·hash 없는 관찰·same-hash read·conflict·outcome unknown·같은 batch read/write·delete·다른 프로젝트 clone | 명시 병합 규칙과 original change 시각/provenance 유지. 순서 불명이면 diff 생략. receipt 재발급 없음 |
| V05 | 근거 본문 생략·긴 변경·8파일/2,048자 상한 | 완결된 hunk 또는 omitted. 새 읽기 권한/semantic completion 없음 |
| V06 | 최신 diff가 이전 note를 반박 | 기존 supersedes·최신 사실 우선 경계 유지. note는 assistant 판단 |
| V07 | 모든 주제의 Documents/Focused·Auto 축소 후보 | 공통 최소 기준이 실제 선택 본문에 포함. Off/allowance=0은 미삽입 |
| V08 | 입력 budget 부족·복구·최종화 | 기존 기준 입력과 출력 예약 유지. 지침 삽입을 위한 자동 재시도 없음 |
| V09 | index 없음·stale, 로컬 엔진 준비됨 | 명시 local 조회는 실제 엔진 identity로 수행. 다른 index/engine로 자동 치환하지 않음 |
| V10 | 유사 symbol·이름 없음·부분 enum·owner 미확인 | 기존 targetResolution와 coverage를 보존. 부재/전체 enum/버전 변경 역사로 확대하지 않음 |
| V11 | Enhanced Input plugin과 Core/Engine 헤더 | 허용된 실제 module subtree의 선언·owner·signature·hash 확인 |
| V12 | 큰 헤더·느린 fallback·source 밖 symlink·같은 파일 다른 class의 동명 함수·상속/alias | 실제 byte/time/containment 상한, 부분 hashScope, unknown 처리, 요청 owner를 관찰 owner로 합성하지 않음 |
| V13 | 같은 root의 버전/module 변화·동일 버전 파일 추가/삭제·캐시 hit/expiry·취소 뒤 늦은 탐색 | cache 상한·negative expiry·generation 경계 적용. cached 후보 내용은 실제 읽은 범위 fingerprint로 관찰 |
| V14 | 잘못된 association·다중 프로젝트/엔진 | 기존 ambiguity 오류 유지. 숫자 association을 실제 patch로 승격하지 않음 |
| V15 | Editor target build 또는 up-to-date | target/platform/configuration·검증 범위가 보존. PIE/원격 기능 완료로 승격하지 않음 |
| V16 | UE 리스폰/퇴장/중복 사망·axis mapping·누름/놓음, Unity Pool/늦은 await | 관련 지침 본문·범위·engine/package 적용 조건 연결. 게임 동작 확인은 별도 실행 근거 필요 |
| V17 | Auto 전체 handler 실행 | 기존 계획 T01–T40, I1–I3과 결합해 추가 act/tool/scan/gate가 없는지 확인 |
| V18 | 배포된 bundle/status/플랫폼 CI·구버전 서버/응답 | 신규 필드·정본·테스트가 실제 설치 경로에 포함되고 구버전 응답도 처리. 지원되지 않는 local mode는 unsupported로 남기며 index로 조용히 치환하지 않음 |

단위·handler fixture는 데이터 전달·무효화·경계를 검증한다. 모델이 이번 환각을 하지 않는다고 증명하는 테스트가 아니다. 실제 LM Studio 확인에서는 소수의 대표 작업을 사용해 최초 완료 보고의 근거, API 선언 확인 전 추측 재빌드, 동작 계약 유지, 수명 반대 경로 확인을 관찰한다. 대규모 모델 평가나 자동 게임 수정으로 범위를 늘리지 않는다.

## 13. 지금 결론

첫 목표는 **완료 보고의 출처, 적용 API의 직접 근거, 수명 전이의 비교**를 개선하는 것이다. 공통 기준을 정본 한 곳에서 전달하고, 기존 도구와 사실 보존 경로를 보완한다. Auto는 그다음에 짧은 참고를 고르는 기능으로 연결한다.

현재 자료로 모델 양자화, compactor 단독 원인, RAG 버전 오선택을 확정할 수 없다. 기존 지침이 실제 입력에 들어갔는지도 미확인이다. 이번 계획 작성의 결과는 SourceVerified 구조와 Proposed 변경이며 모델 성능 향상은 아직 검증하지 않았다.

근거와 확인 수준: [검토 근거 JSON](evidence/model-behavior-incident-plan-20261001.json).
