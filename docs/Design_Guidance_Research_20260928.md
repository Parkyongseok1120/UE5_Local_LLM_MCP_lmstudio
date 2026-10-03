# 설계·생명주기·멀티플레이 지식 보강 조사

- 작성일: 2026-09-28
- 상태: **조사 경위 보관. 후속 문서 공급 구현은 [연결 및 구현 문서](Design_Guidance_Integration_Proposal_20260928.md)에 기록한다.**
- 목적: 모델이 기존 소유자·계약·수명을 이해하고 Unity·Unreal 코드를 설계할 때 참고할 지식을 선정한다.
- 후속 산출물: [주입용 참고 문서 묶음](model-guidance/README.md), [현재 구조와 연결 제안](Design_Guidance_Integration_Proposal_20260928.md), [게임 소스 검토 근거](evidence/combat-source-audit-20260928.json). 이 문서는 조사 경위와 근거를 보관한다. 구현, 설치, 프롬프트 변경, 정적 검사 추가, 빌드 실행은 진행하지 않았다.
- 확인 수준: 공식 문서의 계약·설명은 문헌 확인. 9절의 사례는 사용자 제공 기록과 설치된 UE 5.7 소스를 대조했다. 수록 우선순위와 활용 방식은 제안이다. 최종 빌드 성공은 사용자 제공 후속 기록에 보고되어 있으며, 이번 조사에서 재실행하거나 게임 동작을 검증하지 않았다.

## 1. 조사 결론과 먼저 넣을 것

우선 구성은 **오류와 실제 정의를 연결하는 방법 + 공통 설계 원칙 + 엔진별 수명·계약 + 멀티플레이 공통 개념 + 엔진별 네트워크 계약 + 짧은 프로젝트 현황**을 권장한다. 이름만 나열하는 패턴 사전보다, 적용 조건과 실패 사례를 함께 제공하는 편이 이번 문제에 적합하다.

| 순서 | 수록 묶음 | 줄이려는 실수 |
| --- | --- | --- |
| 1 | 컴파일 오류 위치, 심볼 정의, 부모 계약을 확인하는 방법 | 잘못 기억한 API·헤더·매크로를 근거로 추측 수정을 반복함 |
| 2 | 수정 범위와 의미 보존, 결과 확인 수준 | 컴파일을 통과시키기 위해 판정 방식을 바꾸거나, 다른 오류가 나오면 기존 오류가 해결됐다고 단정함 |
| 3 | 상태의 결정권, 변경 경로, 계약, 수명 | 같은 상태를 여러 관리자가 따로 확정함. 파괴된 객체에 비동기 결과를 적용함 |
| 4 | SSOT·SOLID와 과도한 추상화의 경계 | SSOT를 전역 Singleton으로 구현함. 기능 하나에 불필요한 계층을 만듦 |
| 5 | Unity·Unreal의 초기화·종료·참조 규칙 | 일반 C#/C++ 관습을 엔진 객체에 그대로 적용함 |
| 6 | 멀티플레이 권한·복제·메시지·시간 | 소유권을 권한으로 착각함. RPC를 저장된 상태처럼 사용함 |
| 7 | 예측·보정·지연 참가·재접속·장면 전환 | 로컬/호스트에서만 성공하는 구조, 재시뮬레이션 중 중복 효과 |
| 8 | 문제별 패턴과 짧은 사례 | 이유 없이 패턴을 도입하거나 엔진에 이미 있는 기능을 중복 구현함 |

이 목록을 모든 작업에 강제로 실행하는 체크리스트로 만들지는 않는다. 실제 오류와 일반적인 설계 선호를 구분하고, 관련 항목만 참고하도록 하는 것이 제안의 전제다.

## 2. 지식의 종류와 적용 범위

| 종류 | 의미 | 자료에 적을 내용 |
| --- | --- | --- |
| 엔진·라이브러리 계약 | 특정 버전이 실제로 보장하는 동작 | 적용 버전, 호출 조건, 보장 범위, 보장하지 않는 것 |
| 일반 설계 원칙 | 변경 비용과 오류 가능성을 줄이는 판단 기준 | 목적, 적용 조건, 반례, 비용 |
| 디자인 패턴 | 반복되는 문제의 해결 형태 | 해결할 문제, 더 단순한 대안, 도입·해제 조건 |
| 프로젝트 관례 | 해당 프로젝트가 선택한 구조 | 실제 소유자와 경로, 채택 이유, 예외 |
| 조사 중인 가설 | 아직 소스·실행으로 확인하지 못한 판단 | 미확인 부분, 확인에 필요한 자료 |

