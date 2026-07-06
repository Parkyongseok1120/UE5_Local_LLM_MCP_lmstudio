# Runtime Debug Session (UE 5.8)

1. Classify: compile vs runtime vs input vs replication
2. `read_unreal_logs` (unreal-agent) — filter Error/Assert/fatal
3. `unreal_rag_search` with `mode=runtime_debug` using log lines
4. Minimal patch via `replace_in_file`; use `write_file` only for brand-new files
5. Ask user to re-run PIE; do not claim fix without reproduction note

Never use `run_javascript`, `js-code-sandbox`, `Deno.readTextFile`, `Deno.writeTextFile`, or Node `fs` for project file I/O.
