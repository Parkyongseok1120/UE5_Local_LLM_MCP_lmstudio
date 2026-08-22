# Evidence-First Unreal MCP — portable Direct runtime

이 패키지는 LM Studio, Cline 및 기타 MCP client에서 사용할 작은 기본 RAG
서버와 별도 Unreal 프로젝트 capability 서버를 제공합니다. Epic/Unreal
source나 사전 빌드된 인덱스는 포함하지 않으므로 자신의 프로젝트에 맞춰
인덱스를 빌드하거나 지정해야 합니다.

## 설치

- Windows: `INSTALL.bat`
- Ubuntu Linux 또는 Apple Silicon macOS: `./install.sh`
- 설치된 MCP 설정이 이 runtime tree의 파일을 직접 실행하므로 압축을 푼
  디렉터리를 안정적인 위치에 유지하세요.

installer 기본 권한은 read-only입니다. 신뢰하는 프로젝트에서만 별도 위험
확인과 함께 AGENT 권한을 켜세요.

[통합 installer 문서](docs/Integrated_Installer.md)와
[LM Studio 설정 문서](docs/LMStudio_Unreal_Agent_Setup.md)를 참고하세요.

## 지원 MCP surface

installer가 관리하는 `unreal-rag`는 `scripts/unreal_rag_direct.py`를
실행합니다. 8개 task-free 도구만 제공하며 active project 선택, 사실 기반
RAG 검색/symbol 조회, health/status, 동기 refresh, capability 확인을 담당합니다.

installer가 관리하는 `unreal-agent`는
`lmstudio-unreal-agent-mcp/src/direct-server.js`를 실행합니다. 설정된
SAFE/AGENT 권한 안에서 프로젝트, 읽기, 로그, 변경, 정적 진단, 빌드,
Automation, command capability를 제공합니다.

지원되는 Strict 구현은 별도 구성하는 Node `strict-server.js` 하나뿐입니다.
제거된 Python task/route/planner controller는 배송되지 않으며
`MCP_EXECUTION_MODE`로 어느 Direct entry도 전환할 수 없습니다.

[Direct 도구 규칙](docs/LMStudio_MCP_Tool_Discipline.md),
[SAFE/AGENT 권한](docs/Safe_Agent_Mode.md),
[문제 해결](docs/Troubleshooting.md)을 참고하세요.

## Portable RAG 유지보수

패키지의 `rag.ps1`는 10개 수집/인덱스/프로젝트/refresh/status 명령만 가진
런처이며 모델이나 workflow controller가 아닙니다.

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 collect-projects -Root C:\Projects\MyGame
.\rag.ps1 collect-symbols -Root C:\Projects\MyGame\Source -SymbolScope project -ProjectName MyGame
.\rag.ps1 collect-module-graph
.\rag.ps1 build
.\rag.ps1 doctor
```

`refresh` 기본 scope는 project source이며 Unreal Editor를 시작하지 않습니다.
기존 Editor export는 `-RefreshScope editor_metadata`로 ingest할 수 있고,
Editor 실행은 명시적 `-AllowEditorLaunch`가 추가로 있어야만 허용됩니다.

[RAG 유지보수](docs/RAG_Setup.md)를 참고하세요. Rider/Cline 사용자는
[Direct Cline 설정](docs/Cline_Rider_Unreal_Agent_Setup.md)과 배송되는
[Direct Cline system prompt](prompts/cline_unreal_agent_system.md)를 사용할 수
있습니다.

보안 문제는 [SECURITY.md](SECURITY.md)에 따라 제보하세요.
