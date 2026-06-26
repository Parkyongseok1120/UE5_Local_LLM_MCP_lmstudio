# UE5_Local_LLM_MCP_lmstudio

Local **Unreal Engine 5.x RAG stack** for LM Studio (formerly **Unreal58-RAG**). Officially tested on **UE 5.8**; use BYOI (`collect-source`) for other 5.x versions.

**Monorepo layout (recommended):**
```text
~/.lmstudio/
  Unreal58-RAG/
  lmstudio-unreal-agent-mcp/
```

**Two-repo layout:** clone both repos side by side and point MCP configs at each root.

```powershell
cd $HOME\.lmstudio\Unreal58-RAG
.\installer\INSTALL.bat
.\installer\Configure-Knowledge.ps1   # when available — builds index from YOUR UE install
.\rag.ps1 doctor
```

1. Install LM Studio and load a local coding model (e.g. Qwen 3.6 27B).
2. Patch MCP: `python scripts\patch_mcp_config.py`
3. Paste system prompt: `prompts/lmstudio_unreal_agent_system.md`
4. Enable **unreal-rag** + **unreal-agent** MCP servers in LM Studio.

**Do not commit** pre-built `data/` indexes or Epic source exports — see [EPIC_NOTICE.md](EPIC_NOTICE.md).

Before your first public push: `.\installer\Verify-Oss-Ready.ps1`

---

LM Studio용 **Unreal Engine 5.8 C++ RAG 스택**입니다. 설치 경로 예: `$HOME\.lmstudio\Unreal58-RAG`

LM Studio의 여러 로컬 모델을 Unreal Engine 5.8 C++ 도우미처럼 쓰기 위한 RAG 파이프라인입니다.

LoRA에 모든 문서를 외워 넣는 대신, Unreal 문서/소스/API를 검색 인덱스로 만들고 질문할 때 관련 문맥을 모델에 같이 넣는 방식입니다. 나중에 답변 스타일만 LoRA로 작게 튜닝하면 됩니다.

## 빠른 실행

이 PC처럼 `python` 명령이 PATH에 없어도 `rag.ps1`이 Codex 번들 Python을 자동으로 찾아 실행합니다.

```powershell
.\rag.ps1 collect-source
.\rag.ps1 collect-projects -CopyProjectText
.\rag.ps1 build
.\rag.ps1 query -Question "UActorComponent를 C++로 만드는 법 알려줘"
```

LM Studio Local Server를 켠 뒤에는:

```powershell
.\rag.ps1 lmstudio-models
.\rag.ps1 ask -Question "Actor에 커스텀 Component를 붙이는 C++ 예시 보여줘"
```

## 직접 실행: 로컬 Unreal C++ 소스 수집

Unreal Engine 5.8 소스가 로컬에 있다면 같이 넣을 수 있습니다.

```powershell
python scripts\collect_unreal_source.py `
  --root "C:\Program Files\Epic Games\UE_5.8\Engine\Source" `
  --out data\unreal58\raw_source.jsonl
```

소스 위치가 다르면 `--root`만 바꾸면 됩니다.
기본값은 `ThirdParty`를 제외합니다. Boost/WebRTC 같은 외부 라이브러리까지 모두 넣고 싶으면 `--include-third-party`를 추가하거나 `.\rag.ps1 collect-source -IncludeThirdParty`를 사용하세요.

## 로컬 Unreal 프로젝트 수집

기본 위치 `$HOME\Documents\Unreal Projects` 아래의 `.uproject`를 찾아 C++/Config/플러그인/텍스트 파일을 수집합니다.

```powershell
.\rag.ps1 collect-projects -CopyProjectText
.\rag.ps1 build
```

다른 위치를 쓰려면:

```powershell
.\rag.ps1 collect-projects -ProjectsRoot "D:\MyUnrealProjects" -CopyProjectText
```

텍스트 파일 복사본은 `data\unreal_projects\text_snapshot`에 만들어집니다. 전체 프로젝트 복사 대신 RAG에 필요한 텍스트만 모으는 방식입니다.

