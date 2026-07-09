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
> v1.2.5는 1.2 라인의 마지막 minor release로 봅니다. 이후 1.2.x 업데이트가 있다면 간단한 bug fix, 문서 수정, 낮은 위험도의 안정화 patch로 제한합니다. v1.3.0은 약 **3개월 뒤**를 목표로 하며, C++ capability, semantic-refactor, runtime-debug, negative-control scorecard를 분리하는 방향입니다.
>
> 넓은 양해 부탁드립니다.

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
| Qwen 3.6 27B | 36/36 | 36/36 | `20260709-144441-pass1-target` |
| Qwen 3.5 9B | 35/36 | 33/36 | `20260709-153021-qwen35-9b` |

| 모델 / run | Live 측정 시간 |
|---|---:|
| Qwen 3.6 27B | 약 33분 37초 |
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
| Troubleshooting | [docs/Troubleshooting.md](docs/Troubleshooting.md) |
| Security | [SECURITY.md](SECURITY.md) |

## 요약

아직 experimental 프로젝트이지만, 이제 더 엄격하게 측정됩니다.

좁은 UE 5.8 compile-fix 작업에서는 현재 Qwen 3.6 27B local workflow가 live UBT validation에서 강한 결과를 냈습니다(36/36 Pass@K, 36/36 Pass@1, 12/12 multifile Pass@1). Qwen 3.5 9B도 compact-model 결과를 저장했습니다(35/36 Pass@K, 33/36 Pass@1). 이 결과는 내부 workflow 측정이며, Claude/GPT 계열과의 일반 모델 동등성 주장이 아닙니다.

로컬 LLM으로 Unreal C++ hallucination을 줄이고 싶다면 먼저 근거를 검색하고, 그 다음 답변하거나 patch하세요. Fine-tuning은 workflow가 실제 프로젝트 error에서 측정된 뒤에 적용하는 것이 좋습니다.

---

## ☕ 프로젝트 후원

이 프로젝트가 도움이 되었다면 후원을 고려해 주세요. 개발을 계속 이어가는 데 큰 도움이 됩니다.

[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-%E2%9D%A4-red?logo=github)](https://github.com/sponsors/Parkyongseok1120)

👉 **[https://github.com/sponsors/Parkyongseok1120](https://github.com/sponsors/Parkyongseok1120)**
