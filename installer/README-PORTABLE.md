# Unreal58-RAG Portable MCP — 설치 안내

> **Note:** This document describes the **Portable ZIP** distribution. The GitHub OSS clone is BYOI (no pre-built index). See [`../README.md`](../README.md).

이 패키지는 **LM Studio MCP** (`unreal-rag`, `unreal-agent`, `current-datetime`)를  
다른 Windows PC에서도 동일하게 쓰기 위한 **이식형 번들**입니다.

## 빠른 설치

1. ZIP을 원하는 위치에 풀기 (예: `D:\Tools\Unreal58-RAG-Portable`)
2. **`INSTALL.bat`** 더블클릭 (또는 PowerShell에서 `.\Install-UnrealMcp.ps1`)
3. LM Studio 재시작 → MCP 서버 연결 확인

## 사전 요구

| 항목 | 설명 |
|------|------|
| **Python 3.10+** | [python.org](https://www.python.org/downloads/) 또는 PATH에 `python` |
| **Node.js 20+** | [nodejs.org](https://nodejs.org/) |
| **LM Studio** | Local Server + MCP 플러그인 |
| **Unreal Engine** | 빌드용 (선택, agent MCP) |

설치 스크립트가 `python` / `node.exe` 경로를 자동 탐지합니다.

## 설치 후 경로

- 번들 루트: `%UNREAL58_PORTABLE_ROOT%` (설치 시 설정)
- LM Studio 설정: `%USERPROFILE%\.lmstudio\mcp.json`
- 공유 워크스페이스: `%USERPROFILE%\.lmstudio\config\unreal-workspace.json`
- RAG 워크스페이스: `config\workspace.json`의 `rootPath` (설치 시 자동 기록)
- Editor export: `{ActiveProject}/Saved/LmStudioMetadataExports` (프로젝트별 자동)

사용자 이름, PC 이름, 고정 사용자 프로필 경로 같은 머신 전용 값은 저장소에 넣지 않습니다.  
`INSTALL-*-BUILD-RAG.bat` 또는 `installer\Sync-InstallMachinePaths.ps1`가 현재 PC 기준으로 경로를 다시 씁니다.

## BUILD-RAG 설치 (인덱스 + Editor export)

```bat
installer\INSTALL-SAFE-MODE-BUILD-RAG.bat
```

순서: MCP 설치 → 프로젝트 선택 → 엔진/경로 동기화 → 인덱싱(+자동 Editor export) → doctor

경로만 다시 맞추려면:

```powershell
.\installer\Sync-InstallMachinePaths.ps1
```

## 첫 실행 체크

```powershell
cd Unreal58-RAG
.\rag.ps1 pick-project
.\rag.ps1 query -Question "UActorComponent BeginPlay example"
```

LM Studio 채팅에서 `unreal_rag_health`, `unreal_get_active_project` 도구 호출.

## Cline + Rider (권장)

```powershell
.\installer\Install-ClineUnrealMcp.ps1
```

Rider = Unreal C++ 주력 IDE. Cline = MCP agent. 규칙: `.clinerules`  
가이드: `docs/Cline_Rider_Unreal_Agent_Setup.md`

## RAG 인덱스

번들에 **사전 빌드된 `rag.sqlite`** 가 포함되어 있습니다.  
다른 PC의 UE 소스/프로젝트를 반영하려면:

```powershell
.\rag.ps1 collect-source -SourceRoot "$env:UNREAL_ENGINE_ROOT\Engine\Source"
.\rag.ps1 collect-projects
.\rag.ps1 build-incremental
.\rag.ps1 build-embeddings-full
```

## 문제 해결

- MCP 빨간불 → `.\installer\Verify-UnrealMcp.ps1`
- `rag.sqlite` 잠김 → LM Studio/Cursor 종료 후 `.\rag.ps1 promote-index`
- Python 모듈 없음 → `pip install fastembed` (hybrid 검색용, 선택)

## 업데이트

새 ZIP으로 덮어쓴 뒤 `INSTALL.bat` 다시 실행하면 `mcp.json` 경로가 갱신됩니다.
# Portable Notes

This package is path-portable. Install scripts rewrite machine-specific paths from the current PC and should not require paths such as `%USERPROFILE%\...`.

Common commands after install:

```powershell
cd Unreal58-RAG
.\rag.ps1 pick-project
.\rag.ps1 install-editor-graph-plugin
.\rag.ps1 export-editor-metadata
.\rag.ps1 watch-active-project
```

`install-editor-graph-plugin` copies `tools\ue_plugins\LmStudioGraphExporter` into the active Unreal project's `Plugins` folder, enables it in the `.uproject`, hash-checks existing project plugin copies, updates stale copies, and runs UnrealBuildTool when the module needs compiling. Close Unreal Editor before installing. During the interactive build installer, answer `Y` to install/update it or `N` to skip it. Without the plugin, Blueprint export still works, but full node/pin links may be unavailable on UE 5.8.