SOLID나 패턴을 모든 상황에 동일하게 적용되는 절대 법칙으로 표현하면 과잉 설계를 유도할 수 있다. Unity의 공식 패턴 안내도 패턴을 상황에 맞게 선택할 도구로 설명한다. [Unity: 디자인 패턴과 SOLID 안내](https://unity.com/blog/game-programming-patterns-update-ebook)

### 문헌 버전

- Unity 일반 계약은 Unity 6.0 문서를 확인했다.
- Unity 네트워크는 **Netcode for GameObjects 2.7.0**, **Netcode for Entities 1.10.0**을 구분해 확인했다. 프로젝트가 해당 버전을 사용한다고 가정하지 않는다.
- Epic의 이번 조회 문서는 주로 Unreal Engine 5.8로 표시되었다. 적용할 프로젝트가 5.7 등 다른 버전이면 해당 버전의 문서·헤더·구현으로 다시 대조해야 한다.
- 특정 버전 문서의 존재를 최신 안정 버전이나 현재 설치 버전의 증거로 사용하지 않는다.
- 실제 프로젝트의 네트워크 패키지, 토폴로지, 설정, 런타임 동작은 이번 조사에서 확정하지 않았다.
- 후속 사례의 `Latency_MultiCombat.uproject`는 `EngineAssociation: 5.7`을 확인했다. 해당 사례의 엔진 선언은 로컬 UE 5.7 설치본과 대조했다.

## 3. 공통 설계 원칙에 넣을 내용

다음의 질문·오용 예시는 이번 목적에 맞춘 수록 제안이다. 원칙의 이름을 만족하는지보다 상태 변경과 의존성이 어떻게 움직이는지를 설명하게 한다.

| 항목 | 넣을 핵심 질문 | 함께 넣을 오용 사례 |
| --- | --- | --- |
| SSOT | 이 사실을 최종 확정하는 곳은 어디인가? 복사본의 갱신·폐기 책임은 누구에게 있는가? | 모든 상태를 하나의 전역 관리자에 합침. 캐시나 네트워크 복제본을 무조건 금지함 |
| DRY | 같은 업무 규칙을 여러 곳에서 따로 수정하고 있는가? | 모양이 비슷하지만 변경 이유가 다른 코드를 공통화함 |
| SRP | 누가 요구하는 어떤 변경 때문에 이 단위가 바뀌는가? | 메서드 수나 파일 길이만 보고 무조건 분리함 |
| OCP | 실제로 반복되는 변경 지점을 기존 계약으로 확장할 수 있는가? | 아직 필요 없는 확장 지점을 모두 인터페이스로 만듦 |
| LSP | 구현을 바꿔도 호출자의 유효한 입력, 결과, 실패, 불변 조건이 유지되는가? | 타입만 맞으면 대체 가능하다고 봄 |
| ISP | 소비자가 사용하지 않는 기능까지 의존해야 하는가? | 관계없는 소비자를 거대한 인터페이스 하나로 묶음 |
| DIP | 핵심 규칙이 외부 저장소·UI·전송 구현 때문에 바뀌는가? | 모든 클래스에 인터페이스·DI 컨테이너를 붙임 |
| 응집도·결합도·캡슐화 | 함께 바뀌는 규칙이 모여 있는가? 내부 상태를 누가 직접 수정하는가? | 작은 파일 여러 개가 서로 내부 상태를 조작함 |
| 합성·상속 | 역할 조합이 필요한가, 안정적인 대체 관계가 필요한가? | 엔진 상속까지 제거하려고 불필요한 래퍼를 쌓음 |
| KISS·YAGNI | 현재 요구를 만족하는 가장 단순한 형태는 무엇인가? | 미래 확장성만을 이유로 프레임워크를 만듦. 반대로 필요한 정리까지 미룸 |
| 명령·조회 분리 | 이름과 계약을 보고 상태 변경 여부를 알 수 있는가? | 조회 API가 숨겨진 갱신·삭제를 수행함. 모든 명령의 결과 반환까지 금지함 |
| 계약·불변 조건 | 입력, 결과, 실패, 부분 성공, 중복 요청의 의미가 무엇인가? | 예외를 삼켜 성공처럼 보이게 함. 실패 시 이미 수행된 변경을 설명하지 않음 |
| 수명·취소·재진입 | 시작한 작업·구독·자원을 누가 종료하는가? 늦은 결과는 어떻게 처리하는가? | 모든 취소 책임을 하위 객체에 떠넘김. 종료 후 도착한 결과를 적용함 |

원칙 설명의 근거: [SRP 원저자 설명](https://blog.cleancoder.com/uncle-bob/2014/05/08/SingleReponsibilityPrinciple.html), [Liskov·Wing의 행동적 하위 타입 논문](https://www.cs.cmu.edu/~wing/publications/LiskovWing94.pdf), [의존성 방향에 대한 Clean Architecture 설명](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html), [YAGNI](https://martinfowler.com/bliki/Yagni.html), [Command Query Separation](https://martinfowler.com/bliki/CommandQuerySeparation.html).

### SSOT의 실용적인 설명

SSOT 자료에는 최소한 다음 구분을 넣는다.

- **정의 데이터**: 아이템 설정, 능력치 기본값, 콘텐츠 식별자.
- **권위 있는 실행 상태**: 특정 매치·객체의 현재 체력, 소유 아이템, 진행 상태.
- **파생·표시 상태**: UI 수치, 캐시, 클라이언트 복제본, 예측 표시값.
- **영속 상태**: 세션을 넘어 보존할 저장 데이터와 저장 책임.

복제본이나 캐시의 존재만으로 SSOT 위반이라고 판단하지 않는다. 무엇이 확정값이고 언제 다시 맞추는지 설명할 수 있는지가 중요하다. 예를 들어 권한 있는 게임 상태와 클라이언트 표시값의 분리는 NGO의 anticipation에서도 명시적으로 사용된다. [NGO 2.7: Client anticipation](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/advanced-topics/client-anticipation.html)

### 책임과 수명에 관한 짧은 질문

1. 누가 생성하고 누가 변경을 허용하는가?
2. 누가 참조를 보관하며 어느 시점까지 유효한가?
3. 성공·실패·취소·재시작 시 누가 정리하는가?
4. 한 번 더 호출되거나 이전 실행의 결과가 늦게 와도 계약이 유지되는가?
5. 다른 Scene/World/매치/연결에서도 같은 객체를 사용해도 되는가?

모든 코드에 문답을 붙이는 규칙 대신, 관련 구조를 설명하는 짧은 참조 항목으로 수록한다.

## 4. 디자인 패턴 후보와 도입 조건

| 후보 | 유용한 상황 | 비용·피해야 할 적용 | 참고 |
| --- | --- | --- | --- |
| Strategy | 조준·타겟 선정·비용 계산처럼 실제로 교체되는 정책 | 단일 구현을 위해 계층을 미리 늘림 | [Unity 패턴 안내](https://unity.com/blog/game-programming-patterns-update-ebook) |
| State / FSM | 상태에 따라 허용 동작과 진입·종료 처리가 달라짐 | 단순 플래그를 무조건 클래스로 분해. 동시에 존재하는 독립 상태를 하나의 거대 FSM에 결합 | [저자 원문: State](https://raw.githubusercontent.com/munificent/game-programming-patterns/master/book/state.markdown) |
| Observer | 하나의 사실을 UI·음향 등 여러 소비자에 알림 | 구독 해제·호출 중 재진입·순서·예외 책임을 숨김 | [저자 원문: Observer](https://raw.githubusercontent.com/munificent/game-programming-patterns/master/book/observer.markdown) |
| Command | 입력 전달, 예약 실행, 기록·재실행이 실제로 필요함 | 단순 호출도 모두 객체화. 재실행 가능한 명령과 외부 효과를 혼합 | [저자 원문: Command](https://raw.githubusercontent.com/munificent/game-programming-patterns/master/book/command.markdown) |
| Factory | 생성 조건과 초기화 책임이 여러 호출부에서 반복됨 | 엔진 생성 경로와 별도의 소유권 체계를 만듦 | [Unity 패턴 안내](https://unity.com/blog/game-programming-patterns-update-ebook) |
| Component / 합성 | 서로 다른 기능을 조합하고 각 변경 이유를 분리함 | 컴포넌트끼리 순환 참조하거나 인스펙터 연결이 숨은 필수 조건이 됨 | [저자 원문: Component](https://raw.githubusercontent.com/munificent/game-programming-patterns/master/book/component.markdown) |
| Object Pool | 생성·폐기 비용이 실제로 문제가 되는 반복 객체 | 반환 뒤 참조 사용, 이전 상태·구독·타이머 유지, 과다 보관 | [저자 원문: Object Pool](https://raw.githubusercontent.com/munificent/game-programming-patterns/master/book/object-pool.markdown) |
| MVP / MVVM | 복잡한 UI 표시와 게임 규칙의 변경 이유가 다름 | UI 바인딩과 별도로 같은 게임 상태를 다시 소유함 | [Unity 패턴 안내](https://unity.com/blog/game-programming-patterns-update-ebook) |
| 의존성 주입 / Composition Root | 외부 의존성과 생성·해제 책임을 명시해야 함 | 컨테이너가 필수라고 가정. MonoBehaviour/UObject 생성 계약을 무시함 | [Fowler: Dependency Injection](https://www.martinfowler.com/articles/injection.html) |
| Singleton / Service Locator | 실제로 하나인 범위와 엔진 제공 접근점이 존재함 | 프로세스 전역을 World·매치·플레이어 범위와 혼동. 숨은 의존성 증가 | [저자 원문: Singleton](https://raw.githubusercontent.com/munificent/game-programming-patterns/master/book/singleton.markdown), [Fowler: DI와 Service Locator](https://www.martinfowler.com/articles/injection.html) |

Adapter·Facade도 외부 SDK나 레거시 경계를 다룰 때 추가할 후보로 남긴다. 상세 사례와 원전 대조는 후속 작성 범위다. Event Bus, Repository, CQRS, Event Sourcing, ECS 전환은 프로젝트의 구체적인 필요가 확인됐을 때 별도 자료로 다룬다.

## 5. Unity 전용으로 먼저 넣을 내용

| 항목 | 확인한 계약 또는 수록할 질문 | 주의할 잘못된 일반화 |
| --- | --- | --- |
| 콜백 순서 | 다른 GameObject 사이의 같은 종류 콜백 순서를 임의로 가정하지 않는다. 초기화 의존성이 있으면 보장 근거를 찾는다. [Execution order](https://docs.unity3d.com/6000.0/Documentation/Manual/execution-order.html) | 모든 객체의 Awake/Start 순서가 코드 배치대로 실행된다고 생각함 |
| 활성화 수명과 객체 수명 | 구독이 활성화 동안 필요한지, 객체가 존재하는 동안 필요한지 구분하는 사례를 넣는다. [Execution order](https://docs.unity3d.com/6000.0/Documentation/Manual/execution-order.html) | 모든 구독을 무조건 OnEnable/OnDisable에 배치함. 비활성 상태에서도 필요한 알림을 끊음 |
| Domain Reload | 비활성화 시 static 값과 static 이벤트 핸들러가 Play 사이에 남는 조건과 초기화 책임. [Domain reloading](https://docs.unity3d.com/6000.0/Documentation/Manual/domain-reloading.html) | Play를 다시 누르면 모든 전역 상태가 초기화된다고 가정함 |
| ScriptableObject | 공유 데이터 저장과 인스턴스 실행 상태의 경계. 런타임 변경과 영속 저장 계약은 따로 적는다. [ScriptableObject](https://docs.unity3d.com/6000.0/Documentation/Manual/class-ScriptableObject.html) | ScriptableObject는 항상 불변이라고 설명하거나, 공유 에셋에 모든 개체의 현재 체력을 저장함 |
| 비동기 처리 | Coroutine, Task, Awaitable의 차이, 취소 주체, 종료 뒤 결과, continuation 실행 문맥을 자료화한다. 세부 API는 버전별 추가 대조 대상. [Async support](https://docs.unity3d.com/6000.0/Documentation/Manual/async-await-support.html) | async라는 이유만으로 객체 파괴와 자동 연동되거나 모든 코드가 메인 스레드라고 가정함 |
| 어셈블리 경계 | asmdef를 통한 의존성, Runtime/Editor·테스트·플랫폼 경계를 설명한다. [Assembly definitions](https://docs.unity3d.com/6000.0/Documentation/Manual/assembly-definition-files.html) | 폴더·파일 분리만으로 의존성 경계가 보장됐다고 봄 |
| 풀링 | 대여·반환 때 상태·이벤트·작업을 재설정하는 계약을 넣는다. [ObjectPool API](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Pool.ObjectPool_1.html) | Instantiate/Destroy를 줄였다는 이유로 수명이 해결됐다고 판단함 |

## 6. Unreal 전용으로 먼저 넣을 내용

| 항목 | 확인한 계약 또는 수록할 질문 | 주의할 잘못된 일반화 |
| --- | --- | --- |
| Gameplay Framework | GameInstance, GameMode, GameState, PlayerState, Controller, Pawn의 위치·역할·지속 범위를 먼저 읽게 한다. [Gameplay Framework](https://dev.epicgames.com/documentation/unreal-engine/gameplay-framework-in-unreal-engine) | GameInstance가 자동 복제된다고 봄. 클라이언트 UI가 GameMode를 전제로 동작함 |
| Subsystem | 엔진이 관리하는 범위와 Initialize/Deinitialize 책임을 설명한다. 추가 타입은 실제 프로젝트 버전에서 확인한다. [Subsystems](https://dev.epicgames.com/documentation/en-us/unreal-engine/programming-subsystems-in-unreal-engine) | 기능마다 전역 Subsystem을 만들거나 짧은 수명의 객체 참조를 무기한 보관함 |
| EndPlay와 메모리 수명 | Actor가 레벨에서 제거되는 시점과 메모리 회수 시점을 구분한다. 종료 사유도 구분한다. [AActor](https://dev.epicgames.com/documentation/unreal-engine/API/Runtime/Engine/AActor), [EndPlay reasons](https://dev.epicgames.com/documentation/unreal-engine/API/Runtime/Engine/EEndPlayReason__Type) | 게임플레이 정리를 C++ 소멸자에만 둠 |
| UObject 참조 | UPROPERTY로 추적되는 TObjectPtr, 비소유 weak 참조, soft asset 참조의 용도를 구분한다. [Object pointers](https://dev.epicgames.com/documentation/en-us/unreal-engine/object-pointers-in-unreal-engine) | TObjectPtr만 쓰면 어디서나 GC 유지가 된다고 봄. 임시 포인터도 모두 같은 방식으로 변환함 |
| Timer·Delegate·비동기 | 타이머 대상 객체와 임의 캡처 객체를 구분하고, 핸들·바인딩·취소 책임을 적는다. 타이머 대상 객체 파괴 시 취소 계약의 범위를 넘겨 일반화하지 않는다. [Gameplay timers](https://dev.epicgames.com/documentation/en-us/unreal-engine/gameplay-timers-in-unreal-engine) | 어떤 람다 캡처도 자동으로 안전해진다고 봄 |
| 모듈 경계 | Public/Private 노출과 Build.cs 의존성을 설명한다. Editor 전용 기능이 Runtime 경계로 새지 않는지 살핀다. [Modules](https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-engine-modules) | C++ 접근 제한자와 모듈 공개 범위를 동일하게 봄 |
| 서로 다른 소유 개념 | 상태 변경 책임, GC 참조, Actor Owner/owning connection을 별개로 기록한다. [Owning connection](https://dev.epicgames.com/documentation/en-us/unreal-engine/actor-owner-and-owning-connection-in-unreal-engine), [Object pointers](https://dev.epicgames.com/documentation/en-us/unreal-engine/object-pointers-in-unreal-engine) | Owner 설정을 메모리 수명이나 게임 규칙 권한의 해결책으로 사용함 |

프로젝트 관례 항목에는 사용자가 지정한 **Unreal 프로젝트에서 네임스페이스 사용을 가급적 피한다**는 조건을 별도로 둔다. 이를 Unreal 전체의 엔진 제약이라고 설명하지 않는다. UObject 생성·리플렉션·Blueprint 노출 상세는 실제 코드 예시를 작성할 때 별도 공식 자료와 엔진 소스로 보강할 대상이다.

## 7. 멀티플레이: 공통 지식 묶음

멀티플레이는 우선 수록 대상이다. SOLID를 잘 나눠도 권한·시간·메시지 계약이 틀리면 게임 상태가 어긋날 수 있다. 아래 내용은 엔진별 API와 구분해서 설명할 공통 주제다.

| 주제 | 자료에 넣을 내용 | 실패 사례 |
| --- | --- | --- |
| 토폴로지 | 전용 서버, 호스트/Listen Server, 분산 권한의 차이. 프로젝트가 선택한 신뢰 모델 | 모든 Unity 프로젝트를 서버 권한으로 가정함 |
| Authority / Ownership / Local control | 상태 확정권, 연결 소유권, 로컬 입력 주체, 관찰자를 구분 | 내 캐릭터를 소유하므로 데미지·아이템 결과도 마음대로 확정해도 된다고 봄 |
| 상태와 메시지 | 현재 상태 동기화, 요청, 순간 효과를 구분. 늦게 참가한 사람이 무엇을 받아야 하는지 설명 | 문을 여는 RPC만 보내고 열린 상태를 보존하지 않음 |
| 입력과 판정 | 입력 의도 전달, 권한 측 규칙 검사, 결과 확정, 클라이언트 표시의 책임 | 클라이언트가 보낸 명중·가격·보상을 그대로 확정함 |
| 시간 | 렌더 프레임, 시뮬레이션 tick, 전송 빈도, 서버 시간, 보간 지연의 차이 | FixedUpdate와 네트워크 tick이 항상 같은 시간이라고 가정함 |
| 지연 대응 | 보간, 외삽, anticipation, 입력 예측, reconciliation, rollback의 차이 | 시각적으로 부드러워졌다는 이유로 상태 일치까지 해결됐다고 봄 |
| 재시뮬레이션 | 입력·상태 기록과 일회성 효과의 경계. 사운드·보상·외부 저장의 중복 방지 | 예측 재실행 때마다 효과·구매·보상을 재발행함 |
| 전달 보장 | Reliable/Unreliable의 비용, 보장 범위, 연결 단절, 객체별 순서 | Reliable이면 전역 순서·영구 보관·업무의 정확히 한 번 실행까지 보장된다고 봄 |
| 지연 참가·재접속 | 현재 상태 복원, 플레이어 정체성, 이전 연결과 새 연결의 구분 | 접속 ID를 영속 플레이어 ID로 사용하거나 이전 세션 응답을 새 매치에 적용함 |
| 생성·종료·장면 전환 | 로컬 객체와 네트워크 객체 수명, 소유권 이전, 초기 동기화 완료 시점 | 생성 완료 전에 RPC 전송. 한 클라이언트의 로드 완료를 전원 완료로 취급 |
| 관심 영역·대역폭 | 필요한 관찰자, 상태 변화량, 빈도·크기·CPU 예산을 함께 고려 | 모든 객체의 모든 값을 매 프레임 모두에게 전송함 |
| 네트워크 관찰 | 역할, 연결, 객체 ID, tick/요청 번호, 생성·종료 단계가 드러나는 로그 | 호스트 화면만 보고 원격 클라이언트도 정상이라고 판단함 |

공통 개념의 근거: [Unreal Networking Overview](https://dev.epicgames.com/documentation/unreal-engine/networking-overview-for-unreal-engine), [NGO: NetworkVariables](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/basics/networkvariable.html), [NGO: Reliability](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/advanced-topics/message-system/reliability.html), [Netcode for Entities: Prediction](https://docs.unity3d.com/Packages/com.unity.netcode@1.10/manual/prediction-n4e.html). ID·중복 방지·로그 구성은 이 제약에서 도출한 설계 제안이며, 엔진이 자동으로 제공한다고 주장하는 내용은 아니다.

### 7.1 SSOT·SOLID와 네트워크를 연결하는 예

서버 권한으로 공격을 판정하는 게임을 가정하고, 다음 책임 분담 사례를 넣는다.

| 책임 | 담당 후보 | 계약 |
| --- | --- | --- |
| 입력 | 로컬 입력 처리 | 공격하고 싶다는 의도를 만든다 |
| 송수신 | 기존 네트워크 경계 | 의도·결과를 적절한 상대에게 전달한다 |
| 규칙 판정 | 기존 권한 측 게임 규칙 | 대상·거리·쿨다운 등을 확인하고 결과를 확정한다 |
| 상태 관리 | 기존 체력·전투 상태 소유자 | 확정 상태를 갱신한다. 다른 관리자에 같은 상태를 새로 소유시키지 않는다 |
| 복제·표시 | 엔진 복제와 클라이언트 표시 | 확정 상태를 받아 표시하고 필요한 예측 보정을 수행한다 |
| 종료 처리 | 각 처리를 시작한 범위의 소유자 | Despawn/EndPlay/연결 종료 이후 도착한 처리를 적절하게 폐기한다 |

책임을 설명하기 위한 예시다. 반드시 여섯 개의 클래스나 인터페이스를 만들라는 뜻은 아니다. 기존 컴포넌트 안에서 계약이 명확하다면 그 형태를 유지할 수 있다.

### 7.2 Unity의 네트워크 지식

먼저 패키지를 특정한다. 아래 NGO 계약을 Netcode for Entities, Mirror, Photon/Fusion 등에 그대로 적용하지 않는다. Mirror/Fusion의 상세 계약은 이번에 조사하지 않았다.

| 대상 | 먼저 넣을 내용 | 예방할 구체적인 오류 |
| --- | --- | --- |
| NGO 토폴로지 | client-server와 distributed authority를 구분하고 설정·버전을 명시한다. [Distributed authority](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/terms-concepts/distributed-authority.html) | IsOwner만으로 전체 게임의 신뢰·변경 권한을 판단함 |
| NGO 상태 | NetworkVariable의 초기 동기화, 읽기·쓰기 권한, 변경 알림과 초기 표시. [NetworkVariables](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/basics/networkvariable.html) | 변경 알림에만 의존하고 초기값을 화면에 적용하지 않음 |
| NGO RPC | Rpc 특성의 수신 대상·실행 권한·Host 동작. 기존 ServerRpc/ClientRpc 예시와 채택 버전을 대조한다. [RPC](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/advanced-topics/message-system/rpc.html) | 로컬에서도 실행되는 대상에 같은 처리를 중복 적용함. 전송 대상 지정을 게임 규칙의 인가로 착각함 |
| NGO 순서·전달 | Reliable 순서 보장은 같은 NetworkObject 범위다. 연결 종료·미연결 호출에는 별도 조건이 있다. [Reliability](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/advanced-topics/message-system/reliability.html) | 다른 NetworkObject의 RPC가 먼저 실행됐다고 가정하고 참조를 사용함 |
| NGO 생성·종료 | Start와 OnNetworkSpawn 순서는 생성 방식에 따라 다르다. OnNetworkDespawn은 서버·클라이언트 양쪽에서 실행된다. [NetworkVariables](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/basics/networkvariable.html), [NetworkBehaviour API](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/api/Unity.Netcode.NetworkBehaviour.html) | Start를 네트워크 준비 완료로 취급함. Despawn과 Destroy를 같은 계약으로 봄 |
| NGO 예측 범위 | 2.7의 anticipation은 표시값과 확정값을 구분한다. 완전한 rollback-and-replay 루프는 제공 범위에 포함되지 않는다. [Client anticipation](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/advanced-topics/client-anticipation.html) | AnticipatedNetworkTransform을 붙이면 이동 예측이 모두 완성된다고 설명함 |
| NGO Scene 동기화 | NetworkSceneManager의 세션 수명, 로드·동기화 이벤트, 참가자별 준비 상태. [NetworkSceneManager](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/basics/scenemanagement/using-networkscenemanager.html) | 로컬 Scene 로드 완료를 모든 참가자의 동기화 완료로 취급함 |
| Netcode for Entities | Ghost, tick 단위 입력, PredictedSimulationSystemGroup, Simulate 대상, 재시뮬레이션 비용. [Prediction](https://docs.unity3d.com/Packages/com.unity.netcode@1.10/manual/prediction-n4e.html) | 예측 처리가 렌더링 프레임마다 한 번만 실행된다고 가정함 |

Unity 자료에는 GameObject와 Entities의 설계를 억지로 같은 클래스 구조에 맞추지 않는 설명도 필요하다. Mirror/Fusion 등을 채택한 프로젝트라면 사용 중인 제품·버전에 맞는 별도 자료 조사를 우선한다.

### 7.3 Unreal의 네트워크 지식

| 대상 | 먼저 넣을 내용 | 예방할 구체적인 오류 |
| --- | --- | --- |
| Authority와 Owner | 권한을 가진 Actor, 소유 연결, 로컬 제어의 차이. [Networking Overview](https://dev.epicgames.com/documentation/unreal-engine/networking-overview-for-unreal-engine), [Owning connection](https://dev.epicgames.com/documentation/en-us/unreal-engine/actor-owner-and-owning-connection-in-unreal-engine) | 클라이언트가 Owner라는 사실을 서버 권한과 동일시함 |
| RPC 실행 대상 | Server/Client/NetMulticast 실행 조건과 Actor/Component 복제 전제. [RPC](https://dev.epicgames.com/documentation/en-us/unreal-engine/remote-procedure-calls-in-unreal-engine) | 클라이언트에서 Multicast하면 모두에게 전달된다고 생각함. 클라이언트가 소유하지 않은 Actor에서 Server RPC를 호출함 |
| 복제와 순서 | 다른 Actor 사이 RPC 순서, 다른 변수의 OnRep 순서를 전제로 하지 않는다. 관련 값을 다루는 일관성 계약을 정한다. [Execution order](https://dev.epicgames.com/documentation/en-us/unreal-engine/replicated-object-execution-order-in-unreal-engine) | Weapon이 먼저 도착할 것이라 가정하고 Ammo의 OnRep에서 무조건 참조함 |
| Framework와 지속 범위 | GameMode의 규칙, GameState/PlayerState의 공유 상태, GameInstance의 로컬 지속 범위. [Gameplay Framework](https://dev.epicgames.com/documentation/unreal-engine/gameplay-framework-in-unreal-engine) | GameInstance로 옮기면 세션 공유·재접속 복원이 자동 완성된다고 봄 |
| Character Movement | 기존 CMC의 입력 기록·서버 검증·보정을 이해하고 확장한다. [Networked movement](https://dev.epicgames.com/documentation/unreal-engine/understanding-networked-movement-in-the-character-movement-component-for-unreal-engine) | 일반 이동을 독자적인 Transform 갱신·RPC와 이중 관리하여 보정과 충돌시킴 |
| Relevancy | 연결마다 Actor가 필요한지 판단하는 방식과, 다시 관찰 가능해졌을 때의 상태. [Actor relevancy](https://dev.epicgames.com/documentation/en-us/unreal-engine/actor-relevancy-in-unreal-engine) | 한 번 보낸 순간 이벤트로 모든 참가자의 현재 표시가 계속 일치한다고 봄 |
| 종료와 World 이동 | EndPlay 사유, 참조·타이머·비동기 결과의 World 경계. [EndPlay reasons](https://dev.epicgames.com/documentation/unreal-engine/API/Runtime/Engine/EEndPlayReason__Type) | 이전 World에서 시작한 처리가 새 Pawn이나 매치에 적용됨 |
| 동작 확인 관점 | Listen/전용 서버, 별도 프로세스, 지연·손실을 구분한다. [Testing multiplayer](https://dev.epicgames.com/documentation/en-us/unreal-engine/testing-multiplayer-in-unreal-engine), [Network emulation](https://dev.epicgames.com/documentation/en-us/unreal-engine/using-network-emulation-in-unreal-engine) | PIE 호스트 하나의 성공을 모든 네트워크 조건의 성공으로 보고함 |

Iris, Replication Graph, Dormancy, Fast Array, GAS 예측, Mover, Networked Physics, Seamless Travel 상세는 추가 자료 후보로 둔다. 채택 여부·버전·실험 단계 여부를 확인하고 구체화한다. 특히 CMC 지식을 Mover에 그대로 적용하지 않는다. 이번에 참조한 5.8 비교 자료에서는 Mover가 Experimental로 표시된다. [Mover와 CMC 비교](https://dev.epicgames.com/documentation/unreal-engine/comparing-mover-and-character-movement-component-in-unreal-engine)

### 7.4 전투·물리·재접속에서 더 조사할 주제

다음은 중요한 추가 후보지만, 이번 조사만으로 각 엔진의 구현 절차까지 확정한 항목은 아니다.

| 주제 | 후속 자료에서 다룰 것 | 확인할 전제 |
| --- | --- | --- |
| 명중과 Lag compensation | 판정 시각, 이력을 되돌릴 대상, 되돌릴 수 있는 상한, 공격자와 피격자의 공정성 | 게임 방식, 신뢰 모델, 기존 명중 판정, 물리 방식 |
| 물리 동기화 | 물리 상태 소유자, 예측 대상, 충돌·보정, 표시와 시뮬레이션의 경계 | 물리 엔진, 네트코드 방식, 실제 결정성·재현성 |
| 재접속 | 인증된 플레이어와 새 연결의 대응, 상태 복원, 오래된 요청의 배제 | 저장소, 세션 관리, 시간 제한, 복귀 규칙 |
| Host migration | 상태 이전, 권한 인수, 실패·경쟁 시 처리 | 사용 서비스·라이브러리의 지원 범위 |
| 로비·Relay·인증·게임 상태 | 접속 경로, 참가 관리, 게임 규칙 권한의 경계 | 선택한 백엔드와 서비스의 책임 범위 |

Lag compensation은 과거 상태에서 판정하기 위한 이력과 시간 해석이 필요하다. 개념의 참고 자료로 Valve 설명을 사용할 수 있지만, 그 문서의 이력 보관 시간·tickrate를 Unity/Unreal 권장값으로 옮기지 않는다. [Valve: Source Multiplayer Networking](https://developer.valvesoftware.com/wiki/Source_Multiplayer_Networking?language=uk)

### 7.5 자료에 첨부할 재현 상황

아래는 향후 모델의 제안을 평가할 때 사용할 후보이며, 이번에 실행한 테스트가 아니다. 모든 항목을 매번 필수 게이트로 만들자는 제안도 아니다.

- Host에서는 성공하고 원격 Client에서는 실패하는 상황.
- 전용 서버에 UI·로컬 입력이 없는 상황.
- 상태 변경 이후 참가한 Client에게 현재 상태를 적용하는 상황.
- RPC 대기·로드 도중 연결 종료, Despawn, Scene/World 전환이 발생하는 상황.
- 이전 연결의 응답이 재접속 이후 도착하는 상황.
- 지연·jitter·손실 때문에 다른 객체 사이의 도착 순서가 달라지는 상황.
- 예측을 여러 번 재실행해도 소리·보상·탄환 생성이 의도치 않게 늘지 않아야 하는 상황.
- Pool에서 꺼낸 객체에 이전 소유자·구독·네트워크 상태가 남을 수 있는 상황.

지연값과 손실률은 실제 대상 지역·회선·게임 요구에 맞게 선택한다. 이번 자료에서는 일률적인 합격 수치를 정하지 않는다.

## 8. 향후 자료 구성안

이 절은 자료 편집 방향이며, 현재 도구 실행 계약이나 구현을 변경하는 계획은 아니다.

| 자료 | 정본으로 보관할 내용 | 다른 자료와의 관계 |
| --- | --- | --- |
| 짧은 색인 | 문제에서 관련 항목을 찾을 수 있는 제목 | 상세 내용을 통째로 중복 수록하지 않음 |
| 오류 조사와 의미 보존 | 오류 위치·실제 정의·최소 수정·확인 수준·반복 실패 사례 | 언어 공통 내용에 엔진별 실제 사례를 연결 |
| 공통 설계 | SSOT, SOLID, 계약, 책임, 수명, 패턴 적용 조건 | Unity/Unreal 자료가 공통 설명을 참조 |
| Unity / Unreal 설계 | 각 엔진의 실제 제약과 대표 사례 | 엔진에 고유한 차이를 다룸 |
| 멀티플레이 공통 | 권한·상태·전달·시간·연결·재실행 | 각 네트코드 API와 구분 |
| Unity / Unreal 네트워크 | 사용하는 라이브러리와 버전의 계약 | 필요한 곳에서 공통 개념을 참조 |
| 프로젝트 현황 | 엔진·패키지 버전, 기존 소유자, 변경 경로, 채택 방식, 예외 | 일반 이론을 복사하지 않고 실제 코드 위치를 가리킴 |

### 항목별 편집 틀

1. **해결할 문제**: 어떤 문제가 있을 때 읽는 항목인가.
2. **적용 범위**: 엔진, 버전, 네트워크 방식, 객체 범위.
3. **확인한 계약**: 공식 자료가 보장하는 것과 보장하지 않는 것.
4. **선택지와 비용**: 최소 구현, 필요할 때 사용하는 패턴, 과도해지는 조건.
5. **책임과 수명**: 누가 변경·생성·해제·취소·복원하는가.
6. **짧은 실패 사례와 수정 방향**: 무엇이 고장 나는지까지 설명.
7. **근거와 확인일**: 공식 자료·원저자 설명·필요한 경우 프로젝트 실제 코드.

### 모델 판단을 돕는 사용 방식

- 전체 문서를 매번 상주시켜 읽히기보다, 짧은 색인에서 필요한 항목을 선택할 수 있게 구성한다.
- 색인 문자열 매칭만으로 패턴 채택을 자동 결정하지 않는다.
- 자료는 설계 판단을 돕는 데 사용하고, 도구 실행 여부를 결정하는 추가 검문으로 사용하지 않는다.
- 한번 참조한 내용을 같은 작업에서 반복해서 강제 취득하게 만들지 않는다.
- 특정 패턴이 없다는 이유만으로 결함이라고 하지 않는다. 계약 위반, 구체적 실패, 변경상의 문제와 연결하여 설명한다.
- 제안하는 모델, 실제 계약을 제공하는 엔진, 프로젝트의 선택을 기록하는 자료의 책임을 구분한다.

SSOT는 자료의 정본과 참조 관계, SRP는 자료별 대상, DIP는 공통 개념이 특정 도구 구현에 의존하지 않는 구성으로 반영할 수 있다. 이 설명을 인터페이스나 새로운 서비스를 늘릴 근거로 사용하지 않는다.

## 9. 실제 빌드 반복 사례로 보강할 내용

### 9.1 관찰한 흐름과 확인 범위

사용자가 제공한 2026-09-28 기록에는 최초 첨부 기준 `build_unreal_project()` 실패 표시가 28회 등장한다. 후속 기록에서는 `unreal_symbol_lookup()` 실패 뒤 검색 결과 수를 줄여 다시 호출하고, 정확한 `FDamageEvent` 헤더 경로를 얻은 것으로 보고한다. 이후 충돌 조회 코드를 바꾸고 `Latency_MultiCombatEditor Win64 Development` 빌드 성공을 보고한다.

붙여넣기에는 도구 결과 본문이 생략된 부분이 많다. 따라서 호출별 전체 진단, 최초 심볼 조회 실패 원인, 경과 시간, 최종 컴파일러 종료 코드를 이 기록만으로 독립 검증한 것은 아니다. 결과 수를 줄인 뒤 성공했다는 흐름만으로 최초 실패가 공유 버짓 때문이라고 확정하지 않는다. 후속 코드의 헤더·충돌 조회 변경은 실제 파일에서도 확인했다.

핵심 관찰은 **실제 심볼 근거를 얻은 뒤 헤더 문제 해결 방향이 정확해졌지만, 다음 오류에서는 다시 추측과 동작 변경으로 돌아갔다**는 점이다. 지식을 추가하는 것과 필요할 때 근거를 활용하는 방법을 함께 다뤄야 한다. 이 한 사례만으로 모델 전체의 능력이나 도구 전체의 정상 여부를 단정하지 않는다.

### 9.2 실패·회복 사례와 넣을 설명

| 사례 | 기록에서 나타난 행동 | 확인된 사실과 수록할 설명 |
| --- | --- | --- |
| 부모 클래스의 점수 API | UHT가 부모 함수와의 충돌을 알려줘도 APlayerState에 Score와 OnRep_Score가 없다고 반복함 | UE 5.7 `GameFramework/PlayerState.h`에 두 선언이 있다. 부모 계약과 새 선언을 구분하는 사례로 수록 |
| RepNotify 매크로 | 다른 OnRep 함수까지 UFUNCTION을 제거하고, 이름 규칙으로 자동 등록된다고 설명했다가 되돌림 | 부모에게 등록된 함수의 재정의와 새 반사 함수의 선언을 구분한다. 하나의 충돌을 다른 콜백 전체에 일반화하지 않는다 |
| DOREPLIFETIME | 매크로 구현을 여러 형태로 추측하고, 마지막에는 매개변수 이름을 헤더와 맞춰서 해결했다고 요약함 | UE 5.7 `Net/UnrealNetwork.h`의 매크로가 `OutLifetimeProps` 식별자를 사용한다. C++ 선언·정의의 인자 이름을 같게 해야 한다는 일반 규칙으로 설명하지 않는다 |
| FDamageEvent 사용 위치 | TakeDamage의 참조 전달만 살피며 완전한 타입이 필요한 이유를 계속 바꿈 | 실제 오류 부근에는 `FDamageEvent()` 생성식이 있었다. 생성·멤버 접근·참조 전달 등 사용 문맥을 구분한다 |
| FDamageEvent 정의 조회 | 잘못된 헤더를 시도하다 심볼 조회 후 `Engine/DamageEvents.h`를 찾음 | 정확한 심볼 검색이 도움이 된 회복 사례다. 경로를 못 찾았다는 이유로 Super 호출이나 데미지 처리 구조를 제거할 근거는 생기지 않는다 |
| 헤더 이동 설명 | 정확한 경로를 찾은 뒤 UE 5.7에서 폴더가 이동했다고 설명함 | 현재 정의 위치 확인과 버전 간 이동 이력 확인은 다른 근거가 필요하다. 이번 조사에서는 이동 이력을 확인하지 않았다 |
| 충돌 조회 API | `FCollisionObjectTypes::AllStatic` 오류를 헤더 부족으로 단정하고, 결국 Visibility 채널 조회로 변경함 | 실제 선언은 `FCollisionObjectQueryParams`와 `AllStaticObjects`다. 심볼·인자 계약 확인과 충돌 대상 설계를 먼저 구분한다 |
| 빌드 진행 보고 | 다른 파일 오류가 나오자 이전 오류가 해결됐다고 보고했다가 같은 오류가 재등장함 | 처음 표시된 오류가 달라진 것, 해당 수정의 유효성 확인, 전체 빌드 성공, 실행 동작 확인을 구분한다 |

로컬 근거: [PlayerState.h](<UE_5_7_ROOT/Engine/Source/Runtime/Engine/Classes/GameFramework/PlayerState.h:48>), [DOREPLIFETIME 실제 정의](<UE_5_7_ROOT/Engine/Source/Runtime/Engine/Public/Net/UnrealNetwork.h:250>), [FDamageEvent 정의 파일](<UE_5_7_ROOT/Engine/Source/Runtime/Engine/Classes/Engine/DamageEvents.h>), [충돌 객체 조회 인자](<UE_5_7_ROOT/Engine/Source/Runtime/Engine/Public/CollisionQueryParams.h:429>). 이 경로들은 이번 조사 장비 기준이며 다른 환경에서는 해당 설치 버전의 같은 상대 경로를 사용한다.

### 9.3 충돌 조회 변경에서 보존해야 할 계약

이번 변경은 다음과 같다.

| 변경 전 코드의 표현 | 변경 후 코드 | 달라지는 기준 |
| --- | --- | --- |
| `LineTraceSingleByObjectType` + 유효하지 않은 `FCollisionObjectTypes::AllStatic` | `LineTraceSingleByChannel` + `ECC_Visibility` | 객체 유형에 따른 조회에서 Visibility 채널 응답에 따른 조회로 변경 |

UE 5.7에는 `FCollisionObjectQueryParams::AllStaticObjects`와 이를 받는 생성자가 있다. 따라서 필요한 경우 객체 유형 조회를 유지하며 유효한 인자를 구성할 수 있다. 다만 원래 코드의 `AllStatic`이라는 이름만으로 게임의 의도까지 확정할 수는 없다. 이 Trace는 총격 후 Character에 데미지를 주는 경로에 연결되므로, 정적 환경만 조회하는 것이 적절한지부터 확인해야 한다.

후속 자료에는 다음 질문을 함께 넣는다.

- 맞아야 하는 대상은 벽, Pawn, 캐릭터 Mesh, 물리 객체 중 무엇인가?
- 벽의 차단과 캐릭터 명중은 어떤 충돌 설정으로 판정하는가?
- 자신·아군·비활성 객체의 제외 조건은 어디에서 정하는가?
- 조회를 어느 권한·어느 시점의 위치에서 실행하는가?
- 프로젝트가 실제로 설정한 Object Type과 Visibility 응답은 무엇인가?

현재 코드의 Visibility 조회가 틀렸다고 단정하지 않는다. **빌드 성공으로 확인되는 범위를 넘어 충돌 대상과 데미지 판정 의미까지 보존됐다고 보고하지 않는 것**이 이 사례의 요점이다. 런타임 충돌 프로필, Blueprint 설정, 네트워크 권한 경로는 이번 보강에서 확인하지 않았다.

근거: [World.h의 채널·객체 유형 조회 선언](<UE_5_7_ROOT/Engine/Source/Runtime/Engine/Classes/Engine/World.h:2069>), [현재 TraceHit 구현](GAME_PROJECT_ROOT/Source/Latency_MultiCombat/Private/LatencyMultiCombatCharacter.cpp:131).

### 9.4 추가할 짧은 조사 가이드

다음은 참고 자료의 내용 후보다. 도구 호출 순서나 실행 허가를 강제하는 장치는 추가하지 않는다.

1. **오류의 실제 표현식 보기**: 수정 후 행이 이동했는지 확인하고, 진단 위치의 생성식·호출·매크로를 읽는다. 같은 타입을 쓰는 다른 함수만 보고 원인을 확정하지 않는다.
2. **새 근거를 얻기**: 기억과 진단이 충돌하거나 같은 설명을 반복하기 시작하면, 정확한 심볼·부모 선언·매크로 정의를 찾는 편이 유용하다. 실패한 가설의 문장을 바꾸는 것으로 검증을 대신하지 않는다.
3. **검색 범위를 좁히기**: 심볼 이름·엔진/패키지 버전·후보 모듈부터 찾고 필요한 선언 주변을 읽는다. 가능한 경로 중 직접 파일 검색, 심볼 조회, 공식 문서를 선택한다. 매번 전체 프로젝트나 엔진을 다시 읽지 않는다.
4. **검색 실패를 해석하기**: 결과 없음은 해당 검색 범위의 결과다. 곧바로 API 부재로 단정하지 않는다. 인덱스 범위·갱신 상태·도구 오류·버전 차이를 필요한 만큼 구분한다. 재호출 여부와 범위는 관찰한 오류에 따라 결정한다.
5. **수정 목적을 유지하기**: 헤더·선언·매크로 문제를 고치며 이벤트 전달, Super 호출, 복제 등록, 충돌 필터까지 바꾸려면 동작 변경의 이유가 필요하다. 정확한 API를 못 찾았다는 사실만으로 게임 규칙을 바꾸지 않는다.
6. **확인 수준을 맞추기**: 심볼을 찾음, 코드를 수정함, 특정 오류가 사라짐, 빌드가 성공함, 런타임 의미를 확인함을 구분해 보고한다. 버전 이동·컴파일러 결함·캐시 문제 등의 추가 설명도 근거가 있을 때만 사실로 적는다.

빌드에서 관련 오류가 여러 개 드러났다면 확인된 같은 원인의 사용처를 함께 살펴볼 수 있다. 전체 코드베이스를 매번 재검토하거나 모든 수정을 한꺼번에 묶으라는 뜻은 아니다. 빌드는 가설 검증에 사용하고, 존재하지 않는 API를 계속 추측하는 검색 수단으로 소비하지 않도록 설명한다.

### 9.5 Unity에서 같은 수준으로 보강할 내용

아래는 공식 계약을 확인해 만든 예방용 사례다. 특정 Unity 프로젝트에서 이미 재현한 버그로 취급하지 않는다. Unity 6.0, Input System 1.14 문서와 7.2절의 NGO 2.7·Entities 1.10 문서를 구분한다.

#### 버전·심볼·어셈블리 근거

- 프로젝트의 Editor 버전, 패키지 선언과 실제로 해결된 버전, asmdef 참조, 플랫폼·전처리 조건을 연결해서 본다. 패키지 해석 결과는 `Packages/packages-lock.json`에 기록된다. [Unity 잠금 파일](https://docs.unity3d.com/kr/6000.0/Manual/upm-conflicts-auto.html)
- CS0246·CS1061 같은 오류가 나면 실제 수신 타입·확장 메서드·접근 범위·해당 패키지 선언을 확인한다. 검색이 안 됐다는 이유로 패키지를 교체하거나 유사한 다른 엔진의 API를 만들어내지 않는다.
- 소스를 제공하는 패키지는 실제 해결된 소스 위치를 확인한다. DLL만 제공되면 메타데이터·해당 버전 문서로 확인 범위를 명시한다. 조회를 위해 패키지 캐시를 수정하는 방식은 사용하지 않는다.
- RAG나 심볼 도구를 사용할 수 있다면 정확한 이름으로 좁혀 찾는다. 그 도구가 프로젝트 코드만 검색하는지 엔진·패키지까지 포함하는지 구분한다.

#### 구체적인 계약과 잘못된 수정 사례

| 분야 | 확인한 계약 | 자료에 넣을 실패 사례와 판단 기준 |
| --- | --- | --- |
| 2D/3D 충돌 | Physics2D.Raycast는 2D 결과와 LayerMask·깊이 필터를 사용한다. [Physics2D.Raycast](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Physics2D.Raycast.html) | 오버로드 오류를 피하려고 Physics.Raycast로 바꾸면 대상 물리 시스템부터 달라진다. 차원·필터·Trigger·결과 개수·없을 때 동작을 확인 |
| 여러 충돌 결과 | 3D Physics.RaycastAll의 반환 순서는 보장되지 않는다. [Physics.RaycastAll](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Physics.RaycastAll.html) | 첫 원소를 가장 가까운 대상으로 사용하지 않는다. 2D API의 순서 계약도 별도로 확인하며 일반화하지 않음 |
| 콜백 시그니처 | OnTriggerEnter2D는 Collider2D를 받는 메시지이며, 발생에는 물리 구성 조건이 있다. [OnTriggerEnter2D](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/MonoBehaviour.OnTriggerEnter2D.html) | 컴파일만 되도록 콜백 이름·인자 타입을 바꾼 뒤 엔진 호출도 유지된다고 가정하지 않는다. 등록·실제 호출 조건까지 확인 |
| 코루틴 종료 | MonoBehaviour.enabled를 false로 바꾸는 것만으로 코루틴이 멈추지는 않는다. GameObject 비활성화·파괴는 구분한다. [Coroutines](https://docs.unity3d.com/6000.0/Documentation/Manual/Coroutines.html) | 활성화 수명과 객체 수명을 혼동해, 비활성화 뒤에도 결과를 적용하거나 재활성화 시 중복 작업을 시작함 |
| 파괴와 취소 | destroyCancellationToken은 MonoBehaviour 파괴에 연결되며 파괴 전에 확보해야 한다. [API](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/MonoBehaviour-destroyCancellationToken.html) | 토큰이 존재한다는 이유로 임의 Task가 자동 취소된다고 가정하지 않는다. 실제 비동기 API로 토큰이 전달되고 취소가 처리되는지 확인 |
| continuation | 호출 문맥과 API에 따라 재개 위치가 정해진다. 대부분의 Unity API는 메인 스레드 제약이 있다. [Awaitable continuations](https://docs.unity3d.com/6000.0/Documentation/Manual/async-awaitable-continuations.html) | 컴파일 오류를 피하려고 Task.Run이나 await 제거를 적용한 뒤 스레드·완료 순서·객체 수명 변화는 무시함 |
| 입력 활성화 | Input System 1.14에서 프로젝트 전체 Action은 기본 활성화되지만, 그 밖의 에셋·코드 정의 Action은 활성화가 필요하다. [Actions](https://docs.unity3d.com/Packages/com.unity.inputsystem@1.14/manual/Actions.html) | 모든 Action에 같은 초기 상태를 가정함. 바인딩 선언만으로 입력 경로가 완성됐다고 봄 |
| 네트워크 초기화 | NGO의 초기 상태 동기화와 이후 변경 알림은 구분해야 한다. 생성 방식에 따라 Start/OnNetworkSpawn 순서도 다르다. [NetworkVariables](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/basics/networkvariable.html) | 변경 이벤트만 등록하고 초기 표시를 빠뜨리거나, Start에서 항상 네트워크 준비가 끝났다고 가정함 |
| 네트워크 요청 | NGO RPC의 대상·실행 권한은 사용 중인 버전의 계약으로 확인한다. [RPC](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/advanced-topics/message-system/rpc.html) | 권한 오류를 없애려고 소유권 조건만 완화하고 서버의 요청자·행동 검사를 빠뜨림. 로컬 호출로 바꿔 네트워크 전달을 없앰 |
| 네트워크 시간 | NGO anticipation과 Entities의 예측 재시뮬레이션은 제공 범위가 다르다. 관련 계약은 7.2절 참조 | 위치를 순간 덮어쓰는 방식으로 보정을 대체하고 판정 시각·중복 효과·권위 상태를 잃음 |

#### 프로젝트 수명과 상태 소유자

Unity에서도 공통 사례의 소유권·실행 경로를 끝까지 추적한다.

- ScriptableObject 설정값 → 개체별 실행 상태 생성 → UI/물리/네트워크 소비자까지 연결한다. 선언만 있고 사용처가 없는 값은 구현된 기능으로 세지 않는다.
- Scene 재진입, GameObject 비활성화, Pool 반환, NetworkObject Despawn, 연결 종료를 다른 사건으로 구분한다. 한 사건의 정리 코드가 모든 수명 문제를 해결한다고 가정하지 않는다.
- static 서비스·이벤트를 사용할 때는 Domain Reload 설정과 재등록·해제 책임을 같이 확인한다. 해당 엔진 계약은 5절 참조.
- 사망·취소·완료 이후 늦은 코루틴/Task/네트워크 응답이 이전 상태를 되살리는 사례를 넣는다. 모든 경우에 새 관리자나 전역 이벤트 버스를 만들 필요는 없다.
- Host와 원격 Client의 경로를 구분한다. Host에서 로컬 호출이 성공했다는 사실만으로 원격 입력·권한 경로가 완성됐다고 판단하지 않는다.

구체적 프로젝트에 적용할 때는 기존 패키지·구성·공식 계약을 확인한다. Unreal의 클래스명과 구조를 Unity로 그대로 옮기지 않는다.

### 9.6 지식·조회·판단의 책임과 수명

- **실제 계약의 근거**: 설치된 엔진·패키지 소스와 적용 버전의 공식 문서. 검색 인덱스가 새로운 API 계약의 정본이 되지는 않는다.
- **검색의 책임**: 관련 정의 위치와 근거를 제공한다. 조회 결과만으로 게임 규칙의 의도나 수정 완료를 판정하지 않는다.
- **자료의 책임**: 적용 조건, 오용 사례, 근거를 찾는 방법을 짧게 제공한다. 파일 변경이나 빌드 실행을 승인·차단하지 않는다.
- **모델의 책임**: 실제 오류를 근거와 연결하고 수정 범위·동작 변화를 판단한다. 패턴 선택이나 도구 순서를 자료에 떠넘기지 않는다.
- **확인 사실의 수명**: 경로·심볼·버전·관련 소스 상태를 함께 기록한다. 엔진/패키지/프로젝트가 바뀌거나 반대 증거가 생기면 재확인한다. 같은 조건의 작업 안에서는 확인한 정의를 재사용할 수 있다.
- **가설의 취급**: 확인하지 않은 이동 이력·캐시 문제·API 동작은 가설로 남긴다. 반복해서 언급됐다는 이유로 확인 사실로 승격하지 않는다.

이 구조는 자료에 의해 도구가 차단되는 문제를 추가하지 않으면서, 이번에 도움이 된 정확한 심볼 조회를 적절한 시점에 선택하도록 돕는 제안이다. 실제 개선 효과는 아직 측정하지 않았다.

### 9.7 줄 간격·들여쓰기·읽을 수 있는 실행 흐름

사용자가 지적한 가독성도 수록 대상이다. 조사한 Character.cpp에는 함수 전체, 여러 상태 대입, 중첩 조건문을 한 줄로 압축한 곳이 있다. 이런 표현은 가드·상태 변경·외부 효과의 순서를 검토하기 어렵게 한다.

- 실행문과 상태 변경을 읽기 쉬운 줄로 나누고, 중첩 조건문은 실행 블록에 맞춰 들여쓴다.
- 가드, 핵심 계산, 상태 갱신, 후속 호출 사이에 의미 있는 빈 줄을 둔다. 모든 문장 사이에 빈 줄을 넣는 식으로 기계적으로 늘리지 않는다.
- `S`, `E`, `P`, `T`처럼 역할을 추측해야 하는 이름은 Start/End, QueryParams, AmmoToTransfer 등 문맥에 맞는 이름을 고려한다.
- Unreal은 기존 프로젝트 스타일과 Epic 관례를 함께 참고한다. Epic은 새 줄의 중괄호, 단일 문장 블록의 중괄호, 실행 블록 기준 들여쓰기를 안내한다. [Epic C++ Coding Standard](https://dev.epicgames.com/documentation/unreal-engine/epic-cplusplus-coding-standard-for-unreal-engine)
- Unity C#의 탭·공백·중괄호 스타일은 해당 저장소의 .editorconfig와 일관된 기존 규칙을 우선한다. Unreal 관례를 Unity 전체에 강제하지 않는다.
- 줄 수 감소, FORCEINLINE 사용, 클래스·파일 개수를 설계 품질의 대리 지표로 삼지 않는다. 단순 접근자와 복잡한 상태 전이의 표현을 구분한다.
- 자료에 출력 예시와 편집 기준을 넣되, 이 선호를 새로운 정적 검사 실패나 무한 포맷 수정 루프로 연결하지 않는다.

### 9.8 다른 프로젝트로 일반화하는 기준

이번 프로젝트의 특정 클래스명·경로를 공통 규칙의 필수 구조로 지정하지 않는다. 다음 관계를 확인하는 방식으로 일반화한다.

| 공통 확인 대상 | Unreal 예 | Unity 예 |
| --- | --- | --- |
| 설정이 실제로 소비되는가 | Data Asset → Pawn/PlayerState 초기화 | ScriptableObject → 개체별 실행 상태 |
| 요청이 상태 소유자에게 도달하는가 | 로컬 입력 → Server RPC → 권한 측 판정 | 로컬 입력 → 채택한 네트코드 요청 → 권한 측 판정 |
| 작업마다 수명이 독립적인가 | 플레이어별 리스폰 타이머 | 개체별 코루틴·취소 토큰·리스폰 작업 |
| 종료 사건이 한 번만 효과를 내는가 | 사망 → 점수·리스폰 예약 | 사망/완료 이벤트 → 보상·풀 반환 |
| 부분 실패에서 상태가 유지되는가 | Spawn 실패 시 이전 상태·Controller 처리 | Instantiate/로드 실패 시 기존 상태·대기 작업 처리 |
| 등록과 해제가 연결되어 있는가 | 팀 등록·로그아웃·매핑 컨텍스트 | 이벤트 구독·Scene/Despawn 정리·입력 Action 수명 |
| 동작 의미가 보존되는가 | ObjectType 조회와 TraceChannel | Physics/Physics2D 및 LayerMask·Trigger·결과 순서 |

각 프로젝트가 이미 가진 소유자와 경계를 먼저 찾는다. 공통 지침은 확인할 관계를 제공하고, 엔진별 자료는 실제 계약을 제공하며, 프로젝트 문서는 선택된 구현과 예외를 기록한다.

## 10. 이번에 정리한 것과 남은 확인

### 이번 산출물

- 공통 원칙, 패턴, 엔진 계약, 네트워크 계약을 구분해 수록 후보를 정리했다.
- Unity/Unreal 공식 문서와 원저자 자료를 연결했다.
- 특히 권한과 소유권, 상태와 RPC, 예측과 표시, 객체와 연결의 수명을 우선했다.
- 버전 의존성과 추가 조사가 필요한 항목을 명시했다.
- 실제 빌드 반복과 심볼 조회 회복 사례를 추가하고, 오류 조사·동작 보존 자료를 우선순위에 넣었다.
- 최종 성공 보고의 매개변수 이름·헤더 이동 설명에서 확인된 사실과 미확인 설명을 구분했다.
- 공통 기준과 상황별 참고 문서를 실제 본문으로 분리했다. 줄 간격·들여쓰기 예시, Unity 전용 실패 사례, 프로젝트 현황 템플릿과 현재 Unreal 프로젝트 예시를 포함한다.
- Unreal Source 18개 파일에서 확인한 결함과 미확인 실행 조건을 근거 JSON에 기록했다. 이 현황은 해당 프로젝트에만 적용한다.
- 문서 공급과 기존 입력 조립·공유 예산·도구 실행의 책임을 구분한 연결 제안을 작성했다. 실제 연결은 하지 않았다.

### 남은 확인

- 사례 프로젝트는 UE 5.7 연결을 확인했다. 나머지 대상 프로젝트의 엔진·패키지·네트워크 방식은 별도 확인이 필요하다.
- 사용자 후속 기록은 최종 빌드 성공을 보고한다. 원본 전체 빌드 로그와 런타임 게임 동작은 이번 보강에서 독립 검증하지 않았다.
- 충돌 조회 방식 변경이 의도한 명중·차단·네트워크 권한 계약에 맞는지.
- 어떤 실패 사례를 첫 번째 짧은 참고 자료로 채택할지.
- 자료 추가가 모델의 출력 품질·컨텍스트 사용량·작업 완료율에 주는 영향. 개선 효과는 아직 측정하지 않았다.

**위 조사 단계에서는 자료만 작성했다. 이후 사용자의 구현 요청으로 진행한 문서 공급 기능과 확인 결과는 [연결 및 구현 문서](Design_Guidance_Integration_Proposal_20260928.md)를 참고한다.**
