# Patch report

Start SHA: 3c4375fd3041da16b4e6af721c17fa2c230d2f50 (리팩토링 준비). Final HEAD remains the start SHA; implementation is an uncommitted working-tree patch. The source patch hash and file inventory are in patch-manifest.json.

PredictionLoop dropped from the starting 187,252-byte Git blob to 80,622 UTF-8/LF bytes. This came with independent policy ownership for execution phase/transactions, exact final input and compaction, shared budgets, evidence lifecycle, capabilities and provider boundary, recovery and delivery.

The start-SHA range/capability/exposure/dispatch contracts remain tested: unknown provider start is not inferred from guard approval; completed exposure gates projection; ranges keep explicit units and representation; empty EOF has zero coverage; non-Git first-consumer bodies are retained; read authority is provider-qualified; mutation approval remains in force; raw tool syntax is never executed.

Ratchet changes include finite exact-input candidate measurement, mandatory floor preservation, unreachable-target diagnostics, shared batch reservations, pre-dispatch projected pressure and exact LOW success as the postcondition. Duplicate checkpoint/current-history accumulation is removed. No context size, retry count, recovery bound or HIGH-water shift was introduced.

Changed existing files: package test command, status inventory, context-budget compatibility facade, continuity-text.js, direct-compaction-core.js, prediction-loop.ts, tool-scope.ts, working-context.js, prediction-loop tests and status tests. New source, tests and measurement harnesses are listed in patch-manifest.json. stress-to-380k.cjs is an artifact measurement probe, not production source.

The 380K live stress measurement did not pass. It stopped after an actual model response reached maxPredictedTokensReached and contained the reasoning segment without a user-facing answer. This is retained as a measurement failure; product code was not altered in response.
