# LM Studio Unreal Agent MCP

UE 프로젝트를 읽고, 안전하게 수정하고, 빌드·진단하는 로컬 MCP 서버입니다. 기본 진입점은 `src/direct-server.js`이며 선택된 LM Studio 모델이 추론, 도구 순서, 중단, 최종 답변을 직접 결정합니다. 서버는 작업 계획, route, synthesis readiness, 다음 도구를 소유하지 않습니다.

이 디렉터리는 루트 통합 패키지 안에서 사용합니다. 프로젝트 선택은 저장소의 작은 `scripts/project_controller.py` 상태 기록기를 호출하므로 하위 디렉터리만 `npm pack`한 결과는 지원하지 않습니다. 이것은 workflow controller가 아니며 공유 active-project 값의 원자적 기록만 담당합니다.

## 실행 모드

- Direct(기본): `npm start` 또는 `node src/direct-server.js`
- Strict(명시적 선택): `npm run start:strict` 또는 `node src/strict-server.js`

Strict는 Direct capability 위에 대화별 소형 lifecycle만 추가합니다. 읽기·검색은 세션 없이 가능하고, 변경·빌드처럼 상태를 바꾸거나 오래 걸리는 호출만 해당 대화의 살아 있는 Strict 세션을 요구합니다. 다른 대화나 프로젝트를 전역으로 잠그지 않습니다.

## Direct 도구

프로젝트·환경:

- `get_workspace_info`
- `list_unreal_projects`
- `get_active_project`
- `set_active_project`
- `detect_unreal_project`

읽기·검색:

- `list_directory`
- `search_files`
- `read_file`
- `read_file_range`
- `read_symbol`
- `read_unreal_logs`

변경:

- `write_file`
- `replace_in_file`
- `apply_edit_bundle`
- `propose_file_deletions`
- `delete_file`

진단·실행:

- `static_validate_project` — 빌드를 허가하거나 막지 않는 선택적 진단
- `build_unreal_project` — plan/task/static-validation 선행 조건 없이 즉시 UBT/UHT 실행
- `run_unreal_automation_tests`
- `run_command` — 좁은 진단 allowlist만 허용

## 프로젝트와 경로 소유권

active project의 유일한 저장소는 `SHARED_UNREAL_CONFIG`가 가리키는 공유 JSON이며 기본 경로는 다음과 같습니다.

```text
%USERPROFILE%\.lmstudio\config\unreal-workspace.json
```

`set_active_project`로 정확한 `.uproject` 경로나 정확한 프로젝트 이름을 선택하고 `clear: true`로 해제합니다. 로컬 `config/agent-mcp.json`에 `activeProject`를 직접 기록하지 마십시오. 그 파일은 검색 루트와 로컬 엔진 매핑 같은 비소유 설정에만 사용됩니다.

각 호출의 `project` 인자로 정확한 `.uproject` 경로나 정확한 발견 이름을 넘기면 active project를 바꾸지 않고 해당 호출에만 적용됩니다. 따라서 여러 Unreal 버전과 여러 프로젝트를 한 서버에서 다룰 수 있습니다. 엔진은 각 `.uproject`의 `EngineAssociation`과 명시적 매핑을 기준으로 독립 해석됩니다.

경로 scheme은 containment 경계를 명시합니다.

- `workspace://...`는 `WORKSPACE_ROOT` 아래만 접근합니다.
- `project://...`는 그 호출에서 선택한 Unreal 프로젝트 아래만 접근합니다. 프로젝트가 `WORKSPACE_ROOT` 밖에 있어도 선택된 프로젝트 경계 안에서는 읽을 수 있습니다.
- 쓰기와 삭제는 선택된 프로젝트 경계 및 별도 쓰기 guard를 모두 통과해야 합니다.

## 안전장치

- 쓰기: `ALLOW_WRITE=1` 필요
- 새 파일: create-only; 기존 파일 덮어쓰기 금지
- 기존 파일 변경: 사전 읽기에서 받은 SHA-256 CAS, exact occurrence, path lock, atomic replace 필요
- 여러 파일 변경: transaction journal과 rollback을 갖춘 atomic bundle
- 삭제: `ALLOW_SOURCE_DELETE=1`, Source 경로, 해시, 만료되는 승인 토큰, `userApproved=true`가 모두 필요하며 recoverable trash로 이동
- 빌드·Automation: `ALLOW_UNREAL_BUILD=1` 필요
- 명령: `ALLOW_COMMANDS=1` 및 진단 allowlist 필요
- 읽기·검색·로그·프로세스 출력: byte/line/result/time 경계 적용
- 반복된 동일 실패: 새 정보가 없음을 작은 비재시도 오류로 반환. 성공한 읽기는 새 채팅을 방해하지 않도록 항상 본문을 반환하며, 같은 대화가 앞선 `repeatReceipt`를 명시적으로 되돌려 준 경우에만 축약

## 설치

루트 저장소에서 통합 설치기를 실행합니다.

```powershell
cd $HOME\.lmstudio\UE5_Local_LLM_MCP_lmstudio
.\INSTALL.bat
```

설치 후 LM Studio를 재시작하거나 MCP 목록을 새로고침하십시오. 템플릿은 `config/lmstudio-mcp-unreal-agent.json.template`, 검색·엔진 설정 템플릿은 `config/agent-mcp.json.template`입니다.

기본 MCP 등록은 `src/direct-server.js`를 가리킵니다. Strict를 의도적으로 쓸 때만 별도 MCP 항목의 진입점을 `src/strict-server.js`로 지정하십시오. 환경변수로 Direct와 Strict를 암묵적으로 전환하지 않습니다.

## 일반적인 사용

모델은 고정된 workflow 없이 필요한 capability를 직접 고릅니다. 예를 들어 정확한 프로젝트를 지정해 파일을 읽고 해시 기반 패치를 적용한 뒤 바로 빌드할 수 있으며, static validation은 필요할 때만 별도로 호출합니다. 실패 응답은 사실, 제한된 재시도 정보, 선택적 단일 제안만 제공하고 다음 호출을 강제하지 않습니다.

신뢰하지 않는 프롬프트에서는 쓰기, 명령, 빌드 환경변수를 활성화하지 마십시오.
