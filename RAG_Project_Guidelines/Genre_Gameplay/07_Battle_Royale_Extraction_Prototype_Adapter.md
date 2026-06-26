# Battle Royale / Extraction Prototype Adapter

## 검색 키워드

battle royale prototype, extraction shooter prototype, survival pressure, shrinking zone, last man standing, loot risk, PUBG style, first playable loop

## 적용 조건

사용자가 Battle Royale, PUBG-like, Extraction Shooter, Tarkov-like, last survivor, shrinking zone, loot-and-survive 구조를 말하면 적용한다.

이 Adapter는 특정 게임을 복제하기 위한 문서가 아니다. 대규모 온라인 매치 전체가 아니라 "생존 압박 속에서 교전/회피/이동을 선택하는 루프"를 먼저 검증한다.

## 핵심 재미

제한된 자원과 좁아지는 안전 범위 속에서 싸울지, 숨을지, 이동할지 빠르게 판단하는 긴장감.

## 핵심 루프

Battle Royale:

```text
Spawn -> Find Minimal Resource -> Zone Pressure -> Fight/Avoid -> Survive/Eliminated
```

Extraction:

```text
Enter -> Loot -> Risk Increases -> Fight/Avoid -> Extract or Lose Loot
```

첫 프로토타입은 전체 매치가 아니라 다음 30초 루프를 검증한다.

```text
Move -> Detect Threat/Zone -> Choose Fight or Move -> Receive Damage/Reward Feedback -> Survive or Fail
```

## 최소 프로토타입 범위

Must Have:

- 이동과 조준/상호작용 입력
- 위협 대상 1종 또는 더미 적 1종
- 제한 자원 1종: 탄약, 체력, 회복 아이템, 시간 중 하나
- 압박 장치 1개: 좁아지는 영역, 위험 구역, 추적자, 타이머 중 하나
- 생존/탈락 또는 탈출 성공 조건 1개

Should Have:

- 간단한 아이템 픽업
- 무기 1종
- 피격/명중/위험 구역 피드백
- 봇 또는 스폰 지점 1~2개

Later:

- 실제 대규모 멀티플레이
- Replication, RPC, client prediction
- 매치메이킹, 랭킹, 로비
- 대형 맵 스트리밍
- 차량, 낙하산, 복잡한 인벤토리
- 안티치트, 관전, 리플레이

## 추천 책임 분리

| 시스템 | 책임 | 가지면 안 되는 책임 |
|---|---|---|
| Character | 이동, 조준, 상호작용 요청 | 매치 규칙, 존 진행, 아이템 스폰 |
| Weapon / Action Component | 공격 가능 여부, 탄약, 발사 요청 | Target Health 직접 수정 |
| Health Component | 체력 SSOT, 피해 적용, 사망 이벤트 | 입력 처리, 무기 발사 |
| Zone / Pressure Component | 위험 영역, 타이머, 압박 진행 | 플레이어 체력 직접 임의 수정 |
| Loot / Pickup Actor | 자신이 제공하는 아이템 상태 | 인벤토리 전체 규칙 |
| Prototype GameMode | 시작/종료 조건, 생존/탈락 판단 | 조작, 무기 세부 상태, UI 피드백 |

## 상태 / 프로세스 SSOT

| 항목 | SSOT |
|---|---|
| 플레이어 체력 | Health Component 또는 Character |
| 탄약/쿨다운 | Weapon / Action Component |
| 위험 영역 위치/반경 | Zone / Pressure Component |
| 압박 진행 타이머 | Zone / Pressure Component |
| 아이템 픽업 가능 여부 | Pickup Actor |
| 생존/탈락 상태 | Prototype GameMode 또는 Player State 역할 객체 |

초기 로컬 프로토타입에서는 PlayerState, Replication, 서버 권한 모델을 기본값으로 삼지 않는다. 멀티플레이를 실제로 구현하기 시작할 때 서버 권한 SSOT로 이동할 상태를 따로 표시한다.

## 위험 요소

- 대규모 멀티플레이를 먼저 구현해서 핵심 긴장감 검증이 늦어진다.
- 레퍼런스의 상징 기능인 낙하산, 차량, 대형 맵, 무기 다수를 Phase 1에 넣는다.
- 총기 손맛과 피격 피드백을 검증하기 전에 매치메이킹/Replication부터 설계한다.
- 압박 장치가 없어서 일반 슈터 테스트와 차이가 없어진다.
- 탈락/생존 조건이 없어서 루프가 끝나지 않는다.

## 첫 구현 단위

작은 테스트 맵 하나를 만든다.

- 플레이어 1명
- 더미 적 또는 단순 추적자 1종
- 무기 또는 상호작용 1종
- 좁아지는 원/위험 구역/타이머 중 하나
- 체력과 탈락 조건

검증 질문:

- 압박 때문에 이동/교전 선택이 생기는가?
- 맞췄다/맞았다/위험하다 피드백이 즉시 보이는가?
- 30초 안에 성공 또는 실패가 발생하는가?
