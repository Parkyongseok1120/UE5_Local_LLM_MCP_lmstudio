# 모델 행동 개선 구현 기록 — 2026-10-03

기준 commit: `6893493`. 사용자 요청: 추가 검토 후 개선 구현. 사건 분석과 Auto 계획을 현재 코드에 연결했으며 게임 프로젝트 수정, LM Studio 설정 변경·재시작, 실제 모델 호출, commit/push/release는 실행하지 않았다.

## 구현한 내용과 담당자

| 기능 | 정본·기존 담당자 | 변경 및 경계 |
|---|---|---|
| 공통 API·수명·완료 기준 | `docs/model-guidance/core.md`, 기존 catalogue/compiler | 활성 참고에 공통 최소 기준을 포함. 주제·엔진 문서는 사례별 세부 조건만 보완 |
| Auto | 기존 목적 continuity → 순수 `guidance-topic-selection` → 기존 guidance/round-input | 확인된 현재 사용자 목적에서 주요 주제 하나, 유효한 현재 관찰에서 debugging 보조만 선택. 별도 실행 상태·추론·API 조회 없음 |
| 로컬 Unreal API | 기존 project/engine resolver → `direct_rag_symbol` → 기존 header helper | 명시 `engine_local` 요청만 설치 소스 조회. index가 없어도 동작. 관찰한 Build.version·root·byte 범위/hash를 반환 |
| 실제 변경 근거 | 기존 Direct Agent CAS/commit → 작은 순수 diff helper | 성공적으로 적용된 전후 내용에서만 산출. 부가 계산·응답 크기 문제로 기존 성공/hash/receipt를 실패로 뒤집지 않음 |
| 근거의 수명과 압축 | 기존 EvidenceManager → tool/file facts → continuity/checkpoint | 실행별 provider/호출 대응, 프로젝트/hash 및 순서 검증. 추가 DB나 완료 판정기 없음 |
| 설치·배포 | 기존 integrated builder/status/CI inventory | 신규 helper·source·검사와 이름 resolver CLI 의존성을 명시 목록에 연결 |

공통 정책은 문서 정본, 프로젝트 선택은 기존 resolver, 수정 성공은 CAS/commit, 관찰 수명은 EvidenceManager, 압축은 기존 continuity, 예산은 기존 BudgetBroker/ContextManager가 맡는다. 새 helper는 입력→출력 변환만 수행한다. Auto는 지침 선택 이상의 권한을 갖지 않는다.

## 기본값과 사용

- Design references 기본값은 **Off**. Off에서 Auto가 암묵적으로 켜지지 않는다.
- Auto를 명시 선택하면 실행 시 Focused를 사용한다. 사용자가 저장한 delivery 값은 바꾸지 않는다.
- 후보는 최대 4개, 후보당 최대 6절이며 기존 token allowance와 정확한 입력 측정을 통과해야 한다.
- 목적이 잘리거나 출처가 불명확하면 core로 축소한다. 명확한 참고 제외 요청이면 생략한다.
- Observe only, allowance=0, 복구·최종화에는 기존 생략 계약을 적용한다.
- 모델 호출·도구 실행·정적 검사·빌드를 추가하지 않는다. 자연어의 모든 표현을 이해한다고 보장하지 않는다.

## API 근거의 한계와 수명

`unreal_symbol_lookup({query: "AActor::SetLifeSpan", sourceMode: "engine_local", project: "절대 .uproject 경로"})` 형태로 요청한다. 기본 `index` 동작은 유지한다. 이름 선택은 기존 이름 resolver로 정확한 프로젝트를 확인하고, 경로가 확정된 이후 binding 실패도 해당 프로젝트의 실패 관찰로 남긴다.

헤더 탐색은 Core/CoreUObject/Engine, Input 관련 EnhancedInput의 파일명 후보에 한정한다. 모듈별 최대 4096개 헤더/16384개 디렉터리 엔트리, 최대 4개 파일, 파일당 256 KiB, 총 1 MiB와 헤더 단계 3초 deadline 확인을 사용한다. 디렉터리·파일의 실제 경로가 대상 subtree에 포함되어야 한다. 동기 OS I/O를 즉시 중단하거나 전체 tool transport latency를 3초로 제한하는 계약은 아니다.

완료된 파일명 catalog만 최대 8개/30초 동안 캐시한다. key에는 실제 module root와 Build.version hash가 포함된다. 내용은 매번 다시 읽는다. clear generation 이후의 늦은 탐색은 cache에 쓰지 않는다. stale positive 읽기 실패는 해당 목록을 폐기하고 다음 명시 요청에서 탐색한다. negative query cache와 같은 호출 안 추가 재탐색은 없다.

댓글·문자열·다른 class/nested class·member initializer·friend·decltype/static_assert의 호출을 owner 선언으로 승격하지 않는다. UE reflection annotation을 제한적으로 분리해 UFUNCTION/UMETA 선언도 찾는다. 결과는 lexical evidence이며 상속·전처리 결과·overload·실제 호출 가능성을 확정하지 않는다. no match는 API 부재가 아니다.

부분 읽기 hash는 `hashScope: byte_range`, 범위와 `completeFile: false`로 표시한다. Unreal 적용 버전은 관찰한 엔진 identity에서만 생성하며 프로젝트 안의 Unity 형식 파일로 오염되지 않는다. 모순된 같은 batch나 이후 실패한 binding은 이전 버전 적용 근거를 무효화한다.

