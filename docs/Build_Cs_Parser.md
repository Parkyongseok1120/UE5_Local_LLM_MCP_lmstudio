# Build.cs parser

Shared module: `scripts/parse_build_cs.py`

Supports:

- `PublicDependencyModuleNames.Add("Module")`
- `AddRange(new string[] { ... })` and `AddRange(new[] { ... })`
- `PrivateDependencyModuleNames`, include-path lists, `DynamicallyLoadedModuleNames`
- `if (Target.bBuildEditor) { ... }` as `conditional_dependencies`

After parser changes, reindex:

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 refresh -RefreshScope project_source
.\rag.ps1 doctor
```

The high-level refresh preserves the exact descriptor root plus project stem
required by the project-symbol provenance contract. Do not use the old
name-only `collect-symbols` example for project source.

See [RAG_Setup.md](RAG_Setup.md) for the current portable collect/build commands.
