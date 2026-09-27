# Latency_MultiCombat 프로젝트 현황 예시

- 프로젝트: C:/Users/sster/Documents/Git/UE5_Latency-Resilient-Multiplayer-Combat
- 확인 상태: 2026-09-28, 커밋 `f149e03a9f1e9f1a85a6d66200638674ab461ffc`, Source 18개 파일 검토. 검토 당시 Source 변경 없음.
- 엔진: .uproject의 EngineAssociation 5.7.
- 목표: README에 서버 권한 전투·Prediction·Reconciliation·Lag Compensation을 명시. 목표 문구와 실제 구현은 구분한다.
- 사용자 선호: Unreal 네임스페이스는 가급적 피함. 읽을 수 있는 줄 간격·들여쓰기 사용.
- 확인 수준: 소스·일부 로컬 엔진 계약 확인. 이번 검토에서 빌드·PIE·네트워크 실행은 하지 않음.

## 현재 소유자와 빠진 연결

| 사실 | 현재 위치 | 관찰 |
| --- | --- | --- |
| 캐릭터·무기 정의 | Public/Data/CombatTypes.h, UCharacterDataAsset.h, UWeaponDataAsset.h | 체력·방어·무기 설정이 존재 |
| 체력·사망·개인 점수·팀 | PlayerState | CurrentHealth=0, bIsDead=false로 시작. 캐릭터 체력 설정에서 초기화하는 연결 누락 |
| 입력·발사·탄약·재장전 | Character | 로컬 함수가 직접 피해 적용. 원격 요청→서버 판정 연결 누락 |
| 팀 점수·매치 종료 | GameState | 점수/종료 값은 있으나 팀 등록 호출 연결 누락 |
| 사망 후 점수·리스폰 예약 | GameMode | 단일 타이머 공유, 중복 사망 효과, 실패 복구 문제 |
| 입력 Context 설정 | PlayerController | 프로퍼티는 있지만 실제 등록 없이 준비 로그 출력 |

표는 Source/Latency_MultiCombat 이하의 현재 구현을 요약한다. 원하는 최종 구조를 구현된 사실로 취급하지 않는다.

## 우선 확인된 결함

