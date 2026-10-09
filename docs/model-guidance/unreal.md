# Unreal의 책임·수명·API

적용: Unreal C++·Blueprint 경계, 설정·생성·종료, 엔진 API 오류.
범위: 일반 공식 문서는 조사 당시 주로 5.8; 로컬 심볼 사례는 UE 5.7. 프로젝트 버전의 소스가 우선한다. [근거](sources.md#unreal)

<!-- guidance-section: ownership -->
## 상태와 수명의 위치

- GameMode, GameState, PlayerState, Controller, Pawn이 지금 맡는 역할과 수명을 먼저 읽는다. 기능 이름만 보고 새 Manager/Subsystem을 만들지 않는다.
- GameMode는 서버 규칙 경계다. 클라이언트가 공유할 현재 상태는 프로젝트의 복제 경로로 전달한다. GameInstance의 로컬 지속성을 자동 복제·복원으로 해석하지 않는다.
- Data Asset 정의 → 생성 시 선택 → 개체별 실행 상태 초기화 → 소비자를 연결한다. EditDefaultsOnly와 에셋 선언만으로 설정이 사용되지는 않는다.
- 기본 생성·리스폰·로드가 같은 설정과 초기화 계약을 쓰는지 확인한다. 생성 전에 성공 상태를 확정하지 않는다.
- 기존 World/Player getter를 캐시하기 전에 실제 구현 비용과 World 전환·재스폰·교체 시점을 확인한다. 포인터가 null이 아니라는 사실만으로 현재 대상임을 증명하지 않는다.
- EndPlay와 메모리 회수를 구분한다. World·매치·소유자의 종료 시점에 게임플레이 작업과 구독의 정리 책임을 둔다.
- UPROPERTY로 추적되는 TObjectPtr, 비소유 weak 참조, soft asset 참조의 목적을 구분한다. TObjectPtr 표기만으로 어디서나 GC 유지가 된다고 가정하지 않는다.
- 상태 변경 책임, UObject 참조, Actor Owner/owning connection을 따로 판단한다.
- 타이머가 바인딩한 객체와 람다가 임의로 캡처한 포인터를 구분한다. null 검사만으로 파괴 뒤 유효성을 보증하지 않는다.
<!-- /guidance-section: ownership -->

<!-- guidance-section: connections -->
## 연결과 동작 확인

- 입력 Action 바인딩, Mapping Context 설정, 실제 로컬 플레이어 등록을 구분한다. 완료 로그는 실제 등록 결과와 맞춘다.
- Enhanced Input의 Action 값 타입·이벤트·수신 subsystem 선언을 적용 버전에서 확인한다. legacy 축으로 바꿀 때 이름별 AxisMappings와 누름/놓음 연결이 실제로 있는지 확인한다. AxisConfig만으로 named axis mapping을 증명하지 않는다.
- 좌우·전후 이동은 입력 축과 실제 방향 벡터를 대조한다. 서로 다른 변수명만으로 방향도 다르다고 가정하지 않는다.
- UnPossess와 Pawn 종료는 다르다. 시체 유지, 충돌·피해 차단, 폐기 시점의 책임을 정한다.
<!-- /guidance-section: connections -->
<!-- guidance-section: timer -->
- 플레이어별 리스폰을 단일 FTimerHandle로 덮어쓰지 않는다. UE 5.7 SetTimer는 같은 유효 핸들의 예약을 교체한다.
- UE 5.7의 SetLifeSpan(0)은 기존 소멸 타이머를 해제한다. 즉시 파괴나 1초 후 파괴로 설명하지 않는다. 현재 함수 구현과 부모·다른 호출의 영향을 구분한다.
<!-- /guidance-section: timer -->
<!-- guidance-section: api -->
- 네트워크 판정은 UFUNCTION 지정자와 호출 주체·Actor의 owning connection을 연결한다. Client RPC의 수신 범위를 모든 클라이언트로 확대하지 않는다. 멀티캐스트·복제 프로퍼티·로컬 표시 호출의 계약은 각각 확인한다.
- 매크로는 사용 목적 → 사용처/바인딩 → 적용 엔진의 헤더 선언·부모 계약 순서로 확인한다. 본문/부분 발췌에 없다고 누락으로 단정하지 않는다. 여러 줄 지정자를 포함해 선언을 읽고 누락·부적합·정상·근거 부족을 구분한다.
- UPROPERTY는 GC 추적·직렬화·에디터/Blueprint 노출·복제 목적을 구분한다. 일반 C++ 멤버에 일괄 추가하거나 비소유 참조를 근거 없이 강한 참조로 바꾸지 않는다.
- UFUNCTION은 Blueprint/RPC/RepNotify/리플렉션 호출 목적과 시그니처를 확인한다. Exec는 콘솔 노출이며 OnRep의 일반 표기가 아니다. BlueprintPure는 실제 상태 변경 여부와 대조한다. 부모의 리플렉션 계약과 native override를 확인하고 중복 선언하지 않는다.
- ReplicatedUsing 선언, OnRep, 복제 등록, 객체의 복제 설정을 연결한다. 선언만으로 원격 호출을 증명하지 않는다. 관련 오류 사례는 debugging.md를 참고한다.
- Delegate는 native/dynamic, single/multicast와 실제 바인딩을 대조한다. AddDynamic 대상의 UFUNCTION 등록·시그니처, 중복 등록·해제 책임·늦은 호출을 확인한다. BindUObject 등 native 바인딩에 UFUNCTION을 일괄 요구하지 않는다.
- FORCEINLINE은 성능·정의 가시성·호출 위치·코드 크기 판단이다. 헤더 정의의 ODR을 위한 inline 여부와 최적화 강도를 구분한다. 작은 함수라는 이유로 FORCEINLINE을 필수로 취급하지 않는다.
- 변경 후 실제 선언/diff와 연결을 확인한다. 필요한 UHT·빌드를 변경 단위로 검증하고, 빌드 성공과 실제 바인딩·복제·수명 동작을 구분한다.
- Trace는 대상 Object Type, 채널 응답, 가림, 자신 제외, 판정 위치·시각을 같이 확인한다.
- Build.cs 의존성과 Public/Private 노출, Runtime/Editor 경계를 구분한다. 헤더 경로를 추측하며 엔진 소스를 수정하지 않는다.
- 빈 OnRep나 native override도 부모 호출·다른 소비자가 있으면 정상일 수 있다. 공개 필드·RPC·delegate를 제거/변경할 때 Blueprint·에셋·직렬화 호환성을 확인한다. C++ 검색 결과만으로 모든 사용처가 없다고 단정하지 않는다.
- Editor 빌드 성공과 Server/Shipping 대상, standalone 역할 분기와 실제 연결된 클라이언트 검증을 구별한다. 서버 상태 결정·예측·복제·표시는 각 책임 경로에서 확인한다.
<!-- /guidance-section: api -->

<!-- guidance-section: preference -->
## 이 작업의 선호

이 문서 묶음을 요청한 사용자는 Unreal 프로젝트에서 네임스페이스를 가급적 사용하지 않기를 원한다. 프로젝트 현황 문서에도 기록한다. Unreal의 보편적 금지 규칙으로 설명하지 않는다.
<!-- /guidance-section: preference -->
