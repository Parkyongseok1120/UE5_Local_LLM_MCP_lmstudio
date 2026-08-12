# LM Studio GUI 실사용 테스트 로그

## 18:19~18:50 재실행과 최종 조치

- 모델: `qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max`
- 생성기: `codex/unreal-context-compactor`
- 활성 플러그인: `mcp/unreal-agent`, `mcp/unreal-rag`, `lmstudio/rag-v1`
- 대화 저장본: `C:\Users\sster\.lmstudio\conversations\84\1786526047806.conversation.json`
- 컴팩터 세션: `C:\Users\sster\.lmstudio\unreal-context-compactor\sessions\d482217fe3a93ebfd08455c1fe555120`
- 태스크: `9bf32576314044d3`

이번 재실행은 `unreal_get_active_project`부터 시작해 `unreal_agent_plan`, 원자적
`unreal_feature_intent_resolve`, `unreal_code_sketch_claim_validate`까지 정상 통과했다.
따라서 이전의 planner 미노출, taskAuthorization 위조, 공개/내부 Feature Intent 계약
불일치는 재현되지 않았다. 정상 `CheckWinAt(FIntPoint)` 스케치는 validator가 6 verified,
known_bad 0으로 승인했다.

진행 차단 이상은 executor 진입 뒤 발생했다. 모델이 실제 `read_file_range` 또는 mutation을
호출하지 않고 `unreal_task_checkpoint(action=record)`를 15회 반복했다. 서버 상태는
`repeated_action_no_progress`, `retry_budget_exhausted`로 닫혔고, 컴팩터의
`mutationGeneration`은 0이었다. 대화에는 mutation tool call이 없으므로 이번 GUI 실행이
O-Mock에 새 변경을 적용했다는 증거도 없다.

근본 원인은 체크포인트가 실제 파일/슬라이스/검증 변화가 없어도 새 sequence를 기록하고
작업 호출 예산을 초기화하며, 응답은 `continue_with_current_tool_route`만 돌려주던 경계였다.
수정 후에는 다음 계약을 적용한다.

- 동일 체크포인트는 sequence를 늘리지 않는 heartbeat-only 응답이다.
- 서버의 phase-budget handoff가 `requiredNextAction`을 명시한 경우에만 작업 예산을 초기화한다.
- 그 handoff 응답은 정확한 다음 작업 도구를 `requiredNextTool`로 바인딩한다.
- 일반 routed prediction에서는 `unreal_task_checkpoint` 스키마를 숨기고, 서버가 명시적으로
  요구한 recovery/budget turn에만 노출한다.
- 체크포인트 응답은 후속 서버 지시 없이 체크포인트를 다시 호출하지 말라고 명시한다.

최종 단일 회귀 실행 결과:

- Python checkpoint/route/autonomy: `71 passed`
- Context compactor build/test: `105 passed`
- 실패: 0

해당 checkpoint-loop 수정본은 context compactor `0.4.12`, LM Studio plugin revision `54`로 설치했다.

후속 UX/route 교차검증 수정본은 context compactor `0.4.13`, LM Studio plugin revision `55`이다.

- 실행 시각: 2026-08-12 13:27:21 ~ 13:38:44 (KST)
- 애플리케이션: LM Studio 0.4.20
- 모델: `DavidAU/Qwen3.6-27B-Heretic-Uncensored-FINETUNE-NEO-CODE-Di-IMatrix-MAX-GGUF/Qwen3.6-27B-NEO-CODE-HERE-2T-OT-Q4_K_S.gguf`
- 모델 식별자: `qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max`
- 컨텍스트 길이: 72,448
- 연결된 MCP: `mcp/unreal-rag`, `mcp/unreal-agent`, `mcp/evidence-first`, `codex/unreal-context-compactor`
- 실제 LM Studio 로컬 모델 로드: 완료
- O-Mock 실제 기능 구현: 미수행(중단 조건 발생 전 탐색 단계에서 중단)

## 전송한 자연어 프롬프트

> 현재 O-Mock 프로젝트에서 다음으로 실제 플레이 가능한 기능을 구현해줘. 먼저 현재 코드와 구조를 확인하고 기존 규칙·상태·입력 흐름을 존중해. 이번 작업은 오목의 기본 게임과 로컬 2인 핫시트에 집중해: 보드와 좌표 검증, 빈칸 확인, 돌 배치, 턴 전환, 가로·세로·대각선 승리 판정, 종료 후 추가 입력 차단, 재시작까지 한 흐름으로 동작하게 해줘. 화면에서 두 사람이 번갈아 플레이할 수 있어야 하고, 테스트 가능한 규칙 로직과 필요한 자동 테스트도 함께 추가해. 네트워크, 매치메이킹, 아이템, 미니게임, 서버 기능은 이번 작업에서 건드리지 말고, 구현 전 현재 상태와 위험 요소를 짧게 확인한 다음 구현하고 검증 결과를 보고해.

