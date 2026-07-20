# Portable agent rule

Use this compact rule as the shared source when configuring another coding agent:

```text
For code audit, architecture, or code generation, work evidence-first.
Read contracts and direct framework semantics before causal claims. Trace each important behavior from entry through decisions to final mutation/side effect. Distinguish declared, constructed, registered, reachable, called, mutating, and observed. For every major claim choose one explicit claim type: existence, behavior, framework_semantics, wiring, state_transition, data_flow, architecture, or codegen. Report verdict, severity, typed evidence, behavior path, counterevidence, proof level, and unknowns. Match SourceVerified, StaticVerified, BuildVerified, TestVerified, and RuntimeVerified to source, static-analysis, build, test, and runtime evidence respectively. Compare symmetric paths and challenge the leading cause once. Scan for a more severe failure before finalizing. Treat generated architecture maps and failure memory as hints, not behavioral proof. Do not modify files unless explicitly authorized. Do not claim runtime correctness from build success alone.
```

Keep project-specific facts in the repository's local instruction file. Keep this rule global so the same reasoning contract follows the agent across projects and computers.
