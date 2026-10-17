# Recovery-behaviour evaluation

This checks what a model does after a failed or partial tool result. It covers six failure shapes:

- a wrong engine path that returns NOT_FOUND
- an absolute engine path outside the allowed roots
- `coverage_limited` with zero matches
- a small `byteBudget` that cuts the read short
- a simulated partial projection
- a structure-only packet validation

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
