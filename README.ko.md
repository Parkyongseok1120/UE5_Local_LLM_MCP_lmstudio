<img width="1920" height="1080" alt="Image" src="https://github.com/user-attachments/assets/cd25e0fe-d6fd-4ea8-be24-d1606bb644aa" />


# UE5_Local_LLM_MCP_lmstudio 1.2.5

> **지원 플랫폼: Windows 10/11 전용.** 설치 스크립트는 PowerShell/BAT 기반입니다. macOS와 Linux는 현재 지원하지 않습니다.

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

> **프로젝트 현황 — 2026년 7월**
>
> 이 프로젝트의 초기 목표였던 "로컬 환경에서 Claude Sonnet 4에 근접한 수준의 Unreal Engine 에이전트 워크플로 구축"은 상당 부분 달성되었습니다. **최신 v1.2.5 (2026-07-09):** multifile holdout 수정(`UPROPERTY` return-type drift, callback param expansion) 이후 NavigationSystem module routing, editor-runtime boundary, UObject lifecycle autofix 회귀를 안정화했습니다. dry-run compile gate **36/36** (`20260709-142052`). Live 재검증 **36/36 Pass@K**, **36/36 Pass@1** (`20260709-144441-pass1-target`); multifile tier **12/12 Pass@1**.
>
> v1.2.5는 1.2 라인의 마지막 minor release로 봅니다. 이후 1.2.x 업데이트가 있다면 간단한 bug fix, 문서 수정, 낮은 위험도의 안정화 patch로 제한합니다. v1.3.0 개발은 **v1.2.5 이후 약 4개월 뒤부터 시작**하는 것을 목표로 하며, C++ capability, semantic-refactor, runtime-debug, negative-control scorecard를 분리하는 방향입니다. **프로젝트 자체가 멈춘 것은 아니고**, **대학교 수업과 졸업 작품**에 집중해야 해서 어쩔 수 없이 잠시 일시중단하게 되었습니다. 빠르게 정리하고 돌아와서 **v1.3.0**으로 뵙겠습니다!

---

## 문서 허브

<p>
  <a href="docs/Project_Overview.md"><img alt="Project Overview" src="https://img.shields.io/badge/Docs-Project%20Overview-blue?logo=gitbook"></a>
  <a href="docs/Model_Measurement_Results.md"><img alt="Model Results" src="https://img.shields.io/badge/Docs-Model%20Results-purple?logo=gitbook"></a>
  <a href="docs/Version_Performance_History.md"><img alt="Version Performance" src="https://img.shields.io/badge/Docs-Version%20Performance-green?logo=gitbook"></a>
  <a href="docs/Roadmap_1_3_0.md"><img alt="v1.3.0 Roadmap" src="https://img.shields.io/badge/Roadmap-v1.3.0-orange?logo=gitbook"></a>
  <a href="docs/Evaluation_Claim_Guardrail.md"><img alt="Evaluation Guardrail" src="https://img.shields.io/badge/Docs-Evaluation%20Guardrail-lightgrey?logo=gitbook"></a>
</p>

## 최신 결과

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

> 최고 run의 `Harness average attempts=0.389`는 많은 케이스가 LLM 편집 시도 전에 deterministic static autofix로 해결되었다는 뜻입니다. 일반적인 모델 reasoning-depth 지표가 아닙니다.

> **BYOI** = Bring Your Own Index. 이 저장소는 **tooling만** 제공합니다. Epic source, 사전 빌드된 `rag.sqlite`는 포함하지 않습니다.

## 빠른 설치

```powershell
git clone https://github.com/Parkyongseok1120/UE5_Local_LLM_MCP_lmstudio.git
cd UE5_Local_LLM_MCP_lmstudio
.\installer\INSTALL-SAFE-MODE.bat
.\installer\Configure-Knowledge.ps1
.\rag.ps1 doctor
```

그 다음 LM Studio에서 모델을 로드하고 Local Server를 시작한 뒤, `unreal-rag` / `unreal-agent` MCP를 활성화하고 index를 빌드합니다.

