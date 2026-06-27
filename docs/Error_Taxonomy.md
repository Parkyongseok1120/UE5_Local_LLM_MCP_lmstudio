# Error taxonomy

`scripts/error_taxonomy.py` adds granular `error_subkind` mapped to broad RAG modes:

- `reflection_fix`, `module_fix`, `compile_fix`, `link_fix`, `runtime_debug`

Used by `collect_build_logs.py` and wrapper `rerag_for_build_errors`.

Subkinds include `GENERATED_H_MISSING`, `C1083_MISSING_INCLUDE`, `LNK_MISSING_CPP_DEFINITION`, etc.
