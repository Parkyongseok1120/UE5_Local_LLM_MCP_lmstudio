# Evidence-First Unreal MCP — portable Direct runtime

이 패키지는 LM Studio, Cline 및 기타 MCP client에서 사용할 작은 기본 RAG
서버와 별도 Unreal 프로젝트 capability 서버를 제공합니다. Epic/Unreal
source나 사전 빌드된 인덱스는 포함하지 않으므로 자신의 프로젝트에 맞춰
인덱스를 빌드하거나 지정해야 합니다.

## 설치

- Windows: `INSTALL.bat`
- Ubuntu Linux 또는 Apple Silicon macOS: `./install.sh`
- 지원되는 새 host에는 Python이 미리 없어도 됩니다. launcher가 pin된 uv
  seed의 SHA-256을 검증하고 사용자 state-home에 managed Python 3.12를
  설치하며 system-wide Python이나 PATH는 바꾸지 않습니다.
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

읽기와 변경은 기존 파일 CAS를 위한 scope 제한 `fileVersionReceipt`를
반환합니다. 다음 편집에는 receipt를 우선 사용하고 유효한 raw `expectedHash`도
호환되며, 신뢰 가능한 동일 session에서는 최신 snapshot을 자동 해석할 수
있습니다. Build와 Automation은 하나의 bounded process runner를 공유합니다.
`target=Editor`는 선택 프로젝트의 canonical, 설정된 preferred Editor, 또는
유일하게 발견된 custom `*Editor` target으로 해석되고 명시한 non-Editor target은
바꾸지 않습니다.

정확한 프로젝트 선택으로 여러 프로젝트와 Unreal 버전을 다룰 수 있으며 RAG는
호환되는 엔진별 sibling shard로 이동합니다. 한 호출에서 서로 다른 엔진이 소유한
프로젝트 shard를 합치지 않습니다. 선택적 context compactor는 제한된
objective/work/file/tool/build continuity 사실을 보존하지만 planner, router, 도구
권한 또는 완료 판단 주체가 되지 않습니다. 설치기는 LM Studio가 소유한 상단
채팅 플러그인 토글을 활성화하지 않으므로 채팅별로 OFF인지 확인합니다. 제한된
continuity가 필요한 긴 채팅에서만 그 단일 토글을 켭니다. handler 호출 자체가
활성화 경계입니다.

현재 검증된 주 추천 모델은 Qwen 3.8 27B입니다. Muse Glimmer는 시험 중이며 아직
검증된 추천이 아닙니다. Qwen 3.5, Qwen 3.6 27B, GPT-OSS 언급은 historical
compatibility/evaluation 자료이며 현재 추천이 아닙니다.

지원되는 Strict 구현은 별도 구성하는 Node `strict-server.js` 하나뿐입니다.
제거된 Python task/route/planner controller는 배송되지 않으며
`MCP_EXECUTION_MODE`로 어느 Direct entry도 전환할 수 없습니다.

[Direct 도구 규칙](docs/LMStudio_MCP_Tool_Discipline.md),
[SAFE/AGENT 권한](docs/Safe_Agent_Mode.md),
[문제 해결](docs/Troubleshooting.md)을 참고하세요.

## Portable RAG 유지보수

패키지의 `rag.ps1`는 범위가 제한된 수집/인덱스/프로젝트/refresh/status 유지보수
런처이며 모델이나 workflow controller가 아닙니다.

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 refresh -RefreshScope project_source
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
