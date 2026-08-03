# Troubleshooting

## Capability scorecard is not release-ready

```powershell
.\rag.ps1 report-tier-kpi
```

The report keeps `compile_fix`, `architecture`, `semantic_refactor`,
`runtime_debug`, `negative_control`, and `orchestration_ux` separate. A
`metrics-only`, skipped, or static-only run remains `not_run` or
`static_verified`; it cannot satisfy a live release claim. Run the missing live
suite shown in `data/baseline/tier-kpi-latest.json`.

If a row says `live_verified` but `Claim allowed` is `NO`, inspect its
`identityIssues` and `evidenceIssues`. Live reports need an auditable timestamp,
commit, model, suite/config, a positive executed-case count, and the required
numeric measurements.

If `bench-mcp` FTS flakes, rerun `.\rag.ps1 bench-mcp` alone (min-of-2 timing + one retry).

## Unreal Editor (optional)

**Tier A/B gates do not require Unreal Editor.**

| Environment | Phase 16 Editor export |
|-------------|------------------------|
| VRAM-limited desktop | Skip; ingest pre-exported JSONL via `collect-editor-metadata` |
| High-spec workstation | Run `tools/ue_export/*` in Editor Python, then ingest |

UBT command-line builds and LM Studio live eval are always allowed in the gate.

## Python not found

Install Python 3.10+ or use the Codex bundled runtime path checked by `rag.ps1` (`Find-Python`).

## Generic API queries return only project chunks

Set `UNREAL_RAG_PROJECT_ROUTING=v1` (default) and use MCP `scope=engine` or `mixed`. Legacy: `UNREAL_RAG_LEGACY_PROJECT_FILTER=0`.

## Build.cs index drift after parser fix

```powershell
.\rag.ps1 collect-symbols --tier public
.\rag.ps1 collect-module-graph
.\rag.ps1 build-incremental
```

## Wrapper rewrites entire large files

Use sampling profile with `preferPatchOverFullFile: true` or ensure model returns `patches[]` for files over ~200 lines.

## Eval harness failures

```powershell
.\rag.ps1 eval-harness
```

Reports land in `Reports/<timestamp>/summary.json`.

## Release / install verification

```powershell
.\rag.ps1 verify-release
.\rag.ps1 doctor
```

Output: `data/baseline/verify-release-latest.json`

## Regression gate

```powershell
.\rag.ps1 eval-regression
```

Compare deltas in `Reports/eval/deltas/`. See [Eval_Regression_Workflow.md](Eval_Regression_Workflow.md).

## Agent wrote files unexpectedly

Rerun `python install.py --profile standard --yes` to restore SAFE read-only defaults.

## AGENT write is reported as blocked after selecting Qwen directly

This is not a macOS Privacy & Security problem when the MCP result is `CONTEXT_COMPACTOR_NOT_ACTIVE`. Beta4 installs the LM Studio context proxy as advisory, so direct Qwen/GPT selection remains write-capable. Re-run the installer or `python scripts/patch_mcp_config.py`, then restart/toggle the MCP servers so `MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE=0`, `MCP_CONTEXT_COMPACTOR_ADVISORY=1`, and `MCP_FRONTEND=lmstudio` are loaded.

Only an administrator-set strict LM Studio policy may require selecting `unreal-context-compactor`. Cline, CLI, Ollama, custom, and remote clients must not be blocked by LM Studio proxy telemetry.

### Multi-turn chat goes off the rails after turn 1

Usually the chat model is still the underlying Qwen/GPT, so the context-compactor proxy never runs.

1. **Create a new chat.**
2. Load the underlying LLM once (leave it loaded).
3. Select **`unreal-context-compactor`** in that chat’s model dropdown.
4. Send one message, then run `npm --prefix lmstudio-context-compactor-plugin run status` and confirm fresh proxy telemetry / `PASS`.

Selecting Qwen directly bypasses compaction even when the plugin is installed and pinned.

If the error is `TASK_AUTH_INVALID_FORMAT` or `TASK_STATE_MISSING`, the model fabricated or reused invalid capability data. Call `unreal_agent_plan` once and use the returned authorization unchanged; do not fall back to paste-ready code.

## Oversized Unreal log may hide the original failure

- `read_unreal_logs mode=tail` reads recent failures.
- `mode=first_error` scans from byte zero for the first actionable error within the bounded scan budget.
- `mode=range cursorByte=N` returns `nextCursorByte` and `hasMore` for deterministic traversal.

When `sourceTruncated=true`, do not claim that the returned tail contains the root cause.

## Long task cannot write after an interruption

Inspect `unreal_task_checkpoint` with `action=status`.

- `TASK_LEASE_EXPIRED`: use authorized `recover`; an expired lease cannot be revived by heartbeat without checking checkpoint files.
- `TASK_CHECKPOINT_CONFLICT`: another process changed a tracked file. Review the conflict list. Use `rebase` with `acceptCurrentFiles=true` only after accepting those changes; previous pre-write gates are invalidated.
- `CHECKPOINT_PATH_OUTSIDE_PROJECT`: record project-relative files only. Checkpoints intentionally reject traversal and external paths.

## Semantic refactor gate stays closed

Run `unreal_semantic_refactor_guard` against a distinct isolated `afterRoot`, not the live project. The declared `changedFiles` must equal every `Source`/`Plugins`/`Config` difference, and `diffHash`, static/build proofs, observer snapshot hashes, and any runtime proof must all refer to that same transition. If a reflected/public/module/config surface changes, copy every reported breaking `surfaceId` into a complete migration/compatibility coverage entry with rationale, validation, and rollback.

## Runtime debug gate stays closed

`prepare` no longer authorizes a patch by itself. Record a supporting same-reproduction experiment, materialize and validate two to four isolated patch candidates, then call `compare_patch_candidates`. A textual “looks fixed” result cannot satisfy a policy that requires trace data, minimum samples/duration, or zero crashes/timeouts.
