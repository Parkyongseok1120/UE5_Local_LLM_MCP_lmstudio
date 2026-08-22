# Error taxonomy

> **Partly historical / superseded integration note.** The taxonomy module and
> `collect_build_logs.py` classification remain factual inputs. The legacy
> `rerag_for_build_errors` wrapper named below is preserved only to explain the
> old integration; it is not a current Direct MCP tool or supported workflow
> controller. Current factual lookup uses `unreal_rag_search` after bounded
> collection/refresh.

`scripts/error_taxonomy.py` adds granular `error_subkind` mapped to broad RAG modes:

- `reflection_fix`, `module_fix`, `compile_fix`, `link_fix`, `runtime_debug`

Used by `collect_build_logs.py` and wrapper `rerag_for_build_errors`.

Subkinds include `GENERATED_H_MISSING`, `C1083_MISSING_INCLUDE`, `LNK_MISSING_CPP_DEFINITION`, etc.
