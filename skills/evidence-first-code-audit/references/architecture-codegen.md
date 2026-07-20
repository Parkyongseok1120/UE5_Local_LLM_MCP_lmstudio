# Architecture and code-generation obligations

## Architecture mode

Map these relations before proposing a design:

- state owner and mutation API;
- process owner for timers, progress, retries, and cancellation;
- callers and consumers;
- framework/lifecycle owner;
- configuration declaration and runtime reader;
- public API, schema, generated, and serialized surfaces;
- event publisher and subscribers;
- module, thread, process, or network boundary.

Produce three lists:

- `Existing`: owners and capabilities already present.
- `Proposed`: the smallest delta needed for the requested outcome.
- `DoNotDuplicate`: existing systems, data types, or services a later implementer must reuse.

Treat a generated architecture map as inventory evidence, not behavioral proof. Verify ownership and data flow from source or runtime evidence.

## Codegen mode

Define before editing:

- `invariants`: conditions the change must preserve or establish;
- `impactedSurfaces`: declarations, implementations, callsites, tests, configuration, generated/reflected assets, and external consumers;
- `validationPlan`: static checks, build checks, behavior tests, runtime logs/assertions, and migration checks;
- rollback or failure behavior for partial operations.

Generate the smallest change that satisfies the invariants. Do not add new abstractions when an existing owner can accept the capability without violating its contract.

## Validation ladder

Use the strongest available level and state what remains unproven:

1. `Proposed`: design or code exists but has not been checked.
2. `SourceVerified`: relevant contracts, paths, and callsites were inspected.
3. `StaticVerified`: linters, type checks, schema checks, or static validators pass.
4. `BuildVerified`: compilation, linking, bundling, or packaging succeeds.
5. `TestVerified`: deterministic tests prove the intended behavior.
6. `RuntimeVerified`: the real runtime path was observed.

Do not collapse BuildVerified into RuntimeVerified. Architecture changes often require consumer, serialization, asset, or deployment validation beyond compilation.

## Independent challenge

For P0/P1 or broad architecture changes, use a fresh-context reviewer when available. Give it raw source artifacts and the task, but not the expected answer. Its job is to identify unsupported claims, missed higher-severity failures, and unintended impact surfaces.
