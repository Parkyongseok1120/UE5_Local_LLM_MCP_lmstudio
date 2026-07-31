# Runtime Debug Session (UE 5.8)

1. Classify: compile vs runtime vs input vs replication.
2. Read logs and direct source evidence; filter Error/Assert/fatal without dropping the reproduction context.
3. Call `unreal_runtime_debug_session:prepare` with one observer, baseline evidence, and falsifiable hypotheses.
4. Run the selected hypothesis experiment and record it with the same reproduction fingerprint and observer.
5. Materialize two to four isolated patch candidates, run identical static/build/invariant checks, and compare them.
6. Apply only the selected candidate, record the patch/build proof, and rerun the same observer.
7. Do not claim `RuntimeVerified` unless metric/trace/soak policy passes. A visual impression or compile success is not runtime proof.

Never use `run_javascript`, `js-code-sandbox`, `Deno.readTextFile`, `Deno.writeTextFile`, or Node `fs` for project file I/O.
