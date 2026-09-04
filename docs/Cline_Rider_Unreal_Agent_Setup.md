# Rider·Cline 연결 설정

Rider는 코드 탐색·빌드·디버깅에 쓰고, Cline은 AI가 프로젝트 도구를 호출하는 데 씁니다. 기본 `Direct` 방식에서는 모델이 필요한 도구와 작업 순서를 정합니다.

## 프로젝트와 MCP 연결 절차

1. Rider에서 대상 `.uproject`를 열고 맞는 엔진으로 연결됐는지 확인합니다.
2. Cline의 MCP 설정에 `unreal-rag`, `unreal-agent`를 등록합니다.
3. Cline에서 사용할 모델을 선택합니다. LM Studio를 모델 제공자로 쓰는 경우에만 해당 서버 주소를 설정합니다.
4. 설정 변경 후 Cline을 재시작하고 도구 목록을 확인합니다.

[설정 템플릿](../config/cline_mcp_settings.template.json)을 참고하면 됩니다. 설치기로 설정을 넣을 수도 있습니다.

```powershell
python install.py --profile custom --components codex,lmstudio,unreal,cline --cline-settings C:\path\to\cline_mcp_settings.json
.\rag.ps1 doctor
```

설정 파일 경로는 실제 경로로 바꿔야 합니다. 일반적인 위치는 다음과 같습니다.

- VS Code 확장: `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`
- Cline CLI: `%USERPROFILE%\.cline\data\settings\cline_mcp_settings.json`

작업 규칙은 [`.clinerules`](../.clinerules), 시스템 지시문은 [Cline용 지시문](../prompts/cline_unreal_agent_system.md)을 사용합니다. 설치 후에는 [연결 점검](Rider_Cline_Smoke_Checklist.md)을 보면 됩니다.

## 프로젝트 선택·파일 수정·빌드 규칙

같은 이름의 프로젝트가 있다면 `.uproject` 전체 경로를 지정해야 합니다. 기본 프로젝트를 바꾸고 싶을 때만 `unreal_set_active_project`를 사용합니다. 엔진은 프로젝트 연결 정보로 선택하며 특정 버전으로 고정하지 않습니다.

기존 파일을 읽고 `fileVersionReceipt`를 받은 뒤 `replace_in_file`로 필요한 부분을 수정합니다. 매번 확인값을 직접 전달하며 자동 선택은 하지 않습니다. 수정 성공 후 받은 새 값을 다음 수정에 씁니다. 충돌이나 `FILE_SNAPSHOT_*` 오류가 나면 다시 읽어야 합니다. 새 파일은 `write_file`로만 생성합니다.

`static_validate_project`는 보조 검사입니다. 빌드 권한이 켜져 있으면 이를 먼저 실행하지 않고도 `build_unreal_project`를 호출할 수 있습니다. 직접 빌드·디버깅하려면 Rider를 사용하면 됩니다.

`target=Editor`는 선택한 프로젝트의 에디터 빌드 대상으로 해석합니다. 빌드·자동화 테스트는 시간과 출력량이 제한되며 `fullLogPath`도 앞뒤 일부만 담을 수 있습니다. 결과의 생략 여부를 확인해야 합니다.

원문이 현재 대화에 있을 때만 `repeatReceipt`를 되돌려 줍니다. 이 값을 빼면 같은 성공 호출도 원문을 받습니다.

언리얼 코드에서는 네임스페이스를 웬만하면 추가하지 말아야 합니다. 프로젝트 파일 작업에 일반 샌드박스나 별도 파일 접근을 섞지 말고 MCP 도구를 사용해야 합니다.

## 연결 및 실행 오류 해결

- 도구 목록이 비었으면 설치 설정을 다시 적용하고 Cline의 MCP 오류 출력을 확인합니다.
- 프로젝트가 틀리면 정확한 경로를 호출에 전달합니다.
- 수정이 막히면 [권한 설정](Safe_Agent_Mode.md)과 파일 충돌 여부를 확인합니다.
- 빌드가 꺼져 있으면 Rider에서 실행하거나 설치 시 빌드 권한을 켭니다.

LM Studio 채팅을 직접 쓴다면 [LM Studio 설정](LMStudio_Unreal_Agent_Setup.md)을 참고해야 합니다. 그 채팅의 대화 압축기는 기본 `OFF`이며 Cline의 대화를 자동으로 압축해 주는 기능은 아닙니다.