## Unreal 프로그래밍 보조용 수집

코드 생성, 컴파일 에러 수정, UHT/reflection 문제, Build.cs 의존성 조언을 강화하려면 Unreal C++ 심볼과 빌드 로그를 추가로 수집합니다.

```powershell
.\rag.ps1 collect-symbols
.\rag.ps1 collect-module-graph
.\rag.ps1 collect-project-profile -ProjectsRoot "C:\Path\To\YourProject"
.\rag.ps1 collect-build-logs -BuildLogRoot "C:\Path\To\YourProject" -LogsOnly
.\rag.ps1 build
```

수집되는 심볼 정보:

- `UCLASS`, `USTRUCT`, `UINTERFACE`, `UENUM`
- `UFUNCTION`, `UPROPERTY`
- include 목록
- `*.Build.cs` 모듈 dependency
- 선택 시 C++ 함수 정의 일부

프로그래밍 검색 모드:

- `codegen`: 새 Unreal C++ 코드 작성 근거 검색
- `compile_fix`: 컴파일/링크 에러 수정
- `runtime_debug`: Editor 로그, crash, assert, ensure 분석
- `api_lookup`: Unreal API, 시그니처, include 확인
- `module_fix`: Build.cs, include, module dependency 수정
- `reflection_fix`: UHT, generated.h, reflection macro 문제 수정

`collect-module-graph`는 수집된 심볼에서 include owner와 module dependency graph를 만들어 `GameplayTagContainer.h` 같은 include가 어느 module 소속인지, public header 사용이면 `PublicDependencyModuleNames`가 필요한지, private `.cpp` 사용이면 `PrivateDependencyModuleNames`가 맞는지 판단할 근거를 추가합니다. 요약 리포트는 `Reports/unreal_module_include_graph.md`에 생성됩니다.

`collect-project-profile`은 `.uproject`, `.uplugin`, `*.Build.cs`, `*.Target.cs`, `Config/*.ini`, 모듈 목록, 플러그인 목록을 요약해서 프로젝트별 RAG 규칙으로 넣습니다. 실제 프로젝트가 생기면 `-ProjectFile` 또는 `-ProjectsRoot`를 지정해 다시 수집하세요.

LM Studio로 질문할 때는 검색 결과가 모드별 context assembler를 거쳐 정렬됩니다. 예를 들어 `codegen`은 symbol -> include -> module -> project profile/example -> recipe/playbook 순서로, `compile_fix`는 build log -> symbol -> module/include -> project profile/example -> fix playbook 순서로 조립됩니다.

회귀 테스트:

```powershell
.\rag.ps1 eval-unreal-programming
.\rag.ps1 eval-unreal-review -Answers tests\fixtures\unreal_review_answers\good_answers.jsonl
.\rag.ps1 test-build-logs
.\rag.ps1 test-unreal-readiness
```

`test-build-logs`는 synthetic Unreal 로그 fixture로 `C1083`, `LNK2019`, `UnrealHeaderTool`, `generated.h` 오류가 `build_log` JSONL 레코드로 분리되는지 확인합니다.

`eval-unreal-review`는 모델 답변 JSON/JSONL을 UE C++ 리뷰 E2E 케이스에 맞춰 채점합니다. 프롬프트만 뽑으려면 `.\rag.ps1 eval-unreal-review -PrintPrompts`를 사용하세요. `test-unreal-readiness`는 wrapper 정적 검증이 `generated.h`, reflection namespace, BlueprintNativeEvent, editor-only include, UObject lifetime 위험을 잡는지 확인합니다.

추가 운영 명령:

```powershell
.\rag.ps1 doctor
.\rag.ps1 bench-mcp
.\rag.ps1 eval-debug
.\rag.ps1 eval-genre
.\rag.ps1 eval-e2e-compile
.\rag.ps1 knowledge-audit
.\rag.ps1 agent-session -Question "TPS shooter prototype component"
.\rag.ps1 scaffold-prototype -ScaffoldGenre shooter
.\installer\Install-ClineUnrealMcp.ps1
```

