# 1.3.0 RC3 — recovery and portability hardening prerelease

**Status: GitHub prerelease; not stable/release-ready.** RC3 packages the post-RC2 hardening that was verified on `Develop` through commit `c2504a5ce4dbfcef8bbdd493fdaed0e63cafd2cc`, plus release metadata only. `installer/manifest.json` keeps `portablePackage.releaseReady` at **`false`** because Windows physical installation and the remaining stable-release gates are not complete.

The release tag is **`v1.3.0-rc3`**. Existing RC and historical Beta-alias tags remain immutable.

## Component versions

| Component | RC3 version |
|---|---|
| Product | 1.3.0 RC3 (`v1.3.0-rc3`) |
| Portable manifest | 2.1.6 |
| Node agent MCP | 0.3.18 |
| Context compactor | 0.4.43 / revision 89 |

## What changed since the RC2 tag

- Static, Build, Automation, and failed-gate recovery now use one persisted `recoveryObligation` lifecycle instead of handler-local next-step advice.
- The server-owned transition table publishes phase, disposition, allowed tools, exact required arguments, retry policy, and semantic epoch from the latest task facts.
- Successful gates are idempotent within their task scope and redirect replays to the current authoritative obligation.
- Build and Automation proof is bound to the task's canonical project, engine, target, platform, configuration, mutation generation, and active slice.
- Mutation journals persist write-ahead post-images and compensation receipts so disk, mutation state, and task checkpoints converge after crashes or rollback.
- Automation discovery recognizes classic Automation tests, Spec, and CQTest declarations; includes dependent game/plugin test modules; uses exact filters; and fails closed on incomplete coverage.
- Path identity is host-aware across task control, journals, locks, validation, RAG, project discovery, engine discovery, and compaction: POSIX preserves distinct spellings while Windows uses ASCII case folding plus real-path aliases.
- Package gates require the path-identity, recovery-log, Unreal source-extension, and RC3 release-note runtime inputs.
- Windows, Ubuntu, and macOS CI fixtures now respect filesystem alias capabilities without weakening ownership or isolation assertions.

## Bounded failure policy

RC3 does not make every optional argument or recovery path maximally strict. Server-owned proof fields must match exactly, while harmless caller options remain allowed. Source/test failures receive one bounded evidence-and-repair path, infrastructure failures receive one corrected retry, and unchanged repeated failures become stable blockers rather than infinite loops.

Project and engine selection is derived from the active `.uproject`, module/plugin metadata, task slice, and host filesystem. No production path depends on a named test fixture, a single project layout, or a developer-local absolute directory.

## Model-side bottleneck

The deterministic server control plane removes known stale-transition loops, but it also makes the remaining local-model limits easier to see. Smaller models can still lose long evidence/recovery state, repeat a recently successful tool, or fail to emit an exact narrow schema. This is an **observed limitation, not a new benchmark result**. No RC3 live-model uplift or new Pass@1/Pass@K value is claimed; published scores remain historical v1.2.5 baselines.

Use smaller profiles for bounded tasks with known targets. Long autonomous multi-step work should prefer a stronger tool-calling profile and a fresh task when model-facing history is saturated.

## Verification evidence and boundary

- Frozen-tree verification completed ten green rounds after the last code/test fix.
- Full Python suite: `1952 passed, 12 skipped`.
- Agent MCP: `337 passed, 5 skipped`; Context Compactor: `172 passed` including TypeScript build.
- Transition, atomic rollback, cross-platform, package, hardening, domain, OSS, encoding, syntax, and 10-repeat gates all passed.
- GitHub Actions run [31847183301](https://github.com/Parkyongseok1120/UE5_Local_LLM_MCP_lmstudio/actions/runs/31847183301) completed successfully across Windows, Ubuntu, and macOS.
- GUI E2E was intentionally not run. Automated matrices do **not** prove every physical host, Unreal project, plugin combination, engine installation, or editor runtime.
- Apple Silicon physical FULL-install evidence is inherited from RC1/RC2 documentation. Windows physical install remains unverified, so RC3 remains a prerelease and `releaseReady` remains false.

## 한국어 요약

RC3는 RC2 이후 Develop에서 검증한 복구 상태기계, 원자적 mutation journal, 프로젝트·빌드 증명, Automation 범위, Windows/POSIX 경로 identity를 하나의 새 불변 태그로 묶는 프리릴리스입니다. 단계별 임시 next-action이 아니라 서버 소유 SSOT가 다음 obligation을 계산하며, 성공한 gate의 반복 실행과 이전 obligation 부활을 차단합니다.

정책은 무조건 엄격하게 막는 방식이 아닙니다. 서버가 소유한 증명 필드는 정확히 맞추되 무해한 호출 인자는 허용하고, 소스 실패와 인프라 실패에는 제한된 복구 기회를 제공합니다. 같은 입력의 동일 실패만 안정적인 blocker로 전환합니다.

작은 로컬 모델의 긴 이력 유지와 정확한 tool schema 생성 한계는 여전히 보이지만 새 benchmark로 수치화하지 않았습니다. GUI E2E도 실행하지 않았으므로 자동 테스트 범위를 넘어선 모든 프로젝트·OS·언리얼 조합의 물리 호환성을 주장하지 않습니다.
