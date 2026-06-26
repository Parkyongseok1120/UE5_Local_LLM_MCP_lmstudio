# Genre Adapter Registry

## 검색 키워드

genre adapter, gameplay genre, prototype adapter, action combat, shooter, battle royale, extraction shooter, survival pressure, platformer, puzzle, survival, roguelike, deckbuilder, simulation, tactics, stealth, horror, narrative, rhythm, racing, tower defense

## 목적

사용자가 장르나 레퍼런스 게임을 제시하면 해당 장르의 핵심 루프, 최소 기능 범위, 책임 분리 기준을 선택한다.

## 적용 규칙

1. Core Architecture RAG는 항상 적용한다.
2. 사용자의 장르에 맞는 Genre Adapter를 추가 적용한다.
3. 엔진이 Unreal이면 Unreal Domain RAG를 추가 적용한다.
4. 특정 프로젝트명이 있으면 Project RAG를 마지막에 적용한다.

## 우선순위

Core 원칙 > Domain 구현 규칙 > Genre Adapter > Project-specific 예시

Project-specific 예시는 Core 원칙을 덮어쓸 수 없다.

## 기본 Genre Adapter

- Action Combat
- Shooter
- Battle Royale / Extraction Shooter
- Platformer
- Puzzle
- Survival Crafting
- Roguelike
- Deckbuilder
- Management Simulation
- Strategy / Tactics
- Stealth
- Horror
- Narrative Adventure
- Rhythm
- Racing
- Tower Defense

## 혼합 장르 선택 규칙

레퍼런스가 여러 장르를 섞고 있으면 한 Adapter만 고집하지 않는다.

예:

- Battle Royale = Shooter + Survival Pressure + Match Elimination
- Extraction Shooter = Shooter + Loot Risk + Extraction Decision
- Soulslike = Action Combat + Stamina / Commitment Risk
- Survival Horror = Survival Resource Pressure + Horror Avoidance

이 경우 답변은 먼저 "어떤 압박 속에서 어떤 핵심 행동을 반복하는가"를 정리하고, 첫 프로토타입은 가장 작은 압박 루프 하나만 검증한다.

대규모 온라인, 매치메이킹, 랭킹, 라이브 서비스, 대형 맵은 장르 정체성에 중요해도 첫 구현 단위의 기본값이 아니다.

## Adapter 문서 형식

각 장르 문서는 다음 항목을 가진다.

- 적용 조건
- 핵심 재미
- 핵심 루프
- 최소 프로토타입 범위: Must Have / Should Have / Later
- 추천 책임 분리
- 상태 / 프로세스 SSOT
- 위험 요소
- 첫 구현 단위