## 사용 모드

| 모드 | 표면 | 흐름 |
|------|------|------|
| Q&A | LM Studio 채팅 | `unreal_rag_search` only |
| Agent | Cline + MCP (Rider에서 빌드/디버그) | `unreal_agent_session` → write → Rider Build |
| Compile loop | LM Studio / Cline | `unreal_start_compile_loop` |
| Runtime debug | LM Studio + MCP | `read_unreal_logs` → `runtime_debug` RAG |

LM Studio 설정: [`docs/LMStudio_Unreal_Agent_Setup.md`](docs/LMStudio_Unreal_Agent_Setup.md)

**Rider + Cline (권장 IDE):** [`docs/Cline_Rider_Unreal_Agent_Setup.md`](docs/Cline_Rider_Unreal_Agent_Setup.md)

## VSCode Unreal C++ 환경 세팅 (선택)

UE 5.8 프로젝트를 VSCode에서 열 때 C++ IntelliSense와 디버깅 설정을 한 번에 맞추려면 아래 스크립트를 사용합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_vscode_unreal.ps1 `
  -ProjectFile "C:\Path\To\MyGame.uproject"
```

여러 프로젝트 루트에 한 번에 적용할 수도 있습니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_vscode_unreal.ps1 `
  -ProjectsRoot "$HOME\Documents\Github"
```

이 스크립트는 VSCode 전역 C++ 기본값, Unreal Editor의 Source Code Editor, UBT의 프로젝트 파일 형식, 프로젝트별 `.vscode/settings.json`, `tasks.json`, `launch.json`, `extensions.json`, `compileCommands_*.json` 생성을 맞춥니다. `Source` 폴더가 없는 Blueprint-only 프로젝트는 C++ IntelliSense 대상이 아니므로 건너뜁니다.

실제 UnrealBuildTool 피드백 루프는 프로젝트가 생긴 뒤 아래 형태로 실행합니다.

```powershell
.\rag.ps1 ubt-feedback `
  -ProjectFile "C:\Path\To\YourProject\YourProject.uproject" `
  -UbtTarget "YourProjectEditor" `
  -Mode compile_fix `
  -Question "방금 빌드 실패 원인과 최소 수정안"
```

## 게임 기획 문서 수집

프로젝트별 기획 문서는 `Game_Design_Docs\<ProjectName>` 아래에 넣습니다. 예시는 `Game_Design_Docs\_TEMPLATE`에 있으며, `_`로 시작하는 폴더는 기본 수집에서 제외됩니다.

```powershell
.\rag.ps1 collect-game-design
.\rag.ps1 build
.\rag.ps1 query -Mode planning -Source game_design_doc -Project "MyGame" -Question "핵심 루프와 첫 구현 단위 정리해줘"
```

문서 상단에 선택적으로 front matter를 넣으면 검색 필터에 반영됩니다.

```markdown
---
title: Core Loop
genre: action_combat
design_area: core_loop
---
```

게임 기획 검색 품질은 회귀 테스트로 확인할 수 있습니다. 각 쿼리의 기대 문서가 top-5 안에 들어오는지 검사합니다.

```powershell
.\rag.ps1 eval-game-design
```

## 선택 사항: 공식 문서 수집

Epic 문서 사이트는 SPA와 보안 체크 때문에 일반 HTTP 수집에서 본문 대신 제목만 잡힐 수 있습니다. 안정적인 시작은 위의 로컬 소스 인덱싱입니다.

```powershell
python scripts\collect_unreal_docs.py `
  --seeds config\unreal_57_seed_urls.txt `
  --out data\unreal58\raw_docs.jsonl `
  --max-pages 200 `
  --delay 0.5
```

처음에는 `--max-pages 20` 정도로 작게 테스트한 뒤 늘리는 편이 좋습니다.

## RAG 인덱스 생성

