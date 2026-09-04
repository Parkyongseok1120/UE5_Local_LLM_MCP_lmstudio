# LM Studio 언리얼 프로젝트 연결 설정

먼저 [설치 안내](Integrated_Installer.md)대로 설치한 뒤 LM Studio를 재시작해야 합니다. 압축 배포본이라면 압축을 푼 폴더를 그대로 두어야 합니다.

## 연결 확인

설치 폴더에서 실행합니다.

```powershell
.\rag.ps1 doctor
```

검색 자료와 선택한 프로젝트가 맞는지 확인하면 됩니다. LM Studio의 `~/.lmstudio/mcp.json`에는 다음 두 서버가 있어야 합니다.

- `unreal-rag`: 검색 자료에서 코드·문서를 찾고 갱신합니다. 실행 파일은 `scripts/unreal_rag_direct.py`.
- `unreal-agent`: 프로젝트 파일을 읽고, 허용된 수정·빌드 작업을 합니다. 실행 파일은 `src/direct-server.js`.

설치 위치를 바꿨거나 연결이 깨졌다면 현재 폴더에서 같은 설치기를 다시 실행해야 합니다.

```powershell
python install.py --profile standard --yes
.\rag.ps1 doctor
```

이 예시는 읽기 전용입니다. 원래 수정·빌드까지 허용했다면 같은 권한 옵션을 함께 지정해야 합니다. 재설치 후 LM Studio를 재시작해야 합니다. 배포본 사용법은 여기에 적힌 명령으로 충분하며, 추가 검증 도구 일부는 개발 저장소에만 있습니다.

## 모델과 채팅 설정

1. 사용할 AI 모델을 불러오고 모델 선택 목록에서 직접 고릅니다.
2. 채팅에서 `unreal-rag`, `unreal-agent`를 켭니다.
3. 시스템 지시문에는 [lmstudio_direct_model_system.md](../prompts/lmstudio_direct_model_system.md)를 사용합니다.
4. `codex/unreal-context-compactor`는 기본적으로 꺼두어야 합니다(`OFF`). 기존 채팅에 켜져 있으면 직접 끕니다.
5. 언리얼 파일 작업에 `js-code-sandbox`를 사용하지 않도록 해당 플러그인을 끕니다.

현재 기본 방식은 `Direct`입니다. AI가 필요한 도구와 순서, 끝낼 시점과 최종 답변을 정합니다. 서버가 정해 준 계획을 먼저 시작할 필요는 없습니다.

압축기는 긴 대화를 줄여 주는 선택 기능입니다. 설치는 사용 가능한 파일을 준비할 뿐 채팅에서 활성화하지 않습니다. 필요할 때 해당 채팅의 단일 스위치만 켜면 됩니다. `Observe only`는 대화를 바꾸지 않고 사용량만 측정합니다.

```powershell
npm --prefix lmstudio-context-compactor-plugin run status
```

이 명령은 설치 파일과 빌드 연결 상태를 확인합니다. 채팅에서 실제로 켜졌는지 증명하는 명령은 아닙니다.

## 프로젝트 조회 요청 예시

사용할 `.uproject` 전체 경로와 원하는 결과를 함께 적으면 됩니다. 예를 들어 “이 프로젝트의 이동 컴포넌트를 읽고 중복된 입력 처리가 있는지 확인해줘”처럼 범위를 정하면 됩니다.

필요하면 `unreal_get_active_project`로 기본 프로젝트를 확인하고, `unreal_rag_health`로 검색 상태, `get_workspace_info`로 파일 접근 범위와 권한을 확인합니다. 이 도구를 매번 정해진 순서로 호출해야 하는 건 아닙니다.

같은 이름의 프로젝트가 여러 곳에 있으면 전체 경로를 써야 합니다. `search_files`가 준 `project://` 형식의 `uri`는 응답의 정확한 `activeProject`와 함께 다음 호출의 `project`로 전달해야 합니다.

## 파일 수정과 검증

기존 파일은 `replace_in_file`로 필요한 부분만 고칩니다. `write_file`은 새 파일에만 사용합니다. 기존 파일을 바꾸기 전에 읽고, 반환된 `fileVersionReceipt`를 수정 호출에 직접 전달해야 합니다. 수정 성공 후에는 새로 받은 값을 다음 수정에 씁니다. `expectedHash`도 호환 입력으로 사용할 수 있습니다.

서버가 같은 대화의 이전 읽기 결과를 자동으로 선택하지 않습니다. `FILE_VERSION_CONFLICT`나 `FILE_SNAPSHOT_*`가 나오면 정확한 프로젝트의 파일을 다시 읽고 수정안을 맞춰야 합니다.

`static_validate_project`는 의심되는 코드를 알려 주는 보조 검사입니다. 이를 통과해야 빌드할 수 있는 구조는 아닙니다. 빌드 권한이 켜져 있으면 `build_unreal_project`를 바로 실행할 수 있습니다. 실제 빌드·테스트를 안 했다면 그 한계를 답변에 적어야 합니다.

큰 로그는 앞뒤 일부만 남을 수 있습니다. `fullLogPath`라는 이름만 보고 전체 원본이라고 판단하지 말고 생략 여부를 확인해야 합니다.

## 프로젝트 검색 오류 해결

내 코드가 필요한데 엔진 설명만 나오면 정확한 프로젝트와 `scope=project`를 함께 지정합니다. 검색에서 못 찾았다는 이유만으로 코드가 없다고 결론내리지 말고 파일 검색과 직접 읽기로 확인해야 합니다.

응답을 줄일 때 쓰는 `repeatReceipt`는 이 채팅에 원래 결과가 남아 있을 때만 전달합니다. 원문이 필요하면 이 값을 빼면 됩니다. 결과가 잘렸다면 반환된 `nextDetailLevel`로 범위를 조정할 수 있습니다.

설정값 설명은 [모델 설정](Model_Profiles.md), 상세 오류는 [문제 해결](Troubleshooting.md), 수정 한도는 [도구 규칙](LMStudio_MCP_Tool_Discipline.md)에 있습니다.

## Strict 세션 관리 설정

`Strict`를 의도적으로 쓸 때만 `unreal-agent`를 별도 이름인 `unreal-agent-strict`로 복사하고 `lmstudio-unreal-agent-mcp/src/strict-server.js`를 지정합니다. 기본 항목 자체를 바꾸지 말아야 합니다.

`strict_begin`으로 시작하고 답변 직전에 `strict_complete`로 종료합니다. 실패와 취소에는 `strict_fail`, `strict_cancel`을 사용합니다. 연결이 끊긴 세션은 `orphaned`가 되며 다른 대화나 기본 도구를 막지 않습니다. 이를 이어가려면 사용자 승인 후 `strict_resume`을 사용합니다.
