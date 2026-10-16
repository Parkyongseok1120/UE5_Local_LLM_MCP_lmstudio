# Model input identity: narrowly scoped follow-up

User scope: fix recovery modelInputId reuse, preserve contracts/lifetimes, run one live GUI task.

## Ownership and lifetime

- PredictionLoop owns executionId (a fresh UUID per handler invocation) and roundIndex (monotonic across actual rounds in that invocation).
- RecoveryCoordinator owns episode-local progress counters. reset() is valid for recovery policy, but its toolRounds must not identify model calls.
- An actual recovery call uses `${executionId}:research-recovery-${roundIndex + 1}`. The kind prefix is retained; IDs remain opaque strings to consumers.
- The same immutable local modelInputId flows through exact measurement, input exposure, provider/captured tool trace, round observation and completion exposure.
- Cancel terminates the handler and disposes its tool-use session. A later invocation has a new executionId even though roundIndex starts at zero.
- No global counter, durable ID registry, counter persistence, recovery policy change, or archive migration is introduced.
- Planning-only candidate IDs do not identify dispatched model calls. They are not counted as live predictions.

## Change boundary

The one-line ID fix and its original regression already existed in the working tree when this follow-up started. This follow-up retains that fix, adds the lifetime comment, strengthens the existing five-call re-entry regression, and adds canceled/new-execution isolation coverage. Existing unrelated edits are preserved. No additional budget, compaction, evidence retention, final-delivery, or provider policy is changed in this follow-up.

## Evidence

- `red-input-identity.log`: original implementation produced five calls with only four unique IDs; recovery-2 collided after fresh planning reset the episode counter.
- `test-identity-lifecycle.log`: 3 targeted tests passed, including cancellation preventing final model calls.
- `install-identity-only.log`: installer invokes the full build/test suite, 364 passed, 0 failed; install exit code 0, revision 117.
- `identity-only-live/`: one new GUI prompt, current loaded swift-qwen3.8-27b, context 38,912. The monitor joins by modelInputId alone and reports collisions; roundIndex is checked, never used to hide a collision.

## Rollback

Task start HEAD: 868dedf8069c5b6d015794e32e5435ad540db219. The working tree contains earlier uncommitted changes; do not reset the entire tree.

For this isolated fix only, the prior expression was `${executionId}:research-recovery-${recoveryCoordinator.toolRounds + 1}`. Restoring it reproduces the known collision and should fail the re-entry regression. No rollback was performed.

Live result and limitations are recorded separately after the single run completes.