```powershell
python scripts\build_rag_index.py `
  --input data\unreal58\raw_docs.jsonl data\unreal58\raw_source.jsonl `
  --out-dir data\unreal58
```

생성물:

- `data\unreal58\chunks.jsonl`: 청크 데이터
- `data\unreal58\rag.sqlite`: SQLite FTS 검색 인덱스

`raw_source.jsonl`이 아직 없으면 `raw_docs.jsonl`만 넣어도 됩니다.

## 검색 테스트

```powershell
python scripts\query_rag.py `
  --index data\unreal58\rag.sqlite `
  "UActorComponent를 C++로 만들 때 필요한 매크로와 Build.cs 설정 알려줘"
```

## LM Studio와 연결

LM Studio에서 원하는 모델을 로드하고 Local Server를 켠 뒤 실행합니다. `-Model`을 생략하면 LM Studio에 로드된 첫 번째 모델을 자동으로 사용합니다.

```powershell
python scripts\query_rag.py `
  --index data\unreal58\rag.sqlite `
  --ask-lmstudio `
  "UE 5.8에서 Actor에 커스텀 Component를 붙이는 C++ 예시 보여줘"
```

특정 모델을 지정하려면 `--model "모델_ID"` 또는 `.\rag.ps1 ask -Model "모델_ID" ...`를 사용하세요. `.\rag.ps1 lmstudio-models`로 실제 모델 ID를 확인할 수 있습니다.

## LM Studio 앱 채팅에서 MCP로 사용

LM Studio는 MCP 서버를 앱 채팅에 연결할 수 있습니다. 이 프로젝트는 `unreal_rag_search` MCP 도구를 제공합니다.

1. LM Studio 오른쪽 사이드바에서 `Program` 탭을 엽니다.
2. `Install > Edit mcp.json`을 누릅니다.
3. `config/cline_mcp_settings.template.json`의 `mcpServers` 내용을 LM Studio `mcp.json`에 맞게 추가합니다.
4. LM Studio를 재시작하거나 MCP 목록을 새로고침합니다.
5. 채팅에서 Unreal 관련 질문을 할 때 “필요하면 `unreal_rag_search`를 먼저 사용해”라고 말하면 됩니다.

만약 LM Studio가 `Unexpected token '﻿'` 오류를 내면 `mcp.json`이 UTF-8 BOM으로 저장된 것입니다. 이 명령으로 BOM 없는 UTF-8 설정을 다시 씁니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_lmstudio_mcp.ps1
```

## Workspace 이름

이전 폴더명 `Gemma4 LORA`는 **Unreal58-RAG**로 통일했습니다. MCP 서버 이름 `unreal-rag`와 짝이 맞고, LoRA가 핵심 기능처럼 보이지 않습니다.

물리 폴더가 아직 `Gemma4 LORA`이면 junction `Unreal58-RAG`를 쓰거나, LM Studio/Cursor를 닫은 뒤 rename 하세요.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\rename_workspace_and_refresh_lmstudio.ps1
```

## Workspace 폴더 rename (선택)

스크립트는 폴더명을 바꾼 뒤 RAG 인덱스를 재빌드하고, LM Studio의 `mcp.json`과 내부 동기화 파일을 새 경로로 갱신하며, UTF-8 BOM 없이 저장됐는지 확인합니다.

도구 이름:

```text
unreal_rag_search
```

## LoRA는 언제 하나요?

먼저 RAG 검색 품질을 잡은 뒤, LoRA는 아래처럼 작은 목표로만 시작하는 게 좋습니다.

- Unreal C++ 답변 형식 고정
- `UCLASS`, `UPROPERTY`, `UFUNCTION`, `Build.cs` 설명 습관화
- 모르는 API를 지어내지 않고 근거 문서를 요구하는 습관화
- 한국어 설명 + C++ 예시 코드 스타일 고정

즉, 지식은 RAG가 담당하고 LoRA는 태도와 형식을 담당하게 만드는 구조입니다.
