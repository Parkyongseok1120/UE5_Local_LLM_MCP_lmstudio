# Shooter Prototype Adapter

## 적용 조건

FPS, TPS, 슈터, 총기 액션, 히트스캔, 투사체 전투.

## 핵심 재미

조준, 발사, 명중, 피격 피드백이 즉각적으로 느껴지는지 검증한다.

## 핵심 루프

Aim -> Fire -> Hit/Miss -> Feedback -> Reload/Reposition

## 최소 프로토타입 범위

Must Have:

- 카메라 조준과 사격 입력
- 탄 퍼짐 또는 레이캐스트/투사체 중 하나
- 체력 있는 타겟
- 피격 피드백
- 탄약 또는 재장전 중 하나

Later:

- 무기 다수, 부착물, 커버 시스템, 네트워크, 복잡한 탄도

## 추천 책임 분리

| 시스템 | 책임 | 가지면 안 되는 책임 |
|---|---|---|
| Character | 이동, 시점 회전 | 데미지 최종 적용 |
| WeaponComponent | 발사 가능 여부, 탄약, 쿨다운, 사격 요청 | Target Health 직접 수정 |
| AimComponent | 조준 방향, 조준 보정 | 데미지 계산 |
| HitScan/ProjectileComponent | 명중 판정 | 체력 상태 소유 |
| HealthComponent | 체력 SSOT, ApplyDamage | 사격 입력 처리 |
| FeedbackComponent | 히트마커, 반동, 사운드 | 전투 규칙 판단 |

## 위험 요소

- Weapon이 Target 체력을 직접 깎음
- 카메라, 총기, 탄약, 데미지가 한 클래스에 몰림
- Projectile과 HitScan 규칙이 섞임

## 첫 구현 단위

테스트 맵에서 조준, 발사, 명중/빗나감, 체력 감소, 히트마커, 재장전만 검증한다.