- **F01: 원격 발사 입력을 권한 있는 서버 판정으로 전달하는 경로가 없다.** 원격 클라이언트가 발사해도 이 경로는 서버의 명중·체력·점수 판정으로 연결되지 않는다. 클라이언트에서 바꾼 복제 변수만으로 서버 상태가 갱신되지는 않는다. [소스](<C:/Users/sster/Documents/Git/UE5_Latency-Resilient-Multiplayer-Combat/Source/Latency_MultiCombat/Private/LatencyMultiCombatCharacter.cpp:60>)
- **F02: Data Asset의 체력 설정이 초기 상태에 반영되지 않는다.** 제공된 C++ 초기화만 사용하면 첫 생성에서 체력은 0, bIsDead는 false다. 데미지 경로에 들어가면 즉시 사망 조건에 도달한다. 설정한 최대 체력도 실제 값으로 복사되지 않는다. [소스](<C:/Users/sster/Documents/Git/UE5_Latency-Resilient-Multiplayer-Combat/Source/Latency_MultiCombat/Private/LatencyMultiCombatPlayerState.cpp:6>)
- **F03: 플레이어들이 리스폰 타이머 핸들 하나를 공유한다.** 두 명이 RespawnDelay 안에 사망하면 마지막 플레이어의 예약만 남아 앞선 플레이어가 계속 사망 상태에 머무를 수 있다. [소스](<C:/Users/sster/Documents/Git/UE5_Latency-Resilient-Multiplayer-Combat/Source/Latency_MultiCombat/Public/LatencyMultiCombatGameMode.h:48>)
- **F04: 이미 사망한 대상에 대한 추가 피해가 킬 보상을 반복시킨다.** 사망 처리 후 같은 Pawn에 다시 TakeDamage가 들어오면 킬 수·팀 점수와 리스폰 예약이 반복될 수 있다. 죽음으로 전이한 순간과 이미 죽어 있는 상태를 구분하지 않는다. [소스](<C:/Users/sster/Documents/Git/UE5_Latency-Resilient-Multiplayer-Combat/Source/Latency_MultiCombat/Private/LatencyMultiCombatPlayerState.cpp:55>)
- **F05: 재장전·조준이 Dead 상태를 덮어쓰고 발사를 다시 허용한다.** 재장전 중 죽고 완료 시각에 도달하면 CombatState만 살아 있는 상태로 돌아간다. 탄약이 있으면 다음 발사가 허용되는 경로가 있다. 조준 입력도 사망 상태를 덮어쓴다. [소스](<C:/Users/sster/Documents/Git/UE5_Latency-Resilient-Multiplayer-Combat/Source/Latency_MultiCombat/Private/LatencyMultiCombatCharacter.cpp:44>)
- **F06: 리스폰 성공 전에 생존 상태로 바꾸고 실패 복구가 없다.** 스폰 지점이 없으면 죽은 Pawn에 살아 있는 PlayerState가 연결된다. SpawnActor가 실패하면 Controller는 Pawn 없이 남고 bIsDead는 false가 되어 같은 리스폰 함수의 재시도 가드에도 걸린다. [소스](<C:/Users/sster/Documents/Git/UE5_Latency-Resilient-Multiplayer-Combat/Source/Latency_MultiCombat/Private/LatencyMultiCombatGameMode.cpp:87>)
- **F07: 좌우 입력과 전후 입력이 같은 방향 벡터를 사용한다.** 일반적인 좌우/전후 2D 입력 매핑에서 좌우 이동이 전후 이동으로 합쳐져 정상적인 횡이동이 되지 않는다. [소스](<C:/Users/sster/Documents/Git/UE5_Latency-Resilient-Multiplayer-Combat/Source/Latency_MultiCombat/Private/LatencyMultiCombatCharacter.cpp:49>)
- **F08: 설정 가능한 Input Mapping Context를 실제로 등록하지 않는다.** 이 프로퍼티에 에셋을 지정하는 것만으로 키→Action 경로가 구성되지 않는다. 함수 이름과 로그가 실제 연결 완료를 나타내지 않는다. [소스](<C:/Users/sster/Documents/Git/UE5_Latency-Resilient-Multiplayer-Combat/Source/Latency_MultiCombat/Public/LatencyMultiCombatPlayerController.h:18>)
- **F09: 팀 배정 함수가 접속·스폰 흐름에 연결되지 않는다.** 제공된 C++ 흐름만으로는 플레이어가 Red/Blue에 배정되지 않는다. Neutral 킬은 개인 점수만 늘고 AddKillToTeam에서 팀 점수는 늘지 않아 팀 승리 흐름이 연결되지 않는다. [소스](<C:/Users/sster/Documents/Git/UE5_Latency-Resilient-Multiplayer-Combat/Source/Latency_MultiCombat/Private/LatencyMultiCombatGameMode.cpp:30>)

추가 관찰: 매치 종료 후 일부 행위가 계속 허용되는 경로, 이전 Pawn의 폐기 경로 누락, 한 줄로 압축된 상태 변경. 시작 클래스 선택과 raw 포인터 콜백의 실제 종료 시 동작은 추가 실행·설정 확인이 필요하다.

## 구성과 미확인 사항

- 검토 당시 Content 아래 .uasset/.umap을 찾지 못했다. 실제 Editor의 외부 콘텐츠·개인 설정까지 없다고 단정하지 않는다.
- C++ 기본 생성 클래스와 별도 리스폰 클래스의 설정 정본을 확인해야 한다.
- 총격 대상·가림·충돌 응답·조준 위치·판정 시각의 의도는 별도 확정이 필요하다.
- 사용자 기록은 빌드 성공을 보고했다. 성공한 빌드가 위 실행 경로의 완성을 증명하지 않는다.

[전체 14건의 근거·반대 근거·호출 경로](../../evidence/combat-source-audit-20260928.json). 이 예시는 해당 프로젝트에서만 사용한다. 코드·에셋·버전이 바뀌면 관련 항목을 갱신하고 이전 결함을 현재 사실로 재주입하지 않는다.
