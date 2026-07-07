# Project Cache Inventory

When `activeProject` changes, some state must be invalidated immediately; other state is safe to keep or expires by TTL.

## Must invalidate on project switch

| State | Location | Key includes project? | Action |
|-------|----------|----------------------|--------|
| Project context cache | `project_context._PROJECT_CONTEXT_CACHE` | Yes (path + mtime) | `clear_project_context_cache()` |
| Wrapper file snapshots | `lmstudio_unreal_wrapper._PROJECT_SNAPSHOT_CACHE` | Yes (root path) | Clear dict on switch |
| Index staleness probe | `index_staleness._STALE_CACHE` | N/A | `invalidate_stale_cache()` |
| Symbol disk cache (best-effort) | `data/cache/*.json` | Yes (in cache key) | TTL expiry; optional purge via `invalidate_project_caches()` |

Hook: `project_switch_invalidate.on_project_switch_invalidate()` — called from MCP `unreal_set_active_project`.

## Safe to keep (global / engine-wide)

| State | Notes |
|-------|-------|
| `rag.sqlite` global index | Other projects' chunks remain; project filter excludes them in project scope |
| Engine symbols / guidelines raw JSONL | Shared across projects |
| `load_concept_aliases` / sampling presets | Model profile config, not project-specific |
| Compile loop jobs on disk | Each job stores its own `project_file`; not auto-cancelled |

## Re-sync after switch (not instant delete)

| State | Trigger |
|-------|---------|
| `raw_projects.jsonl` / project symbols | `ensure_active_project_ready()` or `unreal_rag_refresh` |
| Editor metadata JSONL | `unreal_sync_editor_metadata` when Content assets changed |
| SQLite chunks for active project | `incremental_build.py` after raw inputs update |

## Search leakage guard

When `engine_fallback` or `mixed` scope returns rows whose `project` column is not the active project, MCP search annotates them as other-project evidence. Do not cite them as facts about the active project without re-verification.
