# Code Suppression For Planning

## 검색 키워드

planning mode no code, suppress implementation code, no h cpp in planning, pseudocode only, prototype planning answer

## 목적

계획 요청에서 긴 구현 코드가 튀어나오는 것을 막는다.

## 계획 모드 금지

- 전체 `.h/.cpp` 코드
- 50줄 이상의 구현 코드
- 컴파일 가능하다고 주장하는 코드
- Unreal reflection 매크로가 들어간 구현 코드
- 요청하지 않은 GAS, Replication, 대형 Manager 구조

## 계획 모드 허용

- 클래스/컴포넌트 이름 후보
- 책임표
- 상태 전이도
- 짧은 의사코드
- 함수 시그니처 후보
- Phase 계획
- 테스트 씬 구성

## 규칙

사용자가 "계획", "순서", "프로토타입", "같은 게임 만들고 싶다"라고 말하면 구현 코드를 작성하지 않는다. 구현 코드는 사용자가 명시적으로 "코드 작성", ".h/.cpp", "컴파일 가능한 코드"를 요구했을 때만 작성한다.
