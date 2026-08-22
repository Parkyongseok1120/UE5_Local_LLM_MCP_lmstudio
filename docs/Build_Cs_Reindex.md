# Reindex after a Build.cs change

Select the exact project descriptor and run the bounded project-source refresh:

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 refresh -RefreshScope project_source
.\rag.ps1 doctor
```

`project_source` re-runs project text collection, the project profile,
architecture/`Build.cs` parsing, and full project-symbol collection, then commits
one new generation to the project's engine-bound shard. It records project
ownership as canonical descriptor root plus descriptor stem, so a same-name clone
is not refreshed accidentally.

Do not use the old `collect-symbols --tier public` example for a project. `public`
is an engine-symbol tier, and project-symbol collection requires both exact
project name and project root. The packaged launcher keeps that composite identity
inside the `set-project` + `refresh` path.

If the change was to the collector/parser implementation itself, use a fixture
first, then refresh a licensed project explicitly:

```powershell
python -m pytest tests/test_parse_build_cs.py tests/test_direct_rag_project_isolation.py -q --tb=short
.\rag.ps1 refresh -RefreshScope project_source
```
