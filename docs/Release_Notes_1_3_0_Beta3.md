# 1.3.0 Beta3 Release Notes

[English](#english) | [한국어](#korean)

## English

**Status:** Beta  
**Product label:** `1.3.0 Beta3`  
**SemVer/tag form:** `1.3.0-beta.3` / `v1.3.0-beta.3`

Beta3 advances the 1.3 prerelease line with a portable evidence-first analysis layer, deeper architecture and code-generation contracts, a consolidated installer, and stability-focused agent/context recovery. It remains a prerelease: automated repository evidence is green locally, while a new 1.3 live-model benchmark and physical Ubuntu/macOS Unreal certification are still pending.

### Highlights

- Added a project-independent `evidence-first-code-audit` skill and LM Studio MCP/preset path for causal analysis, framework semantics, data flow, state transitions, architecture, and code-generation review.
- Added project-wide symbol, dependency, call, and conservative data-flow graphs with freshness and completeness checks.
- Added architecture proposal, change-impact, and code-generation contracts that fail closed on stale/incomplete evidence, invalid cycles, unmatched symbols, and unsafe write targets.
- Added cached/compact architecture analysis, risk-tiered orchestration, architecture-first routing, and Essential-profile tool exposure.
- Consolidated installation into `INSTALL.bat`, `install.sh`, and their shared `install.py`, with interactive SAFE/AGENT authority confirmation and independent RAG depth selection.
- Added Windows, Ubuntu Linux, and macOS-aware installation, engine discovery, Editor export, indexing, and build launch paths. Physical Ubuntu/macOS Unreal certification remains pending.
- Hardened Windows, macOS, and Ubuntu bootstrap paths with component-scoped runtime downloads, a validated runtime manifest (URL/filename/platform/architecture/SHA-256/probe), bounded traversal-safe extraction, post-install executable probes, concurrent-bootstrap locking, semantic Unreal version selection, and Epic Launcher manifest discovery.
- Normalized portable RAG index separators on every host and ignored stale workspace roots copied from another machine, including the fresh-install case where `workspace.json` does not exist yet.
- Fixed the consolidated `--build-rag` pipeline to collect project guidelines and game-design inputs before indexing, preventing a healthy-looking index from silently omitting compile, Blueprint, and project-profile retrieval guidance.
- Strengthened release verification, atomic RAG index/report writes, timeout handling, live-test quality gates, and regression metrics.
- Hardened the context proxy with per-turn durable checkpoints, atomic output buffering, truncated-prediction rejection, fresh activation evidence, and bounded session-directory retention. Direct Qwen/GPT selection remains write-capable; strict proxy startup is an explicit LM Studio-only policy rather than a cross-frontend prerequisite.
- Classified recoverable task-auth/route failures without terminating the user workflow, rejected fabricated authorization with a server-issued-plan recovery route, and added `tail`, `first_error`, and cursor/range Unreal log reads.
- Added automatic post-mutation task checkpoints, refreshed route authorization in write responses, explicit skipped-validation advisories, and non-retry recovery guidance after post-write bookkeeping failures.
- Bounded activation-telemetry scanning and rejected stale or future-dated proxy evidence so corrupt clocks and oversized histories cannot silently authorize AGENT work.
- Fixed Windows `cp1252` package-builder status output and added deterministic success/error regression coverage.

### Compatibility and component versions

| Component | Version |
|---|---|
| Product | 1.3.0 Beta3 |
| Node agent MCP | 0.3.2 |
| Context compactor | 0.3.5 / revision 8 |
| Portable manifest | 2.1.1 |

Python 3.10+, Node.js 20+, and LM Studio 0.4+ are required according to the selected installer profile. Unreal Engine 5.x support uses a user-built index; UE 5.8 remains the primary validated knowledge target.

### Evidence boundary

- The latest saved live-model measurements remain the v1.2.5 UE 5.8 holdouts: Qwen 3.6 27B community fine-tune at 36/36 Pass@K and 36/36 Pass@1, and the saved Qwen 3.5 9B runs.
- Those results are historical baselines, not a measured 1.3.0 Beta3 performance uplift.
- Beta3 architecture, orchestration, installer, and release changes currently have automated test evidence. A fresh paired live-model benchmark is required before publishing model-quality improvement percentages.
- Fixture-tested Ubuntu/macOS paths are not equivalent to live certification on physical Unreal installations.

### Known beta limitations

- Architecture graphs are conservative static evidence; virtual dispatch, reflection-driven behavior, runtime asset state, and dynamic Blueprint behavior may require Editor metadata or live inspection.
- AGENT authority remains opt-in and must only be enabled for trusted projects.
- Ollama frontend support, frontend-parity measurements, advanced runtime behavior oracles, and physical Ubuntu/macOS certification remain roadmap work.

## Korean

**상태:** Beta  
**제품 표기:** `1.3.0 Beta3`  
**SemVer/tag 표기:** `1.3.0-beta.3` / `v1.3.0-beta.3`

Beta3는 범용 evidence-first 분석 계층, 강화된 아키텍처·코드 생성 계약, 통합 설치기, 안정성 중심의 agent/context 복구를 포함하도록 1.3 prerelease 라인을 발전시킵니다. 저장소 자동 검증은 로컬에서 통과했지만, 1.3 전용 live-model benchmark와 실제 Ubuntu/macOS Unreal 환경 인증은 아직 남아 있습니다.

### 주요 변경

- 특정 프로젝트에 종속되지 않는 `evidence-first-code-audit` skill과 LM Studio MCP/preset 경로를 추가했습니다. 인과 분석, framework semantics, data flow, state transition, architecture, code generation 검토를 지원합니다.
- 프로젝트 전역 symbol, dependency, call, 보수적 data-flow graph와 freshness/completeness 검사를 추가했습니다.
- 오래되거나 불완전한 근거, 잘못된 cycle, 매칭되지 않는 symbol, 안전하지 않은 write target에서 닫힌 상태로 실패하는 architecture proposal, change-impact, code-generation contract를 추가했습니다.
- architecture cache/compact 응답, risk-tier orchestration, architecture-first routing, Essential profile tool 노출을 추가했습니다.
- 설치 진입점을 `INSTALL.bat`, `install.sh`, 공통 `install.py`로 통합하고 SAFE/AGENT 권한 확인과 RAG depth 선택을 분리했습니다.
- Windows, Ubuntu Linux, macOS별 설치, engine 탐색, Editor export, indexing, build 경로를 구현했습니다. 실제 Ubuntu/macOS Unreal 인증은 아직 남아 있습니다.
- Windows, macOS, Ubuntu bootstrap에 컴포넌트별 runtime 다운로드, 검증 가능한 runtime manifest(URL/file/platform/architecture/SHA-256/probe), traversal·용량 제한 안전 추출, 설치 후 실행 검사, 동시 bootstrap lock, Unreal semantic version 선택, Epic Launcher manifest 탐색을 추가했습니다.
- 모든 host에서 portable RAG index 경로 구분자를 정규화하고, 다른 장비에서 복사되어 더 이상 존재하지 않는 workspace root를 무시하도록 했습니다. `workspace.json`이 아직 없는 fresh-install 경로도 포함됩니다.
- 통합 `--build-rag` 파이프라인이 인덱스 생성 전에 프로젝트 guideline과 game-design 입력을 수집하도록 수정하여, 정상처럼 보이는 인덱스에서 compile·Blueprint·project-profile 검색 지침이 조용히 누락되는 문제를 막았습니다.
- release verification, atomic RAG index/report write, timeout, live-test quality gate, regression metric을 강화했습니다.
- 컨텍스트 프록시에 매 턴 영속 체크포인트, 원자적 출력 버퍼링, 잘린 응답 거부, 최근 활성 증거, 제한된 세션 디렉터리 보존 정책을 추가했습니다. Qwen/GPT 직접 선택은 계속 쓰기 가능하며 strict proxy 시작 조건은 모든 frontend의 필수 조건이 아니라 명시적인 LM Studio 전용 정책입니다.
- 복구 가능한 task-auth/route 오류가 전체 작업을 종료하지 않도록 분류하고, 조작된 authorization을 server-issued plan 복구 경로로 거부하며, Unreal 로그의 `tail`/`first_error`/cursor-range 읽기를 추가했습니다.
- 모든 성공한 변경 뒤 자동 task checkpoint를 기록하고, 쓰기 응답에 갱신된 route authorization을 반환하며, 검증 생략을 명시적 advisory로 표시하고, post-write bookkeeping 실패 시 중복 쓰기를 막는 복구 지침을 추가했습니다.
- 활성 텔레메트리 탐색량에 상한을 두고 오래되거나 미래 시각인 프록시 증거를 거부하여 잘못된 시스템 시각과 과도한 이벤트 기록이 AGENT 작업을 잘못 허용하지 않도록 했습니다.
- Windows `cp1252` 환경의 package-builder 상태 출력 실패를 수정하고 성공·오류 회귀 테스트를 추가했습니다.

### 호환성과 컴포넌트 버전

| 컴포넌트 | 버전 |
|---|---|
| 제품 | 1.3.0 Beta3 |
| Node agent MCP | 0.3.2 |
| Context compactor | 0.3.5 / revision 8 |
| Portable manifest | 2.1.1 |

선택한 설치 profile에 따라 Python 3.10+, Node.js 20+, LM Studio 0.4+가 필요합니다. Unreal Engine 5.x 지식은 사용자가 직접 구축한 index를 사용하며, UE 5.8이 주 검증 대상입니다.

### 근거 범위

- 최신 저장 live-model 측정치는 여전히 v1.2.5 UE 5.8 holdout입니다. Qwen 3.6 27B community fine-tune은 36/36 Pass@K와 36/36 Pass@1을 기록했고 Qwen 3.5 9B 측정도 저장되어 있습니다.
- 이 결과는 과거 baseline이며 1.3.0 Beta3의 성능 향상 측정치가 아닙니다.
- Beta3의 아키텍처, 오케스트레이션, 설치기, 릴리스 변경에는 현재 자동 테스트 근거가 있습니다. 모델 품질 개선률을 공개하려면 새로운 paired live-model benchmark가 필요합니다.
- fixture에서 검증한 Ubuntu/macOS 경로는 실제 Unreal 설치 환경의 live 인증과 동일하지 않습니다.

### Beta 제한사항

- Architecture graph는 보수적인 static evidence입니다. virtual dispatch, reflection 기반 동작, runtime asset state, 동적 Blueprint 동작은 Editor metadata나 live inspection이 필요할 수 있습니다.
- AGENT 권한은 계속 opt-in이며 신뢰하는 프로젝트에서만 활성화해야 합니다.
- Ollama frontend, frontend parity 측정, advanced runtime behavior oracle, 실제 Ubuntu/macOS 인증은 roadmap에 남아 있습니다.
