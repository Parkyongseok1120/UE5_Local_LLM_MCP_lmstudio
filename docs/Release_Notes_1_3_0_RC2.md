# 1.3.0 RC2 — control-plane stabilization prerelease

**Status: GitHub prerelease; not stable/release-ready.** The published `v1.3.0-rc2` tag is an immutable snapshot. This document also records the subsequent RC2-line hardening on `Develop`; those follow-up changes are not retroactively part of the existing tag. `installer/manifest.json` keeps `portablePackage.releaseReady` at **`false`** because Windows physical installation and the remaining stable-release gates are not complete.

The release tag is **`v1.3.0-rc2`**. The distinct spelling is intentional: historical tag `v1.3.0-rc.2` remains attached to the milestone now documented as Beta5 and is not moved.

## Component versions

| Component | Published `v1.3.0-rc2` | Current `Develop` |
|---|---|---|
| Product | 1.3.0 RC2 | 1.3.0 RC2 line |
| Portable manifest | 2.1.3 | 2.1.4 |
| Node agent MCP | 0.3.15 | 0.3.16 |
| Context compactor | 0.4.39 / revision 85 | 0.4.40 / revision 86 |

## What changed

### Published RC2 snapshot

- A single `derive_next_obligation()` transition table owns phase, disposition, required tool, allowed tools, blockers, and retry policy.
- `commit_control_transition()` advances `controlEpoch` only when the semantic control fingerprint changes.
- Task-owned results are finalized from the latest authoritative state instead of reconstructing next action from stale public payload fields.
- Successful gates are idempotent within their plan/slice/input/snapshot/mutation generation and redirect repeated calls to the current obligation without rerunning validation.
- Replay tests cover `Code Sketch → Mutation → Static Validate → Build → Automation → Complete`, including repeat-success redirects at every boundary.
- Python and Node control projections share adversarial parity fixtures; runtime identity checks detect stale installed components.
- Feature-intent fast paths use risk and project-convention evidence instead of one fixture project's layout.

### Post-tag `Develop` hardening

- Static, Build, Automation, and gate failures now enter one persisted `recoveryObligation` lifecycle: bounded evidence, repair planning, mutation, revalidation, rebuild, and completion. Handler-local advice can no longer silently diverge from task control.
- Static validation and Automation discovery are bound to the active slice. Automation uses exact declaration filters, recognizes the classic Automation, Spec, and CQTest declaration families, includes dependent test modules, and fails closed when scope coverage is incomplete or truncated.
- Required arguments are enforced as a server-owned subset: exact server fields and ordered filter lists must match, while harmless optional caller fields remain allowed. Build and Automation proof is also bound to the task's canonical `.uproject` and resolved engine; a different project can never complete the active slice. Path identity follows host Windows/POSIX semantics instead of assuming every OS is case-insensitive.
- Mutation bookkeeping uses write-ahead post-image hashes, batch receipts, and journal recovery so disk contents, mutation generations, and task checkpoints can be reconciled after rollback, an expired task lease, or interruption at any persisted write stage.
- Model-facing plans no longer describe Build as unconditionally terminal. They follow the latest server-owned post-Build control: `Build → Automation → Complete` when Automation is declared or required, otherwise `Build → Complete`.
- Portable manifest 2.1.4 makes the new path-identity, recovery-log, and Unreal source-extension runtime modules mandatory package inputs so a pre-commit package check cannot omit them silently.

## Failure-path policy

Recovery remains deliberately bounded rather than maximally strict. A source or test failure gets one exact evidence/repair path; an infrastructure failure gets one corrected retry; an unchanged repeated failure becomes a stable blocker instead of an infinite tool loop. Out-of-slice and unmappable coverage is reported for user direction and never converted into a speculative edit.

The implementation contains no dependency on either named local test project. Project, plugin, module, engine, platform, and path resolution come from the active `.uproject`, task slice, host filesystem, and Unreal build metadata. Automated fixtures exercise game modules, plugin modules, separate test modules, Windows/POSIX path rules, and multiple UE 5.x engine associations.

## Model-side bottleneck

RC2 deliberately makes server control more deterministic, but the model-facing contract is also denser. Once the server publishes one exact next obligation, the remaining failures increasingly depend on whether the local model can:

- retain the active slice, evidence, blocker, and recovery state across a long tool history;
- choose the required tool instead of repeating a recently successful tool;
- emit exact arguments for a narrow schema; and
- recover after a rejected gate without drifting into an older obligation.

This is an **observed limitation, not a new benchmark result**. Some earlier repetitions were server/control bugs and are addressed by RC2; the remaining model-side share has not been isolated or quantified by a paired live-model run. Smaller profiles, especially 9B-class models, should be used for bounded tasks with known targets. Autonomous multi-step work should prefer a stronger 24B–27B tool-calling profile and a fresh chat when the model-facing history is saturated.

## Verification boundary

- GUI E2E was intentionally not run for this release cycle.
- Automated transition replay, idempotence, epoch monotonicity, Python/Node parity, installer/package, OSS hygiene, pytest, and both Node suites are release gates.
- The final code state must complete ten independent green verification rounds; any production change or newly exposed defect restarts that count.
- CI exercises Windows, Ubuntu, and macOS contracts. Source fixtures cover supported UE 5.4–5.8 engine binding and project-independent paths.
- These automated matrices do **not** prove every physical host, Unreal project, plugin combination, or editor runtime. Apple Silicon physical FULL install evidence is inherited from RC1; Windows physical install remains unverified.
- No RC2 live-model uplift or new Pass@1/Pass@K value is claimed. Published model numbers remain historical v1.2.5 baselines.

## 한국어 요약

RC2는 개별 단계 핫픽스가 아니라 상태 전이와 공개 next-action을 하나의 서버 소유 계약으로 중앙화한 프리릴리스입니다. 성공한 gate의 동일 입력 재실행을 막고, Code Sketch부터 Complete까지 후반부 전환을 자동 replay로 검증합니다.

Static·Build·Automation·gate 실패도 하나의 `recoveryObligation`으로 저장되어 근거 수집 → 수리 계획 → 변경 → 재검증 → 재빌드 순서를 따릅니다. Static과 Automation 범위는 현재 slice에 묶이고, 별도 테스트 모듈과 플러그인 모듈도 의존 관계로 찾습니다. Build·Automation 증명은 task의 실제 `.uproject`와 엔진에 결박되어 다른 프로젝트의 성공으로 현재 slice를 완료할 수 없습니다. 서버가 지정한 필드는 정확히 맞춰야 하지만 무해한 선택 인자까지 금지하지는 않습니다. Windows와 POSIX의 경로 대소문자 규칙도 구분합니다.

서버가 정확한 다음 도구를 강제해도 작은 로컬 모델은 긴 근거·복구 상태 유지, 정확한 tool schema 생성, 실패 후 obligation 복귀에서 병목이 될 수 있습니다. 이 한계는 관찰됐지만 RC2에서 새로 수치화하지 않았습니다. GUI E2E도 실행하지 않았으므로 자동 테스트 범위를 넘어선 완주나 모든 실제 프로젝트·OS·언리얼 조합의 물리 호환성을 주장하지 않습니다.