> **필수 — LM Studio 기본 도구 `js-code-sandbox`(JavaScript/TypeScript Code Sandbox)는 반드시 끄세요.**  
> Unreal 코딩 채팅에서는 LM Studio 기본 **JavaScript/TypeScript Code Sandbox** 플러그인을 비활성화하거나 숨기세요. 이 샌드박스는 별도 작업 디렉터리를 쓰며 활성 `.uproject` 루트와 **연결되지 않습니다**. 모델이 여기서 파일 I/O를 하면 경로 오류, 잘못된 편집, `unreal-agent`와의 충돌이 납니다. 프로젝트 파일 작업은 `unreal-rag` + `unreal-agent` MCP만 사용하세요 (`read_file`, `replace_in_file`, 신규 파일만 `write_file`). 자동 승인을 쓰는 경우 `%USERPROFILE%\.lmstudio\settings.json`의 `chat.skipToolConfirmationPatterns`에서 `lmstudio/js-code-sandbox:*` 항목을 제거하고 LM Studio를 재시작하세요. 자세한 내용: [LMStudio_MCP_Tool_Discipline.md](docs/LMStudio_MCP_Tool_Discipline.md).

```powershell
.\rag.ps1 collect-source
.\rag.ps1 collect-projects -CopyProjectText
.\rag.ps1 collect-symbols
.\rag.ps1 collect-module-graph
.\rag.ps1 build
```

처음에는 safe mode를 권장합니다. 파일 쓰기와 UBT 실행은 신뢰하는 프로젝트에서만 켜세요.

```powershell
.\installer\Enable-AgentMode.ps1
.\installer\Disable-AgentMode.ps1
```

질문 예시:

```powershell
.\rag.ps1 lmstudio-models
.\rag.ps1 ask -Question "Show me a C++ example of attaching a custom Component to an Actor"
```

## 실사용 세션 팁

Holdout eval은 짧고 깨끗한 turn에서 돌아갑니다. **LM Studio에서 길게 이어지는 채팅**은 별개 문제입니다. tool 결과, build log, retry가 쌓이면 MCP는 정상인데도 요청이 실패합니다.

| LM Studio 로그 증상 | 대응 |
|---|---|
| `request (...) exceeds the available context size (54272)` | **새 채팅 시작.** 진행 상황을 5–10줄로 요약한 뒤 이어가세요. 같은 스레드에서 반복 재시도하지 마세요. |
| `failed to restore kv cache` / `cache size limit reached` | 위와 동일 — 세션 메모리가 포화된 상태입니다. context만 올리는 것보다 새 채팅이 빠릅니다. |
| 긴 수정 루프 뒤 `Model failed to generate a tool call` | 멈추고, 변경 파일 + 남은 에러를 요약한 뒤 새 채팅으로. |
| Unreal 작업 중 로그에 `js-code-sandbox` 등장 | 위 Quick Install 안내대로 비활성화하세요. |

실프로젝트 작업 시 실전 규칙:

- 가능하면 **채팅 하나에 범위를 좁히기** (예: “컴파일 에러 3개 수정”, “dev console 전체 구현”은 한 세션에 넣지 않기).
- **UBT/linker 전체 로그를 채팅에 붙여넣지 마세요.** `read_unreal_logs` 또는 로그 파일 경로를 쓰고, 첫 번째 의미 있는 에러 구간만 공유하세요.
- **헤더 → .cpp 순서는 정상입니다.** 새 헤더에 `write_file` 후 `CPP_DEFINITION_MISSING` advisory가 보일 수 있습니다. 매칭 `.cpp`를 쓰기 전까지는 기대되는 동작이며, 그 자체로 롤백 사유가 아닙니다.
- 모델이 자주 지어내는 **UE API**는 피하세요: `UCharacterMovementComponent::DisableGravity()`, `UWorld::GetURL()`, `SpawnActor(..., &FTransform)`, `GEngine->GetWorld()`. 대신 `GravityScale`, `GetMapName()` + `OpenLevel`/`ServerTravel`, 값으로 넘기는 `SpawnTransform`, 소유 actor/subsystem의 `GetWorld()`를 쓰세요.

