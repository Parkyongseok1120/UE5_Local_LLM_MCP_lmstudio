# RAG Layering and Example Use

## 검색 키워드

RAG layering, core architecture RAG, domain adapter RAG, project-specific RAG, example misuse, guideline scope, evidence type

## 목적

RAG 문서는 계층으로 구분한다. 모델은 예시를 규칙으로 오해하지 말고, 현재 질문에 맞는 계층의 근거만 적용해야 한다.

| 계층 | 역할 | 예 |
|---|---|---|
| Core Architecture RAG | 도메인과 무관한 설계 검수 원칙 | 책임 분리, 상태/프로세스 SSOT, API 경계, 이벤트 소유권 |
| Intent / Planning RAG | 요청 의도와 계획 답변 형식 | Prototype Planning, Design Review, Implementation, Code Review |
| Genre Adapter RAG | 장르별 최소 프로토타입 기준 | Action Combat, Shooter, Puzzle, Deckbuilder, Racing |
| Domain Adapter RAG | 특정 기술/엔진/플랫폼 규칙 | Unreal C++ reflection, UInterface, Delegate, Timer, UObject lifetime |
| Project-Specific RAG | 특정 프로젝트의 이름, 클래스, 루프, 규칙 | 프로젝트 고유 캐릭터, 무기, 해킹, 전투 루프 |

## 적용 순서

1. 도메인이 불명확하면 Core Architecture RAG만 적용한다.
2. 계획/순서/프로토타입 요청이면 Intent / Planning RAG를 적용한다.
3. 장르나 레퍼런스가 있으면 Genre Adapter RAG를 적용한다.
4. 도메인이 Unreal이면 Core Architecture RAG 다음 Unreal Domain RAG를 적용한다.
5. 특정 프로젝트명이 주어졌을 때만 Project-Specific RAG를 적용한다.
6. Project-Specific 예시를 Core 규칙처럼 일반화하지 않는다.

## 예시 오용 금지

RAG 예시는 원칙 설명용 사례다. 예시에 나온 클래스명, 함수명, 상태명, 도메인 개념을 다른 프로젝트에 그대로 적용하지 않는다.

금지:

- 예시의 Shield를 모든 Hackable 대상의 필수 개념으로 일반화한다.
- 예시의 Enemy를 모든 Target의 기본 구조로 일반화한다.
- 예시의 PlayerController 책임을 모든 Controller 구조에 그대로 적용한다.
- 예시의 Unreal API를 Unity, Backend, Tool, AI Agent 설계에 적용한다.
- Project-specific 용어를 Core Architecture 규칙처럼 사용한다.

## 답변 전 검사

RAG 예시를 사용했다면 다음을 확인한다.

- 이 예시는 현재 질문과 같은 도메인인가?
- 이 예시는 Core 원칙인가, Domain 규칙인가, Project-specific 사례인가?
- 예시 이름을 그대로 재사용하고 있지는 않은가?
- 구현체 전용 개념을 범용 interface 계약에 넣고 있지는 않은가?

## 핵심 규칙

구현체 전용 개념을 범용 인터페이스 계약에 넣지 않는다.
