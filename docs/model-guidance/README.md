# 모델 참고 문서 묶음

- 버전: 0.1 / 2026-09-28
- 상태: 컴팩터에 선택적 공급 기능 구현. 기본 Off. 실행 중인 LM Studio 설치본과 게임 코드에는 적용하지 않았다.
- 목적: 기존 소유자·계약·수명을 확인하면서 읽을 수 있는 코드를 작성하고, 근거 없는 API 수정과 실행 연결 누락을 줄인다.
- 정본: 이 디렉터리의 주제별 Markdown. [조사 기록](../Design_Guidance_Research_20260928.md)은 근거와 조사 경위를 보관한다.

## 모델에 제공할 본문

| 문서 | 참고할 상황 |
| --- | --- |
| [core.md](core.md) | 작업 공통 기준. 기본으로 제공할 후보 |
| [design.md](design.md) | SSOT·SOLID, 계약·수명, 패턴 선택 |
| [debugging.md](debugging.md) | 빌드 실패, API·헤더·매크로, 의미 보존 |
| [code-style.md](code-style.md) | 줄 간격·들여쓰기·이름·주석과 짧은 작성 예시 |
| [unreal.md](unreal.md) | Unreal 설정·생성·입력·참조·종료 |
| [unity.md](unity.md) | Unity 설정·콜백·비동기·풀·입력·물리 |
| [multiplayer.md](multiplayer.md) | 권한·메시지·시간·연결의 공통 개념 |
| [unreal-networking.md](unreal-networking.md) | Unreal RPC·복제·CMC·매치 수명 |
| [unity-networking.md](unity-networking.md) | NGO 2.7·Entities 1.10의 계약과 차이 |

## 프로젝트별 사실

- [project-template.md](project-template.md): 버전, 현재 소유자, 경로, 수명, 설정, 미확인 사항을 짧게 기록하는 틀.
- [examples/latency-multicombat.md](examples/latency-multicombat.md): 이번 Unreal 프로젝트에서 확인한 사실과 우선 결함을 기록한 예시. 다른 프로젝트의 기본값으로 사용하지 않는다.
- [sources.md](sources.md): 버전·근거 목록. 최신 버전·현재 설치 버전을 자동으로 보증하지 않는다.

## 제공 단위 제안

기본 후보는 core와 해당 프로젝트 현황 중 현재 작업에 필요한 사실이다. 상세 문서와 프로젝트의 전체 조사 목록은 현재 질문과 관련된 절을 선택한다.

| 작업 | 추가로 제공할 후보 |
| --- | --- |
| 일반 함수 수정 | 필요하면 code-style의 작성 기준 |
| 컴파일 실패 반복 | debugging와 해당 엔진의 관련 절 |
| 책임·수명 재설계 | design와 해당 엔진의 관련 절 |
| 원격 발사·리스폰 문제 | multiplayer의 권한·수명 + 해당 네트워크 문서 |
| 다른 엔진으로 전환 | 새 프로젝트 현황과 새 엔진 문서; 이전 프로젝트 사실은 적용 종료 |

정확한 문서 개수나 도구 순서를 고정하지 않는다. 전체 묶음·조사 기록·근거 JSON을 상시 프롬프트로 복사하지 않는다. 불필요한 문서를 빼더라도 최신 사용자 요구와 실제 오류·소스 근거가 유지되어야 한다.

파일은 UTF-8이다. 본문 크기는 토큰 수와 다르며 실제 입력 비용은 선택 모델의 템플릿과 tokenizer로 측정해야 한다. 이 단계에서는 실행 중인 모델을 호출해 측정하지 않았다.

## 컴팩터에서 제공하는 방법

새 빌드의 GUI에서 `Design references`로 Off, Common core, SSOT/SOLID, Debugging, Formatting, Engine lifetime, Multiplayer 중 하나를 선택한다. `Design reference token allowance`의 기본값은 2048이며 0이면 본문을 제공하지 않는다.

문서 선택은 core → 선택 주제 → 확인되거나 명시된 엔진 순서다. Formatting은 core와 표현 문서만 사용한다. 도구 목록만으로 추정한 엔진은 선택 근거로 쓰지 않는다. 예산에 맞지 않으면 뒤의 문서를 온전히 제외하며, core도 맞지 않으면 원래 입력으로 진행한다. 상세 절의 자동 검색은 구현하지 않았고 첫 버전의 선택 단위는 짧은 문서다.

본문은 기존 근거 압축·보존 후에 합성하고 실제 템플릿으로 측정한다. 기존 작업 창·도구 결과 예산과 위 allowance를 모두 만족할 때만 제공한다. Observe only와 강제 최종 보고에는 넣지 않는다. 문서를 위해 추가 압축·출력 한도 축소·도구 차단을 하지 않는다.

`design_guidance_input` 디버그 항목에서 requestedIds, selectedIds, omittedIds, reason, addedTokens를 확인할 수 있다. 참고 본문은 일반 대화 이력이나 영속 체크포인트에 저장하지 않고 각 모델 입력에 다시 구성한다.

## 본문 유지보수와 프로젝트 사실

`catalog.json`과 Markdown이 정본이다. `npm run guidance:build`가 배포용 TypeScript 데이터를 생성하며 `npm run build`에서도 자동 생성한다. 생성 파일은 직접 편집하지 않는다. 런타임 파일 읽기나 프로젝트 경로 확대는 필요 없다.

프로젝트 현황 템플릿과 실제 검토 예시는 자동 주입 대상에서 제외했다. 프로젝트 사실은 현재 요청과 기존 파일/첨부 도구의 관찰로 제공한다. 파일명·링크만 붙인 것은 본문 전달과 다르다.

실제 모델의 품질 개선 효과는 아직 측정하지 않았다. [현재 구조와 연결 제안](../Design_Guidance_Integration_Proposal_20260928.md)에 책임·예산·수명·확인 방법을 정리했다.
