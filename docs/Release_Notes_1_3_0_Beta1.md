# 1.3.0 Beta1 Release Notes

[English](#english) | [한국어](#korean)

## English

**Status:** Beta  
**Product label:** `1.3.0 Beta1`  
**SemVer/tag form:** `1.3.0-beta.1` / `v1.3.0-beta.1`

This beta starts the 1.3 line with a portable evidence-first analysis layer, deeper architecture and code-generation contracts, a consolidated installer, and stronger release gates. It is a prerelease: automated repository evidence is green locally, while a new 1.3 live-model benchmark and physical Linux/macOS Unreal certification are still pending.

### Highlights

- Added a project-independent `evidence-first-code-audit` skill and LM Studio MCP/preset path for causal analysis, framework semantics, data flow, state transitions, architecture, and code-generation review.
- Added project-wide symbol, dependency, call, and conservative data-flow graphs with freshness and completeness checks.
- Added architecture proposal, change-impact, and code-generation contracts that fail closed on stale/incomplete evidence, invalid cycles, unmatched symbols, and unsafe write targets.
- Added cached/compact architecture analysis, risk-tiered orchestration, architecture-first routing, and Essential-profile tool exposure.
- Consolidated installation into `INSTALL.bat`, `install.sh`, and their shared `install.py`, with interactive SAFE/AGENT authority confirmation and independent RAG depth selection.
- Added Windows, Linux, and macOS-aware installation, engine discovery, Editor export, indexing, and build launch paths. Physical Linux/macOS Unreal certification remains pending.
- Strengthened release verification, atomic RAG index/report writes, timeout handling, live-test quality gates, and regression metrics.
- Fixed Windows `cp1252` package-builder status output and added deterministic success/error regression coverage.

### Compatibility and component versions

| Component | Version |
|---|---|
| Product | 1.3.0 Beta1 |
| Node agent MCP | 0.3.0 |
| Context compactor | 0.3.2 / revision 5 |
| Portable manifest | 2.1.0 |

Python 3.10+, Node.js 20+, and LM Studio 0.4+ are required according to the selected installer profile. Unreal Engine 5.x support uses a user-built index; UE 5.8 remains the primary validated knowledge target.

### Evidence boundary

- The latest saved live-model measurements remain the v1.2.5 UE 5.8 holdouts: Qwen 3.6 27B community fine-tune at 36/36 Pass@K and 36/36 Pass@1, and the saved Qwen 3.5 9B runs.
- Those results are historical baselines, not a measured 1.3.0 Beta1 performance uplift.
- Beta1 architecture, orchestration, installer, and release changes currently have automated test evidence. A fresh paired live-model benchmark is required before publishing model-quality improvement percentages.
- Fixture-tested Linux/macOS paths are not equivalent to live certification on physical Unreal installations.

### Known beta limitations

- Architecture graphs are conservative static evidence; virtual dispatch, reflection-driven behavior, runtime asset state, and dynamic Blueprint behavior may require Editor metadata or live inspection.
- AGENT authority remains opt-in and must only be enabled for trusted projects.
- Ollama frontend support, frontend-parity measurements, advanced runtime behavior oracles, and physical Linux/macOS certification remain roadmap work.

## Korean

**상태:** Beta  
**제품 표기:** `1.3.0 Beta1`  
**SemVer/tag 표기:** `1.3.0-beta.1` / `v1.3.0-beta.1`

이번 Beta는 범용 evidence-first 분석 계층, 강화된 아키텍처·코드 생성 계약, 통합 설치기, 릴리스 gate를 포함한 1.3 라인의 첫 버전입니다. 저장소 자동 검증은 로컬에서 통과했지만, 1.3 전용 live-model benchmark와 실제 Linux/macOS Unreal 환경 인증은 아직 남아 있는 prerelease입니다.

### 주요 변경

- 특정 프로젝트에 종속되지 않는 `evidence-first-code-audit` skill과 LM Studio MCP/preset 경로를 추가했습니다. 인과 분석, framework semantics, data flow, state transition, architecture, code generation 검토를 지원합니다.
- 프로젝트 전역 symbol, dependency, call, 보수적 data-flow graph와 freshness/completeness 검사를 추가했습니다.
- 오래되거나 불완전한 근거, 잘못된 cycle, 매칭되지 않는 symbol, 안전하지 않은 write target에서 닫힌 상태로 실패하는 architecture proposal, change-impact, code-generation contract를 추가했습니다.
- architecture cache/compact 응답, risk-tier orchestration, architecture-first routing, Essential profile tool 노출을 추가했습니다.
- 설치 진입점을 `INSTALL.bat`, `install.sh`, 공통 `install.py`로 통합하고 SAFE/AGENT 권한 확인과 RAG depth 선택을 분리했습니다.
- Windows, Linux, macOS별 설치, engine 탐색, Editor export, indexing, build 경로를 구현했습니다. 실제 Linux/macOS Unreal 인증은 아직 남아 있습니다.
- release verification, atomic RAG index/report write, timeout, live-test quality gate, regression metric을 강화했습니다.
- Windows `cp1252` 환경의 package-builder 상태 출력 실패를 수정하고 성공·오류 회귀 테스트를 추가했습니다.

### 호환성과 컴포넌트 버전

| 컴포넌트 | 버전 |
|---|---|
| 제품 | 1.3.0 Beta1 |
| Node agent MCP | 0.3.0 |
| Context compactor | 0.3.2 / revision 5 |
| Portable manifest | 2.1.0 |

선택한 설치 profile에 따라 Python 3.10+, Node.js 20+, LM Studio 0.4+가 필요합니다. Unreal Engine 5.x 지식은 사용자가 직접 구축한 index를 사용하며, UE 5.8이 주 검증 대상입니다.

### 근거 범위

- 최신 저장 live-model 측정치는 여전히 v1.2.5 UE 5.8 holdout입니다. Qwen 3.6 27B community fine-tune은 36/36 Pass@K와 36/36 Pass@1을 기록했고 Qwen 3.5 9B 측정도 저장되어 있습니다.
- 이 결과는 과거 baseline이며 1.3.0 Beta1의 성능 향상 측정치가 아닙니다.
- Beta1의 아키텍처, 오케스트레이션, 설치기, 릴리스 변경에는 현재 자동 테스트 근거가 있습니다. 모델 품질 개선률을 공개하려면 새로운 paired live-model benchmark가 필요합니다.
- fixture에서 검증한 Linux/macOS 경로는 실제 Unreal 설치 환경의 live 인증과 동일하지 않습니다.

### Beta 제한사항

- Architecture graph는 보수적인 static evidence입니다. virtual dispatch, reflection 기반 동작, runtime asset state, 동적 Blueprint 동작은 Editor metadata나 live inspection이 필요할 수 있습니다.
- AGENT 권한은 계속 opt-in이며 신뢰하는 프로젝트에서만 활성화해야 합니다.
- Ollama frontend, frontend parity 측정, advanced runtime behavior oracle, 실제 Linux/macOS 인증은 roadmap에 남아 있습니다.
