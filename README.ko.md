<img width="1920" height="1080" alt="Image" src="https://github.com/user-attachments/assets/cd25e0fe-d6fd-4ea8-be24-d1606bb644aa" />


# UE5_Local_LLM_MCP_lmstudio 1.3.3

> **Stable v1.3.3 릴리스:** Direct Model Mode가 기본 지원 경로입니다. 검색 결과를 정확한 프로젝트 identity와 함께 바로 재사용할 수 있게 하고, Direct RAG evidence를 serialized response envelope 안에서 제한하며, Evidence-First contract lookup은 tool-order authority가 아닌 선택적 조회임을 명확히 했습니다. 기존 receipt/CAS/atomic-write 경계와 context compactor 기본 OFF 정책은 그대로입니다. 여러 Unreal Engine 버전과 여러 프로젝트에서 재사용할 수 있도록 설계되었습니다. [1.3.3 릴리스 노트](docs/Release_Notes_1_3_3.md)와 [통합 설치 문서](docs/Integrated_Installer.md)를 참고하세요.

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
> **현재 제품 라벨은 stable 1.3.3입니다.** 지원 runtime은 Direct Model Mode, scoped file-version receipt, provenance-bound RAG generation, bounded Build/Automation process, provenance-aware durable continuity를 사용합니다. 선택적 context-continuity plugin은 installer가 채팅에서 활성화하지 않으므로 LM Studio가 소유한 단일 채팅 토글은 기본 OFF입니다. MCP 서버는 capability와 파일시스템·프로세스·빌드·프로젝트 안전 경계를 제공하며 모델의 task plan이나 tool 순서를 소유하지 않습니다.
>
> Stable v1.3.3 component metadata는 Node agent MCP 0.3.21, Evidence-First MCP server 1.1.1, Context Compactor 0.4.51/revision 98, portable manifest 2.1.16입니다. 검색 결과는 reusable scoped URI와 정확한 project identity를 함께 제공하고, Direct RAG는 serialized envelope와 match metadata를 제한하며, Evidence-First contract lookup은 routine preflight가 아닌 선택적 조회입니다.
>
> 릴리스 운영자가 Qwen 3.8 27B로 실제 Unreal 프로젝트에서 active-project 탐지, 반복 RAG/search/read, 대규모 architecture 보고서 작성, 후속 대화 연속성을 확인했습니다. 이전의 중간 JSON truncation은 재현되지 않았습니다. 이는 기능 E2E 확인이며 benchmark 점수나 모든 host의 보편적 인증은 아닙니다.
>
> 자동 source·package·installer·safety·cross-platform gate가 release readiness를 정의합니다. Gate 통과는 모든 host, Unreal project, engine build, plugin, editor-runtime 조합에 대한 보편적 호환성 주장이 아닙니다.

---

## 문서 허브

<p>
  <a href="docs/Project_Overview.md"><img alt="Project Overview" src="https://img.shields.io/badge/Docs-Project%20Overview-blue?logo=gitbook"></a>
  <a href="docs/Release_Notes_1_3_3.md"><img alt="1.3.3 Notes" src="https://img.shields.io/badge/Release-1.3.3-blue?logo=github"></a>
  <a href="docs/Model_Measurement_Results.md"><img alt="Model Results" src="https://img.shields.io/badge/Docs-Model%20Results-purple?logo=gitbook"></a>
  <a href="docs/Version_Performance_History.md"><img alt="Version Performance" src="https://img.shields.io/badge/Docs-Version%20Performance-green?logo=gitbook"></a>
</p>

## 모델 권고

**Qwen 3.8 27B를 이 stack의 현재 주 검증 operating model로 매우 추천합니다.** v1.3.2 라이브 E2E에서 실제 프로젝트의 장기 RAG/read/report 작업을 이전 context truncation 없이 완료했습니다. Muse Glimmer는 테스트 중이며 아직 검증된 추천이 아닙니다. Qwen 3.5, community Qwen 3.6 27B checkpoint, GPT-OSS는 현재 추천하지 않습니다.

