# LM Studio Session Handoff

Use this when a chat approaches its context limit or hits any of these signals:

- `request (...) exceeds the available context size`
- `failed to restore kv cache`
- `Model failed to generate a tool call`
- the same failed write/build action repeats

Call `write_session_handoff` with:

- a one-sentence task summary
- changed files only
- up to five open errors
- the next three steps in order
- failed calls or approaches that must not be repeated

Then start a fresh chat, paste `prompts/lmstudio_session_bootstrap.md`, and ask:

> Read `.agent/handoff/latest.md` and continue from the smallest next step. Re-read the current target file before editing.

Do not paste the previous conversation or full build log into the new chat.
