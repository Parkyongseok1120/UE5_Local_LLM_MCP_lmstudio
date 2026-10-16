# 모델 참고 문서 묶음

- 버전: 0.2 / 2026-09-29
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

새 빌드의 GUI에서 `Design references`로 Off, Auto, Common core, SSOT/SOLID, Debugging, Formatting, Engine lifetime, Multiplayer 중 하나를 선택한다. 기본 Off는 자동 선택을 하지 않는다. `Design reference token allowance`의 기본값은 2048이며 0이면 본문을 제공하지 않는다.

**Auto**는 출처와 완전성이 확인된 최신 사용자 목적에서 주요 주제를 고른다. 함께 명시한 주제가 둘이면 하나를 보조로 제공하고, 현재 유효한 진단이 있으면 debugging을 보조로 우선한다. 세 주제 이상, 불명확하거나 잘린 목적은 공통 기준으로 축소한다. Auto는 Focused 전달을 사용하며 저장된 delivery 설정은 바꾸지 않는다. 기존 문서 수·실제 토큰 상한과 예산·복구·최종화 조건에 따라 생략한다. 추가 모델 호출·도구 호출·API 조회·빌드를 시작하지 않는다. 아래 Documents와 수동 Focused 설명은 주제를 직접 선택한 경우에 적용된다.

`Design reference delivery`는 **Documents**가 호환 기본값이다. 이 경우 core → 선택 주제 → 확인되거나 명시된 엔진 순서로 기존 문서 본문 전체를 제공한다. Formatting은 core와 표현 문서만 사용한다. 도구 목록만으로 추정한 엔진은 선택 근거로 쓰지 않는다. 예산에 맞지 않으면 뒤의 문서를 온전히 제외하며, core도 맞지 않으면 원래 입력으로 진행한다.

**Focused sections and observations**를 선택하면 같은 주제 안에서 필요한 절과 동반 계약을 고른다. 완료된 실제 도구 결과에서 진단, 파일 관찰, 확인된 Unity 버전·package lock의 설치 버전, 일부 포맷 설정을 제한적으로 요약한다. 모델 추론문이나 파일명의 키워드로 주제를 자동 변경하지 않는다. 추가 도구 호출·파일 탐색·모델 호출은 없다.

Focused의 한도는 실행 내 최근 16개 관찰, 입력에 최대 3개 진단 그룹/12개 진단, 최대 8개 메타데이터 항목, 최대 6개 절, 라운드당 최대 4개 후보다. 절·동반 계약·출처·자료를 묶어 실제 입력 증가량을 측정한다. C1 전체 제한 자료+최우선 계약+최대 2개 보충 묶음 → C2 최신 진단+최우선 계약 → C3 계약만 → C4 공통 최소 절 순서이며, 모두 안 맞으면 기준 입력을 쓴다.

본문은 기존 근거 압축·보존 후에 합성하고 실제 템플릿으로 측정한다. 기존 작업 창·도구 결과 예산과 위 allowance를 모두 만족할 때만 제공한다. Observe only와 강제 최종 보고에는 넣지 않는다. 문서를 위해 추가 압축·출력 한도 축소·도구 차단을 하지 않는다.

Focused는 일반 작업 및 bounded audit의 일반 조사 라운드에서 제공하고, research/reasoning recovery와 tool-planning retry에는 넣지 않는다. Off·Observe only·allowance 0에서는 관찰 요약을 수집하거나 후보를 만들지 않는다. 기존 Documents의 복구 단계 전달 방식은 유지한다.

정적 참고 원칙은 기존 시스템 입력에, 외부 진단·프로젝트 자료는 별도 assistant 참고 데이터 메시지에 넣는다. 자료는 당시 반환값에서 파생된 설명이며 새 파일 읽기·편집 receipt·권한·완료 증거가 아니다. Unity accepted는 요청 수락이고 Player 빌드 성공이 아니다. 현재 컴파일 ID와 다른 로그 행은 현재 결과에 합치지 않으며 구독 범위·누락·세대 미확인을 표시한다.

관찰의 수명은 한 실행이다. 같은 파일의 수정·삭제·충돌을 관찰하면 이전 설정 요약을 버리고, 같은 batch에서 읽기/수정이 겹치면 순서를 확정하지 않는다. 다른 프로젝트 결과는 섞지 않는다. 외부 변경을 아직 관찰하지 못한 경우 마지막 관찰만 표시한다. note v1 판단은 현재 프로젝트 사실로 변환하지 않는다.

`design_guidance_input` 디버그 항목에서 delivery, requestedIds, selectedIds, omittedIds, requestedDataIds, selectedDataIds, omittedDataIds, reason, attempts, addedTokens를 확인할 수 있다. 참고 본문은 일반 대화 이력이나 영속 체크포인트에 저장하지 않고 각 모델 입력에 다시 구성한다. `included`는 입력에 포함됐다는 뜻이며 모델이 원칙을 따랐거나 품질이 좋아졌다는 평가가 아니다.

## 본문 유지보수와 프로젝트 사실

`catalog.json`과 Markdown이 정본이다. `npm run guidance:build`가 배포용 TypeScript 데이터를 생성하며 `npm run build`에서도 자동 생성한다. 생성 파일은 직접 편집하지 않는다. 런타임 파일 읽기나 프로젝트 경로 확대는 필요 없다.

프로젝트 현황 템플릿과 실제 검토 예시는 자동 주입 대상에서 제외했다. 프로젝트 사실은 현재 요청과 기존 파일/첨부 도구의 관찰로 제공한다. 파일명·링크만 붙인 것은 본문 전달과 다르다.

실제 모델의 품질 개선 효과는 아직 측정하지 않았다. [현재 구조와 연결 제안](../Design_Guidance_Integration_Proposal_20260928.md)에 책임·예산·수명·확인 방법을 정리했다.
