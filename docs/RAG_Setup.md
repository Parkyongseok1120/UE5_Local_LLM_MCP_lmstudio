# Direct RAG setup and portable maintenance

The integrated installer is the normal setup path:

```powershell
python install.py --profile standard --yes --build-rag
```

Add `--enable-agent-mode --accept-agent-risk` only when file mutation and build
authority are deliberately needed. The RAG MCP itself remains an eight-tool,
task-free factual evidence service.

## Portable `rag.ps1`

The packaged launcher is intentionally small. It supports only factual
collection, index build, Direct project selection, synchronous refresh, and
health inspection. It does not run a model, planner, task/route controller,
wrapper, or evaluation harness.

If Windows PowerShell blocks the script, keep the system policy unchanged and
use a per-command bypass:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 doctor
```

Select one exact existing descriptor, then refresh its complete project evidence:

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 refresh -RefreshScope project_source
.\rag.ps1 doctor
```

`project_source` runs the project text, profile, architecture/`Build.cs`, and
project-symbol collectors and commits the rebuilt index generation. Do not use a
manual project `collect-symbols` command that supplies only `-ProjectName`: project
symbol ownership requires both the descriptor stem and its exact root, and the
portable wrapper deliberately routes that work through `refresh`.

For full engine-source text, run the explicitly expensive collector before the
build:

```powershell
.\rag.ps1 collect-source -Root C:\UE_5.6\Engine\Source
.\rag.ps1 build-incremental
```

Change or clear the shared default project without creating task state:

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 clear-project
```

## Project provenance and engine-bound indexes

Project-scoped evidence is owned by the composite identity of the canonical
descriptor parent (`project_root`) and the `.uproject` descriptor stem
(`project`). A same-name checkout at another physical root is a different
project. Collection and Editor ingest reject rows that lack this exact root +
stem provenance or that claim a different descriptor, so selecting one clone
cannot replace or retrieve another clone's rows.

Legacy project rows are migrated only when their old path or prior descriptor
inventory proves one unambiguous owner. In particular, stem-only Editor rows are
adopted only when that stem previously mapped to exactly one canonical root.
Missing, malformed, ambiguous, or foreign provenance fails closed instead of
being guessed into the selected clone.

Integrated installs place generated indexes under
`<state-home>/indexes/<namespace>/rag.sqlite`; the default state home is
`~/.evidence-first` unless `--state-home` is supplied. A namespace is bound to the
resolved Unreal engine association/version (for example `unreal58`; custom
associations receive distinct deterministic namespaces). The runtime may select
a matching sibling shard whose `build_manifest.json` proves that binding, but a
single search or refresh never merges results across engine shards. A selection
spanning different engine bindings fails with
`RAG_MULTI_ENGINE_QUERY_UNSUPPORTED`; a missing or mismatched shard fails with
`RAG_ENGINE_INDEX_MISMATCH`.

`refresh` defaults to `project_source` and never starts Unreal Editor:

```powershell
.\rag.ps1 refresh
```

An Editor-metadata refresh ingests existing exports without launching Editor.
Starting Unreal Editor is a separate, explicit side effect and requires both an
Editor scope and `-AllowEditorLaunch`:

```powershell
.\rag.ps1 refresh -RefreshScope editor_metadata
.\rag.ps1 refresh -RefreshScope editor_metadata -AllowEditorLaunch
```

After rebuilding, restart the MCP processes so an already-running server opens
the current `rag.sqlite`. Ask questions through LM Studio/Cline and the
`unreal_rag_search` or `unreal_symbol_lookup` MCP capabilities; `rag.ps1` is not
a query/model frontend.

On Linux and macOS, invoke the same launcher with PowerShell Core, for example
`pwsh ./rag.ps1 doctor`.
