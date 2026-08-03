# 1.3.0 Beta4 Release Notes

[English](#english) | [한국어](#korean)

## English

**Status:** Beta  
**Product label:** `1.3.0 Beta4`  
**SemVer/tag form:** `1.3.0-beta.4` / preferred `v1.3.0-beta.4`  
**Historical publish alias:** GitHub previously shipped this milestone as `1.3.0 RC1` / `v1.3.0-rc.1` (tag kept; label renamed to Beta4).

Beta4 advances the 1.3 line from Beta3 with hardened code-sketch claim validation and a write-path mutation semantic guard so a validated sketch cannot be replaced by a known-bad implementation at apply time. It remains a prerelease: automated repository evidence is required before tagging, while a new 1.3 live-model benchmark and physical Ubuntu/macOS Unreal certification are still pending.

### Highlights

- Treat common untyped `TArray` / `TMap` / `TSet` operations (`Add`, `Num`, `Reset`, and related stable members) as safe container claims so concise sketches stop looping on receiver-type false positives.
- Extend the shared Unreal API denylist for reverse-turn `FMath::Max(0, …)` / `Max(Next, 0)` clamps that break circular player traversal.
- Run the same denylist on prospective `write_file` / `replace_in_file` / `apply_edit_bundle` content through `mutation_semantic_guard.py`, and fail closed when the guard script is missing.
- Package and installer verification now require the mutation semantic guard artifacts, with a startup presence/Python probe on the Node agent MCP.
- Harden sketch-gate recovery guidance, phase-tool routing, and build-recovery task authorization so `known_bad` / weak failures steer a corrected revalidation instead of a no-op retry.

### Compatibility and component versions

| Component | Version |
|---|---|
| Product | 1.3.0 Beta4 |
| Node agent MCP | 0.3.3 |
| Context compactor | 0.3.5 / revision 8 |
| Portable manifest | 2.1.2 |

Python 3.10+, Node.js 20+, and LM Studio 0.4+ are required according to the selected installer profile. Unreal Engine 5.x support uses a user-built index; UE 5.8 remains the primary validated knowledge target.

### Evidence boundary

- The latest saved live-model measurements remain the v1.2.5 UE 5.8 holdouts: Qwen 3.6 27B community fine-tune at 36/36 Pass@K and 36/36 Pass@1, and the saved Qwen 3.5 9B runs.
- Those results are historical baselines, not a measured 1.3.0 Beta4 performance uplift.
- Beta4 sketch, mutation-guard, routing, installer, and release changes currently have automated test evidence. A fresh paired live-model benchmark is required before publishing model-quality improvement percentages.
- Fixture-tested Ubuntu/macOS paths are not equivalent to live certification on physical Unreal installations.

### Known Beta limitations

- Architecture graphs are conservative static evidence; virtual dispatch, reflection-driven behavior, runtime asset state, and dynamic Blueprint behavior may require Editor metadata or live inspection.
- AGENT authority remains opt-in and must only be enabled for trusted projects.
- Ollama frontend support, frontend-parity measurements, advanced runtime behavior oracles, and physical Ubuntu/macOS certification remain roadmap work.

## Korean

**상태:** Beta  
**제품 표기:** `1.3.0 Beta4`  
**SemVer/tag 표기:** `1.3.0-beta.4` / 권장 `v1.3.0-beta.4`  
**과거 배포 별칭:** GitHub에는 이 마일스톤이 `1.3.0 RC1` / `v1.3.0-rc.1`로 게시된 적이 있습니다(태그는 유지, 표기는 Beta4로 변경).

Beta4는 Beta3 이후 code-sketch claim 검증을 강화하고, 검증된 시안이 적용 시점에 known-bad 구현으로 바뀌지 않도록 write-path mutation semantic guard를 추가합니다. prerelease이며, 태그 전에 저장소 CI Green이 필요하고 1.3 전용 live-model benchmark와 실제 Ubuntu/macOS Unreal 인증은 아직 남아 있습니다.

### 주요 변경

- 수신자 타입이 생략된 일반 `TArray` / `TMap` / `TSet` 연산(`Add`, `Num`, `Reset` 등)을 safe container claim으로 처리해 sketch가 오탐 심볼 조회로 루프하지 않게 했습니다.
- 원형 턴 순회를 깨는 reverse-turn `FMath::Max(0, …)` / `Max(Next, 0)` clamp 패턴을 공유 denylist에 추가했습니다.
- `write_file` / `replace_in_file` / `apply_edit_bundle`의 예상 결과에 동일 denylist를 `mutation_semantic_guard.py`로 적용하고, guard 스크립트가 없으면 fail-closed로 차단합니다.
- 패키지/설치 검증이 mutation semantic guard 산출물을 필수로 요구하며, Node agent MCP 시작 시 존재·Python probe를 수행합니다.
- sketch-gate recovery, phase-tool routing, build-recovery task authorization을 강화해 `known_bad`/weak 실패가 무변경 재시도 대신 수정된 재검증으로 이어지게 했습니다.

### 호환성과 컴포넌트 버전

| 컴포넌트 | 버전 |
|---|---|
| 제품 | 1.3.0 Beta4 |
| Node agent MCP | 0.3.3 |
| Context compactor | 0.3.5 / revision 8 |
| Portable manifest | 2.1.2 |

선택한 설치 profile에 따라 Python 3.10+, Node.js 20+, LM Studio 0.4+가 필요합니다. Unreal Engine 5.x 지식은 사용자가 직접 구축한 index를 사용하며, UE 5.8이 주 검증 대상입니다.

### 근거 범위

- 최신 저장 live-model 측정치는 여전히 v1.2.5 UE 5.8 holdout입니다. Qwen 3.6 27B community fine-tune은 36/36 Pass@K와 36/36 Pass@1을 기록했고 Qwen 3.5 9B 측정도 저장되어 있습니다.
- 이 결과는 과거 baseline이며 1.3.0 Beta4의 성능 향상 측정치가 아닙니다.
- Beta4의 sketch, mutation-guard, routing, 설치기, 릴리스 변경에는 현재 자동 테스트 근거가 있습니다. 모델 품질 개선률을 공개하려면 새로운 paired live-model benchmark가 필요합니다.
- fixture에서 검증한 Ubuntu/macOS 경로는 실제 Unreal 설치 환경의 live 인증과 동일하지 않습니다.

### Beta 제한사항

- Architecture graph는 보수적인 static evidence입니다. virtual dispatch, reflection 기반 동작, runtime asset state, 동적 Blueprint 동작은 Editor metadata나 live inspection이 필요할 수 있습니다.
- AGENT 권한은 계속 opt-in이며 신뢰하는 프로젝트에서만 활성화해야 합니다.
- Ollama frontend, frontend parity 측정, advanced runtime behavior oracle, 실제 Ubuntu/macOS 인증은 roadmap에 남아 있습니다.
