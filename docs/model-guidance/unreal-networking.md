# Unreal 네트워크 계약

적용: Unreal의 복제·RPC·Character 이동·전투·매치 수명.
범위: 일반 공식 문서는 조사 당시 주로 5.8. 실제 프로젝트의 엔진 버전·복제 체계·설정과 대조한다. [근거](sources.md#unreal-network)

- HasAuthority, IsLocallyControlled, Actor Owner/owning connection은 다른 질문에 답한다. Owner 설정으로 서버 판정이나 참조 수명이 완성되지 않는다.
- Server/Client/NetMulticast RPC의 실행 조건, 소유 연결, Actor/Component의 복제 전제를 확인한다. 클라이언트의 Multicast 호출을 서버 방송으로 해석하지 않는다.
- 원격 플레이어의 요청이 권한 측 실행까지 도달하는지 본다. HasAuthority 가드만 추가해 원격 요청을 버리면 기능은 연결되지 않는다.
- bReplicates와 SetReplicateMovement는 전투 규칙 구현의 증거가 아니다. 탄약·체력·사망·점수의 실제 변경 권한과 관찰 범위를 각각 정한다.
- 지속 상태와 순간 효과를 구분한다. 늦게 참가하거나 다시 relevant해진 연결의 표시를 복원할 상태를 둔다.
- 서로 다른 Actor의 RPC나 서로 다른 변수의 OnRep 순서를 전제로 하지 않는다. 함께 해석해야 하는 값에는 일관성·준비 조건을 둔다.
- GameMode의 서버 규칙, GameState/PlayerState의 공유 상태, Pawn의 실행 수명을 현재 프로젝트 기준으로 연결한다.
- 일반 Character 이동은 CMC의 기존 예측·서버 검증·보정을 먼저 확인한다. Transform 직접 갱신·별도 RPC로 같은 이동을 이중 관리하지 않는다.
- 전투의 명중 시각·조준·가림·충돌 필터·거리·연사·탄약은 명시적 규칙이 필요하다. Visibility를 썼다는 사실만으로 명중 규칙이 맞는 것은 아니다.
- 사망 전이의 점수·시체·리스폰, 매치 종료 시 요청·타이머, Logout/EndPlay 정리를 연결한다.
- Listen Host의 성공과 원격 Client·전용 서버의 성공을 구분한다. 관련 변경에서는 역할별로 확인한다.

Iris, Replication Graph, GAS 예측, Mover, Networked Physics, Seamless Travel은 채택 여부와 버전 계약을 별도 확인한다. CMC 사례나 이 문서만으로 해당 기능 구현을 완성했다고 판단하지 않는다.
