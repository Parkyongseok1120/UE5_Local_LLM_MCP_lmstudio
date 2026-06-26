# Prototype Planning Template

## 검색 키워드

prototype planning, game prototype plan, core loop, minimum viable prototype, phase plan, planning mode, first implementation slice

## 목적

사용자가 게임이나 소프트웨어 프로토타입 계획을 요청하면 전체 구현이 아니라 핵심 가설을 검증할 수 있는 최소 계획을 작성한다.

계획 답변은 Production Roadmap이 아니라 First Playable Loop를 목표로 한다. 사용자가 명시적으로 장기 로드맵을 요구하지 않았다면, 첫 답변은 1~2주 안에 검증 가능한 작은 루프를 중심으로 작성한다.

## 출력 형식

Prototype Planning Mode 답변에는 다음을 포함한다.

1. 레퍼런스 해석
   - 사용자가 말한 레퍼런스를 그대로 복제하지 않고, 카메라/조작/핵심 행동/피드백 관점으로 해석한다.
2. 핵심 재미 / 핵심 가설
   - 이 프로토타입이 가장 먼저 검증해야 하는 재미를 한 문장으로 적는다.
3. 핵심 루프
   - `[Action] -> [Reaction] -> [Reward]` 흐름으로 작성한다.
   - 레퍼런스의 전체 루프가 크면 30초 안에 반복 가능한 First Playable Loop로 줄인다.
4. 최소 기능 범위
   - Must Have, Should Have, Later로 구분한다.
   - Must Have는 핵심 가설 검증에 필요한 3~5개 기능으로 제한한다.
   - 레퍼런스의 정체성을 이루지만 첫 검증에 필요 없는 기능은 Later로 보낸다.
5. Phase 별 일정
   - 1~2주 단위로 검증 가능한 순서를 제안한다.
   - Phase 1은 반드시 로컬/단일 테스트 씬에서 핵심 조작과 피드백을 검증한다.
6. 책임 분리 초안
   - `시스템 / 책임 / 가지면 안 되는 책임` 표를 포함한다.
7. 상태 / 프로세스 SSOT 초안
   - `상태 또는 프로세스 / SSOT` 표를 포함한다.
8. 위험 요소
   - 범위 폭발, God Object, 구현 순서 위험, 피드백 누락 등을 지적한다.
9. 제외할 것
   - 초기 프로토타입에 넣지 않을 시스템을 명시한다.
10. 첫 구현 단위
   - 가장 먼저 만들 테스트 씬, 맵, 오브젝트, 컴포넌트 단위를 제안한다.
   - 첫 구현 단위는 "이 장르가 재미있는지 30초 안에 반복 확인할 수 있는 장면"이어야 한다.

## 금지

- 계획 요청에서 전체 `.h/.cpp` 구현 코드를 작성하지 않는다.
- 상용 게임 수준의 전체 시스템을 한 번에 계획하지 않는다.
- 사용자가 요구하지 않은 Multiplayer, Replication, GAS, 대형 Manager 구조를 초기 계획에 넣지 않는다.
- 레퍼런스 게임의 기능 목록을 Must Have로 그대로 복사하지 않는다.
- Phase 1에 네트워크, 매치메이킹, 대형 맵, 성장, 저장, 복잡한 UI를 넣지 않는다.
