# 언리얼 프로젝트 파일·빌드 서버

AI가 언리얼 프로젝트 파일을 읽고, 허용된 부분을 고치고, 빌드와 테스트를 실행하는 로컬 MCP 서버입니다. 기본 진입점은 `src/direct-server.js`입니다.

이 폴더만 따로 배포하지 말고 저장소 전체 또는 통합 배포본 안에서 사용해야 합니다. 기본 프로젝트 기록은 상위의 `scripts/project_controller.py`를 사용합니다.

## 실행 방식

- 기본 `Direct`: `npm start` 또는 `node src/direct-server.js`
- 별도 세션 관리 `Strict`: `npm run start:strict` 또는 `node src/strict-server.js`

Direct에서는 모델이 필요한 도구와 순서를 정합니다. Strict는 여기에 대화별 시작·종료를 추가합니다. 읽기·검색은 세션 없이 가능하며 변경·빌드 같은 호출에는 살아 있는 세션이 필요합니다. 다른 대화를 전역으로 잠그지는 않습니다.

## 제공 도구 목록

| 용도 | 이름 |
|---|---|
| 프로젝트·환경 확인 | `get_workspace_info`, `list_unreal_projects`, `get_active_project`, `set_active_project`, `detect_unreal_project` |
| 읽기·검색 | `list_directory`, `search_files`, `read_file`, `read_file_range`, `read_symbol`, `read_unreal_logs` |
| 수정·생성 | `replace_in_file`, `apply_edit_bundle`, `write_file` |
| 승인된 삭제 | `propose_file_deletions`, `delete_file` |
| 진단·실행 | `static_validate_project`, `build_unreal_project`, `run_unreal_automation_tests`, `run_command` |

## 프로젝트 선택

공유 기본값은 `SHARED_UNREAL_CONFIG`가 가리키는 JSON에 저장합니다. 기본 위치는 `%USERPROFILE%\.lmstudio\config\unreal-workspace.json`입니다. 로컬 `config/agent-mcp.json`에 활성 프로젝트를 따로 기록하지 말아야 합니다.

`set_active_project`로 정확한 `.uproject` 경로나 발견된 프로젝트 이름을 선택하고 `clear: true`로 해제합니다. 호출의 `project`를 직접 지정하면 공유 기본값을 바꾸지 않고 그 호출에만 적용됩니다.

`workspace://`는 작업 폴더 아래, `project://`는 선택한 프로젝트 아래를 뜻합니다. 이름이 같은 복사본은 전체 경로로 구분합니다. 엔진은 `.uproject`의 `EngineAssociation`과 설정을 기준으로 찾습니다.

## 수정 규칙

`ALLOW_WRITE=1`이 있어야 수정할 수 있습니다. 기존 파일은 읽기 결과의 `fileVersionReceipt` 또는 유효한 `expectedHash`를 매번 직접 보내야 합니다. 자동 선택은 하지 않습니다. 수정 성공 후 새로 받은 값으로 다음 수정이 가능합니다.

`replace_in_file`은 한 구간만 고칩니다. `expectedOccurrences=1`, 기존 글 1,200자, 새 글 2,800자·32줄, 합계 4,000자가 한도입니다. `apply_edit_bundle`은 서로 다른 기존 파일 1~2개에서 한 구간씩, 합계 64줄까지만 허용합니다. 새 파일은 만들지 않습니다.

새 파일은 단독 `write_file`로 생성합니다. 기존 파일 덮어쓰기는 금지하며 12,000자·160줄 한도가 있습니다. 충돌이나 `FILE_SNAPSHOT_*` 오류가 나오면 현재 파일을 다시 읽어야 합니다.

삭제는 `ALLOW_SOURCE_DELETE=1`, 허용된 Source 경로, 제안 토큰, 파일 확인값, `userApproved=true`와 실제 사용자 승인이 필요합니다. 복구 가능한 보관 위치로 옮깁니다.

## 빌드와 결과

`ALLOW_UNREAL_BUILD=1`이면 빌드·자동화 테스트를 실행할 수 있습니다. `target=Editor`는 선택한 프로젝트의 기본·설정된 우선·유일하게 발견된 사용자 지정 에디터 대상을 찾습니다. 별도 대상을 명시했다면 그대로 사용합니다.

빌드와 테스트는 같은 실행 관리 코드를 사용합니다. 시간이 초과되면 자식 프로세스까지 종료하고 출력은 앞뒤 일부만 남길 수 있습니다. `fullLogPath`와 함께 생략 정보를 확인해야 합니다.

`static_validate_project`는 참고용 코드 검사이며 빌드를 허가하거나 막지 않습니다. `run_command`는 `ALLOW_COMMANDS=1`일 때 허용 목록의 진단 명령만 실행합니다.

성공한 읽기는 원문을 반환합니다. 같은 대화가 앞선 `repeatReceipt`를 직접 보낸 경우에만 줄여서 반환합니다. 반복 실패는 줄여도 실패로 표시합니다.

설치는 저장소 맨 위의 통합 설치기를 사용하고 LM Studio를 재시작해야 합니다. 자세한 조건은 [도구 사용 규칙](../docs/LMStudio_MCP_Tool_Discipline.md)에 있습니다.
