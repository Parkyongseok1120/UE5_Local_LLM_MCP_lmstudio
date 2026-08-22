<img width="1920" height="1080" alt="Image" src="https://github.com/user-attachments/assets/cd25e0fe-d6fd-4ea8-be24-d1606bb644aa" />


# UE5_Local_LLM_MCP_lmstudio 1.3.0 RC3

> **GitHub 프리릴리스 (stable 배포 준비 전):** 제품 메타데이터는 **1.3.0 RC3**로 맞췄지만, Windows 실기 설치와 남은 릴리스 게이트가 끝날 때까지 `releaseReady`는 `false`입니다. portable reasoning skill, LM Studio MCP, preset, Node/Python adapter는 Windows·Ubuntu Linux에서 통합 workflow로 설치할 수 있습니다. **Apple Silicon macOS** 실기 FULL 설치는 **PASS**이고, **Intel macOS**는 LM Studio 구성 설치가 초기에 차단됩니다(custom Codex/Cline-only는 허용). `./install.sh` 실행 전 호스트에 **Python 3.10+**가 필요합니다. [1.3.0 RC3 노트](docs/Release_Notes_1_3_0_RC3.md)와 [통합 설치 문서](docs/Integrated_Installer.md)를 참고하세요.

LM Studio의 로컬 LLM을 Unreal Engine 5.x C++ 보조 에이전트로 쓰기 위한 **RAG + MCP stack**입니다.

<p align="center">
  <a href="README.md"><img alt="English" src="https://img.shields.io/badge/Language-English-blue"></a>
  <a href="README.ko.md"><img alt="Korean" src="https://img.shields.io/badge/Language-%ED%95%9C%EA%B5%AD%EC%96%B4-green"></a>
</p>

---

## ☕ 프로젝트 후원

이 프로젝트가 도움이 되었다면 후원을 고려해 주세요. 개발을 계속 이어가는 데 큰 도움이 됩니다.

