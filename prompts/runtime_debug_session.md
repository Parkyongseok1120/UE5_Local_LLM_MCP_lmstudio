# Runtime Debug Session (UE 5.8)

1. Classify: compile vs runtime vs input vs replication
2. `read_unreal_logs` (unreal-agent) — filter Error/Assert/fatal
3. `unreal_rag_search` with `mode=runtime_debug` using log lines
4. Minimal patch via `write_file` (static validation)
5. Ask user to re-run PIE; do not claim fix without reproduction note
