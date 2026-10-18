# Recovery-behaviour evaluation

This checks what a model does after a failed or partial tool result. It covers these shapes:

- a wrong engine path that returns NOT_FOUND
- an absolute engine path outside the allowed roots
- `coverage_limited` with zero matches
- a small `byteBudget` that cuts the read short
- a simulated partial projection
- a structure-only packet validation
- a budget-cut search that omits the one header inline assignment (`partial_search_absence`, from the 2026-10-18 audit)
- an engine call chain with an intermediate override (`super_chain`: `APawn::TakeDamage` between `ACharacter` and `AActor`)
- a `계속해` after a turn that stopped after its tool results without an answer (`resume_after_cancel`)

Two shapes from that audit are not scenarios here:

- **The shared read-budget denial.** The scripted harness calls tools directly, so the SDK guard that issues a denial never runs. `test/tool-round-lifecycle.test.cjs` covers the denial message instead.
- **A real PC restart.** `resume_after_cancel` replays the interrupted turn in one process. It is not a restart test.

`recovery-harness.cjs` runs the built prediction loop together with the in-process `unreal-agent` runtime. It uses a temporary engine and project, and it does not install anything. It records, for every run:

- the raw call arguments and results
- each model input
- all debug events

`score-recovery.cjs` scores a run using only what was recorded. Any item the record does not contain is scored `not_recorded`, never as a pass.

For source-recovery scenarios, a pass requires a successful follow-up result and the expected source text returned from the specified file. Changing arguments, finding a path, or mentioning the expected answer alone is insufficient. Successful `read_file`, `read_file_range`, and `read_symbol` results can supply source evidence. A recorded run error prevents a pass even when the answer contains the expected text.

There are two different kinds of result, and they must not be confused:

- **Harness tests.** `npm test` runs `test/recovery-eval.test.cjs` against a scripted fake model. This only shows that fault injection, recording and scoring work. It measures no model.
- **Model runs.** You need to start these yourself. First load the model in LM Studio. Then, in `lmstudio-context-compactor-plugin`:

  ```bash
  npm run build && node eval/recovery/run-recovery.cjs --model <identifier> --out eval-results/recovery
  ```

  The score in `summary.json` describes only that model with those settings (temperature 0 and the given `--seed`). It is not a general capability claim.


Unity recovery scenarios use the real in-process `createRuntime` file adapter and a scripted Bridge transport. They cover wrong paths, denied roots, budget paging, Editor reload and compile-result lookup. The Bridge replies are fixtures: these tests verify harness wiring and scoring, not a real Editor, model recovery ability or physical reboot. The separate Unity Editor smoke/RPC/Test Framework scripts exercise the native Bridge. `npm run unitycontract:check` checks the generated read-action profile against the Unity catalog.
