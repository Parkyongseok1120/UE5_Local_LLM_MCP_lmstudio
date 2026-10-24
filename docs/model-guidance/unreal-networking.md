# Unreal 네트워크 계약

적용: Unreal의 복제·RPC·Character 이동·전투·매치 수명.
범위: 일반 공식 문서는 조사 당시 주로 5.8. 실제 프로젝트의 엔진 버전·복제 체계·설정과 대조한다. [근거](sources.md#unreal-network)

<!-- guidance-section: engine-defaults -->
## 엔진 기본 동작

엔진이 이미 제공하는 동작을 프로젝트에 없는 기능으로 보고하지 않는다. 프로젝트가 같은 동작을 다시 구현했는지, 엔진 동작에 의존하는지 구분한다.

<!-- contract-summary: engine-defaults -->
엔진 계약 unreal_contracts@5 (UE 5.8 소스 기준, 다른 버전은 해당 엔진 소스로 재확인):
- `ue.net.cmc-prediction` (엔진 사실) UCharacterMovementComponent already performs owning-client movement prediction: the client moves locally and sends moves with ReplicateMoveToServer, and the server corrects significant errors with ClientAdjustPosition. A project without its own prediction layer still has engine movement prediction. 근거: Engine/Source/Runtime/Engine/Classes/GameFramework/CharacterMovementComponent.h:2394 외 1
- `ue.render.owner-visibility` (엔진 사실) bOnlyOwnerSee and bOwnerNoSee are rendering visibility flags on UPrimitiveComponent. They do not control replication, relevancy or whether the component exists on a machine. 근거: Engine/Source/Runtime/Engine/Classes/Components/PrimitiveComponent.h:497 외 1
- `ue.net.netmode` (엔진 사실) ENetMode is NM_Standalone, NM_DedicatedServer, NM_ListenServer or NM_Client; every value below NM_Client is some kind of server. A NetMode != NM_DedicatedServer branch runs on standalone, listen servers and clients, not only on remote clients. 근거: Engine/Source/Runtime/Engine/Classes/Engine/EngineBaseTypes.h:981 외 2
- `ue.net.replication-conditions` (엔진 사실) Replication conditions choose which connections receive a replicated property: COND_InitialOnly sends only in the initial bunch, COND_OwnerOnly only to the owning connection, COND_SkipOwner to every connection except the owner. 근거: Engine/Source/Runtime/CoreUObject/Public/UObject/CoreNetTypes.h:19 외 2
- `ue.net.client-rpc-callspace` (엔진 사실) A Client RPC called on the server through its generated function goes through AActor::GetFunctionCallspace. If the actor replicates and has a remote owning connection (for example a pawn possessed by a remote PlayerController), the call is sent to and runs on that owning client. With no owning connection and no owning player (for example a server-spawned actor without a player Owner) it runs locally on the server; with a local player owner (listen-server host) it runs locally; a non-replicating actor (RemoteRole None) absorbs it. 근거: Engine/Source/Runtime/Engine/Private/Actor.cpp:5606 외 2
<!-- /contract-summary: engine-defaults -->
<!-- /guidance-section: engine-defaults -->

<!-- guidance-section: authority -->
- HasAuthority, IsLocallyControlled, Actor Owner/owning connection은 다른 질문에 답한다. Owner 설정으로 서버 판정이나 참조 수명이 완성되지 않으며 클라이언트의 Multicast 호출을 서버 방송으로 해석하지 않는다.
- Server/Client/NetMulticast RPC 실행 조건·소유 연결·복제 전제와 프로퍼티의 선언·등록·수신 조건을 구별한다. 계약 위반 판정에는 적용 엔진의 선언/구현을 조회하고 버전·본문이 미확인이면 추정으로 확정하지 않는다.
- 원격 플레이어의 요청이 권한 측 실행까지 도달하는지 본다. HasAuthority 가드만 추가해 원격 요청을 버리면 기능은 연결되지 않는다.
- bReplicates와 SetReplicateMovement는 전투 규칙 구현의 증거가 아니다. 로컬 예측 쓰기는 서버 수락·거부·보정·표시로 판단하며, 쓰기 자체를 결함으로 보거나 다음 복제로 무조건 수렴한다고 가정하지 않는다.
- 지속 상태와 순간 효과를 구분한다. 늦게 참가하거나 다시 relevant해진 연결의 표시를 복원할 상태를 둔다.
- 서로 다른 Actor의 RPC나 서로 다른 변수의 OnRep 순서를 전제로 하지 않는다. 함께 해석해야 하는 값에는 일관성·준비 조건을 둔다.
- GameMode의 서버 규칙, GameState/PlayerState의 공유 상태, Pawn의 실행 수명을 현재 프로젝트 기준으로 연결한다.
<!-- /guidance-section: authority -->
<!-- guidance-section: prediction -->
- 일반 Character 이동은 CMC의 기존 예측·서버 검증·보정을 먼저 확인한다. Transform 직접 갱신·별도 RPC로 같은 이동을 이중 관리하지 않는다.
- 전투의 명중 시각·조준·가림·충돌 필터·거리·연사·탄약은 명시적 규칙이 필요하다. Visibility를 썼다는 사실만으로 명중 규칙이 맞는 것은 아니다.
<!-- /guidance-section: prediction -->
<!-- guidance-section: lifetime -->
- 사망 전이의 점수·시체·리스폰, 매치 종료 시 요청·타이머, Logout/EndPlay 정리를 연결한다.
- 반복 사망 요청의 점수 중복, 플레이어별 예약 교체, 퇴장 후 callback, spawn/possess 실패를 확인한다. 다음 Pawn 준비 전에 성공 상태를 확정하지 않으며 이전 Pawn·팀 목록 정리를 기존 소유자에게 둔다.
- Listen Host의 성공과 원격 Client·전용 서버의 성공을 구분한다. 관련 변경에서는 역할별로 확인한다.

Iris, Replication Graph, GAS 예측, Mover, Networked Physics, Seamless Travel은 채택 여부와 버전 계약을 별도 확인한다. CMC 사례나 이 문서만으로 해당 기능 구현을 완성했다고 판단하지 않는다.
<!-- /guidance-section: lifetime -->
