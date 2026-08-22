# Historical Architecture Understanding Layer

> Archived with the unexposed evaluation analyzers; not part of Direct or Node Strict.

Generated architecture hints. Review before treating as project truth.

## Purpose

The architecture understanding layer scans Unreal project source text and produces compact, structured context for review and refactor planning. It is designed to help local models reason about module ownership, reflected surfaces, header/cpp pairs, component and subsystem boundaries, Blueprint-facing risk, and editor/runtime boundary risk.

The portable code-intelligence companion adds a dependency-free source graph, impact/regression scope, and candidate data/state analysis for non-Unreal projects as well. It is deliberately source-text conservative.

It does not call LM Studio, Unreal Editor, UnrealBuildTool, or network services.

## What It Does

- Detects project, plugin, module, Build.cs, target, source, and header/cpp pair surfaces.
- Parses Build.cs public/private dependencies with the existing parser.
- Detects UCLASS, USTRUCT, UINTERFACE, and simple regular C++ classes.
- Extracts UPROPERTY and UFUNCTION names/specifiers from source text.
- Extracts conservative non-reflected C++ member and method evidence into `memberEvidence`.
- Labels responsibility hints from deterministic name/base-type heuristics.
- Flags conservative risk hints such as Blueprint-facing surface, Blueprint event surface, serialized reflected surface, possible asset reference, missing cpp pair, and runtime/editor boundary risk.
- Validates structured architecture claims against the generated map.
- Builds a portable v2 symbol graph with direct `defines`, `includes`/`imports`, and `inherits` edges, plus explicitly heuristic `calls_candidate` edges.
- Produces source-backed generation contracts for generic examples, existing-file changes, and multifile changes.
- Builds graph-backed impact/regression contracts that distinguish direct source surfaces from candidate callers and declare a test-coverage gap when no focused test is found.
- Analyzes source-boundary dependencies plus candidate assignments/returns/call boundaries and state-looking assignments/setters.
- Validates an architecture proposal's decision, invariants, impacted surfaces, validation plan, and alternatives before allowing its implementation gate to pass.
- Infers lifecycle, cardinality, authority/replication/prediction, persistence, scale, designer/Blueprint, and thread/runtime/editor requirements.
- Searches ActorComponent, subsystem, owned UObject service, GAS, Mass, DataAsset/config, and module-boundary patterns and compositions, eliminates hard contradictions, and returns three to five scored candidates for consistent requirements. If none remain, it blocks selection and asks for the requirements to be corrected or partitioned.
- Requires matching source-owner evidence before recommending an owner, preserves close-score ambiguity and explicit override rationale, and never marks generated candidates implementation-ready without build/runtime proof.
- Requires an asset migration contract for `/Game/...` surfaces: fresh registry snapshot, referencer coverage, redirector policy, cook checks, and rollback.

## What It Does Not Do

- It does not execute refactors.
- It does not prove ownership.
- It does not inspect Blueprint graphs, Material graphs, or loaded assets.
- It does not prove that assets are unused.
- It does not replace compile-fix routing, UBT validation, or live project review.
- It does not prove call dispatch, data flow, state reachability, ownership, framework semantics, or runtime behavior from a graph edge or source-text pattern.

## Generate A Map

```powershell
python scripts/architecture_map.py --project "<PROJECT_ROOT_OR_UPROJECT>" --out data/architecture/architecture_map.json --markdown data/architecture/PROJECT_MAP.generated.md
```

If `--project` is omitted, the script falls back to the configured active project when available.

Generated files under `data/architecture/` are local artifacts and should not be committed.

## Portable P0–P3 code intelligence

Build a full-project graph with `--project-root`; this includes project plugins and test source while pruning generated/state directories before recursion. Use `--source-root` only when an intentionally narrower graph is required. The default output location remains compatible with the existing LM Studio sidecar consumer.

```powershell
python scripts/build_symbol_graph.py --project-root "<PROJECT_ROOT>"
python scripts/build_symbol_graph.py --source-root "<INTENTIONALLY_SCOPED_SOURCE_ROOT>"
python scripts/architecture_reasoning.py --project-root "<PROJECT_ROOT_OR_UPROJECT>" --symbol "TargetSymbol"
```

`symbol_graph.json` v2 preserves the legacy `symbols` list and adds `files`, `edges`, per-edge evidence, confidence, and a proof boundary. Direct edges prove only source-located declarations/textual relations. `calls_candidate`, candidate data flow, and candidate state transitions are navigation/review inputs; they must not be promoted to behavioral conclusions without a behavior path and the appropriate static/build/test/runtime evidence. Persisted graphs are content-checked before architecture/impact gates reuse them; stale or partial graphs are rebuilt or leave the write gate closed.

The CLI analyzers remain usable independently. `unreal_code_sketch_claim_validate` and `unreal_architecture_reasoning` are not exposed by either supported MCP entry: Direct deliberately exposes factual RAG only, while the separate Node Strict server adds only its small conversation lifecycle around the same Direct capabilities. The old Python workflow-gate implementations are unsupported repository-local history and are omitted from portable packages. When an analyzer is invoked explicitly as a CLI, a source-backed code proposal can supply `projectRoot`, `targetFiles`, and `changeKind`, and an architecture proposal contains at least:

```json
{
  "decision": "Keep the public contract in Core",
  "invariants": ["Game does not create a reverse Core dependency"],
  "impactedSurfaces": ["Source/Core/Public/Contract.h"],
  "validationPlan": ["build", "targeted regression"],
  "alternatives": ["Move implementation only, keep contract stable"]
}
```

The proposal validator checks document completeness, not design correctness. Wrong field types, unreadable/incomplete source, unmatched focus symbols, ambiguous or unsupported alternative selection, missing asset migration evidence, and detected source dependency cycles make that standalone validation fail. Its result never authorizes or blocks a Direct MCP read, write, build, test, stop decision, or final response. The selected model and user decide which build, regression, asset, and runtime evidence is appropriate.

## Validate Claims

```powershell
python scripts/architecture_claim_validate.py --architecture data/architecture/architecture_map.json --claims claims.json
```

Claim validation is structured and conservative. A claim should name a subject and list required evidence, for example:

```json
{
  "claims": [
    {
      "claim": "UCombatComponent owns combo state",
      "type": "ownership",
      "subject": "UCombatComponent",
      "requiredEvidence": ["reflected property", "function", "cpp pair"],
      "riskIfChanged": ["Blueprint-facing function may break"]
    }
  ]
}
```

## How This Differs From Compile-Fix

Compile-fix workflows answer "what change makes this build pass?" Architecture understanding answers "what structure and risk context should a reviewer consider before planning changes?" It is inspect-only context. Risky changes still need UBT, Blueprint, asset, and editor/runtime validation.

## Safety Notes

- Treat responsibility hints as hints, not facts.
- Prefer false negatives over false positives.
- Do not rename reflected Blueprint-facing names without migration and reference validation.
- Do not claim an asset is unused from source text alone.
- Do not add editor modules to runtime code without boundary review.
