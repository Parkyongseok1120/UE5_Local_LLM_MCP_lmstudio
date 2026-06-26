# Risk Explanation Required Format

## 검색 키워드

risk explanation, design risk, long-term maintainability, extension risk, minimal fix, responsibility split

## 목적

위험 설계를 표면적으로만 지적하지 않고, 왜 장기적으로 깨지는지 설명한다.

## 위험 지적 형식

위험한 설계를 지적할 때는 아래 형식을 따른다.

### 위험 N: 위험 이름

1. 현재 문제
   - 어떤 책임이 섞였는가?
2. 즉시 발생 가능한 문제
   - 어떤 버그, 테스트 어려움, 디버깅 문제, 의존성 문제가 생기는가?
3. 확장 시 깨지는 지점
   - 새 타입, 새 기능, 새 플랫폼, 멀티플레이, UI, AI, 데이터 확장 시 무엇이 깨지는가?
4. 더 나은 분리
   - 어떤 객체가 어떤 책임을 가져야 하는가?
   - 어떤 API 또는 인터페이스로 나눌 것인가?
5. 최소 수정안
   - 지금 프로토타입에서 과하지 않게 고치는 방법은 무엇인가?

## 나쁜 설명

`GetShieldValue`는 인터페이스에 넣으면 안 된다.

## 좋은 설명

`GetShieldValue`는 특정 Shield 구현체에 종속된다. 나중에 Shield가 없는 대상이 같은 capability interface를 구현하면 더미 값을 반환해야 하므로 contract가 오염된다. Shield 관련 Query는 별도 interface 또는 상태 소유 객체 query로 분리하고, capability interface는 최소 계약만 유지한다.
