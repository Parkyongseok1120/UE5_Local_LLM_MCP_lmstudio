---
name: evidence-first-code-audit
description: Audit code behavior, architecture, and generated-code plans with source-backed claims, end-to-end behavior paths, counterevidence, and explicit proof levels. Use for code review, bug diagnosis, architecture analysis, refactor planning, framework/API reasoning, state-machine review, data-flow review, or code generation where plausible but unverified explanations could cause wrong conclusions or changes.
---

# Evidence-First Code Audit

## Choose the mode

- Use **Audit** to diagnose behavior or find bugs without editing.
- Use **Architecture** to identify owners, boundaries, dependencies, and minimal design deltas.
- Use **Codegen** before generating or modifying code; establish invariants and validation obligations first.

Do not modify files unless the user explicitly requests implementation.

## Run the evidence workflow

1. Confirm the repository, requested scope, runtime/framework version, and write authority.
2. Detect the language, build system, test runner, framework, and repository-local instructions. Use their native inspection and validation tools.
3. Inventory definitions, owners, entry points, consumers, configuration, tests, and framework boundaries.
4. Read explicit contracts before judging implementation: interfaces, comments, schemas, tests, base classes, lifecycle hooks, and versioned documentation.
5. Trace important behavior from entry point through decisions to the final mutation or externally visible effect.
6. Separate `present`, `constructed`, `registered`, `called`, `mutates state`, and `observed` instead of treating them as equivalent.
7. Record major conclusions as typed evidence packets. Include counterevidence and unknowns rather than forcing certainty.
8. Challenge the leading explanation: test at least one alternative cause and scan once for a more severe failure.
9. Propose architecture or code only after the relevant evidence packets pass.

Read [references/audit-contract.md](references/audit-contract.md) for evidence packet fields, path tracing, and severity sweeps. Read [references/architecture-codegen.md](references/architecture-codegen.md) for architecture and implementation proof obligations. Use [references/portable-rule.md](references/portable-rule.md) when adapting the workflow to another coding agent.
Read [references/lmstudio.md](references/lmstudio.md) when installing or validating LM Studio integration.

## Apply non-negotiable gates

- Verify framework semantics from direct framework source or authoritative documentation before using them as a cause.
- Prove integration with a behavior path; symbol existence or construction alone is insufficient.
- In structured `behaviorPath` entries, use only `entry`, `decision`, `dispatch`, `mutation`, `side_effect`, or `observer`, in causal order. Set every `stageStatus` to `present`, `expected_missing`, or `unknown`; this separates reached behavior from a required gap. Describe construction or transformation in `symbol`/evidence rather than inventing a stage name.
- Compare symmetric paths when they should behave alike, such as player/enemy, client/server, success/failure, and start/recovery.
- Trace declared data to runtime readers before calling it functional.
- Close state transitions across entry, exit, recovery, cancellation, re-entry, overlap, and object destruction.
- Label claims `Bug`, `ByDesign`, `Ambiguous`, or `NeedsRuntimeProof`.
- Label proof `Proposed`, `SourceVerified`, `StaticVerified`, `BuildVerified`, `TestVerified`, or `RuntimeVerified`, and match it to evidence of that kind. Build success alone does not prove runtime behavior.

## Keep the core portable

Apply this skill without assuming a particular engine, language, source-tree layout, operating system, or agent frontend. Treat repository-specific prompts, protocol validators, linters, and build commands as adapters around this core contract. Do not copy adapter-only terminology into the portable evidence schema.

## Validate evidence packets

Store the audit packet as JSON and run:

```text
python scripts/validate_evidence_packet.py audit.json
```

The validator rejects unsupported critical findings, incomplete behavioral paths, framework claims without framework evidence, wiring claims without a mutation stage, and code-generation plans without invariants or validation obligations.

Install the skill cross-platform with a preview first:

```text
python scripts/install_skill.py --dry-run
python scripts/install_skill.py
```

On Windows, the PowerShell wrapper is also available:

```text
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-skill.ps1 -WhatIf
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-skill.ps1
```

Export the tool-neutral rule cross-platform to an explicit Composer/Cursor, Cline, Continue, or other agent rule path:

```text
python scripts/install_portable_rule.py <agent-rule-path> --dry-run
python scripts/install_portable_rule.py <agent-rule-path>
```

On Windows, the PowerShell wrapper is also available:

```text
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-portable-rule.ps1 -OutputPath <agent-rule-path> -WhatIf
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-portable-rule.ps1 -OutputPath <agent-rule-path>
```

For LM Studio, install the supplied config preset and read-only `evidence-first` MCP through the repository's integrated installer. The MCP validates reasoning packets but deliberately leaves project file access and execution to separately authorized project adapters.

## Report the result

Lead with the highest-severity verified finding. For each major finding, show:

- verdict and severity;
- source evidence;
- behavior path;
- counterevidence or alternative checked;
- proof level and remaining unknowns.

Keep architecture proposals delta-only. Reuse existing owners and explicitly list `DoNotDuplicate` surfaces.
