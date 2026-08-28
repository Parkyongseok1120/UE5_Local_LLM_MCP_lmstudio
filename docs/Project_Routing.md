# Project routing

> **DIRECT RETRIEVAL SCOPE, NOT WORKFLOW AUTHORITY.** This classifier only chooses which RAG index evidence to search. It does not assign task ownership, restrict the visible Direct catalog, select the model's next tool, or authorize a file/build operation. Exact per-call project selectors remain authoritative for project capabilities.

`scripts/project_routing.py` classifies queries:

- **engine** — generic Unreal API / UHT / Build.cs rules (no activeProject filter)
- **project** — local paths, agent edits, compile errors
- **mixed** — both local and engine evidence (separate context sections)

MCP: `unreal_rag_search` accepts `scope: auto|engine|project|mixed`.

Routing has one runtime policy:

- `scope=auto` uses the classifier above.
- API-looking queries can therefore resolve to engine evidence under `scope=auto`; use an exact `project` selector with `scope=project` when current project source is the intended corpus.
- An explicit per-call `scope` overrides classification.
- An explicit per-call `project` selector binds project evidence to that exact project.
- `use_active_project=false` requests engine-only retrieval when no explicit project is supplied.

Environment variables cannot disable or replace this policy. This keeps one predictable
classifier across LM Studio processes, projects, and Unreal Engine versions.
