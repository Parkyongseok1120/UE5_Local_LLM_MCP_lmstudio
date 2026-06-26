# Unreal Anti-Patterns

## Critical AI Anti-Patterns

검색 키워드: interface event anti-pattern, fake compile-ready code, generic setter anti-pattern, Unreal API hallucination, design implementation mismatch

1. Interface와 Event를 섞는 패턴
   - 증상: UINTERFACE에 `OnXChanged`, `OnXStarted`, `OnXCompleted` 같은 알림 함수를 넣는다.
   - 문제: 호출 계약과 상태 변경 알림이 섞여서 소유권, 구독 수명, Blueprint 구현 규칙이 흐려진다.
   - 대체: 인터페이스는 Command/Query 계약만 제공하고, 상태 소유 객체가 Delegate/Event를 발행한다.

2. 설계 코드와 구현 코드 불일치
   - 증상: 설계에서는 Delegate 분리, SSOT, `ApplyDamage`를 말하지만 코드에서는 인터페이스 이벤트 함수, 중복 상태, `SetHealth`를 사용한다.
   - 문제: 리뷰는 좋아 보이지만 실제 생성 코드는 규칙을 어긴다.
   - 대체: 설계의 Owner, Command, Query, Event 이름을 구현 코드와 1:1로 맞춘다.

3. 컴파일 가능한 코드처럼 보이는 부정확한 예시
   - 증상: Unreal API 시그니처, UFUNCTION/UINTERFACE 규칙, generated.h 위치, Build.cs 의존성이 틀렸는데 코드 블록만 제시한다.
   - 문제: 로컬 AI가 틀린 패턴을 RAG 근거처럼 재사용한다.
   - 대체: 확실하지 않으면 `Pseudocode only`로 표시하고, compile-ready라고 할 때만 실제 규칙을 검증한다.

4. 일반 setter를 안전한 API처럼 제안
   - 증상: `SetHealth`, `SetShield`, `SetAmmo`, `SetIsHacked` 같은 API가 외부에서 원본 상태를 직접 덮어쓴다.
   - 문제: 검증, 권한, 이벤트 발행, clamp, 로그가 우회된다.
   - 대체: `ApplyDamage`, `RestoreShield`, `ConsumeAmmo`, `ApplyActionAttempt`, `ResolveActionAttempt`처럼 의도가 드러나고 재검증 가능한 Mutation API를 사용한다.

## 목적

로컬 AI가 자주 만드는 Unreal C++ 위험 패턴을 금지하고, 대체 패턴을 제시한다.

검색 키워드: Unreal anti-pattern, bad pattern, God Actor, duplicated state, Tick, GetAllActorsOfClass, raw pointer, RPC, Blueprint, namespace, AI code quality

## 구조 안티패턴

1. God Actor
   - 증상: Character 하나가 입력, UI, 인벤토리, 퀘스트, 저장, 전투, 오디오를 모두 처리한다.
   - 대체: ActorComponent, PlayerState, Subsystem, DataAsset으로 책임을 나눈다.

2. Manager 남발
   - 증상: 모든 기능에 `Manager` 싱글턴을 만들고 전역 접근한다.
   - 대체: WorldSubsystem/GameInstanceSubsystem/Component 중 수명과 범위에 맞는 소유자를 고른다.

3. 중복 상태
   - 증상: Health가 Character, Widget, PlayerState에 각각 존재한다.
   - 대체: HealthComponent 또는 AttributeSet 같은 하나의 원본을 두고 UI는 이벤트/쿼리로 표시한다.

4. UI가 게임플레이 상태를 변경
   - 증상: Widget이 직접 탄약, 체력, 인벤토리 배열을 수정한다.
   - 대체: UI는 PlayerController/Component/Ability에 Command를 요청한다.

## Unreal C++ 안티패턴

1. UObject를 `new`로 생성
   - 대체: `NewObject`, `CreateDefaultSubobject`, SpawnActor 등 Unreal 생성 API를 사용한다.

2. UPROPERTY 없는 UObject 멤버 포인터
   - 대체: 소유/강참조는 UPROPERTY/TObjectPtr, 약참조는 TWeakObjectPtr을 사용한다.

3. generated.h 뒤에 include 추가
   - 대체: `.generated.h`를 헤더 include의 마지막으로 유지한다.

4. 불필요한 namespace 안에 UCLASS 배치
   - 대체: reflection 대상 타입은 일반 Unreal 타입 선언 스타일을 따른다.

5. 헤더 include 과다
   - 대체: 헤더는 forward declaration, cpp는 실제 include를 사용한다.

6. Build.cs 의존성 누락
   - 대체: 새 API를 쓰면 모듈 이름을 확인하고 Public/PrivateDependencyModuleNames를 갱신한다.

## 런타임 안티패턴

1. Tick에서 전체 탐색
   - 증상: `GetAllActorsOfClass`, asset scan, component search를 매 프레임 수행한다.
   - 대체: BeginPlay 캐시, overlap event, delegate, timer, subsystem registry를 사용한다.

2. Delegate 해제 누락
   - 증상: 파괴된 객체 콜백, 중복 바인딩, PIE 종료 crash.
   - 대체: EndPlay/NativeDestruct/OnUnregister에서 Remove/Unbind/ClearTimer를 수행한다.

3. Lambda에서 `this` 강한 캡처
   - 증상: 비동기 완료 시 파괴된 객체 접근.
   - 대체: TWeakObjectPtr로 캡처하고 실행 시 유효성 확인한다.

4. Constructor에서 World 접근
   - 증상: CDO 생성 중 World/PlayerController/GameInstance 접근.
   - 대체: BeginPlay, Init, OnRegister 등 적절한 생명주기에서 접근한다.

## 네트워크 안티패턴

1. 클라이언트 신뢰
   - 증상: 클라이언트가 데미지, 보상, 위치 판정을 확정한다.
   - 대체: 서버 검증 Command와 복제 결과를 사용한다.

2. Multicast로 상태 원본 변경
   - 증상: Multicast RPC가 각 클라이언트 상태를 따로 바꾼다.
   - 대체: 서버 원본 상태를 바꾸고 Replication/OnRep로 동기화한다.

3. OnRep에서 gameplay 판정
   - 증상: OnRep가 다시 데미지 계산, 보상 지급, 스폰을 수행한다.
   - 대체: OnRep는 표시 동기화와 cosmetic 반응에 제한한다.

## AI 생성 코드 특별 금지

- 존재 확인 없이 Unreal API 이름을 만들어내지 않는다.
- 프로젝트에 없는 로그 카테고리, 모듈, 태그, DataAsset 경로를 확정적으로 쓰지 않는다.
- "예시니까 대충"이라는 이유로 컴파일 안 되는 include와 매크로를 방치하지 않는다.
- 책임 소유자를 설명하지 않은 채 새 전역 접근점을 만들지 않는다.