## 관찰 타임라인

1. 모델 로드 후 새 채팅에서 프롬프트 전송.
2. 약 10초: 모델이 `unreal_get_active_project`, `get_workspace_info` 호출.
3. 약 30초: `get_active_project`, `list_directory`로 프로젝트 구조 탐색.
4. `list_directory`에서 `Source/O-Mock` 경로가 없다는 실패가 한 번 발생.
5. `unreal_rag_search`가 active-project RAG miss 및 source freshness 경고를 반환.
6. 모델이 `search_files`를 반복 호출하면서 “검색 결과에 실제 파일 경로가 보이지 않는다”는 동일 reasoning을 반복.
7. 서버 측 repeat/stagnation gate가 `RAG_QUERY_REPEAT_BLOCKED`, `EVIDENCE_STAGNATION`, `EVIDENCE_STAGNATION_REPEAT`를 반환했지만 모델은 같은 `search_files` 호출을 계속 생성.
8. 약 1분 경과 후 진행 불능 상태로 판단해 GUI의 정지 버튼을 눌러 중단.
9. 최종 UI 상태: `Model failed to generate a tool call`.

## 호출 통계

- 전체 tool call: 42
- `search_files`: 31
- `list_directory`: 4
- `unreal_rag_search`: 3
- `unreal_get_active_project`: 1
- `get_workspace_info`: 1
- `get_active_project`: 1
- `read_file`: 1
- `write_file`/`apply_edit_bundle`/`build_unreal_project`: 0

## 중단 원인 및 증거

LM Studio 대화 저장본 `C:\Users\sster\.lmstudio\conversations\84\1786508841947.conversation.json`에서 MCP tool result가 다음과 같이 text 한 블록으로만 저장되었다.

```text
OK [list_directory] Completed
Detailed result is available in structuredContent.control and structuredContent data.
```

실제 디렉터리 목록이나 구조화된 결과가 모델 입력에 포함되지 않았다. 그 결과 모델이 “actual file paths가 보이지 않는다”고 반복했고, 동일한 `search_files` 파라미터를 20회 이상 재생성했다. 이는 모델 추론 속도 문제가 아니라 LM Studio 클라이언트가 MCP `structuredContent`를 모델-facing tool result로 전달하지 않는 프로토콜 호환성 문제로 판단한다.

`UNREAL58_ROOT`는 MCP 도구 저장소, `rag.sqlite`는 공용 Unreal 지식 인덱스이므로 `unreal-agent`의 프로젝트 작업 루트와 같아야 하는 값은 아니다. 두 MCP 모두 공유 설정의 `activeProject=C:\Users\sster\Documents\Git\O-Mock\O_Mock.uproject`를 정상적으로 읽었다. 실제 O-Mock 모듈 디렉터리는 `Source\O_Mock`(언더스코어)이며, 모델이 `Source/O-Mock`(하이픈)을 추측한 직접 원인은 첫 `list_directory`의 실제 entries가 모델 입력에서 사라진 것이다.

추가로 `search_files`는 `matchFileNames=true`가 없으면 `\.cpp$`, `\.h$`, `\.Build\.cs$`도 파일명이 아니라 파일 내용에만 적용했다. GUI에서 반복된 빈 검색은 구조화 결과 유실과 이 검색 의미 불일치가 결합된 결과다. 당시 `unreal-agent`는 `ALLOW_WRITE=0`, `ALLOW_UNREAL_BUILD=0`이어서 탐색을 통과해도 구현 및 검증은 별도로 차단될 설정이었다.

## 결론

- 모델 로드와 MCP 연결 자체는 성공.
- 모델은 계획 수립과 초기 도구 선택까지 정상.
- 구조화된 MCP 결과가 모델에 노출되지 않아 프로젝트 탐색이 막혔고, 기능 구현·자동 테스트·빌드에는 도달하지 못함.
- 다음 GUI 재시험 전 `structuredContent`의 actionable projection을 tool result text에도 안전하게 포함하거나, LM Studio가 구조화 결과를 모델 컨텍스트에 전달하도록 호환 계층을 수정해야 함.
- 첫 discovery의 실제 `Source/O_Mock` entries와 filename 검색 결과를 LM Studio model-facing text에도 보존해야 함.
- 기능 구현 재시험 전 신뢰한 O-Mock 프로젝트에 한해 쓰기·UBT 권한을 명시적으로 활성화해야 함.
- 이번 실행으로 프로젝트 파일 변경은 발생하지 않음.
