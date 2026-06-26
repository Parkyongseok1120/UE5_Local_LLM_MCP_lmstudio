# Turn 3+ — Execute slice (thinking OFF, temperature 0.15)

Execute **one approved slice only** (≤3 files).

## Steps

1. Confirm Turn 2 critique PASS for this slice.
2. `detect_unreal_project` if build target unknown.
3. `read_file` targets → `replace_in_file` preferred over full `write_file`.
4. `build_unreal_project` after every C++/Build.cs change.
5. On UBT fail: log → `unreal_rag_search mode=compile_fix` → patch → rebuild (max 4 attempts).
6. On UBT pass: `unreal_runtime_config_check` → fix config if needed → rebuild.

## Hard limits

- Never edit >3 files in one turn.
- Never greenfield 8+ classes in one session — use more Turn 3+ slices.
- Never claim success without build log evidence.

## Agent tools (unreal-agent)

`replace_in_file`, `search_files`, `read_unreal_logs`, `get_active_project`.

See also: `prompts/lmstudio_unreal_agent_system.md`.