[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-%E2%9D%A4-red?logo=github)](https://github.com/sponsors/Parkyongseok1120)

👉 **[https://github.com/sponsors/Parkyongseok1120](https://github.com/sponsors/Parkyongseok1120)**

---

## 프로젝트 현황

> **프로젝트 현황 — 2026년 8월**
>
> **현재 제품 라벨은 1.3.0 RC3 GitHub 프리릴리스입니다.** RC3는 RC2 이후 검증한 recovery state machine, atomic mutation journal, canonical project/build proof, Automation 범위, Windows/POSIX 경로 identity를 배포합니다. Apple Silicon 실기 FULL 설치는 PASS지만 Windows 실기 설치는 아직 미검증이라 `releaseReady`는 false입니다.
>
> 현재 런타임의 기본값은 **Direct Model Mode**입니다. 선택한 LLM이 도구 선택과 순서를 소유하고 MCP 서버는 안정적인 capability와 파일시스템·프로세스·빌드 안전 경계를 제공합니다. 기본 경로에는 서버 소유 task, route, planner, synthesis gate가 없습니다. 저장된 v1.2.5 측정과 RC3 control-plane 검증은 과거 근거로 유지되지만 새 Direct-mode 점수는 아닙니다. Windows 검증과 남은 릴리스 게이트가 끝날 때까지 RC3를 stable로 간주하지 마세요.

---

## 문서 허브

<p>
  <a href="docs/Project_Overview.md"><img alt="Project Overview" src="https://img.shields.io/badge/Docs-Project%20Overview-blue?logo=gitbook"></a>
  <a href="docs/Release_Notes_1_3_0_RC3.md"><img alt="1.3.0 RC3 Notes" src="https://img.shields.io/badge/Release-1.3.0%20RC3-orange?logo=github"></a>
  <a href="docs/Model_Measurement_Results.md"><img alt="Model Results" src="https://img.shields.io/badge/Docs-Model%20Results-purple?logo=gitbook"></a>
  <a href="docs/Version_Performance_History.md"><img alt="Version Performance" src="https://img.shields.io/badge/Docs-Version%20Performance-green?logo=gitbook"></a>
</p>

## 최신 결과

아래 수치는 최신 저장 **v1.2.5 live-model baseline**입니다. 1.3.0 RC3 paired live 재측정은 아직 완료되지 않았습니다.

| 모델 / run | Pass@K | Pass@1 | Artifact |
|---|---:|---:|---|
| Qwen 3.6 27B community fine-tune | 36/36 | 36/36 | `20260709-144441-pass1-target` |
| Qwen 3.5 9B | 35/36 | 33/36 | `20260709-153021-qwen35-9b` |

| 모델 / run | Live 측정 시간 |
|---|---:|
| Qwen 3.6 27B community fine-tune | 약 33분 37초 |
| Qwen 3.5 9B | 약 27분 22초 |

<p>
  <a href="docs/Holdout_Case_Difficulty.md"><img alt="Holdout Difficulty" src="https://img.shields.io/badge/Docs-36%20Case%20Difficulty-red?logo=gitbook"></a>
</p>

이 수치는 UE 5.8 RAG/MCP/UBT 내부 workflow 측정 결과입니다. 공개 표준 모델 벤치마크가 아닙니다.

### 모델 크기와 한국어 사용상의 한계

9B 프로필은 현재 **최소 하단선**이지 안정 운용을 보장하는 기준이 아닙니다. MCP 서버와 validation이 정상이어도 툴 선택, 인자 구성, 반복 MCP 호출, 긴 수정/빌드 루프에서 불안정할 수 있습니다. 이는 자동으로 MCP 버그라고 볼 문제가 아니라 모델 용량과 agentic tool-use 능력의 한계입니다. 측정된 9B와 27B의 차이가 크므로 Pass@1 수치만으로 두 모델의 에이전트 동작을 동등하다고 보면 안 됩니다.

여러 단계를 자율적으로 수행하는 Unreal 작업에는 **24B~27B instruction/tool-calling 모델**을 권장합니다. 9B는 대상 파일·심볼·변경 내용이 이미 확정된 짧은 작업에 한정하는 편이 좋습니다. 한국어 우선 사용이라면 정확한 로컬 checkpoint를 직접 검증해야 합니다. Qwen3는 100개 이상의 언어와 tool calling을 명시하지만, Devstral Small 2처럼 코드베이스 작업에 특화된 모델이 같은 수준의 한국어 표현력을 보장하는 것은 아닙니다. [Qwen3 모델 카드](https://huggingface.co/Qwen/Qwen3-30B-A3B)와 [Devstral Small 2 모델 카드](https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512)를 참고하세요.

과거 RC3 workflow 검증에서는 deterministic handoff가 작은 모델에게 긴 근거 기억이나 정확한 tool call 생성을 보장하지 못한다는 점이 드러났습니다. 이 모델측 한계는 여전히 중요하지만 지원 runtime은 예전 Python task/route/planning/synthesis transition을 더 이상 삽입하지 않습니다. 해당 source는 지원되지 않는 과거 평가 자료로만 남고 portable package에서는 제외됩니다.

> 최고 run의 `Harness average attempts=0.389`는 많은 케이스가 LLM 편집 시도 전에 deterministic static autofix로 해결되었다는 뜻입니다. 일반적인 모델 reasoning-depth 지표가 아닙니다.

> **BYOI** = Bring Your Own Index. 이 저장소는 **tooling만** 제공합니다. Epic source, 사전 빌드된 `rag.sqlite`는 포함하지 않습니다.

Portable ZIP은 운영체제 임시 폴더가 아닌 안정적인 디렉터리에 압축을 풀고,
설치 후에도 해당 디렉터리를 유지해야 합니다. LM Studio의 RAG/Agent MCP는
그 압축 해제 트리의 런타임을 직접 실행합니다. Portable ZIP에는
`node_modules`가 포함되지 않으므로 최초 Unreal 설치에서 `--skip-deps`를
사용하지 마세요. Agent SDK가 이미 resolve되지 않으면 installer는 이제
`mcp.json`을 기록하기 전에 실패하고 정상 dependency 설치 방법을 안내합니다.

## 빠른 설치

```powershell
git clone https://github.com/Parkyongseok1120/UE5_Local_LLM_MCP_lmstudio.git
cd UE5_Local_LLM_MCP_lmstudio
.\INSTALL.bat
.\rag.ps1 doctor
```

### 설치 진입점은 하나입니다

Windows에서는 루트의 `INSTALL.bat`, Linux와 macOS에서는 `install.sh`를 실행합니다. 둘 다 같은 `install.py`를 호출합니다. SAFE/AGENT/RAG/Cline/컨텍스트 압축기별 설치 파일은 없으며, 통합 설치 화면에서 선택합니다. Unreal 어댑터가 포함되면 `SAFE` 또는 `AGENT` 권한을 번호로 고르고, AGENT는 위험 확인을 한 번 더 거친 뒤 최종 설치 요약에서 다시 확인할 수 있습니다. `installer/`에는 bootstrap runtime 코드와 검증 가능한 매니페스트를 두고, 고급 유지보수·검증 도구는 `scripts/installer_support/`로 분리했습니다.

### Direct Model Mode가 기본입니다

일반 `unreal-rag`와 `unreal-agent` entry는 capability provider입니다. 모델은 요청에 맞춰 검색, 읽기, 수정, 검증, 빌드, 테스트 순서를 직접 고릅니다. capability를 쓰기 전에 `unreal_task_start`를 시작하거나 서버 plan을 만들거나 route authorization을 얻거나 synthesis를 commit할 필요가 **없습니다**. 읽기/쓰기 경로 제한, optimistic concurrency, command allowlist, 삭제 승인, SAFE/AGENT 권한은 그대로 적용됩니다.

> **중요 — 실제 LLM을 채팅 모델로 선택하세요.**
>
> 1. Qwen처럼 실제로 사용할 instruction/tool-calling 모델을 LM Studio에 로드하고 **모델 드롭다운**에서 직접 선택합니다.
> 2. 채팅을 만들거나 연 뒤 그 채팅의 **plugin panel**에서 **`codex/unreal-context-compactor`** 를 활성화합니다.
> 3. 실제 LLM 선택을 유지합니다. `unreal-context-compactor`는 chat plugin이지 모델이나 proxy model이 아니며 설정할 `targetModel`도 없습니다.
> 4. Local Server를 시작하고 기본 `unreal-rag`, `unreal-agent` MCP entry를 활성화합니다.

plugin은 선택된 모델로 context pressure를 측정하고 필요할 때 오래된 model-facing 채팅 기록만 압축합니다. 모델을 고르거나 sampling을 바꾸거나 MCP 도구를 필터링하거나 write/build 권한을 부여하지 않습니다. installer는 plugin을 설치하고 revision을 pin하지만 현재 LM Studio에는 특정 채팅에서 활성화됐음을 지속적으로 증명하는 API가 없습니다. chat plugin panel의 toggle을 직접 확인하세요.

아래 명령은 설치된 plugin의 source layout과 컴파일된 prediction-loop wiring을 검증합니다. 채팅별 활성화를 증명하지는 않습니다.

```shell
cd lmstudio-context-compactor-plugin
npm run status
```

context plugin은 세션 continuity 보조 기능이지 Direct MCP 권한의 전제 조건이 아닙니다. Cline, CLI, Ollama, 자체/remote client는 LM Studio chat plugin 없이도 MCP capability server를 사용할 수 있습니다.

### 여러 프로젝트와 Unreal 버전 사용

MCP 설치 하나로 여러 Unreal 프로젝트와 설치된 UE 버전을 함께 사용할 수 있습니다. `set_active_project`는 편리한 기본값을 제공하지만 Direct 파일, 검색, 수정, 로그, command, build, Automation 도구는 해당 schema가 제공하는 `project`, `projectRoot`, `hint` 필드로 정확한 `.uproject` 경로나 정확한 발견 프로젝트 이름을 받을 수 있습니다. 호출별 프로젝트 지정은 그 호출에만 active project를 덮어쓰며 route ownership을 만들거나 다른 채팅의 대상을 바꾸지 않습니다.

build와 Automation 호출은 선택한 프로젝트 descriptor의 engine association을 해석하고 필요하면 명시적 `engineRoot`도 받을 수 있습니다. 따라서 서로 다른 UE 5.x 설치를 쓰는 프로젝트가 같은 서버를 공유할 수 있습니다. 모호한 프로젝트 이름은 다른 프로젝트를 임의로 선택하지 않고 오류를 반환하므로 정확한 selector를 사용하세요.

### Strict는 별도 수동 opt-in입니다

installer가 관리하는 `unreal-rag`, `unreal-agent` entry는 그대로 유지하세요. 지원되는 Strict surface는 별도 이름의 Node entry 하나뿐입니다.

- `unreal-agent`를 `unreal-agent-strict`로 복사하고 entry를 `lmstudio-unreal-agent-mcp/src/strict-server.js`로 지정합니다.
- Node Strict는 대화 단위 `strict_begin` lifecycle을 소유합니다. 읽기와 검색은 task 없이 가능하고, 변경 및 장시간 실행 capability만 살아 있는 Strict session을 요구합니다.

제거된 Python controller는 지원되는 Strict entry가 아니며 Node 변경을 허가할 수 없습니다. portable package는 해당 monolithic MCP entry와 Strict manifest를 포함하지 않습니다. 같은 이름의 Direct tool과 중복 노출하려는 디버깅이 아니라면 Node Strict와 Direct를 동시에 켜지 마세요.

Node MCP transport는 선택된 모델이 최종 채팅 답변을 출력하는 순간을 관찰할 수 없습니다. 따라서 모델은 최종 답변 직전에 `strict_complete`를 명시적으로 호출해야 합니다(실패/취소라면 `strict_fail` / `strict_cancel`). 연결이나 process 종료, TTL 만료, process 재시작으로 끝나지 않은 Node session은 `orphaned`가 되며 Direct Mode, 다른 대화, 다른 프로젝트를 막지 않습니다. `strict_resume`에는 명시적인 사용자 승인이 필요합니다.

> **필수 — LM Studio 기본 도구 `js-code-sandbox`(JavaScript/TypeScript Code Sandbox)는 반드시 끄세요.**  
> Unreal 코딩 채팅에서는 LM Studio 기본 **JavaScript/TypeScript Code Sandbox** 플러그인을 비활성화하거나 숨기세요. 이 샌드박스는 별도 작업 디렉터리를 쓰며 활성 `.uproject` 루트와 **연결되지 않습니다**. 모델이 여기서 파일 I/O를 하면 경로 오류, 잘못된 편집, `unreal-agent`와의 충돌이 납니다. 프로젝트 파일 작업은 `unreal-rag` + `unreal-agent` MCP만 사용하세요 (`read_file`, `replace_in_file`, 신규 파일만 `write_file`). `%USERPROFILE%\.lmstudio\settings.json`에서 `lmstudio/js-code-sandbox:*`, `mcp/unreal-agent:*`, `mcp/unreal-rag:*` 같은 광역 자동 승인 패턴을 제거하고 LM Studio를 재시작하세요. MCP 광역 승인은 삭제 및 명시적으로 허용한 Editor 실행의 host 확인까지 건너뜁니다. installer와 `scripts/patch_mcp_config.py`는 관련 없는 설정을 보존하면서 이 패턴들을 제거합니다. 자세한 내용: [LMStudio_MCP_Tool_Discipline.md](docs/LMStudio_MCP_Tool_Discipline.md).

```powershell
.\rag.ps1 collect-source -Root C:\UE_5.6\Engine\Source
.\rag.ps1 collect-projects -Root C:\Projects\MyGame
.\rag.ps1 collect-symbols -Root C:\Projects\MyGame\Source -SymbolScope project -ProjectName MyGame
.\rag.ps1 build
```

처음에는 safe mode를 권장합니다. 파일 쓰기와 UBT 실행은 신뢰하는 프로젝트에서만 켜세요.

```powershell
python install.py --profile standard --yes --enable-agent-mode --accept-agent-risk
python install.py --profile standard --yes
```

질문은 LM Studio에서 선택한 실제 채팅 모델에 입력하고, 모델이
`unreal_rag_search` 또는 `unreal_symbol_lookup`을 호출하게 하세요. portable
`rag.ps1`는 수집·인덱스·프로젝트 선택·새로고침·상태 확인 전용이며 모델,
wrapper, planner, 평가 harness, query controller를 실행하지 않습니다.

## 실사용 세션 팁

Holdout eval은 짧고 깨끗한 turn에서 돌아갑니다. **LM Studio에서 길게 이어지는 채팅**에는 tool 결과, build log, retry가 계속 쌓입니다. 실제 LLM을 선택한 상태에서 그 채팅에 `codex/unreal-context-compactor`를 활성화하면 plugin이 해당 모델의 tokenizer 예산을 측정하고 hard margin이 소진되기 전에 오래된 model-facing 기록만 결정적 사실 메모리로 교체합니다. task route, required-next-tool 명령, planner state, synthesis gate를 보존하거나 생성하지 않습니다.

| LM Studio 로그 증상 | 대응 |
|---|---|
| `request (...) exceeds the available context size (54272)` | 실제 LLM이 선택되어 있고 이 채팅의 plugin panel에서 `codex/unreal-context-compactor`가 켜져 있는지 확인하세요. `npm --prefix lmstudio-context-compactor-plugin run status`는 설치된 source/build wiring만 검증합니다. 그래도 pressure가 너무 크면 context를 늘리거나 5–10줄 사실 handoff와 함께 새 채팅을 시작하세요. |
| `failed to restore kv cache` / `cache size limit reached` | 위와 동일 — 세션 메모리가 포화된 상태입니다. context만 올리는 것보다 새 채팅이 빠릅니다. |
| 긴 수정 루프 뒤 `Model failed to generate a tool call` | 멈추고, 변경 파일 + 남은 에러를 요약한 뒤 새 채팅으로. |
| Unreal 작업 중 로그에 `js-code-sandbox` 등장 | 위 Quick Install 안내대로 비활성화하세요. |

실프로젝트 작업 시 실전 규칙:

- 가능하면 **채팅 하나에 범위를 좁히기** (예: “컴파일 에러 3개 수정”, “dev console 전체 구현”은 한 세션에 넣지 않기).
- **UBT/linker 전체 로그를 채팅에 붙여넣지 마세요.** `read_unreal_logs`의 `mode=tail`은 최근 오류, `mode=first_error`는 byte 0부터 최초 원인 탐색, `mode=range`와 `cursorByte`/`nextCursorByte`는 제한된 범위 순회에 사용하세요.
- **헤더 → .cpp 순서는 정상입니다.** 새 헤더에 `write_file` 후 `CPP_DEFINITION_MISSING` advisory가 보일 수 있습니다. 매칭 `.cpp`를 쓰기 전까지는 기대되는 동작이며, 그 자체로 롤백 사유가 아닙니다.
- 모델이 자주 지어내는 **UE API**는 피하세요: `UCharacterMovementComponent::DisableGravity()`, `UWorld::GetURL()`, `SpawnActor(..., &FTransform)`, `GEngine->GetWorld()`. 대신 `GravityScale`, `GetMapName()` + `OpenLevel`/`ServerTravel`, 값으로 넘기는 `SpawnTransform`, 소유 actor/subsystem의 `GetWorld()`를 쓰세요.
- **compact tool 응답:** `build_unreal_project`는 한 줄 summary + likely error 최대 40줄 + `.agent/logs` 아래의 timestamped `fullLogPath`를 반환합니다(stdout/stderr 전체 아님). `read_unreal_logs`는 최신 로그의 제한된 tail이 기본이며 원본 잘림 여부를 반환합니다. chat plugin은 최신 실제 사용자 요청, 관찰/수정 파일, 최근 tool outcome, 최근 build/test state 같은 사실 메모리를 유지하고 task/route/control/synthesis 내부 상태와 required-next-tool directive는 의도적으로 제거합니다.

자동 압축은 세션을 연장하지만, 이미 너무 큰 system prompt/tool schema를 줄이거나 포화된 KV cache를 복구할 수는 없습니다. hard safety margin을 회복하지 못하면 정확한 프로젝트, 현재 요청, 이미 바꾼 파일, 남은 build/test 오류를 담은 짧은 사실 handoff와 함께 새 채팅을 시작하세요.

자세한 내용: [LMStudio_MCP_Tool_Discipline.md](docs/LMStudio_MCP_Tool_Discipline.md), [Troubleshooting.md](docs/Troubleshooting.md).

전체 요구사항, Mac remote setup, model profile, security note는 [Project_Overview.md](docs/Project_Overview.md)에 정리되어 있습니다.

## 주요 문서

| 주제 | 파일 |
|---|---|
| 1.3.0 RC3 릴리스 노트 | [docs/Release_Notes_1_3_0_RC3.md](docs/Release_Notes_1_3_0_RC3.md) |
| 1.3.0 RC2 릴리스 노트 | [docs/Release_Notes_1_3_0_RC2.md](docs/Release_Notes_1_3_0_RC2.md) |
| 1.3.0 Beta5 릴리스 노트 (과거 RC2) | [docs/Release_Notes_1_3_0_Beta5.md](docs/Release_Notes_1_3_0_Beta5.md) |
| 1.3.0 Beta4 릴리스 노트 (과거 RC1) | [docs/Release_Notes_1_3_0_Beta4.md](docs/Release_Notes_1_3_0_Beta4.md) |
| 상세 프로젝트 개요 | [docs/Project_Overview.md](docs/Project_Overview.md) |
| 모델 측정 결과 | [docs/Model_Measurement_Results.md](docs/Model_Measurement_Results.md) |
| 버전별 성능 이력 | [docs/Version_Performance_History.md](docs/Version_Performance_History.md) |
| 36-case holdout 난이도 | [docs/Holdout_Case_Difficulty.md](docs/Holdout_Case_Difficulty.md) |
| RAG setup reference | [docs/RAG_Setup.md](docs/RAG_Setup.md) |
| Safe vs agent mode | [docs/Safe_Agent_Mode.md](docs/Safe_Agent_Mode.md) |
| Model profiles | [docs/Model_Profiles.md](docs/Model_Profiles.md) |
| LM Studio MCP tool discipline | [docs/LMStudio_MCP_Tool_Discipline.md](docs/LMStudio_MCP_Tool_Discipline.md) |
| Troubleshooting | [docs/Troubleshooting.md](docs/Troubleshooting.md) |
| Security | [SECURITY.md](SECURITY.md) |

## 요약

1.3.0 RC3는 GitHub prerelease입니다(`releaseReady` false). 새 `v1.3.0-rc3` 태그는 기존 RC/Beta 태그를 이동하지 않습니다. Legacy Strict transition/recovery, 원자적 rollback, project proof, 설치·릴리스 위생은 자동 검증으로 보호하지만 기본 Direct entry는 그 task workflow를 실행하지 않습니다. GUI E2E와 새 paired live-model 점수는 주장하지 않습니다.

좁은 UE 5.8 compile-fix 작업에서는 현재 community fine-tuned Qwen 3.6 27B local workflow가 live UBT validation에서 강한 결과를 냈습니다(36/36 Pass@K, 36/36 Pass@1, 12/12 multifile Pass@1). Qwen 3.5 9B도 compact-model 결과를 저장했습니다(35/36 Pass@K, 33/36 Pass@1). 이 결과는 내부 workflow 측정이며, Claude/GPT 계열과의 일반 모델 동등성 주장이 아닙니다.

로컬 LLM으로 Unreal C++ hallucination을 줄이고 싶다면 실제 모델을 직접 선택하고 근거를 검색한 뒤 정확한 프로젝트 source를 읽고 답변하거나 patch하세요. RAG, validation, safety boundary, failure analysis를 먼저 개선하고 fine-tuning은 workflow가 실제 프로젝트 error에서 측정된 뒤에 적용하는 것이 좋습니다.

---

## ☕ 프로젝트 후원

이 프로젝트가 도움이 되었다면 후원을 고려해 주세요. 개발을 계속 이어가는 데 큰 도움이 됩니다.

[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-%E2%9D%A4-red?logo=github)](https://github.com/sponsors/Parkyongseok1120)

👉 **[https://github.com/sponsors/Parkyongseok1120](https://github.com/sponsors/Parkyongseok1120)**
