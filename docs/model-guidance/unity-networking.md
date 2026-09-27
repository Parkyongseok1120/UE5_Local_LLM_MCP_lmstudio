# Unity 네트워크 계약

적용: Unity 멀티플레이의 권한·생성·상태·RPC·예측.
범위: 조사한 문서는 NGO 2.7.0과 Netcode for Entities 1.10.0. 실제 설치 버전의 보장이 아니다. [근거](sources.md#unity-network)

## 패키지와 토폴로지

패키지 이름·해결된 버전·client-server/분산 권한 여부를 먼저 확인한다. NGO, Entities, Mirror, Photon/Fusion은 API와 예측 모델이 다르다. Mirror/Fusion 상세 계약은 이 묶음에서 조사하지 않았다.

소유자와 확정 권한을 구분한다. 권한 오류를 없애려고 소유권 제한만 풀거나 네트워크 호출을 로컬 함수로 바꾸지 않는다.

## NGO 2.7

- NetworkVariable의 초기 동기화, 읽기·쓰기 권한, 이후 OnValueChanged 알림을 구분한다. 이벤트만 등록하고 최초 표시를 빠뜨리지 않는다.
- Start와 OnNetworkSpawn의 순서는 생성 방식에 따라 다르다. Start를 모든 객체의 네트워크 준비 완료 시점으로 취급하지 않는다.
- OnNetworkDespawn은 서버·클라이언트 양쪽에서 실행된다. Despawn, Destroy, Pool 반환의 정리 책임을 구분한다.
- Rpc 특성의 수신 대상·실행 조건과 프로젝트의 기존 ServerRpc/ClientRpc 사용을 적용 버전으로 확인한다.
- Host에서 로컬 실행과 수신 처리가 중복되지 않는지 살핀다. RPC 대상 설정과 게임 규칙 검사는 별개다.
- Reliable RPC의 순서 보장을 다른 NetworkObject 전체로 확대하지 않는다.
- Anticipation은 표시값과 권위값을 구분하는 기능이다. NGO 2.7의 해당 기능을 완전한 rollback-and-replay 제공으로 설명하지 않는다.
- 로컬 Scene 로드 완료와 참가자들의 네트워크 동기화 완료를 구분한다. NetworkSceneManager의 해당 이벤트·수명을 확인한다.

## Netcode for Entities 1.10

- Ghost, tick별 입력, PredictedSimulationSystemGroup, Simulate 대상의 의미를 사용 중인 구성으로 확인한다.
- 예측 시뮬레이션이 렌더 프레임당 한 번만 실행된다고 가정하지 않는다.
- 재시뮬레이션되는 상태 계산과 일회성 효과를 구분하고, 예측 범위·비용을 살핀다.
- GameObject/NGO의 생명주기와 클래스 구조를 ECS 시스템에 억지로 맞추지 않는다.

## 확인 상황

변경과 관련된 원격 Client·Host·서버 경로, 지연 참가 초기 상태, Despawn 중 대기 작업, 재사용된 객체, Scene 전환, 연결 종료를 선택해 확인한다. 서버 실행에 로컬 UI·입력이 있다고 가정하지 않는다.