## 변경 근거의 한계와 수명

producer는 전후 합산 2,097,152자의 작업 범위에서 실제 CAS 전후 hash를 대조한다. 작은 변경 영역 하나를 최대 768자로 보존하고 큰 변경은 metadata만 반환한다. bundle의 기존 공개 patch/file 상한은 늘리지 않는다.

consumer는 현재 실행의 정확한 provider/name/unique call ID/반환 내용 대응을 기존 EvidenceManager에 최대 2048개로 기록한다. 기존 대화의 raw 결과에 현재 tool registry의 신뢰를 소급 부여하지 않는다. 상한에 도달하면 추가 변경 근거 수집을 비활성화한다.

본문은 최대 8개/2048자로 보존하며 시스템 checkpoint에는 hash·출처·digest만 남긴다. 별도 assistant 데이터는 digest와 canonical file identity가 일치할 때만 복원한다. 기존 reasoning separator가 코드 문자열에 있더라도 저장 시 데이터가 잘리지 않도록 해당 delimiter를 JSON에서 손실 없이 escape한다. 기존 비밀·capability 정리는 유지한다.

다른 프로젝트·hash·실패·충돌·hash 없는 관찰과 같은 파일의 순서 불명 batch는 본문을 무효화한다. raw로 남긴 결과와 최근 64개 durable file 제한 밖의 부정 관찰도 기존 후보의 무효화 판단에 포함한다. 같은 hash의 확인된 읽기는 원래 수정 origin을 유지할 수 있다. 본문은 재읽기, edit authority 또는 기능 완료 증거가 아니다.

mandatory/emergency floor에는 변경 본문이 없다. 정상 압축에서도 기존 사실·이전 판단을 우선 배치한 뒤 남는 전체 checkpoint 예산으로만 본문을 추가한다. 전체 데이터 항목이 맞지 않으면 생략한다.

## 추가 리뷰에서 고친 경계

- Auto의 오래된 진단 사용, 광범위한 주제 단어 매칭, 보조 pack 생략 사유 누락.
- 중첩 fence 예시가 사용자 목적처럼 처리되는 경우와 명확한 참고 제외 표현.
- 로컬 후보 필드와 기존 target resolver 사이의 키 계약 불일치.
- 다른 함수 호출의 owner 오인, reflection annotation 때문에 실제 API를 놓치는 경우.
- bundle child의 프로젝트 불일치, byte 0/newline 수정의 diff 범위 오류.
- split assistant request batch, retained/omitted 결과 혼합과 최신 관찰의 보존 상한에 따른 무효화 누락.
- assistant 데이터 저장의 delimiter 충돌과 광범위 escape가 기존 redaction을 우회하는 문제.

독립 검토는 evidence-first-code-audit의 큰 구조 변경 검토 지침에 따라 기존 검토자 세 명이 읽기 전용으로 수행했다. 실제 실행에 추가 agent가 생기는 기능은 구현하지 않았다.

## 확인 범위

검사 명령과 최종 결과는 구현 근거 JSON에 기록한다. TypeScript build, compactor/agent 자동 검사, Direct RAG·응답 크기·header identity·통합 package 검사를 수행했다. GUI 설치나 실제 LM Studio 대화의 품질 향상은 확인하지 않았다.

| 검사 | 최종 결과 |
|---|---|
| Compactor `npm test` (guidance 생성·TypeScript build 포함) | 488 통과, 실패 0 |
| Unreal Agent `npm test` | 250 통과, POSIX 전용 1개 Windows skip, 실패 0 |
| Python 관련 5개 suite | 73 통과, 실패 0 |
| Portable runtime 격리 import | 배포 허용 목록만 복사한 경로에서 Python `-I` import 성공; Python suite에 포함 |
| 근거 JSON | 구현/codegen 및 기존 두 계획 packet 검사 통과 |
| `git diff --check` | 통과 |

Python 명령: `python -m pytest tests/test_direct_rag_engine_local.py tests/test_engine_header_evidence_identity.py tests/test_python_direct_rag_server.py tests/test_direct_rag_response_budget.py tests/test_integrated_package.py -q`. 패키지 생성·zip inventory·로컬 문서 링크 검사가 포함된다. 배포에 포함되지 않는 내부 구현 문서 링크는 portable README에서 제거했다.

설치된 UE 5.7을 읽기 전용으로 조회하여 다음 실제 소스 반환을 확인했다.

| 조회 | 실제 위치 | 결과 |
|---|---|---|
| FDamageEvent | Engine/Classes/Engine/DamageEvents.h:15 | lexical 선언 후보 |
| AActor::SetLifeSpan | Engine/Classes/GameFramework/Actor.h:2310 | 전처리 조건이 있어 candidate_conditional, 부분 byte-range hash |
| EInputActionValueType::Axis2D | EnhancedInput/Public/InputActionValue.h:17 | enum 선언 후보 |

이는 설치 헤더 조회의 확인이다. 게임 빌드·PIE·멀티플레이 실행 및 모델이 지침을 실제로 준수한다는 증거가 아니다. Linux/macOS CI는 로컬 Windows 검사와 별도로 필요하다.

근거: [model-behavior-implementation-20261003.json](evidence/model-behavior-implementation-20261003.json).
