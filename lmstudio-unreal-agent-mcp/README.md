# LM Studio Unreal Agent MCP

MCP server for **unreal-agent**: sandboxed file tools, Unreal project auto-detection, and optional UBT build.

This folder ships **inside** the [UE5_Local_LLM_MCP_lmstudio](https://github.com/Parkyongseok1120/UE5_Local_LLM_MCP_lmstudio) monorepo at:

```text
UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/
```

Legacy standalone layout (still supported):

```text
~/.lmstudio/lmstudio-unreal-agent-mcp/
```

## 포함 툴

- `get_workspace_info`
- `detect_unreal_project` — `.uproject`, editor target, engine, platform 자동 감지
- `list_directory`
- `read_file`
- `write_file`
- `replace_in_file`
- `propose_file_deletions` (extended; dry-run deletion plan with count/reason/impact)
- `delete_file` (extended; `ALLOW_SOURCE_DELETE=1`, Source/ only, requires matching plan token)
- `static_validate_project` (extended)
- `search_files`
- `run_command`
- `build_unreal_project` — 인자 생략 시 자동 감지 후 UBT 실행

## 핵심 안전장치

- 모든 파일 접근은 `WORKSPACE_ROOT` 안으로 제한
- 쓰기는 `ALLOW_WRITE=1`일 때만
- 명령 실행은 `ALLOW_COMMANDS=1`일 때만
- Unreal 빌드는 `ALLOW_UNREAL_BUILD=1`일 때만
- `run_command`는 allowlist 기반

## 설치

```powershell
cd $HOME\.lmstudio\UE5_Local_LLM_MCP_lmstudio
.\INSTALL.bat
```

LM Studio를 재시작하거나 MCP 목록을 새로고침하세요.

## 자동 프로젝트 감지

에이전트가 매번 `engineRoot`, `project`, `target`을 적을 필요 없습니다.

1. `detect_unreal_project` 또는 인자 없는 `build_unreal_project` 호출
2. 아래 경로에서 `.uproject` 검색
   - `WORKSPACE_ROOT`
   - `config/agent-mcp.json`의 `projectSearchRoots`
   - `Documents\Git`, `Documents\Unreal Projects` (기본)
3. `Source/*Editor.Target.cs`에서 editor target 추론
4. `.uproject`의 `EngineAssociation` + 설치된 UE 폴더에서 engine root 추론
5. Windows 기본값: `Win64` / `Development`

프로젝트가 여러 개면 `hint`를 넘깁니다.

```txt
build_unreal_project를 사용해서 JRPG 프로젝트를 빌드해.
hint: JRPG
```

고정 프로젝트를 쓰려면 MCP tool로 자유롭게 선택/해제합니다.

```txt
list_unreal_projects
set_active_project hint: JRPG
get_active_project
set_active_project clear: true
```

또는 `config/agent-mcp.json`을 직접 편집할 수도 있습니다.

## 설정 파일

| 파일 | 역할 |
|------|------|
| `config/agent-mcp.json.template` | 프로젝트 검색 루트 템플릿 (복사 후 `agent-mcp.json`으로 저장) |
| `config/lmstudio-mcp-unreal-agent.json.template` | LM Studio MCP 등록 템플릿 |

### `WORKSPACE_ROOT` 권장값

파일 read/write 샌드박스입니다. Unreal 프로젝트들이 들어 있는 **공통 상위 폴더**를 지정하세요.

```txt
%USERPROFILE%\Documents
```

Or narrow it to a project root, e.g. `%USERPROFILE%\Documents\Git`.

## LM Studio MCP 설정 예시

루트 통합 설치기가 `mcp.json`에 자동 등록합니다.

## 에이전트 사용 예

```txt
1. detect_unreal_project로 현재 작업 프로젝트 확인
2. 필요한 C++ 파일만 최소 수정
3. build_unreal_project 실행 (hint만 주면 됨)
4. likelyErrors 보고 최소 패치 후 재빌드
```

## 주의

로컬 파일 쓰기와 Unreal Build를 실행할 수 있습니다. 신뢰하지 않는 프롬프트에는 `ALLOW_WRITE`, `ALLOW_COMMANDS`, `ALLOW_UNREAL_BUILD`를 켜지 마세요.
