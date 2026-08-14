# LM Studio GUI 실사용 검증 로그 — 2026-08-12

## 공통 환경

- LM Studio: 0.4.20
- 모델: `qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max`
- 모델 파일: `Qwen3.6-27B-NEO-CODE-HERE-2T-OT-Q4_K_S.gguf`
- 컨텍스트: 72,448 tokens
- 생성기: `codex/unreal-context-compactor`
- 프로젝트: 로컬 Unreal 테스트 프로젝트(경로 비공개)

## revision 54 — checkpoint 반복

계획·Feature Intent·정적 스케치 검증은 통과했지만 executor 진입 후 모델이 실제 읽기나
수정 대신 `unreal_task_checkpoint(action=record)`를 15회 반복했다. checkpoint가 진척 없이도
sequence와 작업 예산을 갱신하고, 다음 도구를 구체적으로 바인딩하지 않던 것이 원인이었다.

수정 사항:

- 동일 checkpoint는 sequence를 늘리지 않는 heartbeat-only 응답으로 처리한다.
- 서버가 `requiredNextAction`을 명시한 경우에만 phase budget을 넘긴다.
- 일반 예측에서는 checkpoint 도구를 숨기고 recovery/budget turn에서만 노출한다.

## revision 55 — 프로젝트 읽기 반복

reasoning 진행 표시와 checkpoint 수정은 동작했지만 자연어 기능 요청이 선행 Architecture
경로로 분류되지 않았다. 모델이 고유한 `read_file` 호출을 14회 이상 계속해 구현으로 넘어가지
못했다. 탐색 예산을 6회로 제한하고 자연어 Architecture 감지 범위를 넓혔다.

## revision 56 — Architecture 계약 자기증폭 루프

- 대화: 로컬 LM Studio 대화 artifact(경로 비공개)
- 활성 플러그인: `lmstudio/rag-v1`, `mcp/unreal-agent`, `mcp/unreal-rag`,
  `mcp/evidence-first`, `codex/unreal-context-compactor`

전송 프롬프트:

> 현재 테스트 프로젝트를 먼저 읽고 구조와 상태 소유권을 파악한 다음, 로컬 대국에서 사용할 착수 기록과 한 수 되돌리기 기능을 기존 상태 소유권과 충돌하지 않는 작은 독립 시스템으로 설계하고 구현해줘. 기존 네트워크·아이템·미니게임·서버 기능은 건드리지 말고, 필요한 자동 테스트를 추가한 뒤 정적 검증과 Unreal 빌드까지 수행해 결과를 알려줘.

첫 탐색은 active project 1회, directory 1회, 파일 읽기 4회 뒤 Architecture 검증으로 정상
수렴했다. 이후 Architecture proposal은 세 번 연속 실패했다.

1. 선언은 `"I1: ..."`, 참조는 `"I1"`이어서 exact string 비교에 실패했다.
2. `no RPC`, `local-only`, `dedicated server 제외` 같은 부정 문구를 단순 keyword scan이
   networked proposal로 오판했다.
3. 모델이 오류를 피하려고 가짜 networking 필드를 추가하자 RPC/소유권 검사가 연쇄적으로
   추가되어 FullReplan으로 커졌다.

이는 모델 추론 속도가 아니라 공개 schema와 validator 의미론이 만든 진행 차단 루프다. GUI는
세 번째 Architecture 실패에서 중단했으며 테스트 프로젝트 파일에는 새 변경이 없었다.

## revision 57 — 수정 및 재검증 대상

- invariant를 `{id, statement}`로 선언하고 slice/matrix는 ID로 참조한다.
- `scope.networked`를 권위값으로 사용한다. scope가 없을 때만 keyword heuristic을 경고용으로
  사용하며 부정 문맥은 networked로 분류하지 않는다.
- Draft/Bound/Strict 검증 강도를 구분한다. 로컬 bounded 기능은 alternatives, migration,
  networking 계약을 요구하지 않는다.
- schema/reference/missing-detail은 exact 또는 bounded repair로 처리하고, 중앙 SSOT·authority
  모순만 FullReplan으로 승격한다.
- 손상된 persisted Architecture state는 Discovery로 초기화하지 않고 FailedClosed로 닫는다.
- 숫자 인수는 exact/widening/narrowing/incompatible을 구분해 축소 변환을 차단한다.

revision 57 설치 후 새 대화에서 위와 동일한 자연어 프롬프트를 다시 실행하고, 결과와 남은
이상을 아래에 추가한다.
