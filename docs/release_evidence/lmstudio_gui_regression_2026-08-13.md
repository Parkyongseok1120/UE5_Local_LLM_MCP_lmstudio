# LM Studio GUI 실사용 회귀 검증 — 2026-08-13

## 범위와 환경

- LM Studio GUI에서만 실행했다. 로컬 API E2E는 사용자 지시에 따라 제외했다.
- 모델: `qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max`
- 컨텍스트: 62,464 tokens
- generator: `codex/unreal-context-compactor` 0.4.27 / revision 73
- MCP: `mcp/unreal-agent`, `mcp/unreal-rag`, `mcp/evidence-first`
- 대상: `C:\Users\sster\Documents\Git\O-Mock\O_Mock.uproject`
- 유효 대화: `C:\Users\sster\.lmstudio\conversations\84\1786605994124.conversation.json`
- compactor session: `1e0274072b81b38eed0d5c9a2f54269b`
- task session: `7599621d767b40a7`

## 최초 유효 실행

실제 tool call은 `unreal_get_active_project` 1회로 시작했고 이어서 `unreal_agent_plan`으로
서버 소유 task route를 만들었다. 따라서 fresh write task에서 active project를 먼저 확인하는
현재 순서는 맞으며, 매 턴 반복하는 절차는 아니다.

정적 검증은 blocking error 0, warning 50으로 통과했다. Unreal build는 exit code 6으로 실패했고
확인된 첫 원인은 `AGomokuGameMode::SetPlayerReady`, `AreAllPlayersReady`, `TryStartMatch`의
LNK2019 missing definition이었다. 모델이 근거 없는 readiness map을 제안하자
`unreal_code_sketch_claim_validate`가 `LINKER_RECOVERY_SEMANTIC_INVENTION`과
`stopCurrentWorkflow=true`로 차단했다.

O-Mock에는 쓰기가 적용되지 않았다. 첫 `apply_edit_bundle`은 workspace-prefix 경로 정규화
불일치로 mutation 전에 거절됐고, 이 경계는 read/write가 같은 project-relative 경로 규칙을
사용하도록 수정했다.

## GUI에서 추가로 발견하고 수정한 결함

1. `아까 작업 계속해.`를 새 목표로 오분류해 semantic blocker를 지웠다.
   - `아까/이전/전에/기존/그 작업을 계속·재개·이어서 진행` 문법을 bounded continuation으로 추가했다.
   - 추가 조건이나 취소 지시가 붙은 문장은 continuation으로 잡지 않는다.
2. 보존된 workflow stop 뒤의 일반 `unreal_task_status.nextActionIsTool=true`가 stop보다 우선했다.
   - clear tool 없는 workflow stop이 후속 generic required tool을 제거하고 tool exposure보다 우선한다.
3. tool schema를 숨겨도 Qwen이 `<tool_call>read_file...</tool_call>`을 일반 텍스트로 출력했다.
   - 서버가 확정한 workflow stop에서는 target model을 호출하지 않고 generator가 blocker 최종문을
     결정적으로 즉시 반환한다.
4. detached read-only side query에서 schema에 없는 `taskAuthorization`, `taskObservation`,
   `sessionId`를 주입해 LM Studio argument validation 재시도 루프가 발생했다.
   - 각 필드는 해당 tool의 실제 advertised schema에 선언됐을 때만 주입한다.

초기 자동화에서 PowerShell → Python 파이프로 전달한 한국어가 `?? ?? ???.`로 변환된 한 실행은
GUI 제품 결함 판정에서 제외했다. 이후 모든 입력은 Unicode escape로 전달하고 conversation JSON에
원문이 저장된 것을 확인했다. revision 73에서는 이런 question-mark placeholder가 기존 목표를
대체하지 못하며, legacy placeholder objective가 있으면 전체 이력을 한 번 재스캔한다.

## revision 73 최종 GUI 결과

### Semantic stop 재개

- 입력: `아까 작업 계속해.`
- `workflowStopActive=true`
- `workflow_stop_final_emitted`
- `targetModelInvoked=false`
- 실제 tool call 증가: 0 (`33 → 33`)
- 5초 이내 한국어 blocker 최종문 반환
- `<tool_call>` 텍스트 및 read/status 재시도 없음

### Detached read-only 질문

- 입력: `지금 프로젝트 구조만 알려줘.`
- `detachedSideQueryActive=true`
- mutation/task-control tool call 증가: 0
- 보존된 직접 근거로 구조 요약 후 `eosFound`
- task state hash 전후 동일:
  `E7D05B75C7D9DE9A0CC9FCFF865193D222C55E9B12724BA39A82AC23DBA4B902`
- active slice: `gomoku_game_mode_linker_fix`
- plan revision: `2`

### Side query 이후 원 작업 복귀

- 입력: `아까 작업 계속해.`
- `detachedSideQueryActive=false`
- checkpoint generation 55에서 원래 한국어 objective, semantic blocker와 stop 상태 유지
- required next tool 없음
- 실제 tool call 증가: 0 (`33 → 33`)
- deterministic blocker 최종문 반환

### 대상 소스 무변경 증거

- `GomokuGameMode.h` SHA-256:
  `471367D7875AC389E0B9A22C33C16D81AEBC83F60937A64C28A5D05E755ABF97`
- `GomokuGameMode.cpp` SHA-256:
  `CB0F5332F45092CFE91CF794A99601428EF2D6D5CCBA63FB621280B8E0982892`

## 자동 회귀와 추가 감사

- Python: 1,798 passed, 8 skipped
- Unreal Agent MCP: 191 passed, 1 skipped
- Context compactor: 130 passed
- `git diff --check`: 오류 없음
- source/install 일치: compactor 0.4.27, revision 73
- UTF-8 strict decode: tracked text 794개, decode error 0
- replacement character가 있는 2개 파일은 mojibake 방지 테스트가 해당 문자를 의도적으로
  matcher/fixture로 선언한 테스트 파일이다. production source 손상은 없다.

세 번의 추가 감사 결과:

1. 계약 감사: 공개 schema에 없는 detached 필드가 주입되지 않고, 선언된 schema에는 서버 필드가
   정상 주입되는 양방향 테스트를 통과했다.
2. 상태 감사: semantic blocker, active slice, plan revision, task ownership이 compaction과 side query
   전후 동일하며 generic status control이 workflow stop을 덮지 못한다.
3. 플랫폼/인코딩 감사: 전체 회귀에서 UE 5.4 mismatch, semantic EngineAssociation,
   Windows Build.bat/cmd, macOS·Linux Build.sh, UTF-8 및 한국어 Windows build 출력 경로를 통과했다.