v1.3.0에서 session handoff artifact와 build-log token diet를 강화할 예정입니다. 그 전까지는 **새 채팅 + 짧은 turn**이 가장 확실합니다.

자세한 내용: [LMStudio_MCP_Tool_Discipline.md](docs/LMStudio_MCP_Tool_Discipline.md), [Troubleshooting.md](docs/Troubleshooting.md).

전체 요구사항, Mac remote setup, model profile, security note는 [Project_Overview.md](docs/Project_Overview.md)에 정리되어 있습니다.

## 주요 문서

| 주제 | 파일 |
|---|---|
| 상세 프로젝트 개요 | [docs/Project_Overview.md](docs/Project_Overview.md) |
| 모델 측정 결과 | [docs/Model_Measurement_Results.md](docs/Model_Measurement_Results.md) |
| 버전별 성능 이력 | [docs/Version_Performance_History.md](docs/Version_Performance_History.md) |
| 36-case holdout 난이도 | [docs/Holdout_Case_Difficulty.md](docs/Holdout_Case_Difficulty.md) |
| v1.3.0 로드맵 | [docs/Roadmap_1_3_0.md](docs/Roadmap_1_3_0.md) |
| 평가 claim guardrail | [docs/Evaluation_Claim_Guardrail.md](docs/Evaluation_Claim_Guardrail.md) |
| Sonnet 5 gap plan | [docs/Sonnet5_Gap_Plan.md](docs/Sonnet5_Gap_Plan.md) |
| Eval metrics / telemetry | [docs/Eval_Metrics_Sonnet5_Gap.md](docs/Eval_Metrics_Sonnet5_Gap.md) |
| Holdout eval guide | [docs/Holdout_Eval_Guide.md](docs/Holdout_Eval_Guide.md) |
| RAG setup reference | [docs/RAG_Setup.md](docs/RAG_Setup.md) |
| Mac mini / Mac Studio remote setup | [docs/Mac_Remote_Setup.md](docs/Mac_Remote_Setup.md) |
| Safe vs agent mode | [docs/Safe_Agent_Mode.md](docs/Safe_Agent_Mode.md) |
| Live eval checklist | [docs/Live_Eval_Checklist.md](docs/Live_Eval_Checklist.md) |
| Model profiles | [docs/Model_Profiles.md](docs/Model_Profiles.md) |
| LM Studio MCP tool discipline | [docs/LMStudio_MCP_Tool_Discipline.md](docs/LMStudio_MCP_Tool_Discipline.md) |
| Troubleshooting | [docs/Troubleshooting.md](docs/Troubleshooting.md) |
| Security | [SECURITY.md](SECURITY.md) |

## 요약

아직 experimental 프로젝트이지만, 이제 더 엄격하게 측정됩니다.

좁은 UE 5.8 compile-fix 작업에서는 현재 community fine-tuned Qwen 3.6 27B local workflow가 live UBT validation에서 강한 결과를 냈습니다(36/36 Pass@K, 36/36 Pass@1, 12/12 multifile Pass@1). Qwen 3.5 9B도 compact-model 결과를 저장했습니다(35/36 Pass@K, 33/36 Pass@1). 이 결과는 내부 workflow 측정이며, Claude/GPT 계열과의 일반 모델 동등성 주장이 아닙니다.

로컬 LLM으로 Unreal C++ hallucination을 줄이고 싶다면 먼저 근거를 검색하고, 그 다음 답변하거나 patch하세요. Fine-tuning은 workflow가 실제 프로젝트 error에서 측정된 뒤에 적용하는 것이 좋습니다.

---

## ☕ 프로젝트 후원

이 프로젝트가 도움이 되었다면 후원을 고려해 주세요. 개발을 계속 이어가는 데 큰 도움이 됩니다.

[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-%E2%9D%A4-red?logo=github)](https://github.com/sponsors/Parkyongseok1120)

👉 **[https://github.com/sponsors/Parkyongseok1120](https://github.com/sponsors/Parkyongseok1120)**
