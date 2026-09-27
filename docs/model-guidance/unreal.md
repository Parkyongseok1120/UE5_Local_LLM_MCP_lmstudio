# Unreal의 책임·수명·API

적용: Unreal C++·Blueprint 경계, 설정·생성·종료, 엔진 API 오류.
범위: 일반 공식 문서는 조사 당시 주로 5.8; 로컬 심볼 사례는 UE 5.7. 프로젝트 버전의 소스가 우선한다. [근거](sources.md#unreal)

## 상태와 수명의 위치

- GameMode, GameState, PlayerState, Controller, Pawn이 지금 맡는 역할과 수명을 먼저 읽는다. 기능 이름만 보고 새 Manager/Subsystem을 만들지 않는다.
- GameMode는 서버 규칙 경계다. 클라이언트가 공유할 현재 상태는 프로젝트의 복제 경로로 전달한다. GameInstance의 로컬 지속성을 자동 복제·복원으로 해석하지 않는다.
- Data Asset 정의 → 생성 시 선택 → 개체별 실행 상태 초기화 → 소비자를 연결한다. EditDefaultsOnly와 에셋 선언만으로 설정이 사용되지는 않는다.
- 기본 생성·리스폰·로드가 같은 설정과 초기화 계약을 쓰는지 확인한다. 생성 전에 성공 상태를 확정하지 않는다.
- EndPlay와 메모리 회수를 구분한다. World·매치·소유자의 종료 시점에 게임플레이 작업과 구독의 정리 책임을 둔다.
- UPROPERTY로 추적되는 TObjectPtr, 비소유 weak 참조, soft asset 참조의 목적을 구분한다. TObjectPtr 표기만으로 어디서나 GC 유지가 된다고 가정하지 않는다.
- 상태 변경 책임, UObject 참조, Actor Owner/owning connection을 따로 판단한다.
- 타이머가 바인딩한 객체와 람다가 임의로 캡처한 포인터를 구분한다. null 검사만으로 파괴 뒤 유효성을 보증하지 않는다.

## 연결과 동작 확인

- 입력 Action 바인딩, Mapping Context 설정, 실제 로컬 플레이어 등록을 구분한다. 완료 로그는 실제 등록 결과와 맞춘다.
- 좌우·전후 이동은 입력 축과 실제 방향 벡터를 대조한다. 서로 다른 변수명만으로 방향도 다르다고 가정하지 않는다.
- UnPossess와 Pawn 종료는 다르다. 시체 유지, 충돌·피해 차단, 폐기 시점의 책임을 정한다.
- 플레이어별 리스폰을 단일 FTimerHandle로 덮어쓰지 않는다. UE 5.7 SetTimer는 같은 유효 핸들의 예약을 교체한다.
- UFUNCTION, RepNotify, 부모 가상 함수, DOREPLIFETIME의 계약은 정확한 선언과 매크로에서 확인한다. 관련 오류 사례는 debugging.md를 참고한다.
- Trace는 대상 Object Type, 채널 응답, 가림, 자신 제외, 판정 위치·시각을 같이 확인한다.
- Build.cs 의존성과 Public/Private 노출, Runtime/Editor 경계를 구분한다. 헤더 경로를 추측하며 엔진 소스를 수정하지 않는다.

## 이 작업의 선호

이 문서 묶음을 요청한 사용자는 Unreal 프로젝트에서 네임스페이스를 가급적 사용하지 않기를 원한다. 프로젝트 현황 문서에도 기록한다. Unreal의 보편적 금지 규칙으로 설명하지 않는다.
