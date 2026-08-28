# Compact Model Setup

Compact local models need smaller load settings and tighter prompts, but they
do not need a second agent framework. The user selects the model in LM Studio;
the selected model remains the sole owner of reasoning, tool choice and order,
stopping, and the final answer.

## Current model status

```powershell
python scripts/load_sampling_preset.py --show-profile
```

Qwen 3.8 27B is the highly recommended primary validated model. Its v1.3.2 live
E2E run completed a long real-project RAG/read/report workflow without the prior
context truncation. Muse Glimmer is under testing and is not yet a validated
recommendation. Qwen 3.5, Qwen 3.6
27B, and GPT-OSS profile names may still exist for historical compatibility or
reproduction, but they are not current recommendations.

If the primary model does not fit the machine, select a compact model only after
validating its exact artifact, tool-call behavior, context size, and memory
headroom locally. This guide intentionally does not endorse an unvalidated
compact fallback.

## Keep the chat usable

These are operator recommendations, not enforced task stages:

- ask for one concrete outcome at a time when a compact model loses focus;
- keep irrelevant logs and generated files out of the prompt;
- use exact project selectors when multiple Unreal projects share the MCP;
- verify the active project's Unreal version before relying on engine-source
  evidence;
- prefer current file reads and concise diagnostics over pasting entire source
  trees into chat;
- use the checked-in Direct system prompt, not the historical turn prompts.

The model can call any available Direct tool whenever it judges the call useful.
There is no mandatory plan call, task activation, route gate, fixed tool order,
or profile-controlled retry loop.

## Safety is server-owned

Smaller models do not weaken the MCP safety boundary. Direct writes still use
path scope, receipt-first snapshot/CAS checks, atomic replacement, locks,
recoverable deletion approval, size limits, and bounded responses. Every
existing-file mutation explicitly passes its receipt or a valid raw
`expectedHash`; session state is never selected automatically. Semantic denylist results are non-blocking
advisories, including when the analyzer is unavailable. Builds are immediate
diagnostic operations when enabled; they are not a completion gate.

The profile's `writeSafety` fields are bounded compatibility inputs for the
legacy compile wrapper. They do not grant write permission and do not change
Direct MCP behavior.

## Optional compaction

Keep the LM Studio context compactor's top-level chat toggle OFF by default.
Installation/pinning only makes it available. For a long chat that needs bounded
continuity, enable that single switch; handler invocation activates compaction.
`Observe only` remains available for measurement without history rewriting.
Compaction retains bounded factual continuity: the active objective, continuation
antecedent, active project/current work, unresolved items, and relevant
file/tool/build facts. File observations retain canonical project/path identity and
the observed SHA but require a fresh read before mutation; runtime-local mutation
receipts are never durable compaction memory. It does not select a profile, alter static sampling,
create a plan, choose tools, require a next call, or decide that the user's
request is complete. A compact model works with or without the compactor.

For model-specific caveats and alias lookup, see
[`Model_Profiles.md`](Model_Profiles.md) and
[`Community_Finetune_Model_Optimization.md`](Community_Finetune_Model_Optimization.md).
