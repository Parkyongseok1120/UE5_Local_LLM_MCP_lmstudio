# 근거와 적용 버전

확인일: 2026-09-28. 이 파일은 근거 조회용이며 상시 입력 대상이 아니다.
세부 관찰과 한계는 저장소 전용 조사 기록 `docs/Design_Guidance_Research_20260928.md`에 보관한다. 아래 버전은 조사한 문헌의 버전이다.

<a id="design"></a>
## 설계 원칙과 패턴

- [Unity의 패턴·SOLID 안내](https://unity.com/blog/game-programming-patterns-update-ebook)
- [SRP 원저자 설명](https://blog.cleancoder.com/uncle-bob/2014/05/08/SingleReponsibilityPrinciple.html)
- [Liskov·Wing: Behavioral Subtyping](https://www.cs.cmu.edu/~wing/publications/LiskovWing94.pdf)
- [의존성 방향](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html)
- [YAGNI](https://martinfowler.com/bliki/Yagni.html), [Command Query Separation](https://martinfowler.com/bliki/CommandQuerySeparation.html), [Dependency Injection](https://www.martinfowler.com/articles/injection.html)
- Game Programming Patterns 저자 원문: [State](https://raw.githubusercontent.com/munificent/game-programming-patterns/master/book/state.markdown), [Observer](https://raw.githubusercontent.com/munificent/game-programming-patterns/master/book/observer.markdown), [Command](https://raw.githubusercontent.com/munificent/game-programming-patterns/master/book/command.markdown), [Component](https://raw.githubusercontent.com/munificent/game-programming-patterns/master/book/component.markdown), [Object Pool](https://raw.githubusercontent.com/munificent/game-programming-patterns/master/book/object-pool.markdown), [Singleton](https://raw.githubusercontent.com/munificent/game-programming-patterns/master/book/singleton.markdown)

<a id="style"></a>
## 표현 관례

- [Epic C++ Coding Standard](https://dev.epicgames.com/documentation/unreal-engine/epic-cplusplus-coding-standard-for-unreal-engine)
- 실제 프로젝트의 .editorconfig·.clang-format·저장소 지침이 있으면 해당 규칙을 확인한다. 이번 검토 대상 게임 루트에서는 두 포맷 설정 파일을 찾지 못했다.

<a id="unreal"></a>
## Unreal 일반

Epic 문서는 조사 당시 주로 5.8로 표시되었다. 아래 일반 계약을 실제 프로젝트 버전에 대조한다.

- [Gameplay Framework](https://dev.epicgames.com/documentation/unreal-engine/gameplay-framework-in-unreal-engine)
- [Subsystems](https://dev.epicgames.com/documentation/en-us/unreal-engine/programming-subsystems-in-unreal-engine)
- [Object pointers](https://dev.epicgames.com/documentation/en-us/unreal-engine/object-pointers-in-unreal-engine)
- [Gameplay timers](https://dev.epicgames.com/documentation/en-us/unreal-engine/gameplay-timers-in-unreal-engine)
- [Modules](https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-engine-modules)
- [AActor](https://dev.epicgames.com/documentation/unreal-engine/API/Runtime/Engine/AActor), [EndPlay reasons](https://dev.epicgames.com/documentation/unreal-engine/API/Runtime/Engine/EEndPlayReason__Type)

<a id="unreal57"></a>
## 직접 확인한 UE 5.7 소스

조사 장비 설치본의 상대 경로다. 다른 환경에서는 실제 설치 엔진을 기준으로 찾는다.

| Engine/Source/Runtime/Engine 이하 | 확인한 내용 |
| --- | --- |
| Classes/GameFramework/PlayerState.h:48,169 | Score와 OnRep_Score 선언 |
| Public/Net/UnrealNetwork.h:250 | DOREPLIFETIME 매크로가 OutLifetimeProps 사용 |
| Classes/Engine/DamageEvents.h | FDamageEvent 정의 |
| Public/CollisionQueryParams.h:429 | FCollisionObjectQueryParams와 AllStaticObjects |
| Classes/Engine/World.h:2069,2080 | 채널·객체 유형 Trace 인자 차이 |
| Classes/Engine/World.h:3768 | GetAuthGameMode의 서버/클라이언트 계약 |
| Private/TimerManager.cpp:610 | 같은 유효 핸들로 SetTimer 시 기존 예약 제거 |
| Private/PlayerController.cpp:1357 | OnUnPossess는 Pawn을 Destroy하지 않음 |
| Private/GameModeBase.cpp:71 | 기본 Pawn·Controller 클래스 |

이력·이전 버전과의 이동은 확인하지 않았다. 프로젝트별 조사 경로는 저장소 전용 `docs/evidence/combat-source-audit-20260928.json`에 보관하며 배포 패키지에는 포함하지 않는다.

<a id="unity"></a>
## Unity 일반

Unity 6.0 및 Input System 1.14 문헌:

- [Execution order](https://docs.unity3d.com/6000.0/Documentation/Manual/execution-order.html), [Domain Reload](https://docs.unity3d.com/6000.0/Documentation/Manual/domain-reloading.html)
- [ScriptableObject](https://docs.unity3d.com/6000.0/Documentation/Manual/class-ScriptableObject.html), [Assembly definitions](https://docs.unity3d.com/6000.0/Documentation/Manual/assembly-definition-files.html)
- [Package resolution lock](https://docs.unity3d.com/kr/6000.0/Manual/upm-conflicts-auto.html)
- [Coroutines](https://docs.unity3d.com/6000.0/Documentation/Manual/Coroutines.html), [destroyCancellationToken](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/MonoBehaviour-destroyCancellationToken.html)
- [Async support](https://docs.unity3d.com/6000.0/Documentation/Manual/async-await-support.html), [Awaitable continuations](https://docs.unity3d.com/6000.0/Documentation/Manual/async-awaitable-continuations.html)
- [ObjectPool](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Pool.ObjectPool_1.html)
- [Physics.RaycastAll](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Physics.RaycastAll.html), [Physics2D.Raycast](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Physics2D.Raycast.html)
- [OnTriggerEnter2D](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/MonoBehaviour.OnTriggerEnter2D.html), [Input Actions 1.14](https://docs.unity3d.com/Packages/com.unity.inputsystem@1.14/manual/Actions.html)

<a id="network"></a>
## 멀티플레이 공통

공통 제안은 아래 두 엔진의 계약과 [Valve Source Multiplayer Networking](https://developer.valvesoftware.com/wiki/Source_Multiplayer_Networking?language=uk)을 근거로 정리했다. 특정 tickrate·이력 길이·네트워크 구성을 공통 기본값으로 제안하지 않는다.

<a id="unreal-network"></a>
## Unreal 네트워크

- [Networking Overview](https://dev.epicgames.com/documentation/unreal-engine/networking-overview-for-unreal-engine), [Owning connection](https://dev.epicgames.com/documentation/en-us/unreal-engine/actor-owner-and-owning-connection-in-unreal-engine)
- [RPC](https://dev.epicgames.com/documentation/en-us/unreal-engine/remote-procedure-calls-in-unreal-engine), [복제 실행 순서](https://dev.epicgames.com/documentation/en-us/unreal-engine/replicated-object-execution-order-in-unreal-engine)
- [Character Movement](https://dev.epicgames.com/documentation/unreal-engine/understanding-networked-movement-in-the-character-movement-component-for-unreal-engine), [Relevancy](https://dev.epicgames.com/documentation/en-us/unreal-engine/actor-relevancy-in-unreal-engine)
- [Multiplayer testing](https://dev.epicgames.com/documentation/en-us/unreal-engine/testing-multiplayer-in-unreal-engine), [Network emulation](https://dev.epicgames.com/documentation/en-us/unreal-engine/using-network-emulation-in-unreal-engine)

<a id="unity-network"></a>
## Unity 네트워크

NGO 2.7.0:

- [Distributed authority](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/terms-concepts/distributed-authority.html)
- [NetworkVariables](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/basics/networkvariable.html)
- [RPC](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/advanced-topics/message-system/rpc.html), [Reliability](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/advanced-topics/message-system/reliability.html)
- [NetworkBehaviour](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/api/Unity.Netcode.NetworkBehaviour.html)
- [Client anticipation](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/advanced-topics/client-anticipation.html)
- [NetworkSceneManager](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.7/manual/basics/scenemanagement/using-networkscenemanager.html)

Netcode for Entities 1.10.0: [Prediction](https://docs.unity3d.com/Packages/com.unity.netcode@1.10/manual/prediction-n4e.html).

Mirror/Fusion, Host migration, GAS·Iris·Mover·Networked Physics의 구현 상세는 현재 묶음의 확인 범위를 벗어난다.
