# 1.3.0 RC2 — control-plane stabilization prerelease

**Status: GitHub prerelease; not stable/release-ready.** RC2 closes the known class of task-transition/public-control divergence with a server-owned transition table and replay coverage. `installer/manifest.json` keeps `portablePackage.releaseReady` at **`false`** because Windows physical installation and the remaining stable-release gates are not complete.

The release tag is **`v1.3.0-rc2`**. The distinct spelling is intentional: historical tag `v1.3.0-rc.2` remains attached to the milestone now documented as Beta5 and is not moved.

## Component versions

| Component | Version |
|---|---|
| Product | 1.3.0 RC2 (`v1.3.0-rc2`) |
| Portable manifest | 2.1.3 |
| Node agent MCP | 0.3.15 |
| Context compactor | 0.4.39 / revision 85 |

## What changed

- A single `derive_next_obligation()` transition table owns phase, disposition, required tool, allowed tools, blockers, and retry policy.
- `commit_control_transition()` advances `controlEpoch` only when the semantic control fingerprint changes.
- Task-owned results are finalized from the latest authoritative state instead of reconstructing next action from stale public payload fields.
- Successful gates are idempotent within their plan/slice/input/snapshot/mutation generation and redirect repeated calls to the current obligation without rerunning validation.
- Replay tests cover `Code Sketch → Mutation → Static Validate → Build → Automation → Complete`, including repeat-success redirects at every boundary.
- Python and Node control projections share adversarial parity fixtures; runtime identity checks detect stale installed components.
- Feature-intent fast paths use risk and project-convention evidence instead of one fixture project's layout.

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
- CI exercises Windows, Ubuntu, and macOS contracts. Source fixtures cover supported UE 5.4–5.8 engine binding and project-independent paths.
- These automated matrices do **not** prove every physical host, Unreal project, plugin combination, or editor runtime. Apple Silicon physical FULL install evidence is inherited from RC1; Windows physical install remains unverified.
- No RC2 live-model uplift or new Pass@1/Pass@K value is claimed. Published model numbers remain historical v1.2.5 baselines.

## 한국어 요약

RC2는 개별 단계 핫픽스가 아니라 상태 전이와 공개 next-action을 하나의 서버 소유 계약으로 중앙화한 프리릴리스입니다. 성공한 gate의 동일 입력 재실행을 막고, Code Sketch부터 Complete까지 후반부 전환을 자동 replay로 검증합니다.

서버가 정확한 다음 도구를 강제해도 작은 로컬 모델은 긴 근거·복구 상태 유지, 정확한 tool schema 생성, 실패 후 obligation 복귀에서 병목이 될 수 있습니다. 이 한계는 관찰됐지만 RC2에서 새로 수치화하지 않았습니다. GUI E2E도 실행하지 않았으므로 자동 테스트 범위를 넘어선 완주나 모든 실제 프로젝트·OS·언리얼 조합의 물리 호환성을 주장하지 않습니다.
