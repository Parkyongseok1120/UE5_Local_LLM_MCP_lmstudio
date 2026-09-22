# Hybrid working context (opt-in)

Baseline: `7ad2a26493de20c821a040959a12d25e96fb67fa`, parent
`e7f4d85651a7654c6b247c038463d4e331247a2d`. Local branch
`v1.4.0-beta-2`; remote Develop also points to 7ad2a264. Initial worktree clean.
Luna's explicit generation caps, single report recovery and canonical-report
selection are part of this baseline. No installed plugin is replaced.

## Existing / reuse / extend / new

| Kind | Owner and contract |
|---|---|
| Existing / Reuse | buildCheckpoint/stateMemory, deterministic Git/file/operation facts, complete-exchange retention |
| Reuse | measureContext, selectMeasuredCandidate, resolveGenerationBudget, input-availability, observation-only tool classification |
| Extend | continuity-model-notes validation, local compactor state root, prediction-loop working history |
| New | bounded scoped evidence archive, read-only evidence tool, atomic window manifest, optional semantic handoff |
| Unverified | host-native chat/fork identifiers, installed model/engine E2E |

## Decision and trust boundary

Keep `legacy` as the default. `deterministic` enables archive/projection/window
selection; `hybrid` additionally permits one tools=[] local call per accepted
compaction event. This explicitly extends the previous no-extra-LLM-call contract.
All summary predictions use the selected model, explicit cap and shared deadline;
BOUNDED does not receive additional summary calls. No cloud service or planner.

SDK 1.5.0 exposes a prediction working directory, not a documented stable chat/fork
ID. The plugin therefore does not infer identity from transcript equality. In
deterministic/hybrid mode, its registered prompt preprocessor issues a signed,
opaque conversation ID and a fresh lineage ID for each user turn, binding the
parent lineage, normalized prior-history hash, workspace and repository. The
prediction loop verifies and strips this marker before model input, and restores a
window only when the parent manifest and exact prefix hash agree. Edited history,
copied/forged markers, a changed project/worktree or an unknown parent fail closed.
The hidden marker remains in the host/UI transcript; original messages are not
deleted or rewritten. A branch that starts from the same verified parent receives
a distinct child lineage on its next user turn, while host-native fork metadata is
still unavailable and is not claimed. If the preprocessor or signed scope is not
available, archives and windows remain invocation-local and report `session_only`.

Archive only completed, unambiguous observation exchanges. Mutation/build and
unknown tools stay intact. Keep original GUI messages; change only model-facing
copies after archive integrity verification. Projections contain original call ID,
status/error, exact archived and projected ranges, omitted ranges and archive ID.
Archive ranges are historical returned text, never current-file permission or a
claim to possess unreturned Git pages. Receipts/capabilities/secrets are not stored.
Sanitization changes are explicit and never described as byte-identical raw.

Use bounded local files below the existing compactor state root when a verified
scope is available, otherwise bounded memory. TTL, byte/file quotas, schema/hash
checks and scope separation fail closed. Lookup accepts only opaque IDs and bounded
UTF-16 ranges, not paths. A failed archive must not authorize raw eviction.

Compaction trigger and target differ: full input target 10000 tokens, trigger
target + 2048 tokens (hysteresis), measured after all instructions and tool schemas.
Retain mandatory system/current user/pending protocol even when target is
impossible. Record exact=false if tokenizer/template counting fails. Summary claims
are assistant messages, never system facts, execution success, approval or review
completion. Invalid JSON/refs, length, cancellation or timeout retain prior note.

## Costs, migration and rollback

Additional archive I/O, retrievals and summary input/output/time must be reported
separately and included in experiment totals. Shorter input alone is not success.
Keep A=Luna, B=deterministic, C=hybrid under identical model/template/cap/scope.
Schema optimization D requires measured schema-dominance evidence; it is not
enabled speculatively. Existing note files remain readable; new state is versioned.
Rollback selects legacy; no transcript rewrite or deletion is required. Delete only
the explicitly identified hybrid-v1 scoped directory to remove archived data.

## Stage gates

S0: baseline build/tests and API boundary. S1: archive/projection integrity,
isolation, pairing and SDK serialization. S2: target/floor and atomic prefix+delta
restoration tests, including restart/fork/project/stale candidate. S3: summary
validation/fallback/cancellation/cost tests. S4: at least ten event updates, paging,
Luna recovery and engine/root regressions; real-model results reported separately.
