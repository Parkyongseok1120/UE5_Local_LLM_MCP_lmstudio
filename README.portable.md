# 언리얼 AI 도구 압축 배포본

내 컴퓨터의 AI가 언리얼 프로젝트를 검색하고, 코드를 읽고, 수정과 빌드를 할 수 있게 연결하는 도구입니다. 엔진 소스와 완성된 검색 자료는 포함하지 않습니다. 사용할 프로젝트에 맞춰 직접 만들어야 합니다.

## 배포본 설치 방법

- Windows에서는 `INSTALL.bat`를 실행합니다.
- Ubuntu Linux와 Apple Silicon macOS에서는 `./install.sh`를 실행합니다.
- Python이 없으면 실행에 필요한 버전을 사용자 폴더에 설치합니다. 시스템 PATH는 바꾸지 않습니다.
- 압축을 푼 폴더는 설치 후에도 그대로 두어야 합니다. MCP가 이 폴더의 프로그램을 실행합니다.
- 첫 설치에서는 `--skip-deps`를 사용하지 말아야 합니다. 필요한 패키지는 설치 과정에서 받습니다.

기본 권한은 읽기 전용입니다. 파일 수정과 빌드가 필요하면 설치 화면에서 `AGENT`를 따로 선택하면 됩니다. [설치 안내](docs/Integrated_Installer.md)를 참고해야 합니다.

## LM Studio 연결 및 사용 설정

실제 AI 모델을 선택한 뒤 `unreal-rag`와 `unreal-agent`를 켜면 됩니다. 기본 방식인 `Direct`에서는 AI가 도구 사용 순서와 답변할 시점을 정합니다.

`codex/unreal-context-compactor`는 긴 대화를 줄여 주는 보조 기능입니다. 기본적으로 꺼두어야 합니다(`OFF`). 설치만으로 채팅에서 켜지지는 않습니다. 필요한 긴 채팅에서 단일 스위치만 켜면 됩니다.

기존 파일은 `replace_in_file`로 필요한 부분만 고칩니다. 읽기나 직전 수정 결과의 `fileVersionReceipt`를 매번 전달해야 합니다. 호환 입력인 `expectedHash`도 가능합니다. `write_file`은 새 파일에만 사용합니다.

[LM Studio 설정](docs/LMStudio_Unreal_Agent_Setup.md), [Rider·Cline 연결](docs/Cline_Rider_Unreal_Agent_Setup.md), [도구 규칙](docs/LMStudio_MCP_Tool_Discipline.md)에 자세한 설명이 있습니다.

## 검색 자료 갱신

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 refresh -RefreshScope project_source
.\rag.ps1 doctor
```

프로젝트 경로를 실제 경로로 바꿔야 합니다. 같은 이름의 복사본도 전체 경로로 구분합니다. 다른 엔진 버전의 검색 자료는 따로 보관합니다.

기본 갱신은 에디터를 실행하지 않습니다. 에디터에서 내보낸 자료를 읽으려면 `-RefreshScope editor_metadata`를 사용합니다. 에디터 실행까지 의도한 경우에만 `-AllowEditorLaunch`를 추가해야 합니다.

[검색 자료 관리](docs/RAG_Setup.md), [문제 해결](docs/Troubleshooting.md), [권한 설정](docs/Safe_Agent_Mode.md), [보안 제보](SECURITY.md)를 참고하면 됩니다.
