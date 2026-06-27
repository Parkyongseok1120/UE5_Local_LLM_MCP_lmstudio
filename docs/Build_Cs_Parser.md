# Build.cs parser

Shared module: `scripts/parse_build_cs.py`

Supports:

- `PublicDependencyModuleNames.Add("Module")`
- `AddRange(new string[] { ... })` and `AddRange(new[] { ... })`
- `PrivateDependencyModuleNames`, include-path lists, `DynamicallyLoadedModuleNames`
- `if (Target.bBuildEditor) { ... }` as `conditional_dependencies`

After parser changes, reindex:

```powershell
.\rag.ps1 collect-symbols --tier public
.\rag.ps1 collect-module-graph
.\rag.ps1 build-incremental
```

See also [Build_Cs_Reindex.md](Build_Cs_Reindex.md).
