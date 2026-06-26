# Cline + Rider Unreal Agent Setup (UE 5.8)

**Primary IDE:** JetBrains Rider (Unreal plugin, UBT, debugger)  
**AI agent:** Cline (MCP tools, local LM Studio model)

Continue / Rider AI plugin는 사용하지 않습니다. Cline + 이 RAG 스택을 사용합니다.

## 1. Prerequisites

```powershell
cd $HOME\.lmstudio\Unreal58-RAG
.\rag.ps1 doctor
.\installer\Verify-UnrealMcp.ps1
```

LM Studio local server (`http://localhost:1234/v1`) with a tool-capable model loaded.

## 2. Rider (주력 IDE)

1. Unreal 프로젝트를 Rider로 엽니다 (`.sln` / `.uproject`).
2. **Settings → Build** 에서 UE 5.8 toolchain 확인.
3. 일반 작업: Rider Build/Rebuild, 디버그, 심볼 탐색.
4. `activeProject` 동기화: `.\rag.ps1 pick-project` 또는 MCP `set_active_project`.

Rider는 **편집·빌드·디버그** 담당. Cline은 **RAG + 패치 + (선택) agent UBT** 담당.

## 3. Cline MCP 설정

템플릿: [`config/cline_mcp_settings.template.json`](../config/cline_mcp_settings.template.json)

### VS Code + Cline 확장

1. Cline 패널 → MCP Servers → **Configure MCP Servers**
2. `cline_mcp_settings.json`에 `unreal-rag`, `unreal-agent` 추가 (템플릿 복사)
3. LM Studio provider: `http://localhost:1234/v1`, tool use 활성

Windows 경로 예:

`%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`

### Cline CLI

`%USERPROFILE%\.cline\data\settings\cline_mcp_settings.json`

설치 스크립트:

```powershell
.\installer\Install-ClineUnrealMcp.ps1
```

## 4. Project rules

Cline은 워크스pace 루트의 [`.clinerules`](../.clinerules)를 읽습니다.  
Unreal 게임 repo에도 동일 규칙을 복사하거나 symlink 하세요.

## 5. Agent workflow (Rider + Cline)

```
unreal_agent_session / unreal_rag_search
  → read_file
  → write_file (static validate)
  → Rider Build OR build_unreal_project
  → read log / read_unreal_logs on failure
```

| Surface | 역할 |
|---------|------|
| Rider | C++ 편집, UBT, 디버그, 프로젝트 구조 |
| Cline | MCP 도구, RAG, 자동 패치 |
| LM Studio | 로컬 LLM API |

## 6. LM Studio chat (보조)

기본 채팅만 쓸 때: [`docs/LMStudio_Unreal_Agent_Setup.md`](LMStudio_Unreal_Agent_Setup.md)

## 7. Legacy: Continue

Continue 설정은 유지되나 **권장하지 않음**. 마이그레이션 전용: [`Continue_Qwen_Unreal_Agent_Setup.md`](Continue_Qwen_Unreal_Agent_Setup.md)

## 8. Troubleshooting

| Issue | Fix |
|-------|-----|
| Cline MCP empty | `Install-ClineUnrealMcp.ps1`, LM Studio 재시작 |
| Wrong project in RAG | `pick-project`, shared `unreal-workspace.json` |
| Slow search | `hybrid=false` on `unreal_rag_search` |
| Validate errors | Fix `BAD_INCLUDE_PATH`, rebuild in Rider |