과거 live-test 점수와 실행 시간 기록은 README에서 의도적으로 제거했습니다. 과거 측정 근거가 필요할 때만 [모델 측정 결과](docs/Model_Measurement_Results.md)를 참고하세요. 보관된 결과는 현재 모델 추천이나 v1.3.3 품질 점수가 아닙니다.

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

포맷 직후의 지원 host에서도 위 launcher는 pin된 uv seed를 내려받아 SHA-256을
검증한 뒤, 선택한 사용자 state-home 아래에 managed Python 3.12를 설치하고
계속 진행합니다. Python을 system-wide로 등록하거나 system PATH를 바꾸지는
않습니다. `python3 install.py`를 직접 실행할 때만 host Python 3.10+가 필요합니다.

### 설치 진입점은 하나입니다

Windows에서는 루트의 `INSTALL.bat`, Linux와 macOS에서는 `install.sh`를 실행합니다. 둘 다 같은 `install.py`를 호출합니다. 작은 pre-Python helper는 새 PC를 해당 구현에 연결하는 역할만 하며 profile·component·실제 설치 로직을 복제하지 않습니다. SAFE/AGENT/RAG/Cline/컨텍스트 압축기별 설치 파일은 없으며, 통합 설치 화면에서 선택합니다. Unreal 어댑터가 포함되면 `SAFE` 또는 `AGENT` 권한을 번호로 고르고, AGENT는 위험 확인을 한 번 더 거친 뒤 최종 설치 요약에서 다시 확인할 수 있습니다. `installer/`에는 bootstrap runtime 코드와 검증 가능한 매니페스트를 두고, 고급 유지보수·검증 도구는 `scripts/installer_support/`로 분리했습니다.

### Direct Model Mode가 기본입니다

일반 `unreal-rag`와 `unreal-agent` entry는 capability provider입니다. 모델은 요청에 맞춰 검색, 읽기, 수정, 검증, 빌드, 테스트 순서를 직접 고릅니다. capability를 쓰기 전에 `unreal_task_start`를 시작하거나 서버 plan을 만들거나 route authorization을 얻거나 synthesis를 commit할 필요가 **없습니다**. 읽기/쓰기 경로 제한, optimistic concurrency, command allowlist, 삭제 승인, SAFE/AGENT 권한은 그대로 적용됩니다.

> **중요 — 실제 LLM을 선택하고 context compactor는 기본적으로 끄세요.**
>
> 1. 실제 instruction/tool-calling 모델을 LM Studio에 로드하고 **모델 드롭다운**에서 직접 선택합니다. 현재 주 검증 모델인 Qwen 3.8 27B를 매우 추천합니다.
> 2. 그 채팅의 **plugin panel**에서 상단 **`codex/unreal-context-compactor`** 토글을 **OFF**로 유지합니다. installer는 LM Studio가 소유한 이 토글을 켜지 않으므로 새 채팅과 기존 채팅 모두에서 OFF인지 직접 확인합니다.
> 3. Local Server를 시작하고 기본 `unreal-rag`, `unreal-agent` MCP entry를 활성화합니다.

기본 설정에서는 compactor가 실행되지 않습니다. installer는 plugin을 설치하고 revision을 pin해 목록에서 사용할 수 있게 할 뿐 채팅에서 활성화하지 않습니다. 제한된 continuity가 필요한 긴 채팅에서는 단일 상단 `codex/unreal-context-compactor` 토글을 그 채팅에서 활성화합니다. handler 호출 자체가 활성화 경계이며 두 번째 enable control은 없습니다. **Observe only**는 model-facing history를 바꾸지 않고 측정할 때 사용할 수 있습니다. plugin은 모델을 고르거나 sampling을 바꾸거나 MCP 도구를 필터링하거나 write/build 권한을 부여하지 않습니다.

아래 명령은 설치된 plugin의 source layout과 컴파일된 prediction-loop wiring을 검증합니다. 채팅별 활성화를 증명하지는 않습니다.

