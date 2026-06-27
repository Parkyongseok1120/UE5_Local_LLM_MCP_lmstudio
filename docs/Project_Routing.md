# Project routing

`scripts/project_routing.py` classifies queries:

- **engine** — generic Unreal API / UHT / Build.cs rules (no activeProject filter)
- **project** — local paths, agent edits, compile errors
- **mixed** — both local and engine evidence (separate context sections)

MCP: `unreal_rag_search` accepts `scope: auto|engine|project|mixed`.

Env:

- `UNREAL_RAG_PROJECT_ROUTING=v1` (default) — smart routing
- `UNREAL_RAG_LEGACY_PROJECT_FILTER=1` — always filter by activeProject when set
