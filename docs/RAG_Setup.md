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

Build project evidence with explicit roots:

```powershell
.\rag.ps1 collect-projects -Root C:\Projects\MyGame
.\rag.ps1 collect-symbols -Root C:\Projects\MyGame\Source -SymbolScope project -ProjectName MyGame
.\rag.ps1 build
.\rag.ps1 doctor
```

For full engine-source text, run the explicitly expensive collector before the
build:

```powershell
.\rag.ps1 collect-source -Root C:\UE_5.6\Engine\Source
.\rag.ps1 build-incremental
```

Select or clear the shared default project without creating task state:

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 clear-project
```

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