```shell
cd lmstudio-context-compactor-plugin
npm run status
```

context plugin은 세션 continuity 보조 기능이지 Direct MCP 권한의 전제 조건이 아닙니다. Cline, CLI, Ollama, 자체/remote client는 LM Studio chat plugin 없이도 MCP capability server를 사용할 수 있습니다.

### 여러 프로젝트와 Unreal 버전 사용

MCP 설치 하나로 여러 Unreal 프로젝트와 설치된 UE 버전을 함께 사용할 수 있습니다. `set_active_project`는 편리한 기본값을 제공하지만 Direct 파일, 검색, 수정, 로그, command, build, Automation 도구는 해당 schema가 제공하는 `project`, `projectRoot`, `hint` 필드로 정확한 `.uproject` 경로나 정확한 발견 프로젝트 이름을 받을 수 있습니다. 호출별 프로젝트 지정은 그 호출에만 active project를 덮어쓰며 route ownership을 만들거나 다른 채팅의 대상을 바꾸지 않습니다.

build와 Automation 호출은 선택한 프로젝트 descriptor의 engine association을 해석하고 필요하면 명시적 `engineRoot`도 받을 수 있습니다. 따라서 서로 다른 UE 5.x 설치를 쓰는 프로젝트가 같은 서버를 공유할 수 있습니다. 모호한 프로젝트 이름은 다른 프로젝트를 임의로 선택하지 않고 오류를 반환하므로 정확한 selector를 사용하세요.

RAG generation은 엔진별 sibling shard에 묶입니다. 정확한 프로젝트 selector는 호환 shard를 선택하며, 한 호출에서 서로 다른 엔진에 묶인 프로젝트 근거를 합치지 않습니다. 기존 파일 읽기와 성공한 변경은 opaque `fileVersionReceipt`를 반환하므로 다음 편집에는 이를 우선 사용하세요. 유효한 raw `expectedHash`도 호환되고 신뢰 가능한 동일 session의 최신 snapshot은 자동 해석될 수 있으며, 외부 변경은 `FILE_VERSION_CONFLICT`로 fail-closed됩니다.

portable build의 `target=Editor`는 선택 프로젝트의 canonical, 설정된 preferred, 또는 유일하게 발견된 custom Editor target으로 해석되고 명시한 non-Editor target은 그대로 유지됩니다. Build와 Automation은 bounded process runner와 timeout process-tree 종료를 공유합니다.

### Strict는 별도 수동 opt-in입니다

installer가 관리하는 `unreal-rag`, `unreal-agent` entry는 그대로 유지하세요. 지원되는 Strict surface는 별도 이름의 Node entry 하나뿐입니다.

- `unreal-agent`를 `unreal-agent-strict`로 복사하고 entry를 `lmstudio-unreal-agent-mcp/src/strict-server.js`로 지정합니다.
- Node Strict는 대화 단위 `strict_begin` lifecycle을 소유합니다. 읽기와 검색은 task 없이 가능하고, 변경 및 장시간 실행 capability만 살아 있는 Strict session을 요구합니다.

제거된 Python controller는 지원되는 Strict entry가 아니며 Node 변경을 허가할 수 없습니다. portable package는 해당 monolithic MCP entry와 Strict manifest를 포함하지 않습니다. 같은 이름의 Direct tool과 중복 노출하려는 디버깅이 아니라면 Node Strict와 Direct를 동시에 켜지 마세요.

Node MCP transport는 선택된 모델이 최종 채팅 답변을 출력하는 순간을 관찰할 수 없습니다. 따라서 모델은 최종 답변 직전에 `strict_complete`를 명시적으로 호출해야 합니다(실패/취소라면 `strict_fail` / `strict_cancel`). 연결이나 process 종료, TTL 만료, process 재시작으로 끝나지 않은 Node session은 `orphaned`가 되며 Direct Mode, 다른 대화, 다른 프로젝트를 막지 않습니다. `strict_resume`에는 명시적인 사용자 승인이 필요합니다.

