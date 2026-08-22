# Project Overview and Detailed README

[English](#english) | [한국어](#korean)

## English

This page holds the detailed project status and usage notes that used to live in the README. The README is intentionally kept short and links here through badge-style buttons.

## Current Status

The current product label is **1.3.0 RC3** (GitHub prerelease; not stable/release-ready until Windows physical install validation and remaining release gates close; `releaseReady` remains false). Apple Silicon physical FULL install is recorded as PASS in [RC3 notes](Release_Notes_1_3_0_RC3.md). It packages centralized task control, persisted recovery, atomic mutation rollback, canonical project/build proof, host-aware path identity, portable evidence-first analysis, and cross-platform integrated installation.

The latest measured live-model results remain the v1.2.5 baseline. RC3 exposes more model-side pressure from long evidence/recovery state and exact tool schemas after server handoffs become deterministic, but no paired run quantifies that effect; do not claim a measured model-quality uplift.

See also: [Live Validation Results — 2026-07-11](Live_Validation_Results_20260711.md) (Project_MJS scoped smoke + post-stabilization 36-case live run). [9B Domain Expansion Roadmap](Roadmap_9B_Domain_Expansion.md).

| Run | Artifact | Wall-clock time | Pass@K | Pass@1 | Notes |
|---|---|---:|---:|---:|---|
| Qwen 3.6 27B community fine-tune live holdout | `20260709-144441-pass1-target` | ~33m 37s | 36/36 | 36/36 | Best v1.2.5 historical baseline. `wrong_file_edits=0`, `build_cs_false_positives=0`, `no_op_edits=0`. |
| Qwen 3.5 9B live holdout (deepseek-v4-flash) | `20260711-090534-qwen35-9b` | ~26m 34s | 36/36 | 35/36 | Post scoped-write-stabilization. Pass@1 miss: `local_component_registration_missing_include`. |
| Qwen 3.5 9B live holdout | `20260709-153021-qwen35-9b` | ~27m 22s | 35/36 | 33/36 | Prior compact baseline. Failed one LNK missing-definition case. |
| Dry-run compile gate | `20260709-142052` | ~23m 29s | 36/36 | 36/36 | Golden oracle + UBT; no LM Studio model. |

`Harness average attempts=0.389` in the best 27B run is not a general reasoning-depth metric. Static autofix successes can count as `attempts=0`, so it means many cases were solved before an LLM edit attempt.

## What Changed in 1.3.0 Beta3

- Added a project-independent `evidence-first-code-audit` skill plus LM Studio MCP/preset integration.
- Added project-wide symbol, dependency, call, and conservative data-flow graphs, including freshness, completeness, truncation, and missing-evidence checks.
- Added fail-closed architecture proposal, change-impact, and code-generation contracts that guard cycles, unmatched symbols, unsafe targets, and edit scope.
- Added cached/compact architecture analysis, Essential-profile exposure, risk-tiered planning, and architecture-first orchestration.
- Consolidated user installation into `INSTALL.bat`, `install.sh`, and shared `install.py`, including explicit SAFE/AGENT authority confirmation and independent RAG depth selection.
- Added host-aware Windows/Ubuntu/macOS engine discovery, indexing, Editor export, and build launch paths. Physical Ubuntu/macOS Unreal certification is still pending.
- Strengthened atomic RAG builds, release verification, timeouts, live-test quality gates, sampling-profile resolution, and regression metrics.
- Added durable post-mutation checkpoints, refreshed route authorization, visible skipped-validation advisories, fail-closed proxy activation checks, and bounded telemetry diagnostics.
- Fixed package-builder JSON output on legacy Windows encodings and added forced-`cp1252` success/error regression coverage.

See [1.3.0 RC3 notes](Release_Notes_1_3_0_RC3.md) for the evidence boundary and component versions.

## Minimum Requirements

| | Minimum | Recommended |
|---|---|---|
| OS | Windows 10/11 | Windows 11 |
| RAM | 16 GB | 32 GB+ |
| GPU VRAM | 8 GB for 7-9B Q4 | 16 GB+ for 20-27B Q4 |
| Free disk | ~30 GB | 100 GB+ |
| CPU | 6-core modern CPU | 8-core+ |

Required software:

- Python 3.10+
- Node.js 20+
- LM Studio 0.4+
- Licensed Unreal Engine 5.x, with UE 5.8 recommended

## Quick Install

```powershell
git clone https://github.com/Parkyongseok1120/UE5_Local_LLM_MCP_lmstudio.git
cd UE5_Local_LLM_MCP_lmstudio
.\INSTALL.bat
.\rag.ps1 doctor
```

Build the RAG index:

```powershell
python install.py --profile standard --yes --build-rag
python install.py --profile standard --yes --build-rag --enable-agent-mode --accept-agent-risk
```

Use safe mode first unless you intentionally want MCP file writes, commands, and Unreal builds enabled.

## Common Workflows

```powershell
.\rag.ps1 collect-source
.\rag.ps1 collect-projects -CopyProjectText
.\rag.ps1 collect-symbols
.\rag.ps1 collect-module-graph
.\rag.ps1 build
.\rag.ps1 query -Question "How do I create a UActorComponent in C++?"
```

With LM Studio Local Server running:

```powershell
.\rag.ps1 lmstudio-models
.\rag.ps1 ask -Question "Show me a C++ example of attaching a custom Component to an Actor"
```

## Mac Remote Server

LM Studio can run on a Mac mini / Mac Studio while Windows handles UE / UBT / this project. See [Mac_Remote_Setup.md](Mac_Remote_Setup.md).

## Safe vs Agent Mode

Default install is read-only safe mode (`ALLOW_WRITE=0`). Enable file writes and UBT only when you trust the project:

```powershell
python install.py --profile standard --yes --enable-agent-mode --accept-agent-risk
python install.py --profile standard --yes
```

Never enable agent mode for untrusted project paths.

## Files You Must Never Commit

| File | Why |
|---|---|
| `config/workspace.json` | Public placeholder / installer-generated local config; keep real local paths out of commits |
| `config/workspace.local.json` | Optional ignored local override pattern for private machine paths |
| `lmstudio-unreal-agent-mcp/config/agent-mcp.json` | Generated by installer; contains your local paths |
| `PORTABLE_ROOT.txt` | Generated by installer; contains your username and Python path |
| `data/` | RAG indexes and eval artifacts; may contain Epic source excerpts |

## Korean

이 페이지는 README에 있던 상세 프로젝트 현황과 사용법을 옮겨둔 문서입니다. README는 짧게 유지하고, 배지 형태의 링크로 이 문서를 연결합니다.

## 현재 상태

현재 제품 라벨은 **1.3.0 RC3**입니다(GitHub 프리릴리스; Windows 실기 설치와 남은 gate가 끝나기 전까지 stable 배포 불가). 중앙 task-control transition, persisted recovery, 원자적 mutation rollback, canonical project/build proof, host-aware path identity를 포함합니다.

최신 live-model 측정 결과는 여전히 v1.2.5 baseline입니다. RC3는 서버 handoff를 명확히 한 뒤 긴 근거·복구 상태와 정확한 tool schema를 처리하는 모델측 병목을 더 드러내지만, 새로운 paired live-model benchmark는 아직 완료되지 않았으므로 아래 표를 Beta4/Beta5/RC1/RC2/RC3 모델 품질 향상 수치로 사용하면 안 됩니다.

자세한 검증 기록: [Live Validation Results — 2026-07-11](Live_Validation_Results_20260711.md) (Project_MJS scoped smoke + stabilization 이후 36-case live run).

| Run | Artifact | 측정 시간 | Pass@K | Pass@1 | 비고 |
|---|---|---:|---:|---:|---|
| Qwen 3.6 27B community fine-tune live holdout | `20260709-144441-pass1-target` | 약 33분 37초 | 36/36 | 36/36 | v1.2.5 최고 historical baseline. `wrong_file_edits=0`, `build_cs_false_positives=0`, `no_op_edits=0`. |
| Qwen 3.5 9B live holdout (deepseek-v4-flash) | `20260711-090534-qwen35-9b` | 약 26분 34초 | 36/36 | 35/36 | scoped write stabilization 이후. Pass@1 miss 1건. |
| Qwen 3.5 9B live holdout | `20260709-153021-qwen35-9b` | 약 27분 22초 | 35/36 | 33/36 | 이전 compact baseline. LNK missing-definition 1건 실패. |
| Dry-run compile gate | `20260709-142052` | 약 23분 29초 | 36/36 | 36/36 | Golden oracle + UBT; LM Studio 모델 없음. |

최고 27B run의 `Harness average attempts=0.389`는 일반적인 모델 reasoning depth 지표가 아닙니다. Static autofix 성공은 `attempts=0`으로 기록될 수 있으므로, 많은 케이스가 LLM 편집 시도 전에 해결되었다는 뜻입니다.

## 1.3.0 Beta3 주요 변경

- 특정 프로젝트에 종속되지 않는 `evidence-first-code-audit` skill과 LM Studio MCP/preset 통합을 추가했습니다.
- 프로젝트 전역 symbol, dependency, call, 보수적 data-flow graph와 freshness, completeness, truncation, missing-evidence 검사를 추가했습니다.
- cycle, unmatched symbol, unsafe target, edit scope를 방어하는 fail-closed architecture proposal, change-impact, code-generation contract를 추가했습니다.
- architecture cache/compact 분석, Essential profile 노출, risk-tier planning, architecture-first orchestration을 추가했습니다.
- 사용자 설치 진입점을 `INSTALL.bat`, `install.sh`, 공통 `install.py`로 통합하고 SAFE/AGENT 권한 확인과 RAG depth 선택을 분리했습니다.
- Windows/Ubuntu/macOS별 engine 탐색, indexing, Editor export, build launcher 경로를 구현했습니다. 실제 Ubuntu/macOS Unreal 인증은 아직 남아 있습니다.
- atomic RAG build, release verification, timeout, live-test quality gate, sampling-profile resolution, regression metric을 강화했습니다.
- 변경 후 영속 checkpoint, 갱신된 route authorization, 검증 생략 advisory, fail-closed 프록시 활성 검사, 상한이 있는 telemetry 진단을 추가했습니다.
- legacy Windows encoding의 package-builder JSON 출력을 수정하고 강제 `cp1252` 성공·오류 회귀 테스트를 추가했습니다.

근거 범위와 컴포넌트 버전은 [1.3.0 RC3 노트](Release_Notes_1_3_0_RC3.md)를 참고하세요.

## 최소 요구사항

| | 최소 | 권장 |
|---|---|---|
| OS | Windows 10/11 | Windows 11 |
| RAM | 16 GB | 32 GB+ |
| GPU VRAM | 7-9B Q4용 8 GB | 20-27B Q4용 16 GB+ |
| 여유 디스크 | 약 30 GB | 100 GB+ |
| CPU | 현대적 6-core | 8-core+ |

필수 소프트웨어:

- Python 3.10+
- Node.js 20+
- LM Studio 0.4+
- 라이선스가 있는 Unreal Engine 5.x, UE 5.8 권장

## 빠른 설치

```powershell
git clone https://github.com/Parkyongseok1120/UE5_Local_LLM_MCP_lmstudio.git
cd UE5_Local_LLM_MCP_lmstudio
.\INSTALL.bat
.\rag.ps1 doctor
```

RAG index까지 한 번에 빌드:

```powershell
python install.py --profile standard --yes --build-rag
python install.py --profile standard --yes --build-rag --enable-agent-mode --accept-agent-risk
```

처음에는 safe mode를 권장합니다. MCP file write, command, Unreal build 권한은 신뢰하는 프로젝트에서만 켜세요.

## 자주 쓰는 Workflow

```powershell
.\rag.ps1 collect-source
.\rag.ps1 collect-projects -CopyProjectText
.\rag.ps1 collect-symbols
.\rag.ps1 collect-module-graph
.\rag.ps1 build
.\rag.ps1 query -Question "How do I create a UActorComponent in C++?"
```

LM Studio Local Server가 켜져 있을 때:

```powershell
.\rag.ps1 lmstudio-models
.\rag.ps1 ask -Question "Show me a C++ example of attaching a custom Component to an Actor"
```

## Mac Remote Server

LM Studio는 Mac mini / Mac Studio에서 실행하고, Windows PC가 UE / UBT / 이 프로젝트를 담당할 수 있습니다. [Mac_Remote_Setup.md](Mac_Remote_Setup.md)를 참고하세요.

## Safe vs Agent Mode

기본 설치는 read-only safe mode (`ALLOW_WRITE=0`)입니다. 신뢰하는 프로젝트에서만 file write와 UBT를 켜세요.

```powershell
python install.py --profile standard --yes --enable-agent-mode --accept-agent-risk
python install.py --profile standard --yes
```

신뢰하지 않는 project path에서는 agent mode를 켜지 마세요.

## 절대 커밋하면 안 되는 파일

| File | 이유 |
|---|---|
| `config/workspace.json` | installer-generated local config placeholder; 실제 local path를 commit하지 않기 위함 |
| `config/workspace.local.json` | 무시되는 local override pattern |
| `lmstudio-unreal-agent-mcp/config/agent-mcp.json` | installer가 생성하며 local path 포함 |
| `PORTABLE_ROOT.txt` | username과 Python path 포함 |
| `data/` | RAG index와 eval artifact; Epic source excerpt를 포함할 수 있음 |
