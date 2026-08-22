# Build.cs parser

Shared module: `scripts/parse_build_cs.py`

Supports:

- `PublicDependencyModuleNames.Add("Module")`
- `AddRange(new string[] { ... })` and `AddRange(new[] { ... })`
- `PrivateDependencyModuleNames`, include-path lists, `DynamicallyLoadedModuleNames`
- `if (Target.bBuildEditor) { ... }` as `conditional_dependencies`

After parser changes, reindex:

```powershell
.\rag.ps1 collect-symbols -Root C:\Projects\MyGame\Source -Tier public -SymbolScope project -ProjectName MyGame
.\rag.ps1 build-incremental
```

See [RAG_Setup.md](RAG_Setup.md) for the current portable collect/build commands.