> **필수 — LM Studio 기본 도구 `js-code-sandbox`(JavaScript/TypeScript Code Sandbox)는 반드시 끄세요.**  
> Unreal 코딩 채팅에서는 LM Studio 기본 **JavaScript/TypeScript Code Sandbox** 플러그인을 비활성화하거나 숨기세요. 이 샌드박스는 별도 작업 디렉터리를 쓰며 활성 `.uproject` 루트와 **연결되지 않습니다**. 모델이 여기서 파일 I/O를 하면 경로 오류, 잘못된 편집, `unreal-agent`와의 충돌이 납니다. 프로젝트 파일 작업은 `unreal-rag` + `unreal-agent` MCP만 사용하세요 (`read_file`, `replace_in_file`, 신규 파일만 `write_file`). `%USERPROFILE%\.lmstudio\settings.json`에서 `lmstudio/js-code-sandbox:*`, `mcp/unreal-agent:*`, `mcp/unreal-rag:*` 같은 광역 자동 승인 패턴을 제거하고 LM Studio를 재시작하세요. MCP 광역 승인은 삭제 및 명시적으로 허용한 Editor 실행의 host 확인까지 건너뜁니다. installer와 `scripts/patch_mcp_config.py`는 관련 없는 설정을 보존하면서 이 패턴들을 제거합니다. 자세한 내용: [LMStudio_MCP_Tool_Discipline.md](docs/LMStudio_MCP_Tool_Discipline.md).

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 refresh -RefreshScope project_source
.\rag.ps1 doctor
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

Holdout eval은 짧고 깨끗한 turn에서 돌아갑니다. **LM Studio에서 길게 이어지는 채팅**에는 tool 결과, build log, retry가 계속 쌓입니다. 기본 운용에서는 `codex/unreal-context-compactor` 상단 토글을 OFF로 유지합니다. continuity가 필요하면 해당 긴 채팅에서 그 단일 토글을 켭니다. handler는 multi-round tool 작업 중 context를 측정하고 압축하지만 task route, required-next-tool 명령, planner state, synthesis gate를 보존하거나 생성하지 않습니다.

hard compaction의 bounded continuity state는 최신 사용자 요청, active objective, continuation antecedent, 현재 작업과 미해결 항목, 관찰·변경 파일, 최근 tool 결과와 build/test 상태를 보존할 수 있습니다. 이 사실 메모리는 plan, route, 도구 권한, 다음 호출, 완료 판단을 소유하지 않습니다.

| LM Studio 로그 증상 | 대응 |
|---|---|
| `request (...) exceeds the available context size (54272)` | 실제 LLM이 선택되어 있는지 확인합니다. 긴 채팅에서는 context pressure가 임계점에 도달하기 전에 단일 `codex/unreal-context-compactor` 채팅 플러그인 토글을 켭니다. 이미 window를 초과했다면 적절한 context 길이를 쓰거나 5–10줄 사실 handoff와 함께 새 채팅을 시작합니다. `npm --prefix lmstudio-context-compactor-plugin run status`는 설치된 source/build wiring만 검증합니다. |
| `failed to restore kv cache` / `cache size limit reached` | 위와 동일 — 세션 메모리가 포화된 상태입니다. context만 올리는 것보다 새 채팅이 빠릅니다. |
| 긴 수정 루프 뒤 `Model failed to generate a tool call` | 멈추고, 변경 파일 + 남은 에러를 요약한 뒤 새 채팅으로. |
| Unreal 작업 중 로그에 `js-code-sandbox` 등장 | 위 Quick Install 안내대로 비활성화하세요. |

실프로젝트 작업 시 실전 규칙:

