# LM Studio user prompt — code sketch / 시안 (evidence-first)

Paste after [session bootstrap](lmstudio_session_bootstrap.md) when you want a **code draft / 시안**, not a finished, built change.

Use this whenever you would otherwise say "그냥 코드 예시/시안 보여줘". Plain "show me code" chat is where local models hallucinate APIs; this contract forces evidence first.

---

Code sketch request — MCP tools only. This is a **Proposed** draft, not a build.

**Goal:** <ONE_SENTENCE_TASK>

**Rules (in order):**

1. `unreal_agent_plan` first (`mode=code_sketch`). Follow the returned `checkpoints` and `toolPolicy`.
2. **Decompose before any code.** State, in plain text:
   - the real problem (e.g. "Sequencer 재생이 끝난 뒤 Actor를 시작 Transform으로 되돌리기")
   - the state that must be preserved (Actor Transform, CharacterMovement mode, Sequencer binding/restore state, Completion Mode)
   - the correct lifecycle point(s) and the restore ordering
   - the unknowns you still need to confirm
3. `unreal_rag_search` (`mode=codegen`, `hybrid=false`, `top_k=6`) for the concepts involved.
4. `unreal_symbol_lookup` for **every** Unreal type/function you intend to name. Do not name an API you did not look up.
5. Draft the sketch. Then call `unreal_code_sketch_claim_validate` with the drafted code/API list.
6. Fix or downgrade every `known_bad` / `unverified` symbol the validator reports. Mark anything you cannot confirm as `UNKNOWN` with the header/log/export needed to confirm it.

**Hard rules:**

- Do not invent Unreal APIs. `bRestoreState`, `SetBindingTag`, `AddBindingOverride`, `OnWorldDestroyed`-style names are red flags — verify or drop them.
- Do not blur similar concepts: Actor Tag ≠ Sequencer Binding Tag; Binding Override ≠ Dynamic Binding; Spawnable ≠ Possessable. Cite evidence for the one you use.
- "Compiles" ≠ "runs as intended." Keep proof level at **Proposed**; do not claim it builds or works.
- No `write_file` / `replace_in_file` / `build_unreal_project`. This is a sketch only. If the user wants it applied, they will start an edit task.
- One tool per turn. API names, types, and paths in English; Korean only for short summaries.

**Output shape:**

1. Problem decomposition (state / lifecycle / restore order / unknowns)
2. The sketch (labeled Proposed), with each Unreal API annotated `verified` / `UNKNOWN`
3. `unreal_code_sketch_claim_validate` result summary
4. Remaining risks and the smallest next step to raise the proof level
