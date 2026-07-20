# Audit contract

## Evidence packet

Represent each material claim with explicit type and proof:

```json
{
  "claim": "The update endpoint accepts the request but never persists the new state.",
  "claimType": "wiring",
  "verdict": "Bug",
  "severity": "P0",
  "proofLevel": "SourceVerified",
  "evidence": [
    {
      "kind": "project_source",
      "location": "src/api/update_handler.py:42",
      "observation": "The handler validates and returns without calling the state store."
    }
  ],
  "behaviorPath": [
    {"stage": "entry", "stageStatus": "present", "location": "src/api/routes.py:18", "symbol": "update_resource"},
    {"stage": "decision", "stageStatus": "present", "location": "src/api/update_handler.py:35", "symbol": "validate_update"},
    {"stage": "side_effect", "stageStatus": "expected_missing", "location": "src/state/store.py:24", "symbol": "store.save"},
    {"stage": "observer", "stageStatus": "present", "location": "src/api/update_handler.py:42", "symbol": "accepted response"}
  ],
  "counterEvidence": [
    {
      "kind": "project_source",
      "location": "src/jobs/batch_update.py:67",
      "observation": "The symmetric batch path explicitly calls store.save."
    }
  ],
  "unknowns": ["Middleware hooks and production traces were not supplied."]
}
```

Use one `claimType`:

- `existence`: a symbol, artifact, service, or configuration exists.
- `behavior`: an input produces an observable result.
- `framework_semantics`: a language, library, framework, or platform contract behaves a certain way.
- `wiring`: a declared or constructed surface is connected to a real mutation or side effect.
- `state_transition`: state can enter, exit, recover, cancel, or re-enter correctly.
- `data_flow`: declared data reaches the runtime consumer and output.
- `architecture`: ownership, dependency, lifecycle, or boundary claim.
- `codegen`: a proposed change satisfies a requirement or invariant.

Do not use optional booleans to classify a claim. A required type prevents an unsupported framework or wiring claim from bypassing its gate.

## Evidence kinds

- `requirement`: user request, specification, schema requirement, or acceptance criterion.
- `project_source`: repository source, configuration, migration, or checked-in contract.
- `framework_source`: direct language, library, framework, or platform implementation.
- `official_docs`: authoritative version-matched documentation.
- `static_analysis`: type checker, linter, analyzer, or schema-validation result.
- `build`: compiler, linker, bundler, or packaging result.
- `test`: deterministic automated test result.
- `runtime`: trace, log, metric, debugger, or observed execution.
- `generated_metadata`: generated call graph, architecture map, reflection output, or symbol index.

Treat generated metadata as a hint unless its generator proves the relevant relation. Do not use prior AI answers or failure memory as authoritative evidence.

Match proof to evidence:

- `SourceVerified` requires project/framework source or authoritative documentation.
- `StaticVerified` requires `static_analysis` evidence.
- `BuildVerified` requires `build` evidence.
- `TestVerified` requires `test` evidence.
- `RuntimeVerified` requires `runtime` evidence.
- `Proposed` may use requirements or source, but must not be presented as verified behavior.

## Behavior path

For `behavior`, `wiring`, `state_transition`, and `data_flow`, trace at least:

1. `entry`: input, event, public API, callback, job, message, or scheduler.
2. `decision` or `dispatch`: validation, branch, routing, policy, transformation, or queue handoff.
3. `mutation`, `side_effect`, or `observer`: state write, persistence, external call, event publication, rendered output, subscriber, or assertion.

A structured path may use only those six exact stage values, ordered from `entry` through `decision`/`dispatch` to the final effect. Every entry also requires one `stageStatus`: `present` means the stage exists on the evidence-backed path, `expected_missing` marks a required but absent/unreached stage, and `unknown` marks a stage that cannot yet be verified. An `unknown` stage requires an `Ambiguous` or `NeedsRuntimeProof` verdict. Represent construction or transformation in the nearest valid stage's symbol/evidence; never invent `construction`, `missing`, or similar stage names.

A path that ends at an event dispatcher, base call, queue, request object, or dependency construction is incomplete unless that surface is itself the claimed final effect.

## Presence ladder

Use the most precise status supported by evidence:

```text
declared → constructed → registered → reachable → called → mutates → observed
```

Apply stages only where the platform uses them. Never promote a status by assumption. An instantiated service may be unreachable from the relevant request. A configuration or schema field may have no runtime reader.

## State transition closure

For stateful systems, inspect:

- initial state and initialization source;
- valid entry and duplicate-entry behavior;
- normal exit;
- failure and cancellation;
- recovery or reset;
- overlapping callbacks, retries, timers, or effects;
- owner/process destruction and callback lifetime;
- concurrent, distributed, or asynchronous ordering when applicable.

## Counterexample pass

Before finalizing:

- try to disprove the leading cause;
- compare at least one symmetric path;
- search for a more severe failure in the same user-visible flow;
- distinguish absence of source evidence from proof of absence;
- downgrade to `Ambiguous` or `NeedsRuntimeProof` when the last step needs execution.

## Severity sweep

Order findings by user-visible failure, not by discovery order:

- `P0`: primary behavior cannot work, corrupts state, loses data, or creates a critical safety/security failure.
- `P1`: major path is inconsistent, fragile, or predictably fails under common conditions.
- `P2`: maintainability, extensibility, or lower-frequency correctness issue.
- `P3`: style or optional improvement.