- 가능하면 **채팅 하나에 범위를 좁히기** (예: “컴파일 에러 3개 수정”, “dev console 전체 구현”은 한 세션에 넣지 않기).
- **UBT/linker 전체 로그를 채팅에 붙여넣지 마세요.** `read_unreal_logs`의 `mode=tail`은 최근 오류, `mode=first_error`는 byte 0부터 최초 원인 탐색, `mode=range`와 `cursorByte`/`nextCursorByte`는 제한된 범위 순회에 사용하세요.
- **헤더 → .cpp 순서는 정상입니다.** 새 헤더에 `write_file` 후 `CPP_DEFINITION_MISSING` advisory가 보일 수 있습니다. 매칭 `.cpp`를 쓰기 전까지는 기대되는 동작이며, 그 자체로 롤백 사유가 아닙니다.
- 모델이 자주 지어내는 **UE API**는 피하세요: `UCharacterMovementComponent::DisableGravity()`, `UWorld::GetURL()`, `SpawnActor(..., &FTransform)`, `GEngine->GetWorld()`. 대신 `GravityScale`, `GetMapName()` + `OpenLevel`/`ServerTravel`, 값으로 넘기는 `SpawnTransform`, 소유 actor/subsystem의 `GetWorld()`를 쓰세요.
- **compact tool 응답:** `build_unreal_project`는 한 줄 summary + likely error 최대 40줄 + `.agent/logs` 아래의 timestamped `fullLogPath`를 반환합니다(stdout/stderr 전체 아님). `read_unreal_logs`는 최신 로그의 제한된 tail이 기본이며 원본 잘림 여부를 반환합니다. 선택적 chat plugin의 단일 상단 토글을 명시적으로 켠 경우에만 최신 실제 사용자 요청, 관찰/수정 파일, 최근 tool outcome, 최근 build/test state 같은 사실 메모리를 유지하며 task/route/control/synthesis 내부 상태와 required-next-tool directive는 의도적으로 제거합니다.

선택적으로 켠 자동 압축도 이미 너무 큰 system prompt/tool schema를 줄이거나 포화된 KV cache를 복구할 수는 없습니다. 기본 복구는 정확한 프로젝트, 현재 요청, 이미 바꾼 파일, 남은 build/test 오류를 담은 짧은 사실 handoff와 함께 새 채팅을 시작하는 것입니다.

자세한 내용: [LMStudio_MCP_Tool_Discipline.md](docs/LMStudio_MCP_Tool_Discipline.md), [Troubleshooting.md](docs/Troubleshooting.md).

전체 요구사항, Mac remote setup, model profile, security note는 [Project_Overview.md](docs/Project_Overview.md)에 정리되어 있습니다.

## 주요 문서

| 주제 | 파일 |
|---|---|
| 1.3.3 릴리스 노트 | [docs/Release_Notes_1_3_3.md](docs/Release_Notes_1_3_3.md) |
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

1.3.3은 현재 stable Direct Model Mode 릴리스입니다. Scoped SHA-256 file-version receipt, receipt 연쇄 수정, bounded process 실행, RAG provenance, response-envelope budget, 정확한 project identity, durable continuity sanitization, installer 경로, package hygiene는 자동 검증으로 보호합니다. 선택적 context compactor는 기본 OFF이며 압축된 history를 통해 파일 변경 권한을 재상속하지 않습니다.

Qwen 3.8 27B가 현재 매우 추천하는 주 operating model입니다. Muse Glimmer는 테스트 중입니다. Qwen 3.5, community Qwen 3.6 27B checkpoint, GPT-OSS는 현재 추천하지 않습니다.

로컬 LLM으로 Unreal C++ hallucination을 줄이고 싶다면 실제 모델을 직접 선택하고 근거를 검색한 뒤 정확한 프로젝트 source를 읽고 답변하거나 patch하세요. RAG, validation, safety boundary, failure analysis를 먼저 개선하고 fine-tuning은 workflow가 실제 프로젝트 error에서 측정된 뒤에 적용하는 것이 좋습니다.

---

## ☕ 프로젝트 후원

이 프로젝트가 도움이 되었다면 후원을 고려해 주세요. 개발을 계속 이어가는 데 큰 도움이 됩니다.

[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-%E2%9D%A4-red?logo=github)](https://github.com/sponsors/Parkyongseok1120)

👉 **[https://github.com/sponsors/Parkyongseok1120](https://github.com/sponsors/Parkyongseok1120)**
